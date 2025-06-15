"""Tests for the Pinboard client."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.models import Bookmark, TagCount


class TestPinboardClient:
    """Test the PinboardClient class."""

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_init(self, mock_pinboard_class, valid_token):
        """Test client initialization."""
        client = PinboardClient(valid_token)

        assert client.token == valid_token
        assert client.min_request_interval == 3.0
        assert client._bookmark_cache is None
        assert client._tag_cache is None
        mock_pinboard_class.assert_called_once_with(valid_token)

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_rate_limiting_sync(self, mock_pinboard_class, valid_token):
        """Test that synchronous rate limiting works correctly."""
        client = PinboardClient(valid_token)

        # Mock time.time() and time.sleep() to control timing
        with patch("time.time") as mock_time, patch("time.sleep") as mock_sleep:
            # Set initial time to avoid triggering rate limit on first call
            client.last_request_time = 10.0
            # Each call to _rate_limit_sync calls time.time() twice: once to check, once to update
            mock_time.side_effect = [14.0, 14.0, 15.0, 15.0, 18.0, 18.0]

            # First call should not sleep (14.0 - 10.0 = 4.0 > 3.0)
            client._rate_limit_sync()
            mock_sleep.assert_not_called()

            # Second call should sleep (15.0 - 14.0 = 1.0 < 3.0)
            client._rate_limit_sync()
            mock_sleep.assert_called_once_with(2.0)  # 3.0 - 1.0 = 2.0

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_get_all_bookmarks_cache_miss(
        self, mock_pinboard_class, valid_token, mock_pinboard_data
    ):
        """Test getting all bookmarks when cache is invalid."""
        # Mock pinboard.Bookmark objects
        mock_bookmarks = []
        for data in mock_pinboard_data:
            mock_bookmark = Mock()
            mock_bookmark.url = data["href"]
            mock_bookmark.description = data["description"]
            mock_bookmark.extended = data["extended"]
            mock_bookmark.tags = data["tags"].split() if data["tags"] else []
            # Mock datetime
            from datetime import datetime

            mock_bookmark.time = datetime.fromisoformat(
                data["time"].replace("Z", "+00:00")
            )
            mock_bookmarks.append(mock_bookmark)

        # Mock the pinboard client
        mock_pb = Mock()
        mock_pb.posts.recent.return_value = {"posts": mock_bookmarks}
        mock_pb.posts.update.return_value = {"update_time": "2024-01-15T10:30:00Z"}
        mock_pinboard_class.return_value = mock_pb

        client = PinboardClient(valid_token)

        with patch.object(client, "_rate_limit_sync") as mock_rate_limit:
            bookmarks = await client.get_all_bookmarks()

            assert len(bookmarks) == len(mock_pinboard_data)
            assert all(isinstance(b, Bookmark) for b in bookmarks)
            # Should call rate limiting for both update check and posts/all
            assert mock_rate_limit.call_count >= 1

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_search_bookmarks(
        self, mock_pinboard_class, valid_token, sample_bookmarks
    ):
        """Test searching bookmarks."""
        client = PinboardClient(valid_token)

        with patch.object(
            client, "get_all_bookmarks", new_callable=AsyncMock
        ) as mock_get_all:
            mock_get_all.return_value = sample_bookmarks

            # Test title search
            results = await client.search_bookmarks("Python", limit=10)

            assert len(results) > 0
            assert all(
                "python" in b.title.lower()
                or any("python" in tag.lower() for tag in b.tags)
                for b in results
            )
            mock_get_all.assert_called_once()

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_get_all_tags(self, mock_pinboard_class, valid_token, mock_tags_data):
        """Test getting all tags."""
        # Mock pinboard.Tag objects (returns list of Tag objects, not dict)
        mock_tag_objects = []
        for tag_name, count in mock_tags_data.items():
            mock_tag = Mock()
            mock_tag.name = tag_name
            mock_tag.count = count
            mock_tag_objects.append(mock_tag)

        # Mock the pinboard client
        mock_pb = Mock()
        mock_pb.tags.get.return_value = mock_tag_objects
        mock_pinboard_class.return_value = mock_pb

        client = PinboardClient(valid_token)

        with patch.object(client, "_rate_limit_sync"):
            tags = await client.get_all_tags()

            assert len(tags) == len(mock_tags_data)
            assert all(isinstance(tag, TagCount) for tag in tags)
            mock_pb.tags.get.assert_called_once()

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_close(self, mock_pinboard_class, valid_token):
        """Test client cleanup."""
        client = PinboardClient(valid_token)

        # Mock the executor shutdown method
        with patch.object(client._executor, "shutdown") as mock_shutdown:
            await client.close()
            mock_shutdown.assert_called_once_with(wait=True)

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_search_bookmarks_with_exact_tag_match(
        self, mock_pinboard_class, valid_token
    ):
        """Test search with exact tag match uses direct tag search."""
        client = PinboardClient(valid_token)

        # Mock tags.get() to return a tag that matches the query
        mock_tags = [Mock(name="python", count=5), Mock(name="web", count=3)]
        client._pb.tags.get.return_value = mock_tags

        # Mock posts.all() for tag search
        mock_posts = [
            Mock(
                url="https://example.com/1",
                description="Python Guide",
                extended="Notes",
                tags=["python"],
                time=Mock(isoformat=Mock(return_value="2024-01-01T00:00:00")),
            )
        ]
        client._pb.posts.all.return_value = mock_posts

        with patch.object(client, "get_all_bookmarks", return_value=[]):
            with patch.object(
                client, "get_all_tags", return_value=[TagCount(tag="python", count=5)]
            ):
                results = await client.search_bookmarks("python", limit=10)

                # Should find results via tag search
                assert len(results) == 1
                assert results[0].title == "Python Guide"

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_search_bookmarks_with_expansion(
        self, mock_pinboard_class, valid_token
    ):
        """Test search that triggers expansion when no initial matches."""
        client = PinboardClient(valid_token)

        # Mock initial empty bookmarks
        with patch.object(client, "get_all_bookmarks") as mock_get_bookmarks:
            with patch.object(client, "get_all_tags", return_value=[]):
                # First call returns empty, second call (with expansion) returns results
                mock_bookmark = Bookmark.from_pinboard(
                    {
                        "href": "https://example.com/expanded",
                        "description": "Expanded Result",
                        "extended": "Contains searchterm",
                        "tags": "",
                        "time": "2024-01-01T00:00:00Z",
                    }
                )
                mock_get_bookmarks.side_effect = [[], [mock_bookmark]]

                results = await client.search_bookmarks("searchterm", limit=10)

                # Should find results after expansion
                assert len(results) == 1
                assert results[0].title == "Expanded Result"

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_search_bookmarks_tag_search_exception(
        self, mock_pinboard_class, valid_token
    ):
        """Test search handles exceptions during tag search gracefully."""
        client = PinboardClient(valid_token)

        with patch.object(client, "get_all_bookmarks", return_value=[]):
            with patch.object(
                client, "get_all_tags", return_value=[TagCount(tag="python", count=5)]
            ):
                with patch.object(
                    client, "_search_by_tag_direct", side_effect=Exception("API Error")
                ):
                    # Should not raise exception, should return empty results
                    results = await client.search_bookmarks("python", limit=10)
                    assert len(results) == 0

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_search_bookmarks_extended_with_tag_match(
        self, mock_pinboard_class, valid_token
    ):
        """Test extended search with exact tag match."""
        client = PinboardClient(valid_token)

        # Mock tag search
        mock_posts = [
            Mock(
                url="https://example.com/tag",
                description="Tag Result",
                extended="",
                tags=["python"],
                time=Mock(isoformat=Mock(return_value="2024-01-01T00:00:00")),
            )
        ]
        client._pb.posts.all.return_value = mock_posts

        with patch.object(
            client, "get_all_tags", return_value=[TagCount(tag="python", count=5)]
        ):
            results = await client.search_bookmarks_extended(
                "python", days_back=30, limit=10
            )

            assert len(results) == 1
            assert results[0].title == "Tag Result"

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_search_bookmarks_extended_time_based(
        self, mock_pinboard_class, valid_token
    ):
        """Test extended search with time-based search when no tag match."""
        client = PinboardClient(valid_token)

        # Mock time-based search
        mock_posts = [
            Mock(
                url="https://example.com/time",
                description="Time Result",
                extended="Contains query",
                tags=[],
                time=Mock(isoformat=Mock(return_value="2024-01-01T00:00:00")),
            )
        ]
        client._pb.posts.all.return_value = mock_posts

        with patch.object(client, "get_all_tags", return_value=[]):
            results = await client.search_bookmarks_extended(
                "query", days_back=30, limit=10
            )

            assert len(results) == 1
            assert results[0].title == "Time Result"

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_get_bookmarks_by_tags_with_tag_search_fallback(
        self, mock_pinboard_class, valid_token
    ):
        """Test get_bookmarks_by_tags falls back to direct tag search."""
        client = PinboardClient(valid_token)

        # Mock posts.all() for tag search
        mock_posts = [
            Mock(
                url="https://example.com/tag",
                description="Tag Bookmark",
                extended="",
                tags=["python"],
                time=Mock(isoformat=Mock(return_value="2024-01-01T00:00:00")),
            )
        ]
        client._pb.posts.all.return_value = mock_posts

        with patch.object(client, "get_all_bookmarks", return_value=[]):
            results = await client.get_bookmarks_by_tags(["python"], limit=10)

            assert len(results) == 1
            assert results[0].title == "Tag Bookmark"

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_get_bookmarks_by_tags_search_exception(
        self, mock_pinboard_class, valid_token
    ):
        """Test get_bookmarks_by_tags handles tag search exceptions."""
        client = PinboardClient(valid_token)

        with patch.object(client, "get_all_bookmarks", return_value=[]):
            with patch.object(
                client, "_search_by_tag_direct", side_effect=Exception("API Error")
            ):
                results = await client.get_bookmarks_by_tags(["python"], limit=10)
                assert len(results) == 0

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_cache_validity_check_exception(
        self, mock_pinboard_class, valid_token
    ):
        """Test cache validity check handles exceptions gracefully."""
        client = PinboardClient(valid_token)

        # Mock posts.update() to raise exception
        client._pb.posts.update.side_effect = Exception("Network error")

        # Should return False when exception occurs
        is_valid = await client._check_cache_validity()
        assert is_valid is False

    @pytest.mark.asyncio
    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    async def test_refresh_bookmark_cache_with_expansion(
        self, mock_pinboard_class, valid_token
    ):
        """Test bookmark cache refresh with expand_search=True."""
        client = PinboardClient(valid_token)

        # Mock posts.all() for expanded search
        mock_posts = [
            Mock(
                url="https://example.com/expanded",
                description="Expanded Bookmark",
                extended="",
                tags=["test"],
                time=Mock(isoformat=Mock(return_value="2024-01-01T00:00:00")),
            )
        ]
        client._pb.posts.all.return_value = mock_posts

        await client._refresh_bookmark_cache(expand_search=True)

        assert client._has_expanded_data is True
        assert len(client._bookmark_cache) == 1
        assert client._bookmark_cache[0].title == "Expanded Bookmark"

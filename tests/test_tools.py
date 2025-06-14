"""Tests for MCP tools."""

from unittest.mock import Mock

import pytest

from pinboard_mcp_server.models import SearchResult
from pinboard_mcp_server.tools import (
    ListBookmarksByTagsParams,
    ListRecentBookmarksParams,
    SearchBookmarksParams,
    list_bookmarks_by_tags,
    list_recent_bookmarks,
    list_tags,
    search_bookmarks,
)


class TestSearchBookmarksTool:
    """Test the searchBookmarks tool."""

    @pytest.mark.asyncio
    async def test_search_bookmarks(self, mock_client, sample_bookmarks):
        """Test searching bookmarks."""
        mock_client.search_bookmarks.return_value = sample_bookmarks[:2]

        tool_func = search_bookmarks(mock_client)
        params = SearchBookmarksParams(query="python", limit=10)
        context = Mock()
        context.session.logger = Mock()

        result = await tool_func(params, context)

        assert isinstance(result, SearchResult)
        assert len(result.bookmarks) == 2
        assert result.query == "python"
        assert result.total == 2
        mock_client.search_bookmarks.assert_called_once_with(query="python", limit=10)


class TestListRecentBookmarksTool:
    """Test the listRecentBookmarks tool."""

    @pytest.mark.asyncio
    async def test_list_recent_bookmarks(self, mock_client, sample_bookmarks):
        """Test listing recent bookmarks."""
        mock_client.get_recent_bookmarks.return_value = sample_bookmarks[:1]

        tool_func = list_recent_bookmarks(mock_client)
        params = ListRecentBookmarksParams(days=7, limit=20)
        context = Mock()
        context.session.logger = Mock()

        result = await tool_func(params, context)

        assert isinstance(result, SearchResult)
        assert len(result.bookmarks) == 1
        assert result.total == 1
        mock_client.get_recent_bookmarks.assert_called_once_with(days=7, limit=20)


class TestListBookmarksByTagsTool:
    """Test the listBookmarksByTags tool."""

    @pytest.mark.asyncio
    async def test_list_bookmarks_by_tags(self, mock_client, sample_bookmarks):
        """Test listing bookmarks by tags."""
        mock_client.get_bookmarks_by_tags.return_value = sample_bookmarks[:2]

        tool_func = list_bookmarks_by_tags(mock_client)
        params = ListBookmarksByTagsParams(tags=["python", "web"], limit=20)
        context = Mock()
        context.session.logger = Mock()

        result = await tool_func(params, context)

        assert isinstance(result, SearchResult)
        assert len(result.bookmarks) == 2
        assert result.tags == ["python", "web"]
        assert result.total == 2
        mock_client.get_bookmarks_by_tags.assert_called_once_with(
            tags=["python", "web"], from_date=None, to_date=None, limit=20
        )

    @pytest.mark.asyncio
    async def test_list_bookmarks_by_tags_with_dates(self, mock_client, sample_bookmarks):
        """Test listing bookmarks by tags with date range."""
        mock_client.get_bookmarks_by_tags.return_value = sample_bookmarks[:1]

        tool_func = list_bookmarks_by_tags(mock_client)
        params = ListBookmarksByTagsParams(
            tags=["python"],
            from_date="2024-01-01",
            to_date="2024-01-31",
            limit=10
        )
        context = Mock()
        context.session.logger = Mock()

        result = await tool_func(params, context)

        assert isinstance(result, SearchResult)
        assert len(result.bookmarks) == 1
        mock_client.get_bookmarks_by_tags.assert_called_once()

        # Check that dates were parsed correctly
        call_args = mock_client.get_bookmarks_by_tags.call_args
        assert call_args.kwargs["from_date"].year == 2024
        assert call_args.kwargs["from_date"].month == 1
        assert call_args.kwargs["from_date"].day == 1


class TestListTagsTool:
    """Test the listTags tool."""

    @pytest.mark.asyncio
    async def test_list_tags(self, mock_client, sample_tags):
        """Test listing all tags."""
        mock_client.get_all_tags.return_value = sample_tags

        tool_func = list_tags(mock_client)
        context = Mock()
        context.session.logger = Mock()

        result = await tool_func(context)

        assert isinstance(result, list)
        assert len(result) == len(sample_tags)
        assert all(hasattr(tag, 'tag') and hasattr(tag, 'count') for tag in result)
        mock_client.get_all_tags.assert_called_once()

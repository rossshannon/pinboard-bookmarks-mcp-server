"""Tests for data models."""

from datetime import datetime

from pinboard_mcp_server.models import Bookmark, SearchResult, TagCount


class TestBookmark:
    """Test the Bookmark model."""

    def test_bookmark_creation(self):
        """Test creating a bookmark directly."""
        bookmark = Bookmark(
            url="https://example.com",
            title="Test Bookmark",
            tags=["test", "example"],
            notes="Test notes",
            saved_at=datetime.now(),
        )

        assert bookmark.url == "https://example.com"
        assert bookmark.title == "Test Bookmark"
        assert bookmark.tags == ["test", "example"]
        assert bookmark.notes == "Test notes"
        assert bookmark.id is not None

    def test_bookmark_from_pinboard(self):
        """Test creating a bookmark from Pinboard API data."""
        pinboard_data = {
            "href": "https://example.com/test",
            "description": "Test Title",
            "extended": "Test notes here",
            "tags": "python testing",
            "time": "2024-01-15T10:30:00Z",
        }

        bookmark = Bookmark.from_pinboard(pinboard_data)

        assert bookmark.url == "https://example.com/test"
        assert bookmark.title == "Test Title"
        assert bookmark.notes == "Test notes here"
        assert bookmark.tags == ["python", "testing"]
        assert bookmark.saved_at.year == 2024
        assert bookmark.saved_at.month == 1
        assert bookmark.saved_at.day == 15

    def test_bookmark_from_pinboard_empty_tags(self):
        """Test creating a bookmark with empty tags."""
        pinboard_data = {
            "href": "https://example.com/test",
            "description": "Test Title",
            "extended": "Test notes",
            "tags": "",
            "time": "2024-01-15T10:30:00Z",
        }

        bookmark = Bookmark.from_pinboard(pinboard_data)
        assert bookmark.tags == []

    def test_bookmark_from_pinboard_no_extended(self):
        """Test creating a bookmark with no extended notes."""
        pinboard_data = {
            "href": "https://example.com/test",
            "description": "Test Title",
            "extended": "",
            "tags": "python",
            "time": "2024-01-15T10:30:00Z",
        }

        bookmark = Bookmark.from_pinboard(pinboard_data)
        assert bookmark.notes == ""


class TestTagCount:
    """Test the TagCount model."""

    def test_tag_count_creation(self):
        """Test creating a TagCount."""
        tag_count = TagCount(tag="python", count=42)

        assert tag_count.tag == "python"
        assert tag_count.count == 42


class TestSearchResult:
    """Test the SearchResult model."""

    def test_search_result_creation(self, sample_bookmarks):
        """Test creating a SearchResult."""
        result = SearchResult(
            bookmarks=sample_bookmarks[:2], total=2, query="python", tags=None
        )

        assert len(result.bookmarks) == 2
        assert result.total == 2
        assert result.query == "python"
        assert result.tags is None

    def test_search_result_with_tags(self, sample_bookmarks):
        """Test creating a SearchResult with tags."""
        result = SearchResult(
            bookmarks=sample_bookmarks, total=3, query=None, tags=["python", "web"]
        )

        assert len(result.bookmarks) == 3
        assert result.total == 3
        assert result.query is None
        assert result.tags == ["python", "web"]

"""Pytest configuration and fixtures for Pinboard MCP Server tests."""

from unittest.mock import AsyncMock, Mock

import pytest

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.models import Bookmark, TagCount


@pytest.fixture
def mock_pinboard_data() -> dict:
    """Sample Pinboard API response data."""
    return [
        {
            "href": "https://example.com/python-testing",
            "description": "Python Testing Best Practices",
            "extended": "Comprehensive guide to testing in Python with pytest",
            "tags": "python testing pytest",
            "time": "2024-01-15T10:30:00Z",
        },
        {
            "href": "https://example.com/fastapi-tutorial",
            "description": "FastAPI Tutorial",
            "extended": "Learn how to build APIs with FastAPI",
            "tags": "python fastapi web",
            "time": "2024-01-10T15:45:00Z",
        },
        {
            "href": "https://example.com/async-programming",
            "description": "Async Programming in Python",
            "extended": "",
            "tags": "python async asyncio",
            "time": "2024-01-05T09:20:00Z",
        },
    ]


@pytest.fixture
def mock_tags_data() -> dict:
    """Sample Pinboard tags API response data."""
    return {
        "python": 3,
        "testing": 1,
        "pytest": 1,
        "fastapi": 1,
        "web": 1,
        "async": 1,
        "asyncio": 1,
    }


@pytest.fixture
def sample_bookmarks(mock_pinboard_data) -> list[Bookmark]:
    """Create sample Bookmark objects from mock data."""
    return [Bookmark.from_pinboard(post) for post in mock_pinboard_data]


@pytest.fixture
def sample_tags(mock_tags_data) -> list[TagCount]:
    """Create sample TagCount objects from mock data."""
    return [TagCount(tag=tag, count=count) for tag, count in mock_tags_data.items()]


@pytest.fixture
async def mock_client(
    mock_pinboard_data, mock_tags_data, sample_bookmarks, sample_tags
):
    """Create a mocked PinboardClient for testing."""
    client = Mock(spec=PinboardClient)

    # Mock async methods
    client.get_all_bookmarks = AsyncMock(return_value=sample_bookmarks)
    client.get_all_tags = AsyncMock(return_value=sample_tags)
    client.search_bookmarks = AsyncMock(return_value=sample_bookmarks[:2])
    client.get_recent_bookmarks = AsyncMock(return_value=sample_bookmarks[:1])
    client.get_bookmarks_by_tags = AsyncMock(return_value=sample_bookmarks[:2])
    client.close = AsyncMock()

    return client


@pytest.fixture
def valid_token() -> str:
    """Valid Pinboard API token for testing."""
    return "testuser:1234567890ABCDEF"


@pytest.fixture
def api_token(monkeypatch, valid_token):
    """Set PINBOARD_TOKEN environment variable."""
    monkeypatch.setenv("PINBOARD_TOKEN", valid_token)
    return valid_token

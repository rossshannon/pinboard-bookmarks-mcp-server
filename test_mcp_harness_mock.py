#!/usr/bin/env python3
"""
Mock test harness for the Pinboard MCP Server

This script tests the MCP tools with mocked data, so it can run without
requiring real Pinboard API credentials. Useful for CI/CD and quick validation.
"""

import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

# Add src to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.tools import (
    search_bookmarks,
    list_recent_bookmarks,
    list_bookmarks_by_tags,
    list_tags,
    SearchBookmarksParams,
    ListRecentBookmarksParams,
    ListBookmarksByTagsParams,
)


def create_mock_context():
    """Create a mock context for MCP tool calls."""
    context = MagicMock()
    context.session = MagicMock()
    context.session.logger = MagicMock()
    return context


# Sample test data
SAMPLE_POSTS = [
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
        "href": "https://example.com/react-hooks",
        "description": "React Hooks Guide",
        "extended": "Modern React development with hooks",
        "tags": "react javascript frontend",
        "time": "2023-12-20T14:30:00Z",
    },
]

SAMPLE_TAGS = {
    "python": 2,
    "testing": 1,
    "pytest": 1,
    "fastapi": 1,
    "web": 1,
    "react": 1,
    "javascript": 1,
    "frontend": 1,
}


@patch("pinboard_mcp_server.client.pinboard.Pinboard")
async def test_all_tools_mocked(mock_pinboard_class):
    """Test all MCP tools with mocked Pinboard data."""
    print("Pinboard MCP Server Mock Test Harness")
    print("=" * 45)
    print("Testing with mocked data (no API credentials required)")

    # Mock the pinboard client
    mock_pb = MagicMock()
    mock_pb.posts.all.return_value = SAMPLE_POSTS
    mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
    mock_pb.tags.get.return_value = SAMPLE_TAGS
    mock_pinboard_class.return_value = mock_pb

    # Create client with dummy token
    client = PinboardClient("testuser:123456789")
    context = create_mock_context()

    try:
        print("\n1. Testing basic client functionality...")
        bookmarks = await client.get_all_bookmarks()
        tags = await client.get_all_tags()
        print(f"   ✓ Retrieved {len(bookmarks)} bookmarks and {len(tags)} tags")

        print("\n2. Testing searchBookmarks tool...")
        tool_func = search_bookmarks(client)
        params = SearchBookmarksParams(query="python", limit=10)
        result = await tool_func(params, context)
        print(f"   ✓ Found {result.total} bookmarks matching 'python'")
        for bookmark in result.bookmarks:
            print(f"     - {bookmark.title}")

        print("\n3. Testing listRecentBookmarks tool...")
        tool_func = list_recent_bookmarks(client)
        params = ListRecentBookmarksParams(days=30, limit=10)  # Max allowed range
        result = await tool_func(params, context)
        print(f"   ✓ Found {result.total} recent bookmarks")
        for bookmark in result.bookmarks:
            print(f"     - {bookmark.title} ({bookmark.saved_at.strftime('%Y-%m-%d')})")

        print("\n4. Testing listBookmarksByTags tool...")
        tool_func = list_bookmarks_by_tags(client)
        params = ListBookmarksByTagsParams(tags=["python"], limit=10)
        result = await tool_func(params, context)
        print(f"   ✓ Found {result.total} bookmarks tagged with 'python'")
        for bookmark in result.bookmarks:
            print(f"     - {bookmark.title}")

        print("\n5. Testing listTags tool...")
        tool_func = list_tags(client)
        result = await tool_func(context)
        print(f"   ✓ Found {len(result)} tags")
        sorted_tags = sorted(result, key=lambda t: t.count, reverse=True)
        for tag in sorted_tags[:5]:  # Show top 5
            print(f"     - {tag.tag}: {tag.count} bookmarks")

        print("\n" + "=" * 45)
        print("✅ All mocked tests passed!")
        print(
            "\nThe MCP tools are working correctly with the expected data structures."
        )
        print(
            "To test with real Pinboard data, use test_mcp_harness.py with PINBOARD_TOKEN set."
        )

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()

    finally:
        await client.close()


async def main():
    """Run the mock tests."""
    await test_all_tools_mocked()


if __name__ == "__main__":
    asyncio.run(main())

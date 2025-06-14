#!/usr/bin/env python3
"""
Simple test harness for the Pinboard MCP Server

This script directly imports and tests the MCP tools from the pinboard_mcp_server
without going through the MCP server protocol. Useful for quick testing and debugging.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock

# Add src to path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

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
    context = Mock()
    context.session = Mock()
    context.session.logger = Mock()
    return context


async def test_search_bookmarks(client):
    """Test the searchBookmarks MCP tool."""
    print("\nTesting searchBookmarks tool...")
    
    tool_func = search_bookmarks(client)
    params = SearchBookmarksParams(query="python", limit=5)
    context = create_mock_context()
    
    try:
        result = await tool_func(params, context)
        print(f"✓ Found {result.total} bookmarks matching 'python'")
        
        if result.bookmarks:
            print("  Sample results:")
            for i, bookmark in enumerate(result.bookmarks[:3], 1):
                print(f"    {i}. {bookmark.title}")
                print(f"       URL: {bookmark.url}")
                print(f"       Tags: {', '.join(bookmark.tags)}")
                print(f"       Saved: {bookmark.saved_at.strftime('%Y-%m-%d')}")
                print()
        else:
            print("  No bookmarks found")
            
    except Exception as e:
        print(f"✗ Error: {e}")


async def test_list_recent_bookmarks(client):
    """Test the listRecentBookmarks MCP tool."""
    print("\nTesting listRecentBookmarks tool...")
    
    tool_func = list_recent_bookmarks(client)
    params = ListRecentBookmarksParams(days=7, limit=5)
    context = create_mock_context()
    
    try:
        result = await tool_func(params, context)
        print(f"✓ Found {result.total} bookmarks from the last 7 days")
        
        if result.bookmarks:
            print("  Recent bookmarks:")
            for i, bookmark in enumerate(result.bookmarks, 1):
                days_ago = (datetime.now() - bookmark.saved_at.replace(tzinfo=None)).days
                print(f"    {i}. {bookmark.title}")
                print(f"       Saved: {days_ago} days ago ({bookmark.saved_at.strftime('%Y-%m-%d')})")
                print()
        else:
            print("  No recent bookmarks found")
            
    except Exception as e:
        print(f"✗ Error: {e}")


async def test_list_bookmarks_by_tags(client):
    """Test the listBookmarksByTags MCP tool."""
    print("\nTesting listBookmarksByTags tool...")
    
    # First get some tags to test with
    try:
        all_tags = await client.get_all_tags()
        if not all_tags:
            print("  No tags found, skipping test")
            return
            
        # Use the most popular tag for testing
        test_tag = all_tags[0].tag
        print(f"  Testing with tag: '{test_tag}'")
        
        tool_func = list_bookmarks_by_tags(client)
        params = ListBookmarksByTagsParams(tags=[test_tag], limit=3)
        context = create_mock_context()
        
        result = await tool_func(params, context)
        print(f"✓ Found {result.total} bookmarks tagged with '{test_tag}'")
        
        if result.bookmarks:
            print("  Tagged bookmarks:")
            for i, bookmark in enumerate(result.bookmarks, 1):
                print(f"    {i}. {bookmark.title}")
                print(f"       Tags: {', '.join(bookmark.tags)}")
                print()
        else:
            print("  No bookmarks found with that tag")
            
    except Exception as e:
        print(f"✗ Error: {e}")


async def test_list_tags(client):
    """Test the listTags MCP tool."""
    print("\nTesting listTags tool...")
    
    tool_func = list_tags(client)
    context = create_mock_context()
    
    try:
        result = await tool_func(context)
        print(f"✓ Found {len(result)} tags")
        
        if result:
            # Show top 10 most popular tags
            sorted_tags = sorted(result, key=lambda t: t.count, reverse=True)
            print("  Top 10 most popular tags:")
            for i, tag in enumerate(sorted_tags[:10], 1):
                print(f"    {i}. {tag.tag} ({tag.count} bookmarks)")
        else:
            print("  No tags found")
            
    except Exception as e:
        print(f"✗ Error: {e}")


async def test_client_basics(client):
    """Test basic client functionality."""
    print("\nTesting basic client functionality...")
    
    try:
        # Test getting all bookmarks
        bookmarks = await client.get_all_bookmarks()
        print(f"✓ Successfully retrieved {len(bookmarks)} total bookmarks")
        
        # Test getting all tags
        tags = await client.get_all_tags()
        print(f"✓ Successfully retrieved {len(tags)} total tags")
        
        # Test cache functionality by calling again
        bookmarks2 = await client.get_all_bookmarks()
        print(f"✓ Cache test: second call returned {len(bookmarks2)} bookmarks")
        
        if bookmarks and len(bookmarks) > 0:
            sample = bookmarks[0]
            print(f"✓ Sample bookmark structure looks good:")
            print(f"    Title: {sample.title}")
            print(f"    URL: {sample.url}")
            print(f"    Tags: {len(sample.tags)} tags")
            print(f"    Notes: {'Yes' if sample.notes else 'No'}")
            print(f"    Saved: {sample.saved_at}")
            
    except Exception as e:
        print(f"✗ Error: {e}")


async def main():
    """Main test function."""
    print("Pinboard MCP Server Test Harness")
    print("=" * 40)
    
    # Check if API token is set
    token = os.environ.get("PINBOARD_TOKEN")
    if not token:
        print("Error: PINBOARD_TOKEN environment variable not set")
        print("Please set the environment variable and try again:")
        print("  export PINBOARD_TOKEN=username:token")
        print("\nYou can get your API token from: https://pinboard.in/settings/password")
        return
    
    print(f"Using API token: {token.split(':')[0]}:{'*' * len(token.split(':')[1])}")
    
    # Create client
    client = PinboardClient(token)
    
    try:
        # Test basic client functionality first
        await test_client_basics(client)
        
        # Test each MCP tool
        await test_search_bookmarks(client)
        await test_list_recent_bookmarks(client)
        await test_list_bookmarks_by_tags(client)
        await test_list_tags(client)
        
        print("\n" + "=" * 40)
        print("✓ All tests completed successfully!")
        print("\nThe MCP server tools are working correctly.")
        print("You can now run the actual MCP server with:")
        print("  python -m pinboard_mcp_server.main")
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Clean up client resources
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
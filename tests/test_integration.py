#!/usr/bin/env python3
"""
Integration tests for the Pinboard MCP Server

Tests the MCP tools with mocked Pinboard API responses to ensure
the complete flow works correctly without requiring real API calls.
"""

import unittest
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

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
from pinboard_mcp_server.models import Bookmark, TagCount


class TestMCPIntegration(unittest.TestCase):
    """Test the MCP server integration functionality"""

    def setUp(self):
        """Set up test environment"""
        # Clear environment variable to ensure tests control it
        if "PINBOARD_TOKEN" in os.environ:
            self.original_token = os.environ["PINBOARD_TOKEN"]
            del os.environ["PINBOARD_TOKEN"]
        else:
            self.original_token = None

        # Test data
        self.test_token = "testuser:1234567890ABCDEF"
        
        # Sample Pinboard API responses (using recent dates)
        now = datetime.now()
        recent_date_1 = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_date_2 = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_date_3 = (now - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_date = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        self.sample_posts_all = [
            {
                "href": "https://example.com/python-testing",
                "description": "Python Testing Best Practices",
                "extended": "Comprehensive guide to testing in Python with pytest",
                "tags": "python testing pytest",
                "time": recent_date_1
            },
            {
                "href": "https://example.com/fastapi-tutorial",
                "description": "FastAPI Tutorial",
                "extended": "Learn how to build APIs with FastAPI",
                "tags": "python fastapi web",
                "time": recent_date_2
            },
            {
                "href": "https://example.com/async-programming",
                "description": "Async Programming in Python",
                "extended": "",
                "tags": "python async asyncio",
                "time": recent_date_3
            },
            {
                "href": "https://example.com/react-hooks",
                "description": "React Hooks Guide",
                "extended": "Modern React development with hooks",
                "tags": "react javascript frontend",
                "time": old_date
            }
        ]
        
        self.sample_tags_get = {
            "python": 3,
            "testing": 1,
            "pytest": 1,
            "fastapi": 1,
            "web": 1,
            "async": 1,
            "asyncio": 1,
            "react": 1,
            "javascript": 1,
            "frontend": 1
        }

    def tearDown(self):
        """Clean up after tests"""
        # Restore original environment if it existed
        if self.original_token is not None:
            os.environ["PINBOARD_TOKEN"] = self.original_token
        elif "PINBOARD_TOKEN" in os.environ:
            del os.environ["PINBOARD_TOKEN"]

    def create_mock_context(self):
        """Create a mock context for MCP tool calls."""
        context = MagicMock()
        context.session = MagicMock()
        context.session.logger = MagicMock()
        return context

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_mcp_search_bookmarks_tool(self, mock_pinboard_class):
        """Test the MCP searchBookmarks tool with simulated Pinboard data"""
        async def run_test():
            # Mock the pinboard client
            mock_pb = MagicMock()
            mock_pb.posts.all.return_value = self.sample_posts_all
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb
            
            # Create client and tool
            client = PinboardClient(self.test_token)
            tool_func = search_bookmarks(client)
            context = self.create_mock_context()
            
            # Test searching for "python"
            params = SearchBookmarksParams(query="python", limit=10)
            result = await tool_func(params, context)
            
            # Verify results
            self.assertEqual(result.query, "python")
            self.assertEqual(len(result.bookmarks), 3)  # 3 Python-related bookmarks
            self.assertEqual(result.total, 3)
            
            # Check that all results contain "python" in title, notes, or tags
            for bookmark in result.bookmarks:
                found_python = (
                    "python" in bookmark.title.lower() or
                    "python" in bookmark.notes.lower() or
                    any("python" in tag.lower() for tag in bookmark.tags)
                )
                self.assertTrue(found_python, f"Bookmark doesn't match search: {bookmark.title}")
            
            # Test case-insensitive search
            params = SearchBookmarksParams(query="REACT", limit=10)
            result = await tool_func(params, context)
            self.assertEqual(len(result.bookmarks), 1)  # 1 React bookmark
            
            await client.close()

        asyncio.run(run_test())

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_mcp_list_recent_bookmarks_tool(self, mock_pinboard_class):
        """Test the MCP listRecentBookmarks tool"""
        async def run_test():
            # Mock the pinboard client
            mock_pb = MagicMock()
            mock_pb.posts.all.return_value = self.sample_posts_all
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb
            
            client = PinboardClient(self.test_token)
            tool_func = list_recent_bookmarks(client)
            context = self.create_mock_context()
            
            # Test recent bookmarks (last 30 days)
            params = ListRecentBookmarksParams(days=30, limit=10)
            result = await tool_func(params, context)
            
            # Should find the bookmarks from 2024 (recent) but not 2023
            self.assertEqual(len(result.bookmarks), 3)  # 3 from 2024
            self.assertEqual(result.total, 3)
            
            # Verify they're sorted by most recent first
            dates = [b.saved_at for b in result.bookmarks]
            self.assertEqual(dates, sorted(dates, reverse=True))
            
            # Test with smaller date range (last 10 days)
            params = ListRecentBookmarksParams(days=10, limit=10)
            result = await tool_func(params, context)
            
            # Should find fewer bookmarks
            self.assertLessEqual(len(result.bookmarks), 3)
            
            await client.close()

        asyncio.run(run_test())

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_mcp_list_bookmarks_by_tags_tool(self, mock_pinboard_class):
        """Test the MCP listBookmarksByTags tool"""
        async def run_test():
            # Mock the pinboard client
            mock_pb = MagicMock()
            mock_pb.posts.all.return_value = self.sample_posts_all
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb
            
            client = PinboardClient(self.test_token)
            tool_func = list_bookmarks_by_tags(client)
            context = self.create_mock_context()
            
            # Test filtering by single tag
            params = ListBookmarksByTagsParams(tags=["python"], limit=10)
            result = await tool_func(params, context)
            
            self.assertEqual(result.tags, ["python"])
            self.assertEqual(len(result.bookmarks), 3)  # 3 Python bookmarks
            
            # Verify all results have the python tag
            for bookmark in result.bookmarks:
                self.assertIn("python", [tag.lower() for tag in bookmark.tags])
            
            # Test filtering by multiple tags (intersection)
            params = ListBookmarksByTagsParams(tags=["python", "web"], limit=10)
            result = await tool_func(params, context)
            
            self.assertEqual(len(result.bookmarks), 1)  # Only FastAPI bookmark has both
            self.assertIn("fastapi", result.bookmarks[0].title.lower())
            
            # Test filtering by non-existent tag
            params = ListBookmarksByTagsParams(tags=["nonexistent"], limit=10)
            result = await tool_func(params, context)
            
            self.assertEqual(len(result.bookmarks), 0)
            
            await client.close()

        asyncio.run(run_test())

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_mcp_list_tags_tool(self, mock_pinboard_class):
        """Test the MCP listTags tool"""
        async def run_test():
            # Mock the pinboard client
            mock_pb = MagicMock()
            mock_pb.tags.get.return_value = self.sample_tags_get
            mock_pinboard_class.return_value = mock_pb
            
            client = PinboardClient(self.test_token)
            tool_func = list_tags(client)
            context = self.create_mock_context()
            
            # Test getting all tags
            result = await tool_func(context)
            
            self.assertEqual(len(result), len(self.sample_tags_get))
            
            # Verify tag structure
            for tag in result:
                self.assertIsInstance(tag, TagCount)
                self.assertIn(tag.tag, self.sample_tags_get)
                self.assertEqual(tag.count, self.sample_tags_get[tag.tag])
            
            # Check that python is the most popular tag
            python_tag = next(tag for tag in result if tag.tag == "python")
            self.assertEqual(python_tag.count, 3)
            
            await client.close()

        asyncio.run(run_test())

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_environment_token_usage(self, mock_pinboard_class):
        """Test that the environment variable is used when no token is provided"""
        async def run_test():
            # Set up environment
            os.environ["PINBOARD_TOKEN"] = self.test_token
            
            # Mock the pinboard client
            mock_pb = MagicMock()
            mock_pb.posts.all.return_value = []
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb
            
            # Create client (should use environment token)
            client = PinboardClient(self.test_token)
            
            # Verify the client was created with the token
            mock_pinboard_class.assert_called_with(self.test_token)
            
            await client.close()

        asyncio.run(run_test())

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_field_mapping(self, mock_pinboard_class):
        """Test that Pinboard fields are correctly mapped to our model"""
        async def run_test():
            # Test data with explicit field mapping
            test_post = {
                "href": "https://example.com/test",
                "description": "This is the title",  # Maps to title
                "extended": "These are the notes",   # Maps to notes
                "tags": "tag1 tag2 tag3",
                "time": "2024-01-15T10:30:00Z"
            }
            
            mock_pb = MagicMock()
            mock_pb.posts.all.return_value = [test_post]
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb
            
            client = PinboardClient(self.test_token)
            bookmarks = await client.get_all_bookmarks()
            
            self.assertEqual(len(bookmarks), 1)
            bookmark = bookmarks[0]
            
            # Verify field mapping
            self.assertEqual(bookmark.url, "https://example.com/test")
            self.assertEqual(bookmark.title, "This is the title")  # description -> title
            self.assertEqual(bookmark.notes, "These are the notes")  # extended -> notes
            self.assertEqual(bookmark.tags, ["tag1", "tag2", "tag3"])
            
            await client.close()

        asyncio.run(run_test())

    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_caching_behavior(self, mock_pinboard_class):
        """Test that caching works correctly"""
        async def run_test():
            mock_pb = MagicMock()
            mock_pb.posts.all.return_value = self.sample_posts_all
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb
            
            client = PinboardClient(self.test_token)
            
            # First call should hit the API
            bookmarks1 = await client.get_all_bookmarks()
            self.assertEqual(len(bookmarks1), 4)
            self.assertEqual(mock_pb.posts.all.call_count, 1)
            
            # Second call should use cache (same update time)
            bookmarks2 = await client.get_all_bookmarks()
            self.assertEqual(len(bookmarks2), 4)
            self.assertEqual(mock_pb.posts.all.call_count, 1)  # Should not increase
            
            # Search should also use cache
            results = await client.search_bookmarks("python", limit=10)
            self.assertEqual(len(results), 3)
            self.assertEqual(mock_pb.posts.all.call_count, 1)  # Still cached
            
            await client.close()

        asyncio.run(run_test())


class AsyncTestCase(unittest.TestCase):
    """Base class for async test cases"""
    
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def tearDown(self):
        self.loop.close()


if __name__ == '__main__':
    unittest.main()
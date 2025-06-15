#!/usr/bin/env python3
"""
Updated integration tests for the Pinboard MCP Server

Tests the client functionality with mocked Pinboard API responses.
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.models import TagCount


class TestPinboardClientIntegration(unittest.TestCase):
    """Test the PinboardClient integration functionality"""

    def setUp(self):
        """Set up test environment"""
        self.test_token = "testuser:1234567890ABCDEF"

        # Sample test data (using recent dates)
        now = datetime.now()
        recent_date_1 = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_date_2 = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_date_3 = (now - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        old_date = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")

        self.sample_posts_data = [
            {
                "href": "https://example.com/python-testing",
                "description": "Python Testing Best Practices",
                "extended": "Comprehensive guide to testing in Python with pytest",
                "tags": "python testing pytest",
                "time": recent_date_1,
            },
            {
                "href": "https://example.com/fastapi-tutorial",
                "description": "FastAPI Tutorial",
                "extended": "Learn how to build APIs with FastAPI",
                "tags": "python fastapi web",
                "time": recent_date_2,
            },
            {
                "href": "https://example.com/async-programming",
                "description": "Async Programming in Python",
                "extended": "",
                "tags": "python async asyncio",
                "time": recent_date_3,
            },
            {
                "href": "https://example.com/react-hooks",
                "description": "React Hooks Guide",
                "extended": "Modern React development with hooks",
                "tags": "react javascript frontend",
                "time": old_date,
            },
        ]

        self.sample_tags_data = {
            "python": 3,
            "testing": 1,
            "pytest": 1,
            "fastapi": 1,
            "web": 1,
            "async": 1,
            "asyncio": 1,
            "react": 1,
            "javascript": 1,
            "frontend": 1,
        }

    def create_mock_pinboard_bookmarks(self, data_list):
        """Convert test data to mock pinboard.Bookmark objects."""
        mock_bookmarks = []
        for data in data_list:
            mock_bookmark = MagicMock()
            mock_bookmark.url = data["href"]
            mock_bookmark.description = data["description"]
            mock_bookmark.extended = data["extended"]
            mock_bookmark.tags = data["tags"].split() if data["tags"] else []
            mock_bookmark.time = datetime.fromisoformat(
                data["time"].replace("Z", "+00:00")
            )
            mock_bookmarks.append(mock_bookmark)
        return mock_bookmarks

    def create_mock_pinboard_tags(self, tags_dict):
        """Convert test tag data to mock pinboard.Tag objects."""
        mock_tags = []
        for tag_name, count in tags_dict.items():
            mock_tag = MagicMock()
            mock_tag.name = tag_name
            mock_tag.count = count
            mock_tags.append(mock_tag)
        return mock_tags

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_search_bookmarks_integration(self, mock_pinboard_class):
        """Test search bookmarks functionality end-to-end"""

        async def run_test():
            # Mock the pinboard client
            mock_bookmarks = self.create_mock_pinboard_bookmarks(self.sample_posts_data)
            mock_pb = MagicMock()
            mock_pb.posts.recent.return_value = {"posts": mock_bookmarks}
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb

            client = PinboardClient(self.test_token)

            # Test search for "python"
            results = await client.search_bookmarks("python", limit=10)
            self.assertEqual(len(results), 3)  # 3 Python-related bookmarks

            # Verify all results contain "python"
            for bookmark in results:
                found_python = (
                    "python" in bookmark.title.lower()
                    or "python" in bookmark.notes.lower()
                    or any("python" in tag.lower() for tag in bookmark.tags)
                )
                self.assertTrue(found_python)

            # Test case-insensitive search
            results = await client.search_bookmarks("REACT", limit=10)
            self.assertEqual(len(results), 1)

            await client.close()

        asyncio.run(run_test())

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_recent_bookmarks_integration(self, mock_pinboard_class):
        """Test recent bookmarks functionality end-to-end"""

        async def run_test():
            mock_bookmarks = self.create_mock_pinboard_bookmarks(self.sample_posts_data)
            mock_pb = MagicMock()
            mock_pb.posts.recent.return_value = {"posts": mock_bookmarks}
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb

            client = PinboardClient(self.test_token)

            # Test getting recent bookmarks (30 days)
            results = await client.get_recent_bookmarks(days=30, limit=20)
            self.assertEqual(len(results), 3)  # 3 recent bookmarks

            # Test with shorter timeframe (3 days)
            results = await client.get_recent_bookmarks(days=3, limit=20)
            self.assertLessEqual(len(results), 3)

            await client.close()

        asyncio.run(run_test())

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_bookmarks_by_tags_integration(self, mock_pinboard_class):
        """Test bookmarks by tags functionality end-to-end"""

        async def run_test():
            mock_bookmarks = self.create_mock_pinboard_bookmarks(self.sample_posts_data)
            mock_pb = MagicMock()
            mock_pb.posts.recent.return_value = {"posts": mock_bookmarks}
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb

            client = PinboardClient(self.test_token)

            # Test filtering by single tag
            results = await client.get_bookmarks_by_tags(["python"], limit=10)
            self.assertEqual(len(results), 3)  # 3 Python bookmarks

            # Test filtering by multiple tags (intersection)
            results = await client.get_bookmarks_by_tags(["python", "web"], limit=10)
            self.assertEqual(len(results), 1)  # Only FastAPI bookmark has both

            # Test filtering by non-existent tag
            results = await client.get_bookmarks_by_tags(["nonexistent"], limit=10)
            self.assertEqual(len(results), 0)

            await client.close()

        asyncio.run(run_test())

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_tags_integration(self, mock_pinboard_class):
        """Test tags functionality end-to-end"""

        async def run_test():
            mock_tags = self.create_mock_pinboard_tags(self.sample_tags_data)
            mock_pb = MagicMock()
            mock_pb.tags.get.return_value = mock_tags
            mock_pinboard_class.return_value = mock_pb

            client = PinboardClient(self.test_token)

            # Test getting all tags
            results = await client.get_all_tags()
            self.assertEqual(len(results), len(self.sample_tags_data))
            self.assertTrue(all(isinstance(tag, TagCount) for tag in results))

            # Check that python tag has count 3
            python_tag = next((tag for tag in results if tag.tag == "python"), None)
            self.assertIsNotNone(python_tag)
            if python_tag is not None:
                self.assertEqual(python_tag.count, 3)

            await client.close()

        asyncio.run(run_test())

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_field_mapping_integration(self, mock_pinboard_class):
        """Test that Pinboard fields are correctly mapped to our model"""

        async def run_test():
            # Create test data with explicit field mapping
            test_data = [
                {
                    "href": "https://example.com/test",
                    "description": "This is the title",  # Maps to title
                    "extended": "These are the notes",  # Maps to notes
                    "tags": "tag1 tag2 tag3",
                    "time": "2024-01-15T10:30:00Z",
                }
            ]

            mock_bookmarks = self.create_mock_pinboard_bookmarks(test_data)
            mock_pb = MagicMock()
            mock_pb.posts.recent.return_value = {"posts": mock_bookmarks}
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb

            client = PinboardClient(self.test_token)
            bookmarks = await client.get_all_bookmarks()

            self.assertEqual(len(bookmarks), 1)
            bookmark = bookmarks[0]

            # Verify field mapping
            self.assertEqual(bookmark.url, "https://example.com/test")
            self.assertEqual(
                bookmark.title, "This is the title"
            )  # description -> title
            self.assertEqual(bookmark.notes, "These are the notes")  # extended -> notes
            self.assertEqual(bookmark.tags, ["tag1", "tag2", "tag3"])

            await client.close()

        asyncio.run(run_test())

    @patch("pinboard_mcp_server.client.pinboard.Pinboard")
    def test_caching_behavior_integration(self, mock_pinboard_class):
        """Test that caching works correctly"""

        async def run_test():
            mock_bookmarks = self.create_mock_pinboard_bookmarks(self.sample_posts_data)
            mock_pb = MagicMock()
            mock_pb.posts.recent.return_value = {"posts": mock_bookmarks}
            mock_pb.posts.update.return_value = {"update_time": "2024-01-15T12:00:00Z"}
            mock_pinboard_class.return_value = mock_pb

            client = PinboardClient(self.test_token)

            # First call should hit the API
            bookmarks1 = await client.get_all_bookmarks()
            self.assertEqual(len(bookmarks1), 4)

            # Second call should use cache (posts.recent should only be called once)
            bookmarks2 = await client.get_all_bookmarks()
            self.assertEqual(len(bookmarks2), 4)

            # Verify API was called only once for posts
            mock_pb.posts.recent.assert_called_once()

            await client.close()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()

"""Tests for the Pinboard client."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.models import Bookmark, TagCount


class TestPinboardClient:
    """Test the PinboardClient class."""
    
    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_init(self, mock_pinboard_class, valid_token):
        """Test client initialization."""
        client = PinboardClient(valid_token)
        
        assert client.token == valid_token
        assert client.min_request_interval == 3.0
        assert client._bookmark_cache is None
        assert client._tag_cache is None
        mock_pinboard_class.assert_called_once_with(valid_token)
    
    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    def test_rate_limiting_sync(self, mock_pinboard_class, valid_token):
        """Test that synchronous rate limiting works correctly."""
        client = PinboardClient(valid_token)
        
        # Mock time.time() and time.sleep() to control timing
        with patch('time.time') as mock_time, patch('time.sleep') as mock_sleep:
            # Set initial time to avoid triggering rate limit on first call
            client.last_request_time = 10.0
            mock_time.side_effect = [14.0, 15.0, 18.0]  # Times for each call
            
            # First call should not sleep (14.0 - 10.0 = 4.0 > 3.0)
            client._rate_limit_sync()
            mock_sleep.assert_not_called()
            
            # Second call should sleep (15.0 - 14.0 = 1.0 < 3.0)
            client._rate_limit_sync()
            mock_sleep.assert_called_once_with(2.0)  # 3.0 - 1.0 = 2.0
    
    @pytest.mark.asyncio
    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    async def test_get_all_bookmarks_cache_miss(self, mock_pinboard_class, valid_token, mock_pinboard_data):
        """Test getting all bookmarks when cache is invalid."""
        # Mock the pinboard client
        mock_pb = Mock()
        mock_pb.posts.all.return_value = mock_pinboard_data
        mock_pb.posts.update.return_value = {"update_time": "2024-01-15T10:30:00Z"}
        mock_pinboard_class.return_value = mock_pb
        
        client = PinboardClient(valid_token)
        
        with patch.object(client, '_rate_limit_sync') as mock_rate_limit:
            bookmarks = await client.get_all_bookmarks()
            
            assert len(bookmarks) == len(mock_pinboard_data)
            assert all(isinstance(b, Bookmark) for b in bookmarks)
            # Should call rate limiting for both update check and posts/all
            assert mock_rate_limit.call_count >= 1
    
    @pytest.mark.asyncio
    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    async def test_search_bookmarks(self, mock_pinboard_class, valid_token, sample_bookmarks):
        """Test searching bookmarks."""
        client = PinboardClient(valid_token)
        
        with patch.object(client, 'get_all_bookmarks', new_callable=AsyncMock) as mock_get_all:
            mock_get_all.return_value = sample_bookmarks
            
            # Test title search
            results = await client.search_bookmarks("Python", limit=10)
            
            assert len(results) > 0
            assert all("python" in b.title.lower() or 
                      any("python" in tag.lower() for tag in b.tags) for b in results)
            mock_get_all.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    async def test_get_all_tags(self, mock_pinboard_class, valid_token, mock_tags_data):
        """Test getting all tags."""
        # Mock the pinboard client
        mock_pb = Mock()
        mock_pb.tags.get.return_value = mock_tags_data
        mock_pinboard_class.return_value = mock_pb
        
        client = PinboardClient(valid_token)
        
        with patch.object(client, '_rate_limit_sync'):
            tags = await client.get_all_tags()
            
            assert len(tags) == len(mock_tags_data)
            assert all(isinstance(tag, TagCount) for tag in tags)
            mock_pb.tags.get.assert_called_once()
    
    @pytest.mark.asyncio
    @patch('pinboard_mcp_server.client.pinboard.Pinboard')
    async def test_close(self, mock_pinboard_class, valid_token):
        """Test client cleanup."""
        client = PinboardClient(valid_token)
        
        # Mock the executor
        client._executor.shutdown = Mock()
        
        await client.close()
        
        client._executor.shutdown.assert_called_once_with(wait=True)
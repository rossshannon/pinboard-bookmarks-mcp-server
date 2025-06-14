"""Pinboard API client wrapper with caching and rate limiting."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Optional

import pinboard  # type: ignore
from cachetools import LRUCache
from dateutil.parser import parse as parse_date

from pinboard_mcp_server.models import Bookmark, TagCount


class PinboardClient:
    """Pinboard API client with caching and rate limiting."""

    def __init__(self, token: str):
        """Initialize the client with API token."""
        self.token = token
        self.last_request_time = 0.0
        self.min_request_interval = 3.0  # 3 seconds between requests

        # Initialize pinboard.py client
        self._pb = pinboard.Pinboard(token)

        # Cache for bookmarks and tags
        self._bookmark_cache: Optional[list[Bookmark]] = None
        self._tag_cache: Optional[list[TagCount]] = None
        self._last_update_time: Optional[datetime] = None
        self._cache_valid_until: Optional[datetime] = None

        # LRU cache for query results
        self._query_cache: LRUCache = LRUCache(maxsize=1000)

        # Thread pool for running sync pinboard.py calls
        self._executor = ThreadPoolExecutor(max_workers=1)

    def _rate_limit_sync(self) -> None:
        """Ensure we don't exceed rate limits (synchronous)."""
        now = time.time()
        time_since_last = now - self.last_request_time
        if time_since_last < self.min_request_interval:
            time.sleep(self.min_request_interval - time_since_last)
        self.last_request_time = time.time()

    async def _run_in_executor(self, func, *args, **kwargs) -> Any:
        """Run a synchronous function in the thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args, **kwargs)

    async def _check_cache_validity(self) -> bool:
        """Check if cache is still valid using posts/update endpoint."""
        if self._cache_valid_until and datetime.now() < self._cache_valid_until:
            return True

        try:

            def _get_update():
                self._rate_limit_sync()
                return self._pb.posts.update()

            result = await self._run_in_executor(_get_update)
            last_update = parse_date(result["update_time"])

            # Cache is valid if last update time hasn't changed
            if self._last_update_time and last_update <= self._last_update_time:
                # Extend cache validity for another hour
                self._cache_valid_until = datetime.now() + timedelta(hours=1)
                return True

            # Cache is invalid, update timestamp
            self._last_update_time = last_update
            return False
        except Exception:
            # If we can't check, assume cache is invalid
            return False

    async def _refresh_bookmark_cache(self) -> None:
        """Refresh the bookmark cache from Pinboard API."""

        def _get_posts() -> list[Any]:
            self._rate_limit_sync()
            return self._pb.posts.all()

        result: list[Any] = await self._run_in_executor(_get_posts)

        self._bookmark_cache = [Bookmark.from_pinboard(post) for post in result]
        self._cache_valid_until = datetime.now() + timedelta(hours=1)

    async def _refresh_tag_cache(self) -> None:
        """Refresh the tag cache from Pinboard API."""

        def _get_tags() -> Any:
            self._rate_limit_sync()
            return self._pb.tags.get()

        result: Any = await self._run_in_executor(_get_tags)

        self._tag_cache = [
            TagCount(tag=tag, count=count) for tag, count in result.items()
        ]

    async def get_all_bookmarks(self) -> list[Bookmark]:
        """Get all bookmarks, using cache when possible."""
        if not await self._check_cache_validity() or self._bookmark_cache is None:
            await self._refresh_bookmark_cache()

        return self._bookmark_cache or []

    async def get_all_tags(self) -> list[TagCount]:
        """Get all tags with counts, using cache when possible."""
        if self._tag_cache is None:
            await self._refresh_tag_cache()

        return self._tag_cache or []

    async def search_bookmarks(self, query: str, limit: int = 20) -> list[Bookmark]:
        """Search bookmarks by query string."""
        # Check query cache first
        cache_key = f"search:{query}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        bookmarks = await self.get_all_bookmarks()
        query_lower = query.lower()

        # Search in title, notes, and tags
        matches = []
        for bookmark in bookmarks:
            if (
                query_lower in bookmark.title.lower()
                or query_lower in bookmark.notes.lower()
                or any(query_lower in tag.lower() for tag in bookmark.tags)
            ):
                matches.append(bookmark)
                if len(matches) >= limit:
                    break

        # Cache the result
        self._query_cache[cache_key] = matches
        return matches

    async def get_recent_bookmarks(
        self, days: int = 7, limit: int = 20
    ) -> list[Bookmark]:
        """Get bookmarks from the last N days."""
        cache_key = f"recent:{days}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        bookmarks = await self.get_all_bookmarks()
        cutoff_date = datetime.now() - timedelta(days=days)

        # Filter by date and sort by most recent first
        recent = [
            bookmark
            for bookmark in bookmarks
            if bookmark.saved_at.replace(tzinfo=None) >= cutoff_date
        ]
        recent.sort(key=lambda b: b.saved_at, reverse=True)

        result = recent[:limit]
        self._query_cache[cache_key] = result
        return result

    async def get_bookmarks_by_tags(
        self,
        tags: list[str],
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 20,
    ) -> list[Bookmark]:
        """Get bookmarks filtered by tags and optional date range."""
        cache_key = f"tags:{':'.join(sorted(tags))}:{from_date}:{to_date}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        bookmarks = await self.get_all_bookmarks()
        tag_set = {tag.lower() for tag in tags}

        matches = []
        for bookmark in bookmarks:
            bookmark_tags = {tag.lower() for tag in bookmark.tags}

            # Check if all requested tags are present
            if tag_set.issubset(bookmark_tags):
                # Check date range if specified
                bookmark_date = bookmark.saved_at.replace(tzinfo=None)
                if from_date and bookmark_date < from_date:
                    continue
                if to_date and bookmark_date > to_date:
                    continue

                matches.append(bookmark)
                if len(matches) >= limit:
                    break

        # Sort by most recent first
        matches.sort(key=lambda b: b.saved_at, reverse=True)

        self._query_cache[cache_key] = matches
        return matches

    async def close(self) -> None:
        """Close the client and clean up resources."""
        self._executor.shutdown(wait=True)

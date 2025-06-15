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
        self._has_expanded_data: bool = False

        # LRU cache for query results
        self._query_cache: LRUCache = LRUCache(maxsize=1000)

        # Thread pool for running sync pinboard.py calls
        self._executor = ThreadPoolExecutor(max_workers=1)

    def _convert_pinboard_bookmark(self, pb_bookmark) -> dict[str, Any]:
        """Convert pinboard.Bookmark object to dict format our models expect."""
        # pinboard.Bookmark has: url, description, extended, tags (list), time (datetime)
        # We need: href, description, extended, tags (space-separated string), time (ISO string)

        tags_list = getattr(pb_bookmark, "tags", [])
        # Remove empty tags and join with spaces
        tags_str = " ".join([tag for tag in tags_list if tag.strip()])

        time_obj = getattr(pb_bookmark, "time", None)
        if time_obj and hasattr(time_obj, "isoformat"):
            # Convert to UTC and format as Z-suffix (no double timezone conversion)
            time_str = time_obj.isoformat().replace("+00:00", "") + "Z"
        else:
            time_str = "2024-01-01T00:00:00Z"

        return {
            "href": pb_bookmark.url,
            "description": pb_bookmark.description,
            "extended": getattr(pb_bookmark, "extended", ""),
            "tags": tags_str,
            "time": time_str,
        }

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

    async def _refresh_bookmark_cache(self, expand_search: bool = False) -> None:
        """Refresh the bookmark cache from Pinboard API.

        Args:
            expand_search: If True, get more bookmarks using posts.all() with date filter.
                          If False, get only recent 100 bookmarks.
        """

        def _get_posts() -> Any:
            self._rate_limit_sync()
            if expand_search:
                # Get bookmarks from the last 6 months - balance between comprehensive and reasonable
                # The LLM can intelligently select what's most relevant
                from_date = datetime.now() - timedelta(days=180)  # 6 months
                return self._pb.posts.all(
                    fromdt=from_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                )
            else:
                # Use posts.recent for initial cache - gets most recent 100 posts
                return self._pb.posts.recent(count=100)

        result: Any = await self._run_in_executor(_get_posts)

        # Handle both posts.recent() and posts.all() response formats
        if expand_search:
            # posts.all() returns a list directly
            posts_list = result if isinstance(result, list) else []
        else:
            # posts.recent() returns a dict with 'posts' key
            posts_list = (
                result["posts"]
                if isinstance(result, dict) and "posts" in result
                else []
            )

        self._bookmark_cache = [
            Bookmark.from_pinboard(self._convert_pinboard_bookmark(post))
            for post in posts_list
        ]
        self._cache_valid_until = datetime.now() + timedelta(hours=1)

        # Mark whether we have expanded data
        self._has_expanded_data = expand_search

    async def _search_by_tag_direct(
        self,
        tag: str,
        matches: list[Bookmark],
        from_date: Optional[datetime],
        to_date: Optional[datetime],
        limit: int,
    ) -> None:
        """Search for bookmarks by tag using Pinboard API directly.

        This is much more efficient than downloading all bookmarks.
        Modifies the matches list in-place.
        """

        def _get_posts_by_tag() -> Any:
            self._rate_limit_sync()
            # Use posts.all with tag filter - much more efficient
            params = {"tag": tag}
            if from_date:
                params["fromdt"] = from_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            if to_date:
                params["todt"] = to_date.strftime("%Y-%m-%dT%H:%M:%SZ")
            return self._pb.posts.all(**params)

        result: Any = await self._run_in_executor(_get_posts_by_tag)

        # posts.all() returns a list directly
        posts_list = result if isinstance(result, list) else []

        # Convert and add to matches (up to limit)
        for post in posts_list:
            if len(matches) >= limit:
                break
            bookmark = Bookmark.from_pinboard(self._convert_pinboard_bookmark(post))
            matches.append(bookmark)

    async def _refresh_tag_cache(self) -> None:
        """Refresh the tag cache from Pinboard API."""

        def _get_tags() -> Any:
            self._rate_limit_sync()
            return self._pb.tags.get()

        result: Any = await self._run_in_executor(_get_tags)

        # The result is a list of Tag objects with .name and .count attributes
        self._tag_cache = [TagCount(tag=tag.name, count=tag.count) for tag in result]

    async def get_all_bookmarks(self, expand_if_needed: bool = False) -> list[Bookmark]:
        """Get bookmarks, using cache when possible.

        Args:
            expand_if_needed: If True, will expand search to get more bookmarks
                            when initial cache is empty or insufficient.
        """
        if not await self._check_cache_validity() or self._bookmark_cache is None:
            await self._refresh_bookmark_cache()

        # If we need expanded data and don't have it yet, fetch it
        if expand_if_needed and not self._has_expanded_data:
            await self._refresh_bookmark_cache(expand_search=True)

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

        # First try with recent bookmarks
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

        # If no matches found, try optimized strategies before full expansion
        if not matches:
            # First, check if the query matches a tag exactly - use direct tag search
            tags = await self.get_all_tags()
            exact_tag_match = next(
                (tag.tag for tag in tags if tag.tag.lower() == query_lower), None
            )

            if exact_tag_match:
                # Use efficient tag-based search
                try:
                    await self._search_by_tag_direct(
                        exact_tag_match, matches, None, None, limit
                    )
                except Exception:
                    pass  # Fall through to expanded search if tag search fails

            # If still no matches and we haven't expanded yet, try with more data
            # This allows comprehensive free-text search but limits scope for safety
            if not matches and not self._has_expanded_data:
                bookmarks = await self.get_all_bookmarks(expand_if_needed=True)
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

    async def search_bookmarks_extended(
        self, query: str, days_back: int = 365, limit: int = 100
    ) -> list[Bookmark]:
        """Extended search that looks further back in time for comprehensive results.

        Args:
            query: Search query to match against bookmark titles, notes, and tags
            days_back: How many days back to search (default 1 year)
            limit: Maximum number of results to return (default 100)

        Note: This provides generous data for LLM filtering and analysis.
        Returns comprehensive results that the client can intelligently filter.
        """
        cache_key = f"extended_search:{query}:{days_back}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        query_lower = query.lower()

        # First check if this is an exact tag match - use efficient tag search
        tags = await self.get_all_tags()
        exact_tag_match = next(
            (tag.tag for tag in tags if tag.tag.lower() == query_lower), None
        )

        matches: list[Bookmark] = []
        if exact_tag_match:
            # Use efficient tag-based search for exact matches
            try:
                await self._search_by_tag_direct(
                    exact_tag_match, matches, None, None, limit
                )
            except Exception:
                pass

        # If no tag match or tag search failed, do extended time-based search
        if not matches:

            def _get_extended_posts() -> Any:
                self._rate_limit_sync()
                from_date = datetime.now() - timedelta(days=days_back)
                return self._pb.posts.all(
                    fromdt=from_date.strftime("%Y-%m-%dT%H:%M:%SZ")
                )

            result: Any = await self._run_in_executor(_get_extended_posts)
            posts_list = result if isinstance(result, list) else []

            # Search through the extended results
            for post in posts_list:
                if len(matches) >= limit:
                    break

                bookmark = Bookmark.from_pinboard(self._convert_pinboard_bookmark(post))
                if (
                    query_lower in bookmark.title.lower()
                    or query_lower in bookmark.notes.lower()
                    or any(query_lower in tag.lower() for tag in bookmark.tags)
                ):
                    matches.append(bookmark)

        # Sort by most recent first
        matches.sort(key=lambda b: b.saved_at, reverse=True)

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

        # First try with recent bookmarks
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

        # If no matches found, try a targeted search by tag using Pinboard API
        # This is much more efficient than downloading all bookmarks and gets EVERYTHING
        if not matches and len(tags) == 1:
            # For single tag searches, use posts.all with tag filter - gets ALL bookmarks with that tag
            try:
                await self._search_by_tag_direct(
                    tags[0], matches, from_date, to_date, limit
                )
            except Exception:
                # If tag search fails, there's likely no bookmarks with that tag
                pass  # matches will remain empty

        # Sort by most recent first
        matches.sort(key=lambda b: b.saved_at, reverse=True)

        self._query_cache[cache_key] = matches
        return matches

    async def close(self) -> None:
        """Close the client and clean up resources."""
        self._executor.shutdown(wait=True)

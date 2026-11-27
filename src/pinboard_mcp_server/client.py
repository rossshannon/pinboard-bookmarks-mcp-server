"""Pinboard API client wrapper with caching and rate limiting.

This is the live path: it asks Pinboard directly and filters in memory. The
tools use it only when the local index (see ``index.py``) is switched off or
has nothing in it yet; with the index in place, every search runs there.
"""

import asyncio
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import pinboard  # type: ignore
from cachetools import TTLCache
from dateutil.parser import parse as parse_date

from pinboard_mcp_server.models import Bookmark, TagCount

#: How long the cache is trusted before ``posts/update`` is asked again.
CACHE_RECHECK = timedelta(minutes=5)

#: How far back the automatic wider search reaches.
EXPANDED_DAYS = 180

_QUERY_PART = re.compile(r'"([^"]*)"|(\S+)')
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def query_terms(query: str) -> list[str]:
    """Split a query into lower-case terms; a quoted phrase stays one term."""
    terms = []
    for phrase, word in _QUERY_PART.findall(query):
        term = (phrase or word).strip().lower()
        if term and term not in terms:
            terms.append(term)
    return terms


def matches_terms(bookmark: Bookmark, terms: list[str]) -> bool:
    """Whether every term appears somewhere in the title, notes, tags or URL."""
    haystack = "\n".join(
        [bookmark.title, bookmark.notes, " ".join(bookmark.tags), bookmark.url]
    ).lower()
    return all(term in haystack for term in terms)


def _aware(moment: datetime | None) -> datetime | None:
    """A datetime as aware UTC; a naive one is taken to be UTC."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _saved(bookmark: Bookmark) -> datetime:
    """A sort key: when the bookmark was saved, undated ones last."""
    return _aware(bookmark.saved_at) or _OLDEST


def _in_range(
    bookmark: Bookmark, from_date: datetime | None, to_date: datetime | None
) -> bool:
    if from_date is None and to_date is None:
        return True
    saved = _aware(bookmark.saved_at)
    if saved is None:
        return False
    start, end = _aware(from_date), _aware(to_date)
    if start and saved < start:
        return False
    if end and saved > end:
        return False
    return True


def _stamp(moment: datetime) -> str:
    aware = _aware(moment)
    assert aware is not None
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ")


#: Pinboard allows ``posts/all`` once every five minutes.
POSTS_ALL_INTERVAL = 300.0


class PostsAllUnavailable(Exception):
    """``posts/all`` cannot be called now: too soon after the last call, or
    the background index sync owns it."""


class PinboardClient:
    """Pinboard API client with caching and rate limiting."""

    def __init__(self, token: str):
        """Initialize the client with API token."""
        self.token = token
        self.last_request_time = 0.0
        self.min_request_interval = 3.0  # 3 seconds between requests
        self._rate_lock = threading.Lock()

        # posts/all is limited to one call every five minutes. A tool call
        # cannot wait that long, so a call that comes too soon is refused and
        # the caller answers with what it already has. While the index is
        # syncing in the background the allowance is the sync's alone.
        self.posts_all_enabled = True
        self.posts_all_interval = POSTS_ALL_INTERVAL
        self._last_posts_all: float | None = None

        # Initialize pinboard.py client
        self._pb = pinboard.Pinboard(token)

        # Cache for bookmarks and tags
        self._bookmark_cache: list[Bookmark] | None = None
        self._tag_cache: list[TagCount] | None = None
        self._last_update_time: datetime | None = None
        self._cache_valid_until: datetime | None = None
        self._has_expanded_data: bool = False

        # Cache for query results. Entries expire with the bookmark cache's
        # recheck interval and are dropped at once when Pinboard reports a
        # change, so a repeated question never returns a stale answer for long.
        self._query_cache: TTLCache = TTLCache(
            maxsize=1000, ttl=CACHE_RECHECK.total_seconds()
        )

        # Thread pool for running sync pinboard.py calls
        self._executor = ThreadPoolExecutor(max_workers=1)

    @property
    def pinboard(self) -> Any:
        """The underlying ``pinboard.Pinboard`` object."""
        return self._pb

    def _convert_pinboard_bookmark(self, pb_bookmark) -> dict[str, Any]:
        """Convert pinboard.Bookmark object to dict format our models expect."""
        # pinboard.Bookmark has: url, description, extended, tags (list), time (datetime)
        # We need: href, description, extended, tags (space-separated string), time (ISO string)

        tags_list = getattr(pb_bookmark, "tags", [])
        # Remove empty tags and join with spaces
        tags_str = " ".join([tag for tag in tags_list if tag.strip()])

        time_obj = getattr(pb_bookmark, "time", None)
        time_str: str | None = None
        if time_obj and hasattr(time_obj, "isoformat"):
            # Convert to UTC and format as Z-suffix (no double timezone conversion)
            time_str = time_obj.isoformat().replace("+00:00", "") + "Z"

        return {
            "href": pb_bookmark.url,
            "description": pb_bookmark.description,
            "extended": getattr(pb_bookmark, "extended", ""),
            "tags": tags_str,
            # A bookmark with no timestamp has no date, rather than an invented one.
            "time": time_str,
            "shared": "no" if getattr(pb_bookmark, "shared", True) is False else "yes",
            "toread": "yes" if getattr(pb_bookmark, "toread", False) is True else "no",
        }

    def _rate_limit_sync(self) -> None:
        """Ensure we don't exceed rate limits (synchronous, safe across threads)."""
        with self._rate_lock:
            now = time.time()
            time_since_last = now - self.last_request_time
            if time_since_last < self.min_request_interval:
                time.sleep(self.min_request_interval - time_since_last)
            self.last_request_time = time.time()

    def _posts_all(self, **params: Any) -> Any:
        """Call ``posts/all`` (always filtered), or raise :class:`PostsAllUnavailable`."""
        if not params:
            raise ValueError("posts/all is never called without a filter")
        with self._rate_lock:
            if not self.posts_all_enabled:
                raise PostsAllUnavailable("the index sync is using posts/all")
            now = time.monotonic()
            if (
                self._last_posts_all is not None
                and now - self._last_posts_all < self.posts_all_interval
            ):
                raise PostsAllUnavailable("posts/all was called too recently")
            self._last_posts_all = now
        self._rate_limit_sync()
        return self._pb.posts.all(**params)

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
            # pinboard.py returns the time itself; the raw API wraps it in a dict.
            if isinstance(result, dict):
                result = result["update_time"]
            last_update = (
                result if isinstance(result, datetime) else parse_date(str(result))
            )

            # Cache is valid if last update time hasn't changed
            if self._last_update_time and last_update <= self._last_update_time:
                self._cache_valid_until = datetime.now() + CACHE_RECHECK
                return True

            # Pinboard has changed: everything remembered is out of date.
            self._last_update_time = last_update
            self._invalidate()
            self._cache_valid_until = datetime.now() + CACHE_RECHECK
            return False
        except Exception:
            # If we can't check, assume cache is invalid
            return False

    def _invalidate(self) -> None:
        """Forget everything derived from Pinboard's previous state."""
        self._query_cache.clear()
        self._tag_cache = None
        self._bookmark_cache = None
        self._has_expanded_data = False
        self._cache_valid_until = None

    def _to_bookmarks(self, posts: Any) -> list[Bookmark]:
        posts_list = posts if isinstance(posts, list) else []
        return [
            Bookmark.from_pinboard(self._convert_pinboard_bookmark(post))
            for post in posts_list
        ]

    async def _refresh_bookmark_cache(self, expand_search: bool = False) -> None:
        """Refresh the bookmark cache from Pinboard API.

        Args:
            expand_search: If True, get more bookmarks using posts.all() with date filter.
                          If False, get only recent 100 bookmarks.
        """

        def _get_posts() -> Any:
            if expand_search:
                # Get bookmarks from the last 6 months - balance between comprehensive and reasonable
                # The LLM can intelligently select what's most relevant
                from_date = datetime.now(timezone.utc) - timedelta(days=EXPANDED_DAYS)
                return self._posts_all(fromdt=_stamp(from_date))
            else:
                # Use posts.recent for initial cache - gets most recent 100 posts
                self._rate_limit_sync()
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

        self._bookmark_cache = self._to_bookmarks(posts_list)
        self._cache_valid_until = datetime.now() + CACHE_RECHECK

        # Mark whether we have expanded data
        self._has_expanded_data = expand_search

    async def _search_by_tag_direct(
        self,
        tag: str,
        matches: list[Bookmark],
        from_date: datetime | None,
        to_date: datetime | None,
        limit: int,
    ) -> None:
        """Search for bookmarks by tag using Pinboard API directly.

        ``tag`` may be up to three tags separated by spaces; Pinboard returns
        bookmarks carrying all of them. This is much more efficient than
        downloading all bookmarks. Modifies the matches list in-place, skipping
        bookmarks already in it.
        """

        def _get_posts_by_tag() -> Any:
            # Use posts.all with tag filter - much more efficient
            params = {"tag": tag}
            if from_date:
                params["fromdt"] = _stamp(from_date)
            if to_date:
                params["todt"] = _stamp(to_date)
            return self._posts_all(**params)

        result: Any = await self._run_in_executor(_get_posts_by_tag)

        seen = {bookmark.url for bookmark in matches}
        for bookmark in self._to_bookmarks(result):
            if len(matches) >= limit:
                break
            if bookmark.url not in seen:
                seen.add(bookmark.url)
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

    @staticmethod
    def _filter(
        bookmarks: list[Bookmark],
        terms: list[str],
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Bookmark]:
        return [
            bookmark
            for bookmark in bookmarks
            if matches_terms(bookmark, terms)
            and _in_range(bookmark, from_date, to_date)
        ]

    async def search_bookmarks(
        self,
        query: str,
        limit: int = 20,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> list[Bookmark]:
        """Search bookmarks: every word of the query must appear, in any order,
        somewhere in the title, notes, tags or URL. Newest first."""
        # Drops remembered answers if Pinboard has changed (no API call
        # inside the recheck interval).
        await self._check_cache_validity()
        cache_key = f"search:{query}:{limit}:{from_date}:{to_date}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        terms = query_terms(query)

        # First try with recent bookmarks
        bookmarks = await self.get_all_bookmarks()
        matches = self._filter(bookmarks, terms, from_date, to_date)
        complete = True

        if len(matches) < limit:
            # If the query is exactly a tag, Pinboard can return every bookmark
            # carrying it, however old.
            query_lower = query.strip().lower()
            tags = await self.get_all_tags()
            exact_tag_match = next(
                (tag.tag for tag in tags if tag.tag.lower() == query_lower), None
            )
            if exact_tag_match:
                try:
                    await self._search_by_tag_direct(
                        exact_tag_match, matches, from_date, to_date, limit
                    )
                except Exception:
                    complete = False  # fall through to the expanded search

            # The recent 100 did not fill the page: look through six months.
            if len(matches) < limit and not self._has_expanded_data:
                try:
                    bookmarks = await self.get_all_bookmarks(expand_if_needed=True)
                except Exception:
                    # Pinboard refused or it is too soon to ask again: answer
                    # with what the recent bookmarks gave.
                    complete = False
                else:
                    seen = {bookmark.url for bookmark in matches}
                    matches.extend(
                        bookmark
                        for bookmark in self._filter(
                            bookmarks, terms, from_date, to_date
                        )
                        if bookmark.url not in seen
                    )

        matches.sort(key=_saved, reverse=True)
        matches = matches[:limit]

        # A partial answer is not remembered: the next ask may do better.
        if complete:
            self._query_cache[cache_key] = matches
        return matches

    async def search_bookmarks_extended(
        self, query: str, days_back: int = 365, limit: int = 100
    ) -> list[Bookmark]:
        """Extended search that looks further back in time for comprehensive results.

        Args:
            query: Words to find in bookmark titles, notes, tags and URLs
            days_back: How many days back to search (default 1 year)
            limit: Maximum number of results to return (default 100)

        Note: This provides generous data for LLM filtering and analysis.
        Returns comprehensive results that the client can intelligently filter.
        """
        # Drops remembered answers if Pinboard has changed (no API call
        # inside the recheck interval).
        await self._check_cache_validity()
        cache_key = f"extended_search:{query}:{days_back}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        query_lower = query.strip().lower()
        terms = query_terms(query)

        # First check if this is an exact tag match - use efficient tag search
        tags = await self.get_all_tags()
        exact_tag_match = next(
            (tag.tag for tag in tags if tag.tag.lower() == query_lower), None
        )

        matches: list[Bookmark] = []
        complete = True
        if exact_tag_match:
            # Use efficient tag-based search for exact matches
            try:
                await self._search_by_tag_direct(
                    exact_tag_match, matches, None, None, limit
                )
            except Exception:
                complete = False

        # If no tag match or tag search failed, do extended time-based search
        if not matches:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

            def _get_extended_posts() -> Any:
                return self._posts_all(fromdt=_stamp(cutoff))

            try:
                result: Any = await self._run_in_executor(_get_extended_posts)
                matches = self._filter(self._to_bookmarks(result), terms, None, None)
                complete = True
            except Exception:
                # Pinboard refused or it is too soon to ask again: answer from
                # the bookmarks already held.
                held = await self.get_all_bookmarks()
                matches = self._filter(held, terms, cutoff, None)
                complete = False

        # Sort by most recent first
        matches.sort(key=_saved, reverse=True)
        matches = matches[:limit]

        # A partial answer is not remembered: the next ask may do better.
        if complete:
            self._query_cache[cache_key] = matches
        return matches

    async def get_recent_bookmarks(
        self, days: int = 7, limit: int = 20
    ) -> list[Bookmark]:
        """Get bookmarks from the last N days."""
        # Drops remembered answers if Pinboard has changed (no API call
        # inside the recheck interval).
        await self._check_cache_validity()
        cache_key = f"recent:{days}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        bookmarks = await self.get_all_bookmarks()
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        # posts/recent stops at 100. When all 100 are inside the window there
        # may be more behind them, so read the window itself.
        if (
            not self._has_expanded_data
            and len(bookmarks) >= 100
            and all(_saved(bookmark) >= cutoff_date for bookmark in bookmarks)
        ):

            def _get_window() -> Any:
                return self._posts_all(fromdt=_stamp(cutoff_date))

            try:
                window = self._to_bookmarks(await self._run_in_executor(_get_window))
            except Exception:
                window = []
            if len(window) > len(bookmarks):
                bookmarks = window

        # Filter by date and sort by most recent first
        recent = [bookmark for bookmark in bookmarks if _saved(bookmark) >= cutoff_date]
        recent.sort(key=_saved, reverse=True)

        result = recent[:limit]
        self._query_cache[cache_key] = result
        return result

    async def get_bookmarks_by_tags(
        self,
        tags: list[str],
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        limit: int = 20,
    ) -> list[Bookmark]:
        """Get bookmarks filtered by tags and optional date range."""
        # Drops remembered answers if Pinboard has changed (no API call
        # inside the recheck interval).
        await self._check_cache_validity()
        cache_key = f"tags:{':'.join(sorted(tags))}:{from_date}:{to_date}:{limit}"
        if cache_key in self._query_cache:
            return self._query_cache[cache_key]

        # First try with recent bookmarks
        bookmarks = await self.get_all_bookmarks()
        tag_set = {tag.lower() for tag in tags}

        matches = []
        for bookmark in bookmarks:
            bookmark_tags = {tag.lower() for tag in bookmark.tags}

            # Check if all requested tags are present, and the date range
            if tag_set.issubset(bookmark_tags) and _in_range(
                bookmark, from_date, to_date
            ):
                matches.append(bookmark)
                if len(matches) >= limit:
                    break

        # The recent 100 did not fill the page: ask Pinboard for every bookmark
        # carrying the tags (it filters on up to three at once), however old.
        if len(matches) < limit and 1 <= len(tags) <= 3:
            try:
                await self._search_by_tag_direct(
                    " ".join(tags), matches, from_date, to_date, limit
                )
            except Exception:
                # If tag search fails, there's likely no bookmarks with that tag
                pass

        # Sort by most recent first
        matches.sort(key=_saved, reverse=True)

        self._query_cache[cache_key] = matches
        return matches

    async def close(self) -> None:
        """Close the client and clean up resources."""
        self._executor.shutdown(wait=True)

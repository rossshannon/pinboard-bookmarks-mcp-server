"""Main entry point for the Pinboard MCP Server."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP  # type: ignore

from pinboard_mcp_server import ui
from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.service import IndexService, index_enabled

# Initialize FastMCP server
mcp: FastMCP = FastMCP("Pinboard MCP Server")

# MCP Apps result cards: each tool's descriptor points at the card that renders
# its result, and the cards are served as ui:// resources. Read once, at import,
# because the tools below are registered at import; PINBOARD_MCP_APPS_UI=false
# switches all of it off.
if ui.ui_enabled():
    ui.register_card_resources(mcp)

# Global client - will be initialized in main()
client: PinboardClient

# The local index (see index.py and docs/search-index.md). None when it is
# switched off or could not be opened; the tools then ask Pinboard directly.
index_service: IndexService | None = None


def _index() -> IndexService | None:
    """The index, when it has something to answer from."""
    if index_service is not None and index_service.ready():
        return index_service
    return None


def _parse_date(value: str | None, name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} must be in ISO format (YYYY-MM-DD)")


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def search_bookmarks(
    query: str = "",
    limit: int = 20,
    from_date: str | None = None,
    to_date: str | None = None,
    author: str | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
    mode: str = "auto",
    date_field: str = "saved",
    offset: int = 0,
) -> dict[str, Any]:
    """Search every bookmark, liked tweet and bookmarked tweet in the Pinboard account.

    This is the main tool: full-text and semantic search over the whole
    account, with filters. Words can come in any order; results matching every
    word rank first, then partial and by-meaning matches. Titles, notes, tags,
    URLs and tweet authors are all searched.

    Args:
        query: What to look for, in plain words ("feedback tool", "sqlite
            vector search"). Put an exact phrase in double quotes. Leave empty
            to list everything matching the filters, newest first.
        limit: Maximum number of results (1-100, default 20)
        from_date: Only bookmarks on or after this date (YYYY-MM-DD or ISO datetime, UTC)
        to_date: Only bookmarks on or before this date (a bare date includes the whole day)
        author: Tweet author, by handle or name ("patrickc", "@patrickc",
            "Patrick Collison"). If a name finds nothing, try the handle, or
            put the name in the query instead.
        domain: Only bookmarks on this site ("github.com"; "x.com" and
            "twitter.com" are the same). Subdomains are included.
        tags: Only bookmarks carrying all of these tags
        source: How it was saved: "twitter-like", "twitter-bookmark", "twitter"
            (any tweet) or "bookmark" (saved by hand)
        mode: "auto" (default: keyword plus meaning when available), "keyword",
            "semantic" (by meaning only) or "hybrid"
        date_field: Which date from_date/to_date filter on: "saved" (default,
            when it was bookmarked or liked) or "posted" (when a tweet was written)
        offset: Skip this many results, for paging when has_more is true

    Example: "that Patrick Collison tweet last week about a feedback tool" is
    query="feedback tool", author="patrickc", from_date=<8 days ago>.

    Note: author, domain, tags, source, mode, date_field and offset need the
    local index (see get_index_status). Without it, the search covers the most
    recent bookmarks and up to six months back.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")
    if offset < 0:
        raise ValueError("Offset must be 0 or more")

    index = _index()
    if index is not None:
        filters = index.filters(
            from_date=from_date,
            to_date=to_date,
            date_field=date_field,
            tags=tags,
            author=author,
            domain=domain,
            source=source,
        )
        return await index.search(query, filters, mode=mode, limit=limit, offset=offset)

    if any([author, domain, tags, source]) or offset:
        raise ValueError(
            "The author, domain, tags, source and offset filters need the local "
            "bookmark index, which is switched off or has not synced yet. Call "
            "get_index_status for details, or put those words in the query."
        )
    if not query.strip():
        raise ValueError("A query is required while the local index is unavailable")

    to_dt = _parse_date(to_date, "to_date")
    if to_dt is not None and to_date is not None and len(to_date.strip()) <= 10:
        to_dt = to_dt.replace(hour=23, minute=59, second=59)
    bookmarks = await client.search_bookmarks(
        query=query,
        limit=limit,
        from_date=_parse_date(from_date, "from_date"),
        to_date=to_dt,
    )

    return {
        "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
        "total": len(bookmarks),
        "query": query,
    }


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def search_bookmarks_extended(
    query: str, days_back: int = 365, limit: int = 100
) -> dict[str, Any]:
    """Search the last N days for a larger set of results. Kept for
    compatibility: search_bookmarks with from_date does the same and more.

    Args:
        query: Words to find in titles, notes, tags, URLs and tweet authors
        days_back: How many days back to search (1-730, default 365 = 1 year)
        limit: Maximum number of results to return (1-200, default 100)
    """
    if not 1 <= days_back <= 730:
        raise ValueError("Days back must be between 1 and 730 (2 years max)")
    if not 1 <= limit <= 200:
        raise ValueError("Limit must be between 1 and 200")

    index = _index()
    if index is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days_back)
        filters = index.filters(from_date=since.strftime("%Y-%m-%dT%H:%M:%SZ"))
        result = await index.search(query, filters, limit=limit, days_back=days_back)
        result.pop("filters", None)
        return result

    bookmarks = await client.search_bookmarks_extended(
        query=query, days_back=days_back, limit=limit
    )

    return {
        "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
        "total": len(bookmarks),
        "query": query,
        "days_back": days_back,
    }


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def list_recent_bookmarks(days: int = 7, limit: int = 20) -> dict[str, Any]:
    """List what was saved in the last N days, newest first: bookmarks, liked
    tweets and bookmarked tweets alike.

    Args:
        days: Number of days to look back (1-365, default 7)
        limit: Maximum number of results to return (1-200, default 20)

    Note: when has_more is true there are more than `limit`; page through them
    with search_bookmarks (empty query, from_date, offset).
    """
    if not 1 <= days <= 365:
        raise ValueError("Days must be between 1 and 365")
    if not 1 <= limit <= 200:
        raise ValueError("Limit must be between 1 and 200")

    index = _index()
    if index is not None:
        return await index.recent(days=days, limit=limit)

    bookmarks = await client.get_recent_bookmarks(days=days, limit=limit)

    return {
        "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
        "total": len(bookmarks),
        "days": days,
    }


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def list_bookmarks_by_tags(
    tags: list[str],
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List bookmarks carrying all of the given tags, newest first, however old.

    Args:
        tags: List of tags to filter by (1-3 tags)
        from_date: Start date in ISO format (YYYY-MM-DD), optional
        to_date: End date in ISO format (YYYY-MM-DD), optional
        limit: Maximum number of results to return (1-200, default 100)
    """
    if not 1 <= len(tags) <= 3:
        raise ValueError("Must provide 1-3 tags")
    if not 1 <= limit <= 200:
        raise ValueError("Limit must be between 1 and 200")

    index = _index()
    if index is not None:
        return await index.by_tags(tags, from_date, to_date, limit)

    from_dt = _parse_date(from_date, "from_date")
    to_dt = _parse_date(to_date, "to_date")

    bookmarks = await client.get_bookmarks_by_tags(
        tags=tags, from_date=from_dt, to_date=to_dt, limit=limit
    )

    return {
        "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
        "total": len(bookmarks),
        "tags": tags,
        "from_date": from_date,
        "to_date": to_date,
    }


@mcp.tool(meta=ui.tool_meta(ui.TAGS_CARD))
async def list_tags() -> list[dict[str, Any]]:
    """List all tags with their usage counts."""
    index = _index()
    if index is not None and index.index.get_meta("last_synced_at"):
        return await index.tags()

    tags = await client.get_all_tags()

    return [tag.model_dump() for tag in tags]


@mcp.tool
async def count_bookmarks(
    group_by: str = "month",
    query: str = "",
    from_date: str | None = None,
    to_date: str | None = None,
    author: str | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
    date_field: str = "saved",
    limit: int = 50,
) -> dict[str, Any]:
    """Count bookmarks in groups: how many per month, per site, per tweet
    author, per tag or per source. For questions like "whose tweets do I like
    most?", "what was I reading about in March?" or "which sites do I save
    from?". Takes the same filters as search_bookmarks.

    Args:
        group_by: "day", "month", "year", "domain", "author", "tag" or "source"
        query: Only count bookmarks containing all of these words (optional)
        from_date: Only bookmarks on or after this date (YYYY-MM-DD)
        to_date: Only bookmarks on or before this date
        author: Tweet author, by handle or name
        domain: Only bookmarks on this site
        tags: Only bookmarks carrying all of these tags
        source: "twitter-like", "twitter-bookmark", "twitter" or "bookmark"
        date_field: "saved" (default) or "posted"
        limit: Maximum number of groups (1-500, default 50). Date groups come
            newest first; the others largest first.

    Note: needs the local index (see get_index_status).
    """
    if not 1 <= limit <= 500:
        raise ValueError("Limit must be between 1 and 500")
    index = _index()
    if index is None:
        raise ValueError(
            "count_bookmarks needs the local bookmark index, which is switched "
            "off or has not synced yet. Call get_index_status for details."
        )
    filters = index.filters(
        from_date=from_date,
        to_date=to_date,
        date_field=date_field,
        tags=tags,
        author=author,
        domain=domain,
        source=source,
    )
    return await index.counts(group_by, filters, query, limit)


@mcp.tool
async def get_index_status(refresh: bool = False) -> dict[str, Any]:
    """Report on the local search index: how many bookmarks it holds, when it
    last synced from Pinboard, whether a sync is still running, and whether
    semantic search is available.

    Args:
        refresh: Ask the background sync to check Pinboard for changes now
            rather than at its next scheduled check. Returns at once; call
            again shortly to see the result.
    """
    if index_service is None:
        return {
            "enabled": False,
            "note": (
                "The local index is switched off (PINBOARD_INDEX=off) or could "
                "not be opened. Searches ask Pinboard directly and cover recent "
                "bookmarks only."
            ),
        }
    if refresh:
        index_service.worker.wake()
    status = await asyncio.to_thread(index_service.status)
    status["refresh_requested"] = refresh
    return status


def _open_index(token_client: PinboardClient) -> IndexService | None:
    """Open the index and start syncing it, unless it is switched off."""
    if not index_enabled():
        return None
    try:
        service = IndexService.from_environment(
            pinboard_client=token_client.pinboard,
            rate_limit=token_client._rate_limit_sync,
        )
    except Exception as error:
        # The index is an accelerator: without it the server still works.
        print(
            f"Bookmark index unavailable ({type(error).__name__}: {error}); "
            "searching Pinboard directly",
            file=sys.stderr,
        )
        return None
    # posts/all is allowed once every five minutes. The sync needs that
    # allowance; the live fallback (used until the index has data) keeps to
    # posts/recent meanwhile.
    token_client.posts_all_enabled = False
    service.start()
    return service


def main() -> None:
    """Main entry point."""
    global client, index_service

    try:
        # Check for help flag first
        if "--help" in sys.argv or "-h" in sys.argv:
            from pinboard_mcp_server import __version__

            print(f"Pinboard MCP Server v{__version__}")
            print("Read-only access to Pinboard.in bookmarks for LLMs via MCP")
            print()
            print("Usage: pinboard-mcp-server")
            print()
            print("Environment Variables:")
            print("  PINBOARD_TOKEN    Your Pinboard API token (username:token)")
            print("  PINBOARD_INDEX    Set to 'off' to disable the local search index")
            print("  PINBOARD_INDEX_PATH  Where the index file lives")
            print()
            print("See also: pinboard-mcp-index --help (sync, import, search)")
            print()
            print("Get your token at: https://pinboard.in/settings/password")
            return

        # Get Pinboard API token
        token = os.getenv("PINBOARD_TOKEN")
        if not token:
            print(
                "Error: PINBOARD_TOKEN environment variable is required",
                file=sys.stderr,
            )
            print(
                "Get your token at: https://pinboard.in/settings/password",
                file=sys.stderr,
            )
            sys.exit(1)

        # Initialize Pinboard client
        client = PinboardClient(token)

        # Open the local index and start syncing it in the background
        index_service = _open_index(client)

        # Run the server
        mcp.run()
    except KeyboardInterrupt:
        print("\nServer stopped", file=sys.stderr)
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

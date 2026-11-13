"""Main entry point for the Pinboard MCP Server."""

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastmcp import FastMCP  # type: ignore
from fastmcp.tools import ToolResult  # type: ignore

from pinboard_mcp_server import previews, ui
from pinboard_mcp_server.client import PinboardClient


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[None]:
    """Close the preview bridge's client when the server stops.

    Here, and not after ``mcp.run()`` returns, because by then the event loop
    its lookups ran in is gone: this is the last moment they can be cancelled
    and its connections closed cleanly.
    """
    try:
        yield
    finally:
        if link_previews is not None:
            await link_previews.aclose()


# Initialize FastMCP server
mcp: FastMCP = FastMCP("Pinboard MCP Server", lifespan=_lifespan)

# MCP Apps result cards: each tool's descriptor points at the card that renders
# its result, and the cards are served as ui:// resources. Read once, at import,
# because the tools below are registered at import; PINBOARD_MCP_APPS_UI=false
# switches all of it off.
if ui.ui_enabled():
    ui.register_card_resources(mcp)

# Link previews for the bookmarks card (see previews.py): opt-in, by naming a
# pinboard-bridge instance in PINBOARD_PREVIEW_BRIDGE_URL, and only with the
# cards on, because the card is the only thing that asks for them. Read once,
# at import, like the switch above.
PREVIEWS_ENABLED = (
    ui.ui_enabled()
    and ui.card_available(ui.BOOKMARKS_CARD)
    and previews.bridge_url() is not None
)

# Global client - will be initialized in main()
client: PinboardClient

# The preview bridge's client - initialized in main() when previews are on
link_previews: previews.LinkPreviews | None = None

# The key preview tokens are made and checked with, derived from the Pinboard
# token on first use (see previews.preview_token).
_preview_key: bytes | None = None


def _key() -> bytes | None:
    """The preview-token key, or ``None`` when there is no Pinboard token to derive it from."""
    global _preview_key
    if _preview_key is None:
        token = os.getenv("PINBOARD_TOKEN")
        if token:
            _preview_key = previews.preview_key(token)
    return _preview_key


def _bookmarks_result(result: dict[str, Any]) -> dict[str, Any]:
    """A bookmark tool's result, telling the card when it may ask for previews.

    With previews on, the result says so, and each bookmark carries the
    ``preview_token`` the card must hand back to have that URL looked up:
    get_link_previews looks up nothing else, so it cannot be used to make the
    bridge fetch an arbitrary URL with the user's token. Both keys are added
    only then, so with previews off every tool returns exactly what it
    always has.
    """
    key = _key() if PREVIEWS_ENABLED else None
    if key is not None:
        result["previews_available"] = True
        for bookmark in result["bookmarks"]:
            url = bookmark.get("url")
            if isinstance(url, str):
                bookmark["preview_token"] = previews.preview_token(key, url)
    return result


def _without_images(result: dict[str, Any]) -> dict[str, Any]:
    """A get_link_previews result with its images left out, for the text block."""
    return {
        **result,
        "previews": [
            {**entry, "image": None, "favicon": None} for entry in result["previews"]
        ],
    }


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def search_bookmarks(query: str, limit: int = 20) -> dict[str, Any]:
    """Search bookmarks by query string across titles, notes, and tags (recent focus).

    Args:
        query: Search query to match against bookmark titles, notes, and tags
        limit: Maximum number of results to return (1-100, default 20)

    Note: Searches recent bookmarks first, expands automatically if needed.
    For comprehensive historical search, use search_bookmarks_extended.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")

    bookmarks = await client.search_bookmarks(query=query, limit=limit)

    return _bookmarks_result(
        {
            "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
            "total": len(bookmarks),
            "query": query,
        }
    )


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def search_bookmarks_extended(
    query: str, days_back: int = 365, limit: int = 100
) -> dict[str, Any]:
    """Extended search for comprehensive historical results across titles, notes, and tags.

    Args:
        query: Search query to match against bookmark titles, notes, and tags
        days_back: How many days back to search (1-730, default 365 = 1 year)
        limit: Maximum number of results to return (1-200, default 100)

    Note: Provides comprehensive results while being mindful of server load.
    Use tag-based searches for most efficient access to historical bookmarks.
    """
    if not 1 <= days_back <= 730:
        raise ValueError("Days back must be between 1 and 730 (2 years max)")
    if not 1 <= limit <= 200:
        raise ValueError("Limit must be between 1 and 200")

    bookmarks = await client.search_bookmarks_extended(
        query=query, days_back=days_back, limit=limit
    )

    return _bookmarks_result(
        {
            "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
            "total": len(bookmarks),
            "query": query,
            "days_back": days_back,
        }
    )


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def list_recent_bookmarks(days: int = 7, limit: int = 20) -> dict[str, Any]:
    """List bookmarks saved in the last N days.

    Args:
        days: Number of days to look back (1-30, default 7)
        limit: Maximum number of results to return (1-100, default 20)
    """
    if not 1 <= days <= 30:
        raise ValueError("Days must be between 1 and 30")
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")

    bookmarks = await client.get_recent_bookmarks(days=days, limit=limit)

    return _bookmarks_result(
        {
            "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
            "total": len(bookmarks),
            "days": days,
        }
    )


@mcp.tool(meta=ui.tool_meta(ui.BOOKMARKS_CARD))
async def list_bookmarks_by_tags(
    tags: list[str],
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List ALL bookmarks filtered by tags and optional date range.

    Args:
        tags: List of tags to filter by (1-3 tags)
        from_date: Start date in ISO format (YYYY-MM-DD), optional
        to_date: End date in ISO format (YYYY-MM-DD), optional
        limit: Maximum number of results to return (1-200, default 100)

    Note: Gets ALL bookmarks with specified tags, regardless of age.
    Very efficient for tag-based searches. Provides generous data for analysis.
    """
    if not 1 <= len(tags) <= 3:
        raise ValueError("Must provide 1-3 tags")
    if not 1 <= limit <= 200:
        raise ValueError("Limit must be between 1 and 200")

    from_dt = None
    to_dt = None

    if from_date:
        try:
            from_dt = datetime.fromisoformat(from_date)
        except ValueError:
            raise ValueError("from_date must be in ISO format (YYYY-MM-DD)")

    if to_date:
        try:
            to_dt = datetime.fromisoformat(to_date)
        except ValueError:
            raise ValueError("to_date must be in ISO format (YYYY-MM-DD)")

    bookmarks = await client.get_bookmarks_by_tags(
        tags=tags, from_date=from_dt, to_date=to_dt, limit=limit
    )

    return _bookmarks_result(
        {
            "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
            "total": len(bookmarks),
            "tags": tags,
            "from_date": from_date,
            "to_date": to_date,
        }
    )


@mcp.tool(meta=ui.tool_meta(ui.TAGS_CARD))
async def list_tags() -> list[dict[str, Any]]:
    """List all tags with their usage counts."""
    tags = await client.get_all_tags()

    return [tag.model_dump() for tag in tags]


if PREVIEWS_ENABLED:

    @mcp.tool(meta=ui.APP_ONLY_TOOL_META)
    async def get_link_previews(items: list[dict[str, Any]]) -> ToolResult:
        """Internal helper for the Pinboard bookmarks result card; not useful to call directly.

        The card calls this itself, after it has rendered, to decorate the
        bookmarks it shows with a favicon, site name, description and
        thumbnail. It returns inline image data that is meaningless as text
        and says nothing about a bookmark that the bookmark tools do not. It
        only answers for a bookmark a bookmark tool returned, identified by
        that bookmark's url and preview_token together.

        Args:
            items: 1-8 objects, each {"url": a bookmark's url, "token": its preview_token}
        """
        pairs = previews.requested_items(items)
        key = _key()
        if link_previews is None or key is None:
            raise RuntimeError("Link previews are not configured")

        # Only a pair this server made itself is looked up. The rest come back
        # as errors without the bridge hearing of them.
        allowed = {
            url for url, token in pairs if previews.token_matches(key, url, token)
        }
        result = await link_previews.fetch_many(
            [url for url, _token in pairs], allowed=allowed
        )

        # A ToolResult, not a dict: FastMCP would serialise a dict a second
        # time as the text block, and every image would cross the wire twice.
        # The card reads structuredContent. The text block is the same result
        # as JSON, for a channel that relays only content, with the images
        # left out so that it stays small.
        return ToolResult(
            content=json.dumps(_without_images(result), separators=(",", ":")),
            structured_content=result,
        )


def main() -> None:
    """Main entry point."""
    global client, link_previews

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
            print(
                "  PINBOARD_PREVIEW_BRIDGE_URL"
                "    Optional pinboard-bridge instance for link previews on result cards"
            )
            print(
                "  PINBOARD_MCP_APPS_UI    Set to false to switch the result cards off"
            )
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

        # And the preview bridge's, when the card may ask for previews
        bridge = previews.bridge_url()
        if PREVIEWS_ENABLED and bridge is not None:
            link_previews = previews.LinkPreviews(bridge, token)

        # Run the server
        mcp.run()
    except KeyboardInterrupt:
        print("\nServer stopped", file=sys.stderr)
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

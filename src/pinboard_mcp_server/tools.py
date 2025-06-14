"""MCP tools for Pinboard bookmark operations."""

from datetime import datetime
from typing import Callable, Optional

from fastmcp import Context  # type: ignore
from pydantic import BaseModel, Field

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.models import SearchResult, TagCount


class SearchBookmarksParams(BaseModel):
    """Parameters for searching bookmarks."""

    query: str = Field(
        description="Search query to match against bookmark titles, notes, and tags"
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum number of results to return"
    )


class ListRecentBookmarksParams(BaseModel):
    """Parameters for listing recent bookmarks."""

    days: int = Field(default=7, ge=1, le=30, description="Number of days to look back")
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum number of results to return"
    )


class ListBookmarksByTagsParams(BaseModel):
    """Parameters for listing bookmarks by tags."""

    tags: list[str] = Field(
        description="List of tags to filter by (1-3 tags)", min_length=1, max_length=3
    )
    from_date: Optional[str] = Field(
        None, description="Start date in ISO format (YYYY-MM-DD)"
    )
    to_date: Optional[str] = Field(
        None, description="End date in ISO format (YYYY-MM-DD)"
    )
    limit: int = Field(
        default=20, ge=1, le=100, description="Maximum number of results to return"
    )


def search_bookmarks(client: PinboardClient) -> Callable:
    """Create the searchBookmarks MCP tool."""

    async def _search_bookmarks(
        params: SearchBookmarksParams, context: Context
    ) -> SearchResult:
        """Search bookmarks by query string across titles, notes, and tags."""
        try:
            bookmarks = await client.search_bookmarks(
                query=params.query, limit=params.limit
            )

            return SearchResult(
                bookmarks=bookmarks, total=len(bookmarks), query=params.query, tags=None
            )
        except Exception as e:
            context.session.logger.error(f"Error searching bookmarks: {e}")
            raise

    return _search_bookmarks


def list_recent_bookmarks(client: PinboardClient) -> Callable:
    """Create the listRecentBookmarks MCP tool."""

    async def _list_recent_bookmarks(
        params: ListRecentBookmarksParams, context: Context
    ) -> SearchResult:
        """List bookmarks saved in the last N days."""
        try:
            bookmarks = await client.get_recent_bookmarks(
                days=params.days, limit=params.limit
            )

            return SearchResult(
                bookmarks=bookmarks, total=len(bookmarks), query=None, tags=None
            )
        except Exception as e:
            context.session.logger.error(f"Error listing recent bookmarks: {e}")
            raise

    return _list_recent_bookmarks


def list_bookmarks_by_tags(client: PinboardClient) -> Callable:
    """Create the listBookmarksByTags MCP tool."""

    async def _list_bookmarks_by_tags(
        params: ListBookmarksByTagsParams, context: Context
    ) -> SearchResult:
        """List bookmarks filtered by tags and optional date range."""
        try:
            from_date = None
            to_date = None

            if params.from_date:
                from_date = datetime.fromisoformat(params.from_date)
            if params.to_date:
                to_date = datetime.fromisoformat(params.to_date)

            bookmarks = await client.get_bookmarks_by_tags(
                tags=params.tags,
                from_date=from_date,
                to_date=to_date,
                limit=params.limit,
            )

            return SearchResult(
                bookmarks=bookmarks, total=len(bookmarks), query=None, tags=params.tags
            )
        except ValueError as e:
            context.session.logger.error(f"Invalid date format: {e}")
            raise ValueError("Date must be in ISO format (YYYY-MM-DD)")
        except Exception as e:
            context.session.logger.error(f"Error listing bookmarks by tags: {e}")
            raise

    return _list_bookmarks_by_tags


def list_tags(client: PinboardClient) -> Callable:
    """Create the listTags MCP tool."""

    async def _list_tags(context: Context) -> list[TagCount]:
        """List all tags with their usage counts."""
        try:
            tags = await client.get_all_tags()
            return tags
        except Exception as e:
            context.session.logger.error(f"Error listing tags: {e}")
            raise

    return _list_tags

"""Data models for the Pinboard MCP Server."""

from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class Bookmark(BaseModel):
    """A Pinboard bookmark with mapped field names."""

    id: str = Field(
        default_factory=lambda: str(uuid4()), description="Unique bookmark identifier"
    )
    url: str = Field(description="The bookmark URL")
    title: str = Field(
        description="The bookmark title (mapped from Pinboard's 'description')"
    )
    tags: list[str] = Field(default_factory=list, description="List of tags")
    notes: str = Field(
        default="", description="Free-form notes (mapped from Pinboard's 'extended')"
    )
    saved_at: datetime = Field(description="When the bookmark was saved")

    @classmethod
    def from_pinboard(cls, pinboard_post: dict[str, Any]) -> "Bookmark":
        """Create a Bookmark from a Pinboard API post response."""
        return cls(
            url=pinboard_post["href"],
            title=pinboard_post["description"],  # Pinboard's description -> our title
            tags=pinboard_post["tags"].split() if pinboard_post["tags"] else [],
            notes=pinboard_post["extended"],  # Pinboard's extended -> our notes
            saved_at=datetime.fromisoformat(
                pinboard_post["time"].replace("Z", "+00:00")
            ),
        )


class TagCount(BaseModel):
    """A tag with its usage count."""

    tag: str = Field(description="The tag name")
    count: int = Field(description="Number of bookmarks with this tag")


class SearchResult(BaseModel):
    """Search results container."""

    bookmarks: list[Bookmark] = Field(description="List of matching bookmarks")
    total: int = Field(description="Total number of results")
    query: Optional[str] = Field(None, description="The search query used")
    tags: Optional[list[str]] = Field(None, description="Tags used for filtering")


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str = Field(description="Error message")
    code: Optional[str] = Field(None, description="Error code")
    details: Optional[dict[str, Any]] = Field(
        None, description="Additional error details"
    )

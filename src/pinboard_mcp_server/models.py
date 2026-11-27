"""Data models for the Pinboard MCP Server."""

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


def _url_id(url: str) -> str:
    """Pinboard's own identifier for a bookmark: the MD5 of its URL."""
    return hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest()


class Bookmark(BaseModel):
    """A Pinboard bookmark with mapped field names."""

    id: str = Field(
        default="",
        description="Stable bookmark identifier (the MD5 of the URL, as Pinboard uses)",
    )
    url: str = Field(description="The bookmark URL")
    title: str = Field(
        description="The bookmark title (mapped from Pinboard's 'description')"
    )
    tags: list[str] = Field(default_factory=list, description="List of tags")
    notes: str = Field(
        default="", description="Free-form notes (mapped from Pinboard's 'extended')"
    )
    saved_at: datetime | None = Field(
        default=None, description="When the bookmark was saved, if Pinboard said"
    )
    private: bool = Field(default=False, description="Not shared publicly")
    to_read: bool = Field(default=False, description="Marked 'to read'")

    @model_validator(mode="after")
    def _stable_id(self) -> "Bookmark":
        if not self.id:
            self.id = _url_id(self.url)
        return self

    @classmethod
    def from_pinboard(cls, pinboard_post: dict[str, Any]) -> "Bookmark":
        """Create a Bookmark from a Pinboard API post response."""
        time = pinboard_post.get("time")
        return cls(
            url=pinboard_post["href"],
            title=pinboard_post["description"],  # Pinboard's description -> our title
            tags=pinboard_post["tags"].split() if pinboard_post["tags"] else [],
            notes=pinboard_post["extended"],  # Pinboard's extended -> our notes
            saved_at=(
                datetime.fromisoformat(time.replace("Z", "+00:00")) if time else None
            ),
            private=str(pinboard_post.get("shared", "yes")).lower() == "no",
            to_read=str(pinboard_post.get("toread", "no")).lower() == "yes",
        )


class TagCount(BaseModel):
    """A tag with its usage count."""

    tag: str = Field(description="The tag name")
    count: int = Field(description="Number of bookmarks with this tag")


class SearchResult(BaseModel):
    """Search results container."""

    bookmarks: list[Bookmark] = Field(description="List of matching bookmarks")
    total: int = Field(description="Total number of results")
    query: str | None = Field(None, description="The search query used")
    tags: list[str] | None = Field(None, description="Tags used for filtering")


class ErrorResponse(BaseModel):
    """Error response model."""

    error: str = Field(description="Error message")
    code: str | None = Field(None, description="Error code")
    details: dict[str, Any] | None = Field(None, description="Additional error details")

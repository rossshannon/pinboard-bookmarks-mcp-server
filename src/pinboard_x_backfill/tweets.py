"""The tweet record, and how a tweet becomes a Pinboard bookmark."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

LIKE = "like"
BOOKMARK = "bookmark"
KINDS = (LIKE, BOOKMARK)

TAG_FOR_KIND = {LIKE: "via:twitter-like", BOOKMARK: "via:twitter-bookmark"}

# Pinboard allows 255 characters of title. It allows 65,536 of notes, but its
# API is GET-only, so the notes travel in the URL: they are cut to a size
# (measured URL-encoded) that keeps the whole request comfortably under the
# 8 KB request-line limit common to web servers.
TITLE_MAX = 255
NOTES_MAX_ENCODED = 4000

_TWITTER_HOSTS = {"twitter.com", "x.com"}
_HOST_PREFIXES = ("www.", "mobile.", "m.")
_STATUS_PATH = re.compile(r"^/(?:i/web/status|[^/]+/status(?:es)?)/(\d+)(?:/|$)")
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# Snowflake ids start in November 2010; earlier ids are plain counters.
_SNOWFLAKE_EPOCH_MS = 1288834974657
_SNOWFLAKE_MIN_ID = 10**12


def tweet_id_from_url(url: str) -> str | None:
    """The tweet id in a twitter.com or x.com status URL, else None.

    Query strings, fragments, ``/photo/1`` suffixes, ``www.``/``mobile.``
    hosts and the scheme are all ignored, so every spelling of one tweet's
    URL gives the same id.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    for prefix in _HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    if host not in _TWITTER_HOSTS:
        return None
    match = _STATUS_PATH.match(parts.path)
    return match.group(1) if match else None


def snowflake_time(tweet_id: str) -> str:
    """The posting time encoded in a snowflake tweet id, as ISO-8601 Zulu.

    Returns "" for ids that are not snowflakes (tweets before November 2010).
    """
    try:
        number = int(tweet_id)
    except ValueError:
        return ""
    if number < _SNOWFLAKE_MIN_ID:
        return ""
    millis = (number >> 22) + _SNOWFLAKE_EPOCH_MS
    try:
        moment = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_time(value: str) -> str:
    """Any ISO-8601 time as ``YYYY-MM-DDTHH:MM:SSZ``, or "" if unparseable."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_handle(value: str) -> str:
    """A handle without its ``@``, or "" if it is not a valid X handle."""
    handle = (value or "").strip().lstrip("@")
    return handle if _HANDLE.match(handle) else ""


@dataclass
class Tweet:
    """One liked or bookmarked tweet, from the API or from the data archive."""

    id: str
    kinds: set[str] = field(default_factory=set)
    text: str = ""
    author_handle: str = ""
    author_name: str = ""
    created_at: str = ""
    source: str = ""

    @property
    def posted_at(self) -> str:
        """When the tweet was posted: the API's time, else the id's."""
        return self.created_at or snowflake_time(self.id)

    def url(self, host: str = "x.com") -> str:
        if self.author_handle:
            return f"https://{host}/{self.author_handle}/status/{self.id}"
        return f"https://{host}/i/web/status/{self.id}"

    def merge(self, other: Tweet) -> bool:
        """Fold another record of the same tweet into this one.

        Kinds are united. A record from the API replaces the text and author
        of one from the archive; otherwise only blanks are filled. Returns
        whether anything changed.
        """
        before = self.to_json()
        self.kinds |= other.kinds
        prefer_other = other.source == "api" and self.source != "api"
        for name in ("text", "author_handle", "author_name", "created_at"):
            theirs = getattr(other, name)
            if theirs and (prefer_other or not getattr(self, name)):
                setattr(self, name, theirs)
        if prefer_other or not self.source:
            self.source = other.source
        return self.to_json() != before

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kinds": sorted(self.kinds),
            "text": self.text,
            "author_handle": self.author_handle,
            "author_name": self.author_name,
            "created_at": self.created_at,
            "source": self.source,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Tweet:
        return cls(
            id=str(data["id"]),
            kinds={k for k in data.get("kinds", []) if k in KINDS},
            text=data.get("text", ""),
            author_handle=data.get("author_handle", ""),
            author_name=data.get("author_name", ""),
            created_at=data.get("created_at", ""),
            source=data.get("source", ""),
        )


def tidy_text(text: str) -> str:
    """Tweet text with HTML entities decoded and stray whitespace trimmed."""
    return html.unescape(text or "").replace("\r\n", "\n").strip()


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _truncate_encoded(text: str, limit: int) -> str:
    """Cut text so its URL-encoded form fits in ``limit`` bytes."""
    if len(quote(text, safe="")) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(quote(text[:middle] + "…", safe="")) <= limit:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…"


def bookmark_title(tweet: Tweet) -> str:
    """``Full Name (@handle): text`` on one line, within Pinboard's limit."""
    text = _one_line(tweet.text)
    if tweet.author_handle and tweet.author_name:
        who = f"{_one_line(tweet.author_name)} (@{tweet.author_handle})"
    elif tweet.author_handle:
        who = f"@{tweet.author_handle}"
    else:
        who = ""
    if who and text:
        title = f"{who}: {text}"
    elif who:
        title = f"{who}: tweet {tweet.id}"
    else:
        title = text or f"Tweet {tweet.id}"
    return _truncate(title, TITLE_MAX)


def bookmark_notes(tweet: Tweet) -> str:
    """The full text, then ``Author:`` and ``Posted:`` lines when known."""
    trailer = []
    if tweet.author_handle:
        name = _one_line(tweet.author_name)
        who = f"{name} (@{tweet.author_handle})" if name else f"@{tweet.author_handle}"
        trailer.append(f"Author: {who}")
    if tweet.posted_at:
        trailer.append(f"Posted: {tweet.posted_at}")
    tail = "\n".join(trailer)
    room = NOTES_MAX_ENCODED - len(quote("\n\n" + tail, safe=""))
    body = _truncate_encoded(tweet.text, room)
    return "\n\n".join(part for part in (body, tail) if part)


def bookmark_tags(tweet: Tweet, extra: list[str] | None = None) -> list[str]:
    tags = [TAG_FOR_KIND[kind] for kind in KINDS if kind in tweet.kinds]
    for tag in extra or []:
        if tag and tag not in tags:
            tags.append(tag)
    return tags

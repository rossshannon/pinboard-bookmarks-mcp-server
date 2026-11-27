"""Fields derived from a bookmark that Pinboard has no column for.

Pinboard stores a URL, a title, notes and tags. The questions worth asking of
a bookmark collection need more than that: who wrote the thing, which site it
is on, how it got saved, and when it was *posted* as opposed to when it was
saved. All of that can be read back out of what Pinboard does store:

- the **domain** from the URL (``www.`` dropped, ``twitter.com`` folded into
  ``x.com`` so one filter finds both);
- for a tweet, the **author's handle** and the **tweet id** from the URL, and
  the **posted date** from the id itself (ids are "snowflakes": the top bits
  are a millisecond timestamp);
- the author's **display name** from a title shaped ``Full Name (@handle):
  text``, which is what the Twitter backfill writes, or the older page-title
  shape ``Full Name on X: "text"``;
- the **source** from a ``via:`` tag (``via:twitter-like``,
  ``via:twitter-bookmark``), with the legacy IFTTT-era tags understood too.

Everything here is a pure function of the bookmark's own fields, so the index
can re-derive it at any time and nothing needs to be migrated when the rules
improve.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

#: Milliseconds between the Unix epoch and Twitter's snowflake epoch.
TWITTER_EPOCH_MS = 1288834974657

#: Ids below this predate snowflakes (sequential ids ran to about 3e10) and
#: carry no timestamp.
_FIRST_SNOWFLAKE = 10**12

_TWEET_HOSTS = {"twitter.com", "x.com"}

_HANDLE = r"[A-Za-z0-9_]{1,15}"

_TWEET_PATH = re.compile(
    rf"^/(?:i/web|i|(?P<handle>{_HANDLE}))/status(?:es)?/(?P<id>\d{{1,20}})(?:[/?#]|$)"
)

#: ``Full Name (@handle): text``, the shape the Twitter backfill writes.
_TITLE_NAME_HANDLE = re.compile(
    rf"^(?P<name>[^\n]{{1,80}}?)\s*\(@(?P<handle>{_HANDLE})\)\s*(?::|on (?:Twitter|X):?)"
)

#: ``@handle: text``.
_TITLE_HANDLE = re.compile(rf"^@(?P<handle>{_HANDLE}):\s")

#: ``Full Name on X: "text"``, the page title a browser gives a tweet.
_TITLE_NAME_ON = re.compile(r"^(?P<name>[^\n]{1,80}?) on (?:Twitter|X):\s")

#: A ``Posted: <ISO-8601>`` line in the notes, when the saver knew the date.
_POSTED_LINE = re.compile(
    r"^\s*Posted(?: at| on)?:\s*(?P<when>\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

#: Tags older importers used for liked tweets (IFTTT and Pinboard's own, now
#: retired, Twitter integration).
_LEGACY_LIKE_TAGS = {".from:twitter", "twitter-fav", "!fromtwitter", "via:like"}
_LEGACY_BOOKMARK_TAGS = {"via:bookmark"}

SOURCE_TWITTER_LIKE = "twitter-like"
SOURCE_TWITTER_BOOKMARK = "twitter-bookmark"
SOURCE_TWITTER = "twitter"
SOURCE_BOOKMARK = "bookmark"


@dataclass(frozen=True)
class Derived:
    """What :func:`derive` reads out of one bookmark."""

    domain: str
    source: str
    author_handle: str | None = None
    author_name: str | None = None
    posted_at: str | None = None
    tweet_id: str | None = None

    @property
    def author_text(self) -> str:
        """Handle and name together, as the full-text index stores them."""
        return " ".join(part for part in (self.author_name, self.author_handle) if part)


def url_hash(url: str) -> str:
    """Pinboard's own identifier for a bookmark: the MD5 of its URL."""
    return hashlib.md5(url.encode("utf-8"), usedforsecurity=False).hexdigest()


def format_utc(moment: datetime) -> str:
    """``moment`` as ``YYYY-MM-DDTHH:MM:SSZ``; a naive datetime is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime | None:
    """Parse an ISO-8601 date or datetime into an aware UTC datetime, else ``None``."""
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def normalise_domain(host: str) -> str:
    """A hostname as the index stores and filters it."""
    host = host.strip().lower().rstrip(".")
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.split("/", 1)[0].split(":", 1)[0]
    for prefix in ("www.", "mobile.", "m."):
        if host.startswith(prefix) and host.count(".") >= 2:
            host = host[len(prefix) :]
            break
    if host == "twitter.com":
        host = "x.com"
    return host


def normalise_handle(handle: str) -> str:
    """A handle for comparison: no ``@``, lower case."""
    return handle.strip().lstrip("@").lower()


def snowflake_time(tweet_id: int, now: datetime | None = None) -> datetime | None:
    """When a tweet was posted, read from its id; ``None`` for pre-2010 ids."""
    if tweet_id < _FIRST_SNOWFLAKE:
        return None
    try:
        moment = datetime.fromtimestamp(
            ((tweet_id >> 22) + TWITTER_EPOCH_MS) / 1000, tz=timezone.utc
        )
    except (OverflowError, OSError, ValueError):
        return None
    limit = (now or datetime.now(timezone.utc)) + timedelta(days=1)
    return moment if moment <= limit else None


def _split_url(url: str) -> tuple[str, str]:
    try:
        parts = urlsplit(url.strip())
        path = parts.path or ""
        if parts.fragment.startswith("!/") and path in ("", "/"):
            # 2011-era "hashbang" URLs: twitter.com/#!/user/status/1
            path = parts.fragment[1:]
        return parts.hostname or "", path
    except ValueError:
        return "", ""


def _source(tags: set[str], is_tweet: bool) -> str:
    if "via:twitter-bookmark" in tags or (is_tweet and tags & _LEGACY_BOOKMARK_TAGS):
        return SOURCE_TWITTER_BOOKMARK
    if "via:twitter-like" in tags or (is_tweet and tags & _LEGACY_LIKE_TAGS):
        return SOURCE_TWITTER_LIKE
    for tag in sorted(tags):
        if tag.startswith("via:") and len(tag) > 4:
            return tag[4:]
    return SOURCE_TWITTER if is_tweet else SOURCE_BOOKMARK


def sources_of(tags: list[str], source: str) -> set[str]:
    """Every source a bookmark answers to: its main one and any other ``via:`` tag."""
    found = {source}
    for tag in tags:
        lowered = tag.lower()
        if lowered.startswith("via:") and len(lowered) > 4:
            found.add(lowered[4:])
    return found


def derive(
    url: str,
    title: str = "",
    notes: str = "",
    tags: list[str] | None = None,
    now: datetime | None = None,
) -> Derived:
    """Read domain, source, author and posted date out of a bookmark's fields."""
    host, path = _split_url(url)
    domain = normalise_domain(host)
    lowered_tags = {tag.lower() for tag in tags or []}

    handle: str | None = None
    name: str | None = None
    tweet_id: str | None = None
    posted: datetime | None = None

    is_tweet = False
    if domain in _TWEET_HOSTS:
        match = _TWEET_PATH.match(path)
        if match:
            is_tweet = True
            tweet_id = match.group("id")
            handle = match.group("handle")
            posted = snowflake_time(int(tweet_id), now)

    if is_tweet:
        title = title.strip()
        named = _TITLE_NAME_HANDLE.match(title)
        if named:
            name = named.group("name").strip() or None
            handle = handle or named.group("handle")
        else:
            bare = _TITLE_HANDLE.match(title)
            if bare:
                handle = handle or bare.group("handle")
            else:
                on = _TITLE_NAME_ON.match(title)
                if on:
                    name = on.group("name").strip() or None

    stated = _POSTED_LINE.search(notes)
    if stated:
        posted = parse_utc(stated.group("when")) or posted

    return Derived(
        domain=domain,
        source=_source(lowered_tags, is_tweet),
        author_handle=handle,
        author_name=name,
        posted_at=format_utc(posted) if posted else None,
        tweet_id=tweet_id,
    )

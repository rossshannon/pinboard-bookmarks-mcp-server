"""Read likes (and bookmarks, where present) from an X data archive.

The archive is the zip X emails you from Settings, "Download an archive of
your data". Its ``data/like.js`` holds every like the account still has, which
the API may not page all the way back to. Each entry carries only the tweet
id, the text and a URL: no author and no date. The date is recovered from the
id; the author stays blank unless the same tweet also arrives from the API.

``like.js`` is a JavaScript assignment, not JSON::

    window.YTD.like.part0 = [ {"like": {"tweetId": "1", "fullText": "…",
                                         "expandedUrl": "…"}} ]

Large archives split it into ``like-part1.js`` and so on. A ``bookmark.js``
with the same shape is read too if the archive has one; X has not documented
that file, so treat bookmark support here as best-effort.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from pinboard_x_backfill.tweets import (
    BOOKMARK,
    LIKE,
    Tweet,
    tidy_text,
    tweet_id_from_url,
)

_PREFIX = re.compile(r"^\s*window\.YTD\.([a-z_]+)\.part\d+\s*=\s*")
_FILE = re.compile(r"^(like|bookmark)s?(-part\d+)?\.js$")
_KIND_FOR_FEED = {
    "like": LIKE,
    "likes": LIKE,
    "bookmark": BOOKMARK,
    "bookmarks": BOOKMARK,
}


class ArchiveError(Exception):
    """The path is not a readable X archive, or holds no likes or bookmarks."""


def parse_archive_js(source: str) -> list[Tweet]:
    """The tweets in the text of one ``like.js``-style file."""
    match = _PREFIX.match(source)
    if not match:
        raise ArchiveError("not an X archive file: no window.YTD.<name>.part0 prefix")
    feed = match.group(1)
    kind = _KIND_FOR_FEED.get(feed)
    if kind is None:
        raise ArchiveError(f"unsupported archive feed: {feed}")
    try:
        entries = json.loads(source[match.end() :].rstrip().rstrip(";"))
    except json.JSONDecodeError as error:
        raise ArchiveError(f"archive file is not valid JSON: {error}") from None
    if not isinstance(entries, list):
        raise ArchiveError("archive file does not hold a list")

    tweets = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        inner = next((v for v in entry.values() if isinstance(v, dict)), None)
        if inner is None:
            continue
        tweet_id = str(inner.get("tweetId") or "").strip()
        if not tweet_id.isdigit():
            tweet_id = tweet_id_from_url(str(inner.get("expandedUrl") or "")) or ""
        if not tweet_id:
            continue
        tweets.append(
            Tweet(
                id=tweet_id,
                kinds={kind},
                text=tidy_text(str(inner.get("fullText") or "")),
                source="archive",
            )
        )
    return tweets


def _sources(path: Path) -> Iterator[str]:
    """The text of every like/bookmark file at a path (file, folder or zip)."""
    if path.is_dir():
        folder = path / "data" if (path / "data").is_dir() else path
        for child in sorted(folder.iterdir()):
            if child.is_file() and _FILE.match(child.name):
                yield child.read_text(encoding="utf-8")
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                parts = PurePosixPath(name)
                if _FILE.match(parts.name) and parts.parent.name in ("data", ""):
                    yield archive.read(name).decode("utf-8")
    elif path.is_file():
        yield path.read_text(encoding="utf-8")
    else:
        raise ArchiveError(f"no such file or folder: {path}")


def read_archive(path: Path) -> list[Tweet]:
    """Every like and bookmark in an archive zip, folder, or single .js file."""
    merged: dict[str, Tweet] = {}
    found = False
    for source in _sources(path):
        found = True
        for tweet in parse_archive_js(source):
            if tweet.id in merged:
                merged[tweet.id].merge(tweet)
            else:
                merged[tweet.id] = tweet
    if not found:
        raise ArchiveError(f"no like.js or bookmark.js found in {path}")
    return list(merged.values())

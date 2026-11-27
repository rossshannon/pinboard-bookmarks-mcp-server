"""Local files: the tweet snapshot, and small private JSON files.

Reading likes and bookmarks from the X API costs money per tweet, so what is
fetched is kept in a snapshot (``tweets.json`` in the data folder). ``sync``
works from the snapshot alone and can be dry-run as often as you like for
nothing.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pinboard_x_backfill.tweets import Tweet


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "pinboard-x-backfill"


def write_private_json(path: Path, data: Any) -> None:
    """Write JSON atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any | None:
    """The JSON in a file, or None if the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class TweetStore:
    """The snapshot of fetched tweets, keyed by tweet id."""

    def __init__(self, path: Path):
        self.path = path
        self.tweets: dict[str, Tweet] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("tweets"), list):
                raise ValueError("not a tweet snapshot")
            for item in data["tweets"]:
                tweet = Tweet.from_json(item)
                self.tweets[tweet.id] = tweet

    def __len__(self) -> int:
        return len(self.tweets)

    def has(self, tweet_id: str, kind: str) -> bool:
        tweet = self.tweets.get(tweet_id)
        return tweet is not None and kind in tweet.kinds

    def merge(self, tweets: list[Tweet]) -> tuple[int, int]:
        """Fold tweets in. Returns how many were new and how many changed."""
        new = changed = 0
        for tweet in tweets:
            mine = self.tweets.get(tweet.id)
            if mine is None:
                self.tweets[tweet.id] = tweet
                new += 1
            elif mine.merge(tweet):
                changed += 1
        return new, changed

    def save(self) -> None:
        ordered = sorted(self.tweets.values(), key=lambda t: (len(t.id), t.id))
        write_private_json(
            self.path, {"version": 1, "tweets": [t.to_json() for t in ordered]}
        )


class FetchState:
    """How far the first full read of each kind has got.

    ``complete`` is set once a read has reached the oldest like or bookmark.
    Until then ``next_token`` is the page to carry on from, so an interrupted
    backfill resumes where it stopped instead of starting, and paying, again.
    """

    def __init__(self, path: Path):
        self.path = path
        data = read_json(path)
        self.kinds: dict[str, dict[str, Any]] = data if isinstance(data, dict) else {}

    def _entry(self, kind: str) -> dict[str, Any]:
        entry = self.kinds.get(kind)
        return entry if isinstance(entry, dict) else {}

    def complete(self, kind: str) -> bool:
        return bool(self._entry(kind).get("complete"))

    def next_token(self, kind: str) -> str | None:
        token = self._entry(kind).get("next_token")
        return str(token) if token else None

    def update(self, kind: str, *, complete: bool, next_token: str | None) -> None:
        self.kinds[kind] = {"complete": complete, "next_token": next_token}
        write_private_json(self.path, self.kinds)

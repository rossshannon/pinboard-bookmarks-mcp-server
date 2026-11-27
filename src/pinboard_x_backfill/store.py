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
            for item in data.get("tweets", []):
                tweet = Tweet.from_json(item)
                self.tweets[tweet.id] = tweet

    def __len__(self) -> int:
        return len(self.tweets)

    def fetched(self, tweet_id: str, kind: str) -> bool:
        """Whether the API has already returned this tweet as this kind.

        A copy that came only from the data archive does not count: the API
        version carries the author, so it is still worth reading.
        """
        tweet = self.tweets.get(tweet_id)
        return tweet is not None and kind in tweet.kinds and tweet.source == "api"

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

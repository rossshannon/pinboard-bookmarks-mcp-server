"""Test doubles: a Pinboard account held in memory, and an embedding model."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

EXPORT = Path(__file__).parent / "fixtures" / "pinboard_export.json"

#: "Now" for tests built on the export fixture, whose newest bookmark is 25 November 2026.
NOW = datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc)


def export_posts() -> list[dict[str, Any]]:
    return json.loads(EXPORT.read_text(encoding="utf-8"))


def post(
    url: str,
    title: str = "Title",
    time: str = "2026-01-01T00:00:00Z",
    tags: str = "",
    notes: str = "",
    shared: str = "yes",
    toread: str = "no",
) -> dict[str, Any]:
    return {
        "href": url,
        "description": title,
        "extended": notes,
        "tags": tags,
        "time": time,
        "shared": shared,
        "toread": toread,
        "meta": hashlib.md5(f"{title}{notes}{tags}".encode()).hexdigest(),
        "hash": hashlib.md5(url.encode()).hexdigest(),
    }


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


class Clock:
    """A clock tests move by hand; ``wait`` advances it instead of sleeping."""

    def __init__(self, now: datetime = NOW):
        self.now = now
        self.waits: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self.now += timedelta(seconds=seconds)
        return True


class FakePinboard:
    """Stands in for ``pinboard.Pinboard``: ``posts.update``, ``posts.dates``, ``posts.all``.

    Returns what pinboard.py returns: a naive UTC datetime from ``update``,
    ``{"dates": {date: count}}`` from ``dates`` and bookmark objects from ``all``.
    """

    def __init__(self, posts: list[dict[str, Any]] | None = None):
        self.store: dict[str, dict[str, Any]] = {}
        self.update_time = datetime(2026, 11, 26, 9, 0, 0)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_all_with: list[int] = []
        self.posts = SimpleNamespace(
            update=self._update, dates=self._dates, all=self._all
        )
        for entry in posts or []:
            self.store[entry["href"]] = entry

    # -- changing the account ------------------------------------------------

    def _touch(self) -> None:
        self.update_time += timedelta(minutes=1)

    def add(self, entry: dict[str, Any]) -> None:
        self.store[entry["href"]] = entry
        self._touch()

    def delete(self, url: str) -> None:
        del self.store[url]
        self._touch()

    def edit(self, url: str, **changes: Any) -> None:
        self.store[url] = {**self.store[url], **changes, "meta": "edited"}
        self._touch()

    def count(self, method: str) -> int:
        return sum(1 for name, _ in self.calls if name == method)

    # -- the API ---------------------------------------------------------------

    def _update(self) -> datetime:
        self.calls.append(("update", {}))
        return self.update_time

    def _dates(self) -> dict[str, Any]:
        self.calls.append(("dates", {}))
        counts = Counter(_parse(entry["time"]).date() for entry in self.store.values())
        return {"user": "test", "tag": "", "dates": dict(counts)}

    def _all(self, **params: Any) -> list[Any]:
        self.calls.append(("all", params))
        if self.fail_all_with:
            code = self.fail_all_with.pop(0)
            raise urllib.error.HTTPError(
                "https://api.pinboard.in/v1/posts/all?auth_token=user:SECRET",
                code,
                "Too Many Requests",
                None,  # type: ignore[arg-type]
                None,
            )
        since = _parse(params["fromdt"]) if params.get("fromdt") else None
        until = _parse(params["todt"]) if params.get("todt") else None
        found = []
        for entry in self.store.values():
            when = _parse(entry["time"])
            if since and when < since:
                continue
            if until and when > until:
                continue
            found.append(as_pinboard_object(entry))
        found.sort(key=lambda bookmark: bookmark.time, reverse=True)
        return found


def as_pinboard_object(entry: dict[str, Any]) -> Any:
    """A raw post as the ``pinboard.Bookmark`` object pinboard.py builds from it."""
    return SimpleNamespace(
        url=entry["href"],
        description=entry["description"],
        extended=entry["extended"],
        tags=entry["tags"].split(" "),
        time=_parse(entry["time"]).replace(tzinfo=None),
        shared=entry.get("shared", "yes") == "yes",
        toread=entry.get("toread", "no") == "yes",
        meta=entry.get("meta", ""),
        hash=entry.get("hash", ""),
    )


#: Words the fake model treats as one idea, so "by meaning" has something to find.
_SYNONYMS = {
    "complaints": "feedback",
    "complaint": "feedback",
    "requests": "feedback",
    "survey": "feedback",
    "customers": "users",
    "customer": "users",
    "user": "users",
    "telescope": "astronomy",
    "galaxies": "astronomy",
}


class FakeEmbedder:
    """A deterministic bag-of-words "model": texts sharing words (or the
    synonyms above) get similar vectors. Enough to exercise the plumbing."""

    model_id = "fake:bag-of-words"
    dim = 64

    def __init__(self) -> None:
        self.ready = True
        self.documents_embedded = 0

    def _vector(self, text: str) -> bytes:
        vector = np.zeros(self.dim, dtype=np.float32)
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            word = _SYNONYMS.get(word, word)
            if len(word) < 4:
                continue
            slot = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim
            vector[slot] += 1.0
        norm = float(np.linalg.norm(vector))
        if norm:
            vector /= norm
        return vector.tobytes()

    def embed_documents(self, texts: list[str]) -> list[bytes]:
        self.documents_embedded += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> bytes:
        return self._vector(text)

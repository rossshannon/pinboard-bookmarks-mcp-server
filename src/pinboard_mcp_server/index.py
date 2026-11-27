"""A local SQLite index of the Pinboard account: the place questions are asked.

Pinboard's API has no search endpoint, and every call to it waits behind a
three-second spacing rule. So the account is mirrored into one SQLite file
(see :mod:`pinboard_mcp_server.sync`) and every query runs there:

- ``bookmarks`` holds each bookmark plus the fields derived from it
  (:mod:`pinboard_mcp_server.enrich`): domain, source, author, posted date;
- ``bookmarks_fts`` is an FTS5 index over title, notes, tags, URL and author,
  ranked by BM25 with stemming, so ``collison feedback tool`` finds a tweet by
  ``@patrickc`` that says "tools for feedback";
- ``embeddings`` holds one vector per bookmark when a model is installed
  (:mod:`pinboard_mcp_server.embeddings`), for search by meaning;
- ``meta`` holds the sync state.

The vectors are compared by brute force in numpy rather than through a vector
extension. A lifetime of bookmarks is tens of thousands of rows, which numpy
scans in a few milliseconds, and it avoids SQLite's loadable-extension
support, which the Python that ships with macOS is built without.

A connection is opened per thread (SQLite connections are not shared across
threads here) and the file runs in WAL mode, so the background sync, the MCP
tools and the command line can all use it at once.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinboard_mcp_server import enrich

SCHEMA_VERSION = 1

#: Bumped when :mod:`enrich` or the embedded text changes shape; the derived
#: columns are then recomputed from the stored fields the next time the index
#: is opened.
DERIVED_VERSION = 1

#: BM25 column weights, in ``bookmarks_fts`` column order: title, notes, tags,
#: url, author. A hit on the author or the title says more than one in the URL.
_BM25_WEIGHTS = (4.0, 2.0, 3.0, 1.0, 5.0)

#: Reciprocal-rank-fusion constant for hybrid search.
_RRF_K = 60

#: How deep the keyword ranking is read before fusing. Fixed, so that the
#: fused order is the same whichever page is asked for; hybrid results page
#: through at most this many keyword matches.
_KEYWORD_POOL = 200

#: By-meaning neighbours taken into a hybrid search. Deliberately small: a
#: nearest neighbour always exists, relevant or not.
_SEMANTIC_POOL = 30

_EMBED_TEXT_LIMIT = 2000

GROUPINGS = ("day", "month", "year", "domain", "author", "tag", "source")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id            INTEGER PRIMARY KEY,
    url           TEXT NOT NULL UNIQUE,
    hash          TEXT NOT NULL,
    meta          TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    tags          TEXT NOT NULL DEFAULT '',
    saved_at      TEXT,
    private       INTEGER NOT NULL DEFAULT 0,
    to_read       INTEGER NOT NULL DEFAULT 0,
    signature     TEXT NOT NULL DEFAULT '',
    domain        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    author_handle TEXT,
    author_name   TEXT,
    author        TEXT NOT NULL DEFAULT '',
    posted_at     TEXT,
    tweet_id      TEXT,
    text_hash     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS bookmarks_saved_at ON bookmarks (saved_at);
CREATE INDEX IF NOT EXISTS bookmarks_posted_at ON bookmarks (posted_at);
CREATE INDEX IF NOT EXISTS bookmarks_domain ON bookmarks (domain);
CREATE INDEX IF NOT EXISTS bookmarks_author ON bookmarks (author_handle COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS tags (
    tag         TEXT NOT NULL COLLATE NOCASE,
    bookmark_id INTEGER NOT NULL REFERENCES bookmarks (id) ON DELETE CASCADE,
    PRIMARY KEY (tag, bookmark_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS tags_bookmark ON tags (bookmark_id);

CREATE TABLE IF NOT EXISTS embeddings (
    bookmark_id INTEGER PRIMARY KEY REFERENCES bookmarks (id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    vector      BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE VIRTUAL TABLE IF NOT EXISTS bookmarks_fts USING fts5(
    title, notes, tags, url, author,
    content='bookmarks', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS bookmarks_fts_insert AFTER INSERT ON bookmarks BEGIN
    INSERT INTO bookmarks_fts (rowid, title, notes, tags, url, author)
    VALUES (new.id, new.title, new.notes, new.tags, new.url, new.author);
END;
CREATE TRIGGER IF NOT EXISTS bookmarks_fts_delete AFTER DELETE ON bookmarks BEGIN
    INSERT INTO bookmarks_fts (bookmarks_fts, rowid, title, notes, tags, url, author)
    VALUES ('delete', old.id, old.title, old.notes, old.tags, old.url, old.author);
END;
CREATE TRIGGER IF NOT EXISTS bookmarks_fts_update AFTER UPDATE ON bookmarks BEGIN
    INSERT INTO bookmarks_fts (bookmarks_fts, rowid, title, notes, tags, url, author)
    VALUES ('delete', old.id, old.title, old.notes, old.tags, old.url, old.author);
    INSERT INTO bookmarks_fts (rowid, title, notes, tags, url, author)
    VALUES (new.id, new.title, new.notes, new.tags, new.url, new.author);
END;
"""


class IndexUnavailable(RuntimeError):
    """The index cannot be used here (for one, SQLite built without FTS5)."""


@dataclass(frozen=True)
class Post:
    """One bookmark as Pinboard describes it, before anything is derived."""

    url: str
    title: str = ""
    notes: str = ""
    tags: tuple[str, ...] = ()
    saved_at: str | None = None
    private: bool = False
    to_read: bool = False
    meta: str = ""

    @classmethod
    def from_pinboard(cls, post: Any) -> Post:
        """From a ``pinboard.Bookmark`` object or a raw API/export dict."""
        if isinstance(post, dict):
            raw_tags = post.get("tags") or ""
            tags = raw_tags.split() if isinstance(raw_tags, str) else list(raw_tags)
            when = post.get("time") or post.get("dt")
            return cls(
                url=str(post.get("href") or post.get("url") or ""),
                title=str(post.get("description") or ""),
                notes=str(post.get("extended") or ""),
                tags=tuple(tag for tag in tags if tag and tag.strip()),
                saved_at=_saved_at(when),
                private=str(post.get("shared", "yes")).lower() == "no",
                to_read=str(post.get("toread", "no")).lower() == "yes",
                meta=str(post.get("meta") or ""),
            )
        tags = getattr(post, "tags", None) or []
        return cls(
            url=str(getattr(post, "url", "") or ""),
            title=str(getattr(post, "description", "") or ""),
            notes=str(getattr(post, "extended", "") or ""),
            tags=tuple(tag for tag in tags if isinstance(tag, str) and tag.strip()),
            saved_at=_saved_at(getattr(post, "time", None)),
            private=getattr(post, "shared", True) is False,
            to_read=getattr(post, "toread", False) is True,
            meta=str(getattr(post, "meta", "") or ""),
        )


def _saved_at(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        moment = enrich.parse_utc(value)
        return enrich.format_utc(moment) if moment else None
    if hasattr(value, "isoformat"):
        try:
            return enrich.format_utc(value)
        except (TypeError, ValueError, AttributeError):
            return None
    return None


@dataclass(frozen=True)
class Filters:
    """What narrows a search or a listing. Every field is optional."""

    #: Inclusive lower bound, ``YYYY-MM-DDTHH:MM:SSZ``.
    since: str | None = None
    #: Exclusive upper bound, same format.
    until: str | None = None
    #: ``saved`` compares when the bookmark was saved; ``posted`` when the
    #: content was posted, falling back to the save date where that is unknown.
    date_field: str = "saved"
    tags: tuple[str, ...] = ()
    author: str | None = None
    domain: str | None = None
    source: str | None = None
    to_read: bool | None = None
    private: bool | None = None

    def is_empty(self) -> bool:
        return self == Filters(date_field=self.date_field)


@dataclass
class SyncDelta:
    """What one write changed."""

    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0

    def __iadd__(self, other: SyncDelta) -> SyncDelta:
        self.added += other.added
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.deleted += other.deleted
        return self

    @property
    def changed(self) -> int:
        return self.added + self.updated + self.deleted

    def as_dict(self) -> dict[str, int]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
        }


@dataclass
class SearchOutcome:
    """A page of results and how they were found."""

    bookmarks: list[dict[str, Any]] = field(default_factory=list)
    #: ``keyword``, ``semantic``, ``hybrid`` or ``listing`` (no query).
    mode: str = "keyword"
    #: More results exist past this page.
    has_more: bool = False
    #: Why semantic search did not take part, when it was asked for and could not.
    semantic_note: str | None = None


_SOURCE_ALIASES = {
    "like": enrich.SOURCE_TWITTER_LIKE,
    "likes": enrich.SOURCE_TWITTER_LIKE,
    "x-like": enrich.SOURCE_TWITTER_LIKE,
    "twitter-likes": enrich.SOURCE_TWITTER_LIKE,
    "x-bookmark": enrich.SOURCE_TWITTER_BOOKMARK,
    "twitter-bookmarks": enrich.SOURCE_TWITTER_BOOKMARK,
    "x": enrich.SOURCE_TWITTER,
    "tweet": enrich.SOURCE_TWITTER,
    "tweets": enrich.SOURCE_TWITTER,
}

#: A letter or a digit. Not ``\w``: that takes an underscore, which the FTS5
#: tokenizer drops, and a term of nothing else can never match.
_WORD = re.compile(r"[^\W_]", re.UNICODE)
_QUERY_PART = re.compile(r'"([^"]*)"|(\S+)')


def query_terms(query: str) -> list[str]:
    """Split a query into FTS5 terms: quoted phrases kept whole, everything escaped.

    Each term comes back wrapped in double quotes, so nothing a caller types
    (``AND``, ``*``, ``:``, a stray quote) is read as FTS5 syntax.
    """
    terms: list[str] = []
    for phrase, word in _QUERY_PART.findall(query):
        text = (phrase or word).replace('"', " ").strip()
        if text and _WORD.search(text):
            terms.append(f'"{text}"')
    # Order-preserving de-duplication.
    return list(dict.fromkeys(terms))


def embedding_text(title: str, notes: str, tags: Sequence[str], author: str) -> str:
    """The text a bookmark is embedded as."""
    topical = [tag for tag in tags if not tag.startswith((".", "via:", "!"))]
    parts = [title.strip(), notes.strip(), author.strip(), " ".join(topical)]
    return "\n".join(part for part in parts if part)[:_EMBED_TEXT_LIMIT]


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _signature(post: Post) -> str:
    payload = json.dumps(
        [
            post.title,
            post.notes,
            list(post.tags),
            post.saved_at,
            post.private,
            post.to_read,
            post.meta,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def default_index_path(
    environ: dict[str, str] | os._Environ[str] | None = None,
) -> Path:
    """Where the index lives unless ``PINBOARD_INDEX_PATH`` says otherwise."""
    env = environ if environ is not None else os.environ
    explicit = env.get("PINBOARD_INDEX_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path(env.get("HOME") or Path.home())
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
    else:
        base = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "pinboard-mcp-server" / "bookmarks.sqlite3"


class BookmarkIndex:
    """The SQLite index. Safe to use from several threads and processes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._vector_lock = threading.Lock()
        self._vectors: tuple[str, Any, Any] | None = None
        self._prepare_file()
        with self._write_lock:
            db = self._db()
            try:
                # executescript manages its own transaction; the statements
                # are all IF NOT EXISTS, so two processes opening at once agree.
                db.executescript(_SCHEMA)
            except sqlite3.OperationalError as error:
                if "fts5" in str(error).lower():
                    raise IndexUnavailable(
                        "This Python's SQLite was built without FTS5, which the "
                        "bookmark index needs"
                    ) from error
                raise
            db.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        if self.get_meta("derived_version") != str(DERIVED_VERSION):
            self.rederive()

    # -- connections -------------------------------------------------------

    def _prepare_file(self) -> None:
        """Create the directory and the file, readable by the owner only.

        The index holds private bookmarks, so it gets the permissions a
        credentials file would.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
            except OSError:
                pass

    def _db(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            self._local.db = db
        return db

    @contextmanager
    def _writing(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, serialised within the process."""
        db = self._db()
        with self._write_lock:
            if db.in_transaction:
                yield db
                return
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            else:
                db.execute("COMMIT")

    def close(self) -> None:
        """Close this thread's connection."""
        db = getattr(self._local, "db", None)
        if db is not None:
            db.close()
            self._local.db = None

    # -- meta --------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = (
            self._db()
            .execute("SELECT value FROM meta WHERE key = ?", (key,))
            .fetchone()
        )
        return row["value"] if row else default

    def set_meta(self, key: str, value: str | None) -> None:
        with self._writing() as db:
            if value is None:
                db.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                db.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    def get_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_meta(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def set_json(self, key: str, value: Any) -> None:
        self.set_meta(key, json.dumps(value, separators=(",", ":"), sort_keys=True))

    # -- writing -----------------------------------------------------------

    def _row_values(self, post: Post) -> dict[str, Any]:
        tags = list(post.tags)
        derived = enrich.derive(post.url, post.title, post.notes, tags)
        author = derived.author_text
        return {
            "url": post.url,
            "hash": enrich.url_hash(post.url),
            "meta": post.meta,
            "title": post.title,
            "notes": post.notes,
            "tags": " ".join(tags),
            "saved_at": post.saved_at,
            "private": int(post.private),
            "to_read": int(post.to_read),
            "signature": _signature(post),
            "domain": derived.domain,
            "source": derived.source,
            "author_handle": derived.author_handle,
            "author_name": derived.author_name,
            "author": author,
            "posted_at": derived.posted_at,
            "tweet_id": derived.tweet_id,
            "text_hash": _text_hash(
                embedding_text(post.title, post.notes, tags, author)
            ),
        }

    _COLUMNS = (
        "url",
        "hash",
        "meta",
        "title",
        "notes",
        "tags",
        "saved_at",
        "private",
        "to_read",
        "signature",
        "domain",
        "source",
        "author_handle",
        "author_name",
        "author",
        "posted_at",
        "tweet_id",
        "text_hash",
    )

    def upsert(self, posts: Iterable[Post]) -> SyncDelta:
        """Add or update bookmarks by URL. Rows whose content is unchanged are left alone."""
        delta = SyncDelta()
        by_url = {post.url: post for post in posts if post.url}
        if not by_url:
            return delta
        with self._writing() as db:
            existing = self._signatures(db, list(by_url))
            for url, post in by_url.items():
                known = existing.get(url)
                signature = _signature(post)
                if known is not None and known[1] == signature:
                    delta.unchanged += 1
                    continue
                values = self._row_values(post)
                if known is None:
                    cursor = db.execute(
                        f"INSERT INTO bookmarks ({', '.join(self._COLUMNS)}) "
                        f"VALUES ({', '.join(':' + c for c in self._COLUMNS)})",
                        values,
                    )
                    bookmark_id = cursor.lastrowid
                    delta.added += 1
                else:
                    bookmark_id = known[0]
                    assignments = ", ".join(
                        f"{c} = :{c}" for c in self._COLUMNS if c != "url"
                    )
                    db.execute(
                        f"UPDATE bookmarks SET {assignments} WHERE id = :id",
                        {**values, "id": bookmark_id},
                    )
                    db.execute("DELETE FROM tags WHERE bookmark_id = ?", (bookmark_id,))
                    delta.updated += 1
                db.executemany(
                    "INSERT OR IGNORE INTO tags (tag, bookmark_id) VALUES (?, ?)",
                    [(tag, bookmark_id) for tag in post.tags],
                )
        return delta

    @staticmethod
    def _signatures(
        db: sqlite3.Connection, urls: list[str]
    ) -> dict[str, tuple[int, str]]:
        found: dict[str, tuple[int, str]] = {}
        for start in range(0, len(urls), 500):
            chunk = urls[start : start + 500]
            marks = ", ".join("?" for _ in chunk)
            for row in db.execute(
                f"SELECT id, url, signature FROM bookmarks WHERE url IN ({marks})",
                chunk,
            ):
                found[row["url"]] = (row["id"], row["signature"])
        return found

    def replace_range(self, since: str, until: str, posts: Iterable[Post]) -> SyncDelta:
        """Make ``[since, until)`` of the index match ``posts``.

        The range is exclusive at both ends: whether Pinboard's ``fromdt`` and
        ``todt`` include a bookmark saved exactly on the bound is not something
        to bet a deletion on. Every post is upserted, and any indexed bookmark
        saved inside the range
        that is not among them is removed: it was deleted (or re-dated) on
        Pinboard. ``posts`` may spill outside the range; those are upserted and
        nothing outside the range is removed.
        """
        batch = [post for post in posts if post.url]
        with self._writing() as db:
            delta = self.upsert(batch)
            keep = {post.url for post in batch}
            stale = [
                row["id"]
                for row in db.execute(
                    "SELECT id, url FROM bookmarks WHERE saved_at > ? AND saved_at < ?",
                    (since, until),
                )
                if row["url"] not in keep
            ]
            delta.deleted += self._delete_ids(db, stale)
        return delta

    def reset(self) -> None:
        """Empty the index: every bookmark, and everything recorded about syncing.

        The schema, the pacing of ``posts/all`` and the embedding revision stay.
        """
        with self._writing() as db:
            db.execute("DELETE FROM bookmarks")
            db.execute(
                "DELETE FROM meta WHERE key NOT IN "
                "('schema_version', 'derived_version', 'embeddings_revision', "
                "'last_posts_all_at')"
            )

    def delete_urls(self, urls: Iterable[str]) -> int:
        with self._writing() as db:
            found = self._signatures(db, list(urls))
            return self._delete_ids(db, [entry[0] for entry in found.values()])

    @staticmethod
    def _delete_ids(db: sqlite3.Connection, ids: list[int]) -> int:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            db.execute(
                f"DELETE FROM bookmarks WHERE id IN ({', '.join('?' for _ in chunk)})",
                chunk,
            )
        return len(ids)

    def rederive(self) -> int:
        """Recompute every derived column from the stored fields."""
        count = 0
        with self._writing() as db:
            rows = db.execute(
                "SELECT id, url, title, notes, tags, saved_at, private, to_read, meta FROM bookmarks"
            ).fetchall()
            assignments = ", ".join(f"{c} = :{c}" for c in self._COLUMNS if c != "url")
            for row in rows:
                post = Post(
                    url=row["url"],
                    title=row["title"],
                    notes=row["notes"],
                    tags=tuple(row["tags"].split()),
                    saved_at=row["saved_at"],
                    private=bool(row["private"]),
                    to_read=bool(row["to_read"]),
                    meta=row["meta"],
                )
                db.execute(
                    f"UPDATE bookmarks SET {assignments} WHERE id = :id",
                    {**self._row_values(post), "id": row["id"]},
                )
                count += 1
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('derived_version', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(DERIVED_VERSION),),
            )
        return count

    # -- reading -----------------------------------------------------------

    def count(self) -> int:
        return int(self._db().execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0])

    def stats(self) -> dict[str, Any]:
        db = self._db()
        row = db.execute(
            "SELECT COUNT(*) AS n, MIN(saved_at) AS oldest, MAX(saved_at) AS newest, "
            "SUM(tweet_id IS NOT NULL) AS tweets FROM bookmarks"
        ).fetchone()
        embedded = db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        return {
            "bookmarks": int(row["n"]),
            "tweets": int(row["tweets"] or 0),
            "oldest_saved_at": row["oldest"],
            "newest_saved_at": row["newest"],
            "embedded": int(embedded),
        }

    def day_counts(self) -> dict[str, int]:
        """Bookmarks per UTC day, keyed ``YYYY-MM-DD``."""
        return {
            row[0]: int(row[1])
            for row in self._db().execute(
                "SELECT substr(saved_at, 1, 10), COUNT(*) FROM bookmarks "
                "WHERE saved_at IS NOT NULL GROUP BY 1"
            )
        }

    def get(self, url: str) -> dict[str, Any] | None:
        row = (
            self._db()
            .execute("SELECT * FROM bookmarks WHERE url = ?", (url,))
            .fetchone()
        )
        return _as_bookmark(row) if row else None

    def tag_counts(self) -> list[dict[str, Any]]:
        """Every tag with its count, alphabetical: the shape ``list_tags`` returns."""
        return [
            {"tag": row[0], "count": int(row[1])}
            for row in self._db().execute(
                "SELECT MIN(tag), COUNT(*) FROM tags GROUP BY tag COLLATE NOCASE "
                "ORDER BY tag COLLATE NOCASE"
            )
        ]

    @staticmethod
    def _where(filters: Filters) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        when = (
            "COALESCE(b.posted_at, b.saved_at)"
            if filters.date_field == "posted"
            else "b.saved_at"
        )
        if filters.since:
            clauses.append(f"{when} >= ?")
            params.append(filters.since)
        if filters.until:
            clauses.append(f"{when} < ?")
            params.append(filters.until)
        for tag in filters.tags:
            clauses.append(
                "EXISTS (SELECT 1 FROM tags t WHERE t.bookmark_id = b.id AND t.tag = ?)"
            )
            params.append(tag)
        if filters.author:
            words = [
                w for w in re.split(r"[\s,]+", filters.author.replace("@", " ")) if w
            ]
            for word in words:
                clauses.append("b.author LIKE ? ESCAPE '\\'")
                params.append(_like(word))
        if filters.domain:
            domain = enrich.normalise_domain(filters.domain)
            clauses.append("(b.domain = ? OR b.domain LIKE ? ESCAPE '\\')")
            params.extend([domain, "%." + _like(domain)[1:-1]])
        if filters.source:
            source = filters.source.strip().lower()
            source = source[4:] if source.startswith("via:") else source
            source = _SOURCE_ALIASES.get(source, source)
            if source == enrich.SOURCE_TWITTER:
                clauses.append("b.tweet_id IS NOT NULL")
            else:
                clauses.append(
                    "(b.source = ? OR EXISTS (SELECT 1 FROM tags t "
                    "WHERE t.bookmark_id = b.id AND t.tag = ?))"
                )
                params.extend([source, f"via:{source}"])
        if filters.to_read is not None:
            clauses.append("b.to_read = ?")
            params.append(int(filters.to_read))
        if filters.private is not None:
            clauses.append("b.private = ?")
            params.append(int(filters.private))
        return clauses, params

    def listing(self, filters: Filters, limit: int, offset: int = 0) -> SearchOutcome:
        """Bookmarks matching ``filters``, newest first."""
        clauses, params = self._where(filters)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = (
            "COALESCE(b.posted_at, b.saved_at)"
            if filters.date_field == "posted"
            else "b.saved_at"
        )
        rows = (
            self._db()
            .execute(
                f"SELECT b.* FROM bookmarks b {where} ORDER BY {order} DESC, b.id DESC "
                "LIMIT ? OFFSET ?",
                [*params, limit + 1, offset],
            )
            .fetchall()
        )
        return SearchOutcome(
            bookmarks=[_as_bookmark(row) for row in rows[:limit]],
            mode="listing",
            has_more=len(rows) > limit,
        )

    def _keyword_ids(
        self, query: str, filters: Filters, limit: int
    ) -> list[tuple[int, str]]:
        """Ids ranked by BM25: bookmarks matching every term, then those matching any."""
        terms = query_terms(query)
        if not terms:
            return []
        clauses, params = self._where(filters)
        extra = f"AND {' AND '.join(clauses)}" if clauses else ""
        weights = ", ".join(str(w) for w in _BM25_WEIGHTS)
        sql = (
            "SELECT b.id FROM bookmarks_fts JOIN bookmarks b ON b.id = bookmarks_fts.rowid "
            f"WHERE bookmarks_fts MATCH ? {extra} "
            f"ORDER BY bm25(bookmarks_fts, {weights}), b.saved_at DESC LIMIT ?"
        )
        db = self._db()
        ranked: list[tuple[int, str]] = []
        seen: set[int] = set()
        expressions = [(" AND ".join(terms), "all")]
        if len(terms) > 1:
            expressions.append((" OR ".join(terms), "some"))
        for expression, kind in expressions:
            if len(ranked) >= limit:
                break
            for row in db.execute(sql, [expression, *params, limit]):
                if row[0] not in seen:
                    seen.add(row[0])
                    ranked.append((row[0], kind))
        return ranked[:limit]

    def _rows_by_id(self, ids: Sequence[int]) -> dict[int, sqlite3.Row]:
        found: dict[int, sqlite3.Row] = {}
        db = self._db()
        for start in range(0, len(ids), 500):
            chunk = list(ids[start : start + 500])
            marks = ", ".join("?" for _ in chunk)
            for row in db.execute(
                f"SELECT * FROM bookmarks WHERE id IN ({marks})", chunk
            ):
                found[row["id"]] = row
        return found

    def search(
        self,
        query: str,
        filters: Filters | None = None,
        *,
        mode: str = "auto",
        limit: int = 20,
        offset: int = 0,
        embedder: Any = None,
    ) -> SearchOutcome:
        """Search the index.

        ``mode`` is ``keyword`` (BM25 full text), ``semantic`` (nearest by
        embedding), ``hybrid`` (both, fused by reciprocal rank) or ``auto``:
        hybrid when embeddings are available, keyword otherwise. An empty
        query lists by date.
        """
        filters = filters or Filters()
        if mode not in {"auto", "keyword", "semantic", "hybrid"}:
            raise ValueError("mode must be auto, keyword, semantic or hybrid")
        if not query_terms(query):
            return self.listing(filters, limit, offset)

        want = offset + limit + 1
        semantic_only = mode == "semantic"
        note: str | None = None
        semantic: list[tuple[int, float]] = []
        if mode != "keyword":
            if embedder is None:
                note = "semantic search is not set up (no embedding model installed)"
            elif not getattr(embedder, "ready", True):
                note = "the embedding model is still loading; keyword results only"
            else:
                try:
                    semantic = self._semantic_ids(
                        query,
                        filters,
                        want if semantic_only else _SEMANTIC_POOL,
                        embedder,
                    )
                    if not semantic and not self._has_embeddings(embedder.model_id):
                        note = "no bookmarks have been embedded yet"
                except Exception as error:  # a model that fails must not fail search
                    note = f"semantic search failed: {type(error).__name__}"
        if mode == "semantic" and note is None:
            page = semantic[offset : offset + limit]
            rows = self._rows_by_id([i for i, _ in page])
            return SearchOutcome(
                bookmarks=[
                    {
                        **_as_bookmark(rows[i]),
                        "matched_by": ["semantic"],
                        "similarity": round(s, 4),
                    }
                    for i, s in page
                    if i in rows
                ],
                mode="semantic",
                has_more=len(semantic) > offset + limit,
            )

        # In hybrid the pools are a fixed size, whatever the offset: a pool that
        # grew with the page would reorder the fused list between pages.
        keyword = self._keyword_ids(
            query, filters, _KEYWORD_POOL if semantic else max(want, 50)
        )
        if not semantic:
            page_ids = keyword[offset : offset + limit]
            rows = self._rows_by_id([i for i, _ in page_ids])
            return SearchOutcome(
                bookmarks=[
                    {
                        **_as_bookmark(rows[i]),
                        "matched_by": ["keyword"],
                        "matched_terms": kind,
                    }
                    for i, kind in page_ids
                    if i in rows
                ],
                mode="keyword",
                has_more=len(keyword) > offset + limit,
                semantic_note=note,
            )

        # Reciprocal rank fusion. A bookmark containing every query word is
        # worth more than one that only sits nearby in meaning or shares one
        # word, so partial keyword matches count for half.
        scores: dict[int, float] = {}
        kinds: dict[int, str] = {}
        for rank, (bookmark_id, kind) in enumerate(keyword):
            weight = 1.0 if kind == "all" else 0.5
            scores[bookmark_id] = scores.get(bookmark_id, 0.0) + weight / (
                _RRF_K + rank + 1
            )
            kinds[bookmark_id] = kind
        similarity = dict(semantic)
        for rank, (bookmark_id, _) in enumerate(semantic):
            scores[bookmark_id] = scores.get(bookmark_id, 0.0) + 1.0 / (
                _RRF_K + rank + 1
            )
        # Bookmarks containing every query word always come first; the fused
        # score orders within that group and within the rest.
        fused = sorted(scores, key=lambda i: (kinds.get(i) != "all", -scores[i], -i))
        page_fused = fused[offset : offset + limit]
        rows = self._rows_by_id(page_fused)
        bookmarks = []
        for bookmark_id in page_fused:
            if bookmark_id not in rows:
                continue
            entry = _as_bookmark(rows[bookmark_id])
            matched = []
            if bookmark_id in kinds:
                matched.append("keyword")
                entry["matched_terms"] = kinds[bookmark_id]
            if bookmark_id in similarity:
                matched.append("semantic")
                entry["similarity"] = round(similarity[bookmark_id], 4)
            entry["matched_by"] = matched
            bookmarks.append(entry)
        return SearchOutcome(
            bookmarks=bookmarks, mode="hybrid", has_more=len(fused) > offset + limit
        )

    def counts(
        self,
        group_by: str,
        filters: Filters | None = None,
        query: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Bookmark counts grouped by a date bucket, domain, author, tag or source."""
        if group_by not in GROUPINGS:
            raise ValueError(f"group_by must be one of: {', '.join(GROUPINGS)}")
        filters = filters or Filters()
        clauses, params = self._where(filters)
        terms = query_terms(query)
        if terms:
            clauses.append(
                "b.id IN (SELECT rowid FROM bookmarks_fts WHERE bookmarks_fts MATCH ?)"
            )
            params.append(" AND ".join(terms))
        when = (
            "COALESCE(b.posted_at, b.saved_at)"
            if filters.date_field == "posted"
            else "b.saved_at"
        )
        join = ""
        order = "n DESC, key"
        if group_by in {"day", "month", "year"}:
            width = {"day": 10, "month": 7, "year": 4}[group_by]
            key = f"substr({when}, 1, {width})"
            clauses.append(f"{when} IS NOT NULL")
            order = "key DESC"
        elif group_by == "domain":
            key = "b.domain"
            clauses.append("b.domain != ''")
        elif group_by == "author":
            key = "lower(b.author_handle)"
            clauses.append("b.author_handle IS NOT NULL")
        elif group_by == "tag":
            key = "lower(g.tag)"
            join = "JOIN tags g ON g.bookmark_id = b.id"
        else:
            key = "b.source"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        name = ", MAX(b.author_name) AS name" if group_by == "author" else ""
        rows = self._db().execute(
            f"SELECT {key} AS key, COUNT(*) AS n{name} FROM bookmarks b {join} {where} "
            f"GROUP BY key ORDER BY {order} LIMIT ?",
            [*params, limit],
        )
        result = []
        for row in rows:
            entry: dict[str, Any] = {group_by: row["key"], "count": int(row["n"])}
            if group_by == "author" and row["name"]:
                entry["name"] = row["name"]
            result.append(entry)
        return result

    # -- embeddings --------------------------------------------------------

    def _has_embeddings(self, model_id: str) -> bool:
        return (
            self._db()
            .execute("SELECT 1 FROM embeddings WHERE model = ? LIMIT 1", (model_id,))
            .fetchone()
            is not None
        )

    def pending_embeddings(
        self, model_id: str, limit: int = 64
    ) -> list[tuple[int, str, str]]:
        """Bookmarks with no current vector for ``model_id``: ``(id, text, text_hash)``."""
        rows = self._db().execute(
            "SELECT b.id, b.title, b.notes, b.tags, b.author, b.text_hash FROM bookmarks b "
            "LEFT JOIN embeddings e ON e.bookmark_id = b.id "
            "WHERE e.bookmark_id IS NULL OR e.model != ? OR e.text_hash != b.text_hash "
            "ORDER BY b.saved_at DESC LIMIT ?",
            (model_id, limit),
        )
        return [
            (
                row["id"],
                embedding_text(
                    row["title"], row["notes"], row["tags"].split(), row["author"]
                ),
                row["text_hash"],
            )
            for row in rows
        ]

    def pending_embedding_count(self, model_id: str) -> int:
        return int(
            self._db()
            .execute(
                "SELECT COUNT(*) FROM bookmarks b LEFT JOIN embeddings e ON e.bookmark_id = b.id "
                "WHERE e.bookmark_id IS NULL OR e.model != ? OR e.text_hash != b.text_hash",
                (model_id,),
            )
            .fetchone()[0]
        )

    def store_embeddings(
        self, model_id: str, vectors: Iterable[tuple[int, str, bytes]]
    ) -> None:
        """Store ``(bookmark_id, text_hash, float32 bytes)`` vectors."""
        with self._writing() as db:
            db.executemany(
                "INSERT INTO embeddings (bookmark_id, model, text_hash, vector) "
                "SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM bookmarks WHERE id = ?) "
                "ON CONFLICT (bookmark_id) DO UPDATE SET model = excluded.model, "
                "text_hash = excluded.text_hash, vector = excluded.vector",
                [(i, model_id, h, v, i) for i, h, v in vectors],
            )
            revision = int(
                (
                    db.execute(
                        "SELECT value FROM meta WHERE key = 'embeddings_revision'"
                    ).fetchone()
                    or ["0"]
                )[0]
            )
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('embeddings_revision', ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (str(revision + 1),),
            )

    def _matrix(self, model_id: str) -> tuple[Any, Any]:
        """Every vector for ``model_id`` as ``(ids, matrix)``, cached until embeddings change."""
        import numpy as np

        db = self._db()
        stamp = f"{model_id}:{self.get_meta('embeddings_revision', '0')}:" + str(
            db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        )
        with self._vector_lock:
            if self._vectors is not None and self._vectors[0] == stamp:
                return self._vectors[1], self._vectors[2]
            ids: list[int] = []
            blobs: list[bytes] = []
            width = 0
            for row in db.execute(
                "SELECT bookmark_id, vector FROM embeddings WHERE model = ? ORDER BY bookmark_id",
                (model_id,),
            ):
                if not width:
                    width = len(row[1])
                if len(row[1]) == width:
                    ids.append(row[0])
                    blobs.append(row[1])
            if ids:
                matrix = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(
                    len(ids), -1
                )
            else:
                matrix = np.zeros((0, 0), dtype=np.float32)
            id_array = np.asarray(ids, dtype=np.int64)
            self._vectors = (stamp, id_array, matrix)
            return id_array, matrix

    def _semantic_ids(
        self, query: str, filters: Filters, limit: int, embedder: Any
    ) -> list[tuple[int, float]]:
        """Ids nearest ``query`` by cosine similarity, best first."""
        import numpy as np

        ids, matrix = self._matrix(embedder.model_id)
        if len(ids) == 0:
            return []
        vector = np.frombuffer(embedder.embed_query(query), dtype=np.float32)
        if vector.shape[0] != matrix.shape[1]:
            return []
        scores = matrix @ vector
        clauses, params = self._where(filters)
        if clauses:
            allowed = np.fromiter(
                (
                    row[0]
                    for row in self._db().execute(
                        f"SELECT b.id FROM bookmarks b WHERE {' AND '.join(clauses)}",
                        params,
                    )
                ),
                dtype=np.int64,
            )
            scores = np.where(np.isin(ids, allowed), scores, -np.inf)
        count = min(limit, len(ids))
        top = np.argpartition(-scores, count - 1)[:count]
        top = top[np.argsort(-scores[top], kind="stable")]
        floor = getattr(embedder, "min_similarity", 0.0)
        return [
            (int(ids[i]), float(scores[i]))
            for i in top
            if np.isfinite(scores[i]) and scores[i] >= floor
        ]


def _as_bookmark(row: sqlite3.Row) -> dict[str, Any]:
    """A row as the tools return it: the card's keys first, derived fields after."""
    bookmark: dict[str, Any] = {
        "id": row["hash"],
        "url": row["url"],
        "title": row["title"],
        "tags": row["tags"].split(),
        "notes": row["notes"],
        "saved_at": row["saved_at"],
        "private": bool(row["private"]),
        "to_read": bool(row["to_read"]),
        "domain": row["domain"],
        "source": row["source"],
    }
    if row["author_handle"]:
        bookmark["author_handle"] = row["author_handle"]
    if row["author_name"]:
        bookmark["author_name"] = row["author_name"]
    if row["posted_at"]:
        bookmark["posted_at"] = row["posted_at"]
    return bookmark

"""The index as the tools use it: argument parsing, result shaping and status.

``main.py`` holds the MCP tool definitions; each one hands its arguments here
when the index is ready and falls back to the live Pinboard client when it is
not (switched off, or nothing synced yet).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from pinboard_mcp_server import enrich
from pinboard_mcp_server.embeddings import Embedder, default_embedder
from pinboard_mcp_server.index import (
    BookmarkIndex,
    Filters,
    SearchOutcome,
    default_index_path,
)
from pinboard_mcp_server.sync import IndexWorker, PinboardSync, sync_status

logger = logging.getLogger(__name__)

INDEX_ENV_VAR = "PINBOARD_INDEX"
SYNC_INTERVAL_ENV_VAR = "PINBOARD_INDEX_SYNC_INTERVAL"

_OFF = {"false", "0", "no", "off"}

DATE_FIELDS = ("saved", "posted")


def index_enabled(environ: dict[str, str] | os._Environ[str] | None = None) -> bool:
    """Whether the local index is on. On unless ``PINBOARD_INDEX`` says ``off``."""
    env = environ if environ is not None else os.environ
    return env.get(INDEX_ENV_VAR, "").strip().lower() not in _OFF


def parse_bound(value: str | None, name: str, end: bool = False) -> str | None:
    """An ISO date or datetime argument as an index timestamp (UTC).

    A bare date as an upper bound covers that whole day; the result is always
    used as an exclusive bound.
    """
    if value is None or not value.strip():
        return None
    text = value.strip()
    moment = enrich.parse_utc(text)
    if moment is None:
        raise ValueError(f"{name} must be in ISO format (YYYY-MM-DD)")
    if end:
        date_only = len(text) <= 10
        moment += timedelta(days=1) if date_only else timedelta(seconds=1)
    return enrich.format_utc(moment)


class IndexService:
    """One index, its background worker, and the queries the tools make of it."""

    def __init__(
        self,
        index: BookmarkIndex,
        syncer: PinboardSync | None = None,
        embedder: Embedder | None = None,
        sync_interval: float = 300.0,
    ):
        self.index = index
        self.syncer = syncer
        self.embedder = embedder
        self.worker = IndexWorker(syncer, index, embedder, interval=sync_interval)

    @classmethod
    def from_environment(
        cls,
        pinboard_client: Any = None,
        rate_limit: Any = None,
        environ: dict[str, str] | os._Environ[str] | None = None,
    ) -> IndexService:
        """Open the configured index. ``pinboard_client`` is the ``pinboard.Pinboard``
        to sync from; without one the index is read-only."""
        env = environ if environ is not None else os.environ
        index = BookmarkIndex(default_index_path(env))
        syncer = (
            PinboardSync(pinboard_client, index, rate_limit=rate_limit)
            if pinboard_client is not None
            else None
        )
        try:
            interval = max(60.0, float(env.get(SYNC_INTERVAL_ENV_VAR, "") or 300.0))
        except ValueError:
            interval = 300.0
        return cls(index, syncer, default_embedder(env), sync_interval=interval)

    def start(self) -> None:
        """Start syncing and embedding in the background."""
        if not self.worker.is_alive():
            self.worker.start()

    def stop(self) -> None:
        self.worker.stop()

    def ready(self) -> bool:
        """Whether there is anything in the index to answer from."""
        try:
            return self.index.count() > 0
        except Exception as error:
            logger.warning("Bookmark index unavailable: %s", type(error).__name__)
            return False

    # -- shaping -----------------------------------------------------------

    def _brief_status(self) -> dict[str, Any]:
        status = sync_status(self.index)
        brief: dict[str, Any] = {
            "bookmarks": status["bookmarks"],
            "last_synced_at": status["last_synced_at"],
            "complete": status["complete"],
        }
        if not status["complete"]:
            brief["note"] = (
                "The first sync from Pinboard is still running, newest bookmarks "
                "first; older ones may be missing from these results."
            )
        if status["last_error"]:
            brief["last_error"] = status["last_error"]
        return brief

    def _result(self, outcome: SearchOutcome, **extra: Any) -> dict[str, Any]:
        result: dict[str, Any] = {
            "bookmarks": outcome.bookmarks,
            "total": len(outcome.bookmarks),
            **extra,
            "has_more": outcome.has_more,
            "search_mode": outcome.mode,
            "index": self._brief_status(),
        }
        if outcome.semantic_note:
            result["semantic_note"] = outcome.semantic_note
        return result

    @staticmethod
    def filters(
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        date_field: str = "saved",
        tags: list[str] | None = None,
        author: str | None = None,
        domain: str | None = None,
        source: str | None = None,
        to_read: bool | None = None,
    ) -> Filters:
        if date_field not in DATE_FIELDS:
            raise ValueError("date_field must be 'saved' or 'posted'")
        return Filters(
            since=parse_bound(from_date, "from_date"),
            until=parse_bound(to_date, "to_date", end=True),
            date_field=date_field,
            tags=tuple(tag.strip() for tag in tags or [] if tag and tag.strip()),
            author=(author or "").strip() or None,
            domain=(domain or "").strip() or None,
            source=(source or "").strip() or None,
            to_read=to_read,
        )

    @staticmethod
    def _described(filters: Filters) -> dict[str, Any]:
        described: dict[str, Any] = {}
        if filters.since:
            described["from"] = filters.since
        if filters.until:
            described["before"] = filters.until
        if filters.since or filters.until:
            described["date_field"] = filters.date_field
        for key in ("author", "domain", "source", "to_read"):
            value = getattr(filters, key)
            if value is not None:
                described[key] = value
        if filters.tags:
            described["with_tags"] = list(filters.tags)
        return described

    # -- queries -----------------------------------------------------------

    async def search(
        self,
        query: str,
        filters: Filters,
        *,
        mode: str = "auto",
        limit: int = 20,
        offset: int = 0,
        **extra: Any,
    ) -> dict[str, Any]:
        outcome = await asyncio.to_thread(
            self.index.search,
            query,
            filters,
            mode=mode,
            limit=limit,
            offset=offset,
            embedder=self.embedder,
        )
        described = self._described(filters)
        if described:
            extra = {**extra, "filters": described}
        return self._result(outcome, query=query, **extra)

    async def recent(self, days: int, limit: int) -> dict[str, Any]:
        since = enrich.format_utc(datetime.now(timezone.utc) - timedelta(days=days))
        outcome = await asyncio.to_thread(
            self.index.listing, Filters(since=since), limit
        )
        return self._result(outcome, days=days)

    async def by_tags(
        self, tags: list[str], from_date: str | None, to_date: str | None, limit: int
    ) -> dict[str, Any]:
        filters = self.filters(from_date=from_date, to_date=to_date, tags=tags)
        outcome = await asyncio.to_thread(self.index.listing, filters, limit)
        return self._result(outcome, tags=tags, from_date=from_date, to_date=to_date)

    async def tags(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.index.tag_counts)

    async def counts(
        self, group_by: str, filters: Filters, query: str, limit: int
    ) -> dict[str, Any]:
        groups = await asyncio.to_thread(
            self.index.counts, group_by, filters, query, limit
        )
        result: dict[str, Any] = {"group_by": group_by, "groups": groups}
        if query.strip():
            result["query"] = query
        described = self._described(filters)
        if described:
            result["filters"] = described
        result["index"] = self._brief_status()
        return result

    def status(self) -> dict[str, Any]:
        status = sync_status(self.index)
        status["enabled"] = True
        status["syncing_from_pinboard"] = self.syncer is not None
        if self.embedder is None:
            status["semantic_search"] = {
                "available": False,
                "how_to_enable": (
                    'Install the optional extra: pip install "pinboard-bookmarks-mcp-server[semantic]"'
                ),
            }
        else:
            pending = self.index.pending_embedding_count(self.embedder.model_id)
            status["semantic_search"] = {
                "available": True,
                "model": self.embedder.model_id,
                "model_loaded": bool(getattr(self.embedder, "ready", True)),
                "bookmarks_waiting_to_be_embedded": pending,
            }
            if self.worker.embedding_error:
                status["semantic_search"]["last_error"] = self.worker.embedding_error
        return status

"""Keeping the local index in step with Pinboard.

Pinboard stays the place bookmarks are saved; this module copies them into the
index (:mod:`pinboard_mcp_server.index`) and keeps the copy current with as few
API calls as possible. One sync is:

1. ``posts/update``: one cheap call that says when the account last changed.
   If that has not moved since the last sync, stop: nothing to do.
2. ``posts/dates``: one cheap call that returns the number of bookmarks on
   every day of the account's history. Comparing it with the counts recorded
   at the last sync names exactly the days that gained or lost a bookmark,
   wherever in history they are. That matters for imports that backdate what
   they save (a Twitter backfill, say), which a "what is new since my newest
   bookmark" check would never see.
3. ``posts/all`` with ``fromdt`` and ``todt`` for each run of changed days,
   and the index is made to match what comes back: new bookmarks added,
   edited ones updated, missing ones removed.

The first sync is the same algorithm with nothing recorded, so every day is
"changed" and the whole account is read, in windows of at most
``max_window`` bookmarks, newest first. ``posts/all`` is never called without
both date bounds.

Pinboard asks for ``posts/all`` no more than once every five minutes, so a
sync that needs several windows waits between them. Progress is recorded
after each window: a sync that is stopped, or a process that exits, picks up
where it left off.

An edit to an old bookmark changes ``posts/update`` but no day's count. When
the account fits in one window the whole of it is re-read (one call); for a
larger account the last 30 days are re-read and a full pass is scheduled, at
most once a week.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
import urllib.error
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pinboard_mcp_server import enrich
from pinboard_mcp_server.index import BookmarkIndex, Post, SyncDelta

logger = logging.getLogger(__name__)

#: Pinboard's documented limit for ``posts/all``: once every five minutes.
POSTS_ALL_INTERVAL = 300.0

#: Pinboard's documented limit for everything else: once every three seconds.
CALL_INTERVAL = 3.0

#: The most bookmarks one ``posts/all`` window is planned to hold.
MAX_WINDOW = 10_000

#: How many recent days are re-read when an edit cannot be located.
EDIT_REFRESH_DAYS = 30

#: An account this small is re-read whole (one ``posts/all`` call) whenever
#: anything changes, which catches every edit at once.
FULL_REFRESH_LIMIT = 2_000

#: The least time between automatic full passes.
FULL_RESYNC_INTERVAL = timedelta(days=7)

_MAX_BACKOFF = 3600.0

#: A window Pinboard could not serve is halved for the next attempt, down to this.
MIN_WINDOW = 500

#: Seconds a call may go without a byte from Pinboard. pinboard.py sets no
#: timeout of its own, and a connection that hangs would hold the sync for ever.
NETWORK_TIMEOUT = 180.0

_timeout_lock = threading.Lock()


@contextmanager
def _network_timeout(seconds: float) -> Iterator[None]:
    """Give sockets opened inside the block a timeout, if none is set already.

    The default is process-wide, which is the only handle pinboard.py leaves;
    it is put back straight after the call.
    """
    with _timeout_lock:
        previous = socket.getdefaulttimeout()
        if previous is None:
            socket.setdefaulttimeout(seconds)
        try:
            yield
        finally:
            socket.setdefaulttimeout(previous)


def _too_big(error: Exception) -> bool:
    """Whether a failed ``posts/all`` looks like a window Pinboard could not serve."""
    if isinstance(error, urllib.error.HTTPError):
        return 500 <= error.code < 600
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, TimeoutError)
    return isinstance(error, TimeoutError)


_MAX_RATE_LIMIT_RETRIES = 3

_TOKEN = re.compile(r"(auth_token=)[^&\s]+")


class SyncStopped(Exception):
    """The sync was asked to stop while it waited."""


def _redact(text: str) -> str:
    return _TOKEN.sub(r"\1[redacted]", text)


def safe_error(error: BaseException) -> str:
    """An error as text that is safe to log and show: never the request URL or the token."""
    if isinstance(error, urllib.error.HTTPError):
        return f"Pinboard answered HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        return f"Could not reach Pinboard: {_redact(str(error.reason))}"
    text = _redact(str(error))
    return f"{type(error).__name__}: {text}"[:300]


@dataclass
class SyncReport:
    """What one sync did."""

    up_to_date: bool = False
    windows_planned: int = 0
    windows_done: int = 0
    delta: SyncDelta = field(default_factory=SyncDelta)
    remote_total: int | None = None
    stopped: bool = False
    error: str | None = None

    @property
    def complete(self) -> bool:
        return (
            self.error is None
            and not self.stopped
            and self.windows_done == self.windows_planned
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "up_to_date": self.up_to_date,
            "complete": self.complete,
            "windows_planned": self.windows_planned,
            "windows_done": self.windows_done,
            **self.delta.as_dict(),
            "remote_total": self.remote_total,
            "error": self.error,
        }


@dataclass(frozen=True)
class Window:
    """A run of days to re-read: ``first`` to ``last`` inclusive."""

    first: date
    last: date
    count: int


def plan_windows(
    remote: dict[str, int], changed: set[str], max_window: int = MAX_WINDOW
) -> list[Window]:
    """Group changed days into ``posts/all`` windows, newest first.

    ``posts/all`` calls are the scarce thing (one per five minutes), bandwidth
    is not, so a window stretches across unchanged days to reach the next
    changed one for as long as everything in between stays under
    ``max_window`` bookmarks.
    """
    days = sorted({date.fromisoformat(day) for day in changed}, reverse=True)
    if not days:
        return []
    known = sorted((date.fromisoformat(day), count) for day, count in remote.items())

    def span_count(first: date, last: date) -> int:
        return sum(count for day, count in known if first <= day <= last)

    windows: list[Window] = []
    last = first = days[0]
    for day in days[1:]:
        if span_count(day, last) <= max_window:
            first = day
        else:
            windows.append(Window(first, last, span_count(first, last)))
            last = first = day
    windows.append(Window(first, last, span_count(first, last)))
    return windows


class PinboardSync:
    """Runs syncs against one Pinboard account and one index."""

    def __init__(
        self,
        pinboard_client: Any,
        index: BookmarkIndex,
        *,
        rate_limit: Callable[[], None] | None = None,
        wait: Callable[[float], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
        posts_all_interval: float = POSTS_ALL_INTERVAL,
        max_window: int = MAX_WINDOW,
        full_refresh_limit: int = FULL_REFRESH_LIMIT,
        wait_for_windows: bool = True,
        account: str | None = None,
        network_timeout: float = NETWORK_TIMEOUT,
    ):
        """
        Args:
            pinboard_client: a ``pinboard.Pinboard`` (or anything shaped like one).
            index: the index to keep in step.
            rate_limit: called before every API call; blocks until a call is
                allowed. Defaults to three-second spacing.
            wait: sleeps for the given seconds; returns ``False`` to stop the
                sync. Defaults to ``time.sleep``.
            clock: the current UTC time; a seam for tests.
            posts_all_interval: seconds between ``posts/all`` calls.
            max_window: the most bookmarks to plan into one window.
            full_refresh_limit: accounts with at most this many bookmarks are
                re-read whole on any change.
            account: the Pinboard username being synced. An index that was
                filled from another account is emptied first, so two accounts
                never share one file's bookmarks.
            network_timeout: seconds a call may wait on Pinboard.
            wait_for_windows: ``False`` stops after the windows that can be
                fetched now rather than waiting out the ``posts/all`` interval.
        """
        self._pb = pinboard_client
        self.index = index
        self._last_call = 0.0
        self._rate_limit = rate_limit or self._default_rate_limit
        self.wait = wait or self._default_wait
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.posts_all_interval = posts_all_interval
        self.max_window = max_window
        self.full_refresh_limit = min(full_refresh_limit, max_window)
        self.account = (account or "").strip().lower() or None
        self.network_timeout = network_timeout
        self.wait_for_windows = wait_for_windows
        self._lock = threading.Lock()

    # -- pacing ------------------------------------------------------------

    def _default_rate_limit(self) -> None:
        pause = CALL_INTERVAL - (time.monotonic() - self._last_call)
        if pause > 0:
            time.sleep(pause)
        self._last_call = time.monotonic()

    @staticmethod
    def _default_wait(seconds: float) -> bool:
        time.sleep(seconds)
        return True

    def _pause(self, seconds: float) -> None:
        if seconds > 0 and not self.wait(seconds):
            raise SyncStopped

    def _call(self, func: Callable[[], Any]) -> Any:
        self._rate_limit()
        with _network_timeout(self.network_timeout):
            return func()

    def _await_posts_all_slot(self) -> None:
        """Wait until ``posts/all`` may be called again. The last call's time is
        kept in the index, so a restart or a second process honours it too."""
        last = enrich.parse_utc(self.index.get_meta("last_posts_all_at") or "")
        if last is None:
            return
        remaining = self.posts_all_interval - (self._clock() - last).total_seconds()
        if remaining <= 0:
            return
        if not self.wait_for_windows:
            raise SyncStopped
        logger.info("Waiting %.0f s before the next posts/all call", remaining)
        self._pause(remaining)

    def _fetch_window(self, since: str, until: str, expected: int = 0) -> list[Any]:
        """One date-bounded ``posts/all``. ``expected`` is how many bookmarks
        ``posts/dates`` says the window holds."""
        backoff = max(self.posts_all_interval, 1.0)
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            self._await_posts_all_slot()
            self.index.set_meta("last_posts_all_at", enrich.format_utc(self._clock()))
            try:
                posts = self._call(
                    lambda: self._pb.posts.all(fromdt=since, todt=until, meta=True)
                )
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == _MAX_RATE_LIMIT_RETRIES:
                    raise
                if not self.wait_for_windows:
                    raise SyncStopped from None
                logger.info("Pinboard said 429; backing off for %.0f s", backoff)
                self._pause(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            # What comes back replaces what the index holds for the window, so
            # an answer that cannot be right must not be taken for "nothing here".
            if not isinstance(posts, list):
                raise RuntimeError("Pinboard did not return a list of bookmarks")
            if not posts and expected > 0:
                raise RuntimeError(
                    f"Pinboard returned no bookmarks for a window holding {expected}"
                )
            return posts
        return []

    # -- the sync ----------------------------------------------------------

    def sync(self, full: bool = False) -> SyncReport:
        """Bring the index up to date. Never raises for a Pinboard failure:
        the error is in the report and in the index's status."""
        with self._lock:
            report = SyncReport()
            try:
                self._sync(full, report)
            except SyncStopped:
                report.stopped = True
            except Exception as error:
                report.error = safe_error(error)
                logger.warning("Pinboard sync failed: %s", report.error)
            self.index.set_meta("last_checked_at", enrich.format_utc(self._clock()))
            self.index.set_meta("last_error", report.error)
            return report

    def _claim_index(self) -> None:
        """Make sure the index holds this account's bookmarks and no other's."""
        if self.account is None:
            return
        known = self.index.get_meta("account")
        if known is not None and known != self.account:
            logger.warning(
                "The index was synced from another Pinboard account; starting it again"
            )
            self.index.reset()
        if known != self.account:
            self.index.set_meta("account", self.account)

    def _window_limit(self) -> int:
        learned = _int(self.index.get_meta("window_limit"))
        return min(self.max_window, learned) if learned else self.max_window

    def _sync(self, full: bool, report: SyncReport) -> None:
        index = self.index
        self._claim_index()
        update = self._call(lambda: self._pb.posts.update())
        update_time = (
            enrich.format_utc(update) if hasattr(update, "isoformat") else str(update)
        )

        recorded: dict[str, int] | None = index.get_json("synced_dates")
        last_full = enrich.parse_utc(index.get_meta("last_full_sync_at") or "")
        full_due = index.get_meta("needs_full_sync") == "1" and (
            last_full is None or self._clock() - last_full >= FULL_RESYNC_INTERVAL
        )
        if (full or full_due) and index.get_meta("full_sync_pending") != "1":
            # Not restarted if one is already under way: it carries on below.
            self._begin_full_pass()
            recorded = {}

        unfinished = (
            index.get_meta("sync_incomplete") == "1"
            or index.get_meta("full_sync_pending") == "1"
        )
        if (
            not unfinished
            and recorded is not None
            and update_time == index.get_meta("last_update_time")
        ):
            report.up_to_date = True
            return

        dates_response = self._call(lambda: self._pb.posts.dates())
        raw_dates = (
            dates_response.get("dates", {}) if isinstance(dates_response, dict) else {}
        )
        remote = {
            _day(day): int(count) for day, count in raw_dates.items() if int(count) > 0
        }
        report.remote_total = sum(remote.values())

        if recorded is None:
            # Nothing recorded: a fresh index, or one seeded from an export
            # file, whose own day counts stand in for the first comparison.
            recorded = index.day_counts() if index.count() else {}
            index.set_json("synced_dates", recorded)

        def differing(known: dict[str, int]) -> set[str]:
            return {
                day
                for day in remote.keys() | known.keys()
                if remote.get(day, 0) != known.get(day, 0)
            }

        changed = differing(recorded)
        previous_update = index.get_meta("last_update_time")
        if (
            previous_update is not None
            and update_time != previous_update
            and index.get_meta("full_sync_pending") != "1"
        ):
            # Something changed since the last sync. Day counts show what was
            # added or deleted, but not what was edited in place (retagged,
            # retitled), and an edit can sit alongside an addition, so a
            # changed count does not rule one out. A small account is simply
            # re-read. A large one re-reads its recent days, where most edits
            # happen, and leaves older edits to the weekly full pass.
            if report.remote_total <= self.full_refresh_limit:
                self._begin_full_pass()
                recorded = {}
                changed = differing(recorded)
            else:
                cutoff = (
                    self._clock().date() - timedelta(days=EDIT_REFRESH_DAYS)
                ).isoformat()
                changed |= {day for day in remote if day >= cutoff}
                index.set_meta("needs_full_sync", "1")

        # A full pass re-reads every day Pinboard has (nothing is recorded, so
        # every day differs) and also clears days the index has that Pinboard
        # no longer does. It stays pending until it finishes, so one that is
        # interrupted carries on at the next sync instead of being forgotten.
        full_pass = index.get_meta("full_sync_pending") == "1"
        if full_pass:
            changed |= index.day_counts().keys() - remote.keys()

        windows = plan_windows(remote, changed, self._window_limit())
        report.windows_planned = len(windows)
        covers_everything = full_pass or (bool(remote) and changed >= set(remote))
        if windows:
            index.set_meta("sync_incomplete", "1")
        if index.get_meta("pass_update_time") is None:
            # Pinboard's change time as this pass began. A pass can take
            # several runs, and an edit made between them to a window already
            # read would otherwise go unseen: the pass finishes by recording
            # this time, not the latest, so the next sync notices the change.
            index.set_meta("pass_update_time", update_time)

        for window in windows:
            # A day's margin either side, in case Pinboard buckets a day's
            # bookmarks in a timezone other than UTC.
            since = _midnight(window.first - timedelta(days=1))
            until = _midnight(window.last + timedelta(days=2))
            try:
                posts = self._fetch_window(since, until, window.count)
            except Exception as error:
                if _too_big(error) and window.count > MIN_WINDOW:
                    # Ask for half as much next time instead of repeating a
                    # request Pinboard cannot serve.
                    index.set_meta(
                        "window_limit", str(max(window.count // 2, MIN_WINDOW))
                    )
                raise
            report.delta += index.replace_range(
                since, until, (Post.from_pinboard(post) for post in posts)
            )
            day = window.first
            while day <= window.last:
                key = day.isoformat()
                if key in remote:
                    recorded[key] = remote[key]
                else:
                    recorded.pop(key, None)
                day += timedelta(days=1)
            index.set_json("synced_dates", recorded)
            report.windows_done += 1
            logger.info(
                "Synced %s to %s: %d bookmarks", window.first, window.last, len(posts)
            )

        now = enrich.format_utc(self._clock())
        index.set_meta("sync_incomplete", None)
        index.set_meta(
            "last_update_time", index.get_meta("pass_update_time") or update_time
        )
        index.set_meta("pass_update_time", None)
        index.set_meta("last_synced_at", now)
        index.set_meta("remote_total", str(report.remote_total))
        if covers_everything:
            index.set_meta("last_full_sync_at", now)
            index.set_meta("needs_full_sync", None)
            index.set_meta("full_sync_pending", None)
        elif index.count() != report.remote_total:
            # The counts should agree after any sync. When they do not, a full
            # pass (rate-limited to one a week) settles it.
            index.set_meta("needs_full_sync", "1")

    def _begin_full_pass(self) -> None:
        self.index.set_meta("full_sync_pending", "1")
        self.index.set_meta("pass_update_time", None)
        self.index.set_json("synced_dates", {})


def _day(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)[:10]


def _midnight(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


def import_export(index: BookmarkIndex, path: str | Path) -> SyncDelta:
    """Load a Pinboard JSON export (pinboard.in/export/format:json/) into the index.

    The quickest way to seed the index: one file, no API calls. The next sync
    then only fetches the days whose counts differ from the file.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("A Pinboard export is a JSON list of bookmarks")
    posts = [Post.from_pinboard(entry) for entry in data if isinstance(entry, dict)]
    return index.upsert(posts)


def sync_status(index: BookmarkIndex) -> dict[str, Any]:
    """The index's size and sync state, as the tools and the CLI report it."""
    status: dict[str, Any] = {
        **index.stats(),
        "path": str(index.path),
        "last_synced_at": index.get_meta("last_synced_at"),
        "last_checked_at": index.get_meta("last_checked_at"),
        "last_full_sync_at": index.get_meta("last_full_sync_at"),
        "pinboard_total": _int(index.get_meta("remote_total")),
        "sync_in_progress": index.get_meta("sync_incomplete") == "1",
        "last_error": index.get_meta("last_error"),
    }
    status["complete"] = (
        bool(status["last_synced_at"]) and not status["sync_in_progress"]
    )
    return status


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class IndexWorker(threading.Thread):
    """A daemon thread that syncs the index, embeds what is new, and repeats.

    A daemon so that it can never hold the server open: a sync waiting out
    Pinboard's five-minute ``posts/all`` interval dies with the process, and
    SQLite's journal makes a write cut off mid-way harmless.
    """

    def __init__(
        self,
        syncer: PinboardSync | None,
        index: BookmarkIndex,
        embedder: Any = None,
        interval: float = 300.0,
    ):
        super().__init__(name="pinboard-index", daemon=True)
        self.syncer = syncer
        self.index = index
        self.embedder = embedder
        self.interval = interval
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self.embedding_error: str | None = None
        if syncer is not None:
            syncer.wait = self._interruptible_wait

    def _interruptible_wait(self, seconds: float) -> bool:
        return not self._stop_event.wait(seconds)

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def wake(self) -> None:
        """Run a pass now instead of waiting out the interval."""
        self._wake_event.set()

    def run_once(self) -> None:
        if self.syncer is not None:
            self.syncer.sync()
        if self.embedder is not None and not self._stop_event.is_set():
            from pinboard_mcp_server.embeddings import embed_pending

            try:
                warm_up = getattr(self.embedder, "warm_up", None)
                if warm_up is not None and self.index.count():
                    warm_up()
                embed_pending(
                    self.index, self.embedder, should_stop=self._stop_event.is_set
                )
                self.embedding_error = None
            except Exception as error:
                self.embedding_error = safe_error(error)
                logger.warning("Embedding failed: %s", self.embedding_error)

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            try:
                self.run_once()
            except Exception as error:  # the worker must outlive any one failure
                logger.warning("Index worker pass failed: %s", safe_error(error))
            self._wake_event.wait(self.interval)

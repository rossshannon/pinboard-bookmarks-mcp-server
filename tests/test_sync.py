"""Keeping the index in step with Pinboard: the first sync, increments, edits, deletions, pacing."""

import urllib.error
from datetime import date, timedelta

import pytest

from pinboard_mcp_server.embeddings import embed_pending
from pinboard_mcp_server.index import BookmarkIndex, Filters, Post
from pinboard_mcp_server.sync import (
    IndexWorker,
    PinboardSync,
    Window,
    import_export,
    plan_windows,
    safe_error,
    sync_status,
)

from .fakes import EXPORT, Clock, FakeEmbedder, FakePinboard, export_posts, post


@pytest.fixture
def index(tmp_path) -> BookmarkIndex:
    return BookmarkIndex(tmp_path / "index.sqlite3")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def account() -> FakePinboard:
    return FakePinboard(export_posts())


def syncer(account, index, clock, **options) -> PinboardSync:
    return PinboardSync(
        account, index, rate_limit=lambda: None, wait=clock.wait, clock=clock, **options
    )


def later(clock: Clock, minutes: float = 10) -> None:
    clock.now += timedelta(minutes=minutes)


class TestPlanWindows:
    def test_nothing_changed_needs_no_windows(self):
        assert plan_windows({"2026-01-01": 5}, set()) == []

    def test_a_small_account_is_one_window(self):
        remote = {"2020-01-01": 10, "2023-06-01": 20, "2026-11-01": 30}

        assert plan_windows(remote, set(remote), 100) == [
            Window(date(2020, 1, 1), date(2026, 11, 1), 60)
        ]

    def test_a_large_account_splits_newest_first(self):
        remote = {f"2026-01-{day:02d}": 40 for day in range(1, 11)}

        windows = plan_windows(remote, set(remote), 100)

        assert [(w.first.day, w.last.day, w.count) for w in windows] == [
            (9, 10, 80),
            (7, 8, 80),
            (5, 6, 80),
            (3, 4, 80),
            (1, 2, 80),
        ]

    def test_a_window_reaches_across_unchanged_days_while_it_stays_small(self):
        remote = {"2026-01-01": 1, "2026-01-05": 3, "2026-01-09": 1}

        assert plan_windows(remote, {"2026-01-01", "2026-01-09"}, 10) == [
            Window(date(2026, 1, 1), date(2026, 1, 9), 5)
        ]

    def test_but_not_across_a_mountain(self):
        remote = {"2026-01-01": 1, "2026-01-05": 500, "2026-01-09": 1}

        assert plan_windows(remote, {"2026-01-01", "2026-01-09"}, 10) == [
            Window(date(2026, 1, 9), date(2026, 1, 9), 1),
            Window(date(2026, 1, 1), date(2026, 1, 1), 1),
        ]

    def test_a_single_huge_day_is_its_own_window(self):
        assert plan_windows({"2026-01-01": 50_000}, {"2026-01-01"}, 10) == [
            Window(date(2026, 1, 1), date(2026, 1, 1), 50_000)
        ]

    def test_a_day_pinboard_no_longer_has_still_gets_a_window(self):
        assert plan_windows({}, {"2026-01-01"}) == [
            Window(date(2026, 1, 1), date(2026, 1, 1), 0)
        ]


class TestFirstSync:
    def test_reads_the_whole_account_in_one_bounded_call(self, account, index, clock):
        report = syncer(account, index, clock).sync()

        assert report.complete and not report.up_to_date
        assert report.delta.added == 23
        assert index.count() == report.remote_total == 23
        assert account.count("all") == 1
        _, params = next(call for call in account.calls if call[0] == "all")
        assert params["fromdt"] == "2015-03-20T00:00:00Z"
        assert params["todt"] == "2026-11-27T00:00:00Z"

    def test_posts_all_is_never_called_without_both_bounds(self, account, index, clock):
        sync = syncer(account, index, clock, max_window=5)
        sync.sync()
        account.add(post("https://new/", time="2026-11-26T10:00:00Z"))
        later(clock)
        sync.sync()

        for name, params in account.calls:
            if name == "all":
                assert params.get("fromdt") and params.get("todt")

    def test_records_status(self, account, index, clock):
        syncer(account, index, clock).sync()

        status = sync_status(index)
        assert status["complete"] is True
        assert status["bookmarks"] == status["pinboard_total"] == 23
        assert status["last_synced_at"] == "2026-11-27T12:00:00Z"
        assert status["last_full_sync_at"] == "2026-11-27T12:00:00Z"
        assert status["last_error"] is None
        assert status["tweets"] == 11

    def test_a_large_account_waits_five_minutes_between_windows(
        self, account, index, clock
    ):
        report = syncer(account, index, clock, max_window=8).sync()

        assert report.windows_planned == account.count("all") > 1
        assert index.count() == 23
        assert len(clock.waits) == report.windows_planned - 1
        assert all(wait == 300 for wait in clock.waits)

    def test_newest_bookmarks_arrive_first(self, account, index, clock):
        sync = syncer(account, index, clock, max_window=8, wait_for_windows=False)

        report = sync.sync()

        assert report.stopped and not report.complete
        assert report.windows_done == 1
        assert index.stats()["newest_saved_at"] == "2026-11-25T09:00:00Z"
        assert sync_status(index)["complete"] is False
        assert sync_status(index)["sync_in_progress"] is True

    def test_a_stopped_sync_resumes_where_it_left_off(self, account, index, clock):
        sync = syncer(account, index, clock, max_window=8, wait_for_windows=False)
        sync.sync()
        fetched = account.count("all")

        while not sync_status(index)["complete"]:
            later(clock)
            sync.sync()

        assert index.count() == 23
        # One window per run, none fetched twice.
        windows = [params["fromdt"] for name, params in account.calls if name == "all"]
        assert len(windows) == len(set(windows)) > fetched

    def test_an_empty_account(self, index, clock):
        report = syncer(FakePinboard(), index, clock).sync()

        assert report.complete
        assert index.count() == 0
        assert sync_status(index)["complete"] is True


class TestIncrementalSync:
    @pytest.fixture
    def synced(self, account, index, clock) -> PinboardSync:
        sync = syncer(account, index, clock)
        sync.sync()
        account.calls.clear()
        later(clock)
        return sync

    def test_nothing_changed_costs_one_cheap_call(self, synced, account):
        report = synced.sync()

        assert report.up_to_date
        assert [name for name, _ in account.calls] == ["update"]

    def test_a_new_bookmark_is_found_in_one_call(self, synced, account, index):
        account.add(post("https://new/", "Brand new", time="2026-11-27T09:00:00Z"))

        report = synced.sync()

        assert report.delta.added == 1
        assert index.get("https://new/")["title"] == "Brand new"
        assert account.count("all") == 1

    def test_an_edit_made_alongside_an_addition_is_not_missed(
        self, synced, account, index
    ):
        """The day counts change, but only for the addition's day."""
        account.add(post("https://new/", "Brand new", time="2026-11-27T09:00:00Z"))
        account.edit("https://pinboard.in/howto/", description="Renamed")

        synced.sync()

        assert index.get("https://new/") is not None
        assert index.get("https://pinboard.in/howto/")["title"] == "Renamed"

    def test_a_backdated_save_is_found(self, synced, account, index):
        """A Twitter backfill saves old likes under their original dates."""
        account.add(
            post(
                "https://x.com/someone/status/1100000000000000000",
                "Someone (@someone): an old like",
                time="2019-02-25T10:00:00Z",
                tags="via:twitter-like",
            )
        )

        report = synced.sync()

        assert report.delta.added == 1
        found = index.search("", Filters(author="someone")).bookmarks
        assert found[0]["source"] == "twitter-like"

    def test_a_deletion_is_removed(self, synced, account, index):
        account.delete("https://sqlite.org/fts5.html")

        report = synced.sync()

        assert report.delta.deleted == 1
        assert index.get("https://sqlite.org/fts5.html") is None
        assert index.count() == 22

    def test_deleting_the_only_bookmark_of_a_day(self, synced, account, index):
        account.delete("https://m.youtube.com/watch?v=dQw4w9WgXcQ")

        synced.sync()

        assert index.get("https://m.youtube.com/watch?v=dQw4w9WgXcQ") is None

    def test_an_edit_is_picked_up(self, synced, account, index):
        account.edit(
            "https://pinboard.in/howto/", description="Renamed", tags="pinboard renamed"
        )

        report = synced.sync()

        assert report.delta.updated == 1
        assert index.get("https://pinboard.in/howto/")["title"] == "Renamed"
        assert index.search("", Filters(tags=("renamed",))).bookmarks

    def test_a_re_dated_bookmark_moves(self, synced, account, index):
        account.edit("https://pinboard.in/howto/", time="2026-11-26T08:00:00Z")

        synced.sync()

        assert (
            index.get("https://pinboard.in/howto/")["saved_at"]
            == "2026-11-26T08:00:00Z"
        )
        assert index.count() == 23

    def test_a_sync_after_that_is_quiet_again(self, synced, account):
        account.add(post("https://new/", time="2026-11-27T09:00:00Z"))
        synced.sync()
        account.calls.clear()

        assert synced.sync().up_to_date
        assert [name for name, _ in account.calls] == ["update"]

    def test_posts_all_spacing_holds_across_syncs(self, synced, account, clock):
        clock.now -= timedelta(minutes=8)  # two minutes after the first sync's call
        clock.waits.clear()
        account.add(post("https://new/", time="2026-11-27T09:00:00Z"))

        synced.sync()

        assert clock.waits == [180]


class TestEditsInALargeAccount:
    """When the account does not fit one window, an edit cannot be located by counts."""

    @pytest.fixture
    def synced(self, account, index, clock) -> PinboardSync:
        sync = syncer(account, index, clock, max_window=8)
        sync.sync()
        account.calls.clear()
        later(clock)
        return sync

    def test_a_recent_edit_is_caught_by_re_reading_the_last_month(
        self, synced, account, index
    ):
        account.edit("https://sqlite.org/fts5.html", description="Recent edit")

        synced.sync()

        assert index.get("https://sqlite.org/fts5.html")["title"] == "Recent edit"
        assert account.count("all") == 1

    def test_a_recent_edit_alongside_an_old_addition_is_caught(
        self, synced, account, index
    ):
        account.add(post("https://old/", "Backdated", time="2012-05-05T09:00:00Z"))
        account.edit("https://sqlite.org/fts5.html", description="Recent edit")

        synced.sync()

        assert index.get("https://old/") is not None
        assert index.get("https://sqlite.org/fts5.html")["title"] == "Recent edit"
        assert index.get_meta("needs_full_sync") == "1"

    def test_a_new_bookmark_re_reads_only_recent_days(self, synced, account, index):
        account.add(post("https://new/", "Brand new", time="2026-11-27T09:00:00Z"))

        synced.sync()

        assert index.get("https://new/") is not None
        fetched = [params["fromdt"] for name, params in account.calls if name == "all"]
        assert fetched and min(fetched) >= "2026-10-26T00:00:00Z"

    def test_an_old_edit_waits_for_the_weekly_full_pass(
        self, synced, account, index, clock
    ):
        account.edit("https://pinboard.in/howto/", description="Old edit")
        synced.sync()
        assert index.get("https://pinboard.in/howto/")["title"] != "Old edit"

        clock.now += timedelta(days=8)
        synced.sync()

        assert index.get("https://pinboard.in/howto/")["title"] == "Old edit"
        assert index.get_meta("needs_full_sync") is None

    def test_a_forced_full_sync_catches_it_at_once(self, synced, account, index, clock):
        account.edit("https://pinboard.in/howto/", description="Old edit")
        synced.sync()
        later(clock)

        report = synced.sync(full=True)

        assert report.complete
        assert index.get("https://pinboard.in/howto/")["title"] == "Old edit"


class TestFullSync:
    def test_removes_strays_pinboard_never_had(self, account, index, clock):
        sync = syncer(account, index, clock)
        sync.sync()
        index.upsert([Post(url="https://stray/", saved_at="2001-01-01T00:00:00Z")])
        later(clock)

        sync.sync(full=True)

        assert index.get("https://stray/") is None
        assert index.count() == 23


class TestFailures:
    def test_a_rate_limit_backs_off_and_retries(self, account, index, clock):
        account.fail_all_with = [429, 429]

        report = syncer(account, index, clock).sync()

        assert report.complete
        assert clock.waits == [300, 600]
        assert index.count() == 23

    def test_it_gives_up_after_repeated_rate_limits(self, account, index, clock):
        account.fail_all_with = [429] * 10

        report = syncer(account, index, clock).sync()

        assert report.error == "Pinboard answered HTTP 429"
        assert account.count("all") == 4
        assert sync_status(index)["last_error"] == "Pinboard answered HTTP 429"

    def test_an_error_is_reported_without_the_token(self, account, index, clock):
        account.fail_all_with = [500]

        report = syncer(account, index, clock).sync()

        assert report.error == "Pinboard answered HTTP 500"
        assert not report.complete
        assert "SECRET" not in str(sync_status(index))

    def test_the_next_sync_recovers(self, account, index, clock):
        account.fail_all_with = [500]
        sync = syncer(account, index, clock)
        sync.sync()
        later(clock)

        report = sync.sync()

        assert report.complete and index.count() == 23
        assert sync_status(index)["last_error"] is None

    def test_an_auth_failure_on_the_first_call(self, index, clock):
        account = FakePinboard()

        def refuse():
            raise urllib.error.HTTPError(
                "https://api.pinboard.in/v1/posts/update?auth_token=u:SECRET",
                401,
                "Unauthorized",
                None,
                None,
            )

        account.posts.update = refuse

        report = syncer(account, index, clock).sync()

        assert report.error == "Pinboard answered HTTP 401"

    def test_safe_error_redacts_a_token_wherever_it_appears(self):
        error = urllib.error.URLError(
            "refused https://x/?auth_token=u:SECRET&format=json"
        )

        assert "SECRET" not in safe_error(error)
        assert "SECRET" not in safe_error(RuntimeError("bad auth_token=u:SECRET here"))


class TestImport:
    def test_an_export_file_seeds_the_index(self, index):
        delta = import_export(index, EXPORT)

        assert delta.added == 23
        assert index.search("", Filters(author="patrickc")).bookmarks

    def test_the_sync_after_an_import_fetches_only_what_differs(
        self, account, index, clock
    ):
        import_export(index, EXPORT)
        account.add(post("https://new/", time="2026-11-26T09:00:00Z"))

        report = syncer(account, index, clock).sync()

        assert report.delta.added == 1
        assert report.delta.unchanged <= 3
        _, params = next(call for call in account.calls if call[0] == "all")
        assert params["fromdt"] == "2026-11-25T00:00:00Z"

    def test_something_that_is_not_an_export_is_refused(self, index, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text('{"not": "a list"}')

        with pytest.raises(ValueError, match="export"):
            import_export(index, bad)


class TestWorker:
    def test_one_pass_syncs_then_embeds(self, account, index, clock):
        embedder = FakeEmbedder()
        worker = IndexWorker(syncer(account, index, clock), index, embedder)

        worker.run_once()

        assert index.count() == 23
        assert index.pending_embedding_count(embedder.model_id) == 0

    def test_an_embedding_failure_is_kept_for_the_status(self, account, index, clock):
        embedder = FakeEmbedder()
        embedder.embed_documents = lambda texts: (_ for _ in ()).throw(
            OSError("no model")
        )
        worker = IndexWorker(syncer(account, index, clock), index, embedder)

        worker.run_once()

        assert index.count() == 23
        assert "no model" in worker.embedding_error

    def test_runs_in_the_background_and_stops_when_told(self, account, index, clock):
        worker = IndexWorker(syncer(account, index, clock), index, interval=3600)
        worker.start()
        try:
            for _ in range(200):
                if sync_status(index)["complete"]:
                    break
                worker.join(0.05)
            assert index.count() == 23
            assert worker.daemon
        finally:
            worker.stop()
            worker.join(5)
        assert not worker.is_alive()

    def test_stopping_interrupts_a_wait_for_the_next_window(self, account, index):
        sync = PinboardSync(account, index, rate_limit=lambda: None, max_window=8)
        worker = IndexWorker(sync, index, interval=3600)
        worker.start()
        for _ in range(200):
            if account.count("all") >= 1:
                break
            worker.join(0.05)

        worker.stop()
        worker.join(5)

        assert not worker.is_alive()
        assert account.count("all") == 1

    def test_embedding_alone_without_a_syncer(self, index):
        import_export(index, EXPORT)
        embedder = FakeEmbedder()

        IndexWorker(None, index, embedder).run_once()

        assert embed_pending(index, embedder) == 0
        assert index.stats()["embedded"] == 23


class TestInterruptedFullPass:
    def test_a_full_pass_that_is_interrupted_carries_on(self, account, index, clock):
        sync = syncer(account, index, clock, max_window=8, wait_for_windows=False)
        while not sync_status(index)["complete"]:
            sync.sync()
            later(clock)
        account.edit("https://pinboard.in/howto/", description="Old edit")
        index.upsert([Post(url="https://stray/", saved_at="2001-01-01T00:00:00Z")])
        account.calls.clear()

        first = sync.sync(full=True)
        assert first.stopped and index.get_meta("full_sync_pending") == "1"
        for _ in range(20):
            later(clock)
            if sync.sync().complete:
                break

        assert index.get("https://pinboard.in/howto/")["title"] == "Old edit"
        assert index.get("https://stray/") is None
        assert index.get_meta("full_sync_pending") is None
        assert (
            index.get_meta("last_full_sync_at") == sync_status(index)["last_synced_at"]
        )
        windows = [params["fromdt"] for name, params in account.calls if name == "all"]
        assert len(windows) == len(set(windows))

    def test_a_count_that_disagrees_schedules_a_full_pass(self, account, index, clock):
        sync = syncer(account, index, clock, max_window=8)
        sync.sync()
        index.upsert([Post(url="https://stray/", saved_at="2001-01-01T00:00:00Z")])
        account.add(post("https://new/", time="2026-11-27T09:00:00Z"))
        later(clock)

        sync.sync()

        assert index.get_meta("needs_full_sync") == "1"
        clock.now += timedelta(days=8)
        account.add(post("https://newer/", time="2026-11-27T10:00:00Z"))
        sync.sync()
        assert index.get("https://stray/") is None
        assert index.get_meta("needs_full_sync") is None

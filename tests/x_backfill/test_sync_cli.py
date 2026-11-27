"""Planning, the Pinboard client, and the command line end to end."""

import functools
import json
import threading
import urllib.request

import httpx
import pytest

from pinboard_x_backfill import cli
from pinboard_x_backfill.pinboard_api import PinboardAPI, PinboardError
from pinboard_x_backfill.sync import (
    ADD,
    RETAG,
    SKIP,
    build_plan,
    execute_plan,
    index_by_tweet_id,
    load_existing,
    load_export,
)
from pinboard_x_backfill.tweets import BOOKMARK, LIKE, Tweet

from .conftest import FIXTURES, FakePinboard, load

TOKEN = "ross:ABC123"


def pinboard(fake, waits=None, log=None):
    http = httpx.Client(transport=httpx.MockTransport(fake.handler))
    clock = iter(range(0, 10**6, 10))
    return PinboardAPI(
        TOKEN,
        http,
        sleep=(waits if waits is not None else []).append,
        monotonic=lambda: next(clock),
        log=(log if log is not None else []).append,
    )


# -- Pinboard client -----------------------------------------------------------


def test_token_shape_is_checked():
    with pytest.raises(PinboardError, match="username:HEXTOKEN"):
        PinboardAPI("nonsense", httpx.Client())


def test_calls_are_spaced_three_seconds_apart(fake_pinboard):
    waits: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(fake_pinboard.handler))
    times = iter([100.0, 101.0, 103.0, 110.0, 110.0])
    api = PinboardAPI(TOKEN, http, sleep=waits.append, monotonic=lambda: next(times))
    api.last_update()
    api.last_update()  # 1 s after the first: waits the other 2
    api.last_update()  # 7 s after: no wait
    assert waits == [2.0]


def test_all_posts_pages_until_a_short_page():
    fake = FakePinboard([{"href": f"https://e.com/{n}"} for n in range(5)])
    api = pinboard(fake)
    assert len(list(api.all_posts(page_size=2))) == 5
    assert [params for _, params in fake.calls] == [
        {"start": "0", "results": "2"},
        {"start": "2", "results": "2"},
        {"start": "4", "results": "2"},
    ]


def test_backs_off_on_429_and_5xx_then_succeeds(fake_pinboard):
    waits, log = [], []
    api = pinboard(fake_pinboard, waits, log)
    fake_pinboard.fail_next = [429, 500, 429]
    assert api.last_update() == "2026-11-26T00:00:00Z"
    assert waits == [6.0, 12.0, 24.0]
    assert log and all(TOKEN not in line and "ABC123" not in line for line in log)


def test_gives_up_eventually_without_leaking_the_token(fake_pinboard):
    waits: list[float] = []
    api = pinboard(fake_pinboard, waits)
    fake_pinboard.fail_next = [429] * 8
    with pytest.raises(PinboardError) as caught:
        api.last_update()
    assert str(caught.value) == "Pinboard posts/update failed: HTTP 429"
    assert max(waits) == 320.0

    fake_pinboard.fail_next = [404]
    with pytest.raises(PinboardError, match="HTTP 404"):
        api.last_update()
    fake_pinboard.fail_next = [401]
    with pytest.raises(PinboardError, match="rejected the API token"):
        api.last_update()


def test_network_failure_and_bad_json_never_show_the_url():
    def offline(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    api = PinboardAPI(
        TOKEN,
        httpx.Client(transport=httpx.MockTransport(offline)),
        sleep=lambda _: None,
        log=lambda _: None,
    )
    with pytest.raises(PinboardError) as caught:
        api.last_update()
    assert str(caught.value) == "Pinboard posts/update failed: ConnectError"
    assert caught.value.__cause__ is None and caught.value.__context__ is None

    def garbage(request):
        return httpx.Response(200, text="<html>")

    api = PinboardAPI(TOKEN, httpx.Client(transport=httpx.MockTransport(garbage)))
    with pytest.raises(PinboardError, match="not JSON"):
        api.last_update()
    with pytest.raises(PinboardError):
        api.add({"url": "https://x.com/a/status/1", "description": "d"}, False)


def test_all_posts_rejects_a_non_list():
    def handler(request):
        return httpx.Response(200, json={"oops": 1})

    api = PinboardAPI(TOKEN, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PinboardError, match="did not return a list"):
        list(api.all_posts())
    with pytest.raises(PinboardError, match="no result code"):
        api.add({"url": "u", "description": "d"}, False)


# -- Planning ------------------------------------------------------------------


def snapshot(ids):
    return [
        Tweet(
            id=ids["collison"],
            kinds={LIKE},
            text="We built a feedback tool",
            author_handle="patrickc",
            author_name="Patrick Collison",
            created_at="2026-11-20T14:03:00Z",
            source="api",
        ),
        Tweet(
            id=ids["ifttt"],
            kinds={LIKE, BOOKMARK},
            text="Datasette 1.0 is out",
            author_handle="simonw",
            source="api",
        ),
        Tweet(id=ids["old"], kinds={LIKE}, text="An old favourite", source="archive"),
    ]


def test_plan_adds_new_tweets_and_skips_ones_saved_under_any_url(ids):
    existing = index_by_tweet_id(load_export(FIXTURES / "pinboard_export.json"))
    assert list(existing) == [ids["ifttt"]]
    plan = build_plan(snapshot(ids), existing)
    assert [(a.op, a.tweet.id) for a in plan] == [
        (ADD, ids["old"]),
        (SKIP, ids["ifttt"]),
        (ADD, ids["collison"]),
    ]
    old, _, collison = plan
    assert old.post == {
        "url": f"https://x.com/i/web/status/{ids['old']}",
        "description": "An old favourite",
        "extended": "An old favourite\n\nPosted: 2019-06-01T12:00:00Z",
        "tags": "via:twitter-like",
        "shared": "no",
        "toread": "no",
        "dt": "2019-06-01T12:00:00Z",
    }
    assert collison.post["url"] == f"https://x.com/patrickc/status/{ids['collison']}"
    assert collison.post["description"].startswith("Patrick Collison (@patrickc): ")
    assert json.dumps(plan[1].to_json())  # serialisable


def test_plan_options(ids):
    plan = build_plan(
        snapshot(ids),
        {},
        host="twitter.com",
        shared=True,
        extra_tags=["twitter"],
        kinds=[BOOKMARK],
    )
    (only,) = plan
    assert only.tweet.id == ids["ifttt"]
    assert only.post["url"].startswith("https://twitter.com/simonw/status/")
    assert only.post["tags"] == "via:twitter-like via:twitter-bookmark twitter"
    assert only.post["shared"] == "yes"


def test_tag_existing_resends_the_bookmark_untouched_but_for_tags(ids):
    existing = index_by_tweet_id(load("pinboard_export.json"))
    plan = build_plan(snapshot(ids), existing, tag_existing=True)
    retag = next(a for a in plan if a.op == RETAG)
    assert retag.post == {
        "url": f"https://twitter.com/simonw/status/{ids['ifttt']}?s=20&t=abcDEF",
        "description": "Simon Willison on Twitter",
        "extended": "My own note: read this before the 1.0 upgrade",
        "tags": "datasette sqlite via:twitter-like via:twitter-bookmark",
        "shared": "yes",
        "toread": "no",
        "dt": "2025-03-14T09:12:44Z",
    }
    # Once tagged there is nothing more to do.
    existing[ids["ifttt"]]["tags"] = retag.post["tags"]
    again = build_plan(snapshot(ids), existing, tag_existing=True)
    assert [a.op for a in again if a.tweet.id == ids["ifttt"]] == [SKIP]


def test_load_export_refuses_other_files(tmp_path):
    bad = tmp_path / "export.json"
    bad.write_text('{"not": "a list"}')
    with pytest.raises(PinboardError, match="not a Pinboard JSON export"):
        load_export(bad)


def test_existing_list_is_cached_until_pinboard_changes(fake_pinboard, tmp_path, ids):
    api = pinboard(fake_pinboard)
    cache = tmp_path / "pinboard-tweets.json"
    log: list[str] = []
    posts = load_existing(api, cache, log.append)
    assert [p["href"] for p in posts] == [
        f"https://twitter.com/simonw/status/{ids['ifttt']}?s=20&t=abcDEF"
    ]
    assert "3 bookmarks, 1 of them tweets" in log[-1]
    before = len(fake_pinboard.calls)
    assert load_existing(api, cache, log.append) == posts
    assert [m for m, _ in fake_pinboard.calls[before:]] == ["posts/update"]

    fake_pinboard.posts.insert(0, {"href": "https://x.com/a/status/55"})
    fake_pinboard._touch()
    assert len(load_existing(api, cache, log.append)) == 2


def test_execute_counts_and_stops_after_a_run_of_failures(fake_pinboard, ids):
    api = pinboard(fake_pinboard)
    plan = build_plan(snapshot(ids), {})
    assert execute_plan(plan, api, limit=1, log=lambda _: None).added == 1
    outcome = execute_plan(plan, api, log=lambda _: None)
    assert (outcome.added, outcome.already_there, outcome.failed) == (2, 1, 0)

    many = build_plan([Tweet(id=str(n), kinds={LIKE}) for n in range(1, 31)], {})
    fake_pinboard.fail_next = [404] * 5
    log: list[str] = []
    stopped = execute_plan(many, api, log=log.append)
    assert stopped.stopped_early and stopped.failed == 5 and stopped.added == 0
    assert "HTTP 404" in stopped.errors[0] and TOKEN not in stopped.errors[0]
    done = execute_plan(many, api, log=log.append)
    assert done.added == 30 and "  25 of 30 sent" in log


# -- Command line --------------------------------------------------------------


@pytest.fixture
def run(monkeypatch, tmp_path, fake_x, fake_pinboard, capsys):
    """Run the CLI against the fakes; returns (exit code, stdout, stderr)."""

    def route(request):
        if request.url.host == "api.pinboard.in":
            return fake_pinboard.handler(request)
        return fake_x.handler(request)

    monkeypatch.setattr(
        cli, "_http", lambda: httpx.Client(transport=httpx.MockTransport(route))
    )
    monkeypatch.setattr(
        cli, "PinboardAPI", functools.partial(PinboardAPI, min_interval=0.0)
    )
    monkeypatch.setenv("X_CLIENT_ID", "client-1")
    monkeypatch.delenv("X_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINBOARD_TOKEN", TOKEN)
    data_dir = tmp_path / "data"

    def invoke(*argv):
        code = cli.main(["--data-dir", str(data_dir), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    invoke.data_dir = data_dir
    return invoke


def sign_in(run, monkeypatch):
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "fixed-state")
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: "http://127.0.0.1:8739/callback?state=fixed-state&code=abc",
    )
    return run("auth", "--paste")


def test_full_backfill_is_idempotent(run, monkeypatch, fake_pinboard, fake_x, ids):
    code, out, _ = sign_in(run, monkeypatch)
    assert code == 0 and "Signed in as @rossshannon (id 999)." in out

    code, out, _ = run("import-archive", str(FIXTURES / "like.js"))
    assert code == 0 and "Archive holds 4 likes and 0 bookmarks: 4 new" in out

    code, out, _ = run("fetch")
    assert code == 0
    assert "likes: read 4 in 2 page(s), 4 new" in out
    assert "bookmarks: read 1 in 1 page(s), 1 new" in out
    assert "Snapshot now holds 6 tweets" in out

    # Dry run: a plan, and not one write.
    code, out, _ = run("sync")
    assert code == 0
    assert "Plan: 5 to add, 0 to tag, 1 already in Pinboard." in out
    assert "Dry run: nothing was sent to Pinboard" in out
    assert fake_pinboard.adds() == []
    assert len(fake_pinboard.posts) == 3

    code, out, _ = run("sync", "--write")
    assert code == 0 and "Saved 5, tagged 0, 0 were already there, 0 failed." in out
    adds = fake_pinboard.adds()
    assert len(adds) == 5 and all(a["replace"] == "no" for a in adds)
    assert all(a["shared"] == "no" for a in adds)
    saved = {p["href"]: p for p in fake_pinboard.posts}
    both = saved[f"https://x.com/simonw/status/{ids['both']}"]
    assert both["tags"] == "via:twitter-like via:twitter-bookmark"
    assert both["time"] == "2026-11-22T18:45:10Z"
    assert "https://sqlite.org/fts5.html" in both["extended"]
    assert f"https://x.com/i/web/status/{ids['gone']}" in saved  # archive-only
    # Ross's own bookmark of the Datasette tweet is exactly as it was.
    assert load("pinboard_export.json")[0] in fake_pinboard.posts

    # Second run: everything is found in Pinboard, nothing is sent.
    code, out, _ = run("sync", "--write")
    assert code == 0
    assert "Plan: 0 to add, 0 to tag, 6 already in Pinboard." in out
    assert len(fake_pinboard.adds()) == 5 and len(fake_pinboard.posts) == 8

    # Third run with tagging: only the pre-existing bookmark gains tags.
    code, out, _ = run("sync", "--write", "--tag-existing")
    assert "Saved 0, tagged 1" in out
    retagged = fake_pinboard.posts[-3]
    assert retagged["tags"] == "datasette sqlite via:twitter-like"
    assert retagged["extended"] == "My own note: read this before the 1.0 upgrade"
    assert retagged["description"] == "Simon Willison on Twitter"
    assert retagged["time"] == "2025-03-14T09:12:44Z" and retagged["shared"] == "yes"

    # A later fetch stops at the first page of tweets it has already seen.
    before = len(fake_x.requests)
    code, out, _ = run("fetch", "--only", "like")
    assert "reached tweets already fetched" in out
    assert len(fake_x.requests) - before == 1
    code, out, _ = run("fetch", "--only", "like", "--full", "--max-pages", "1")
    assert "stopped at --max-pages 1" in out

    code, out, _ = run("status")
    assert "Snapshot: 6 tweets" in out and "X sign-in: @rossshannon" in out
    assert "PINBOARD_TOKEN: set" in out and TOKEN not in out


def test_replace_no_protects_a_bookmark_the_index_missed(run, fake_pinboard, ids):
    """Even with a stale view of Pinboard, an existing URL is not overwritten."""
    run("import-archive", str(FIXTURES / "like.js"))
    url = f"https://x.com/i/web/status/{ids['old']}"
    mine = {
        "href": url,
        "description": "My title",
        "extended": "My notes",
        "tags": "mine",
        "time": "2020-01-01T00:00:00Z",
        "shared": "yes",
        "toread": "no",
    }
    real_handler = fake_pinboard.handler
    hidden = True

    def handler(request):
        # Hide the bookmark from posts/all, as if it were saved mid-run.
        nonlocal hidden
        if request.url.path.endswith("posts/all") and hidden:
            hidden = False
            fake_pinboard.posts.remove(mine)
            try:
                return real_handler(request)
            finally:
                fake_pinboard.posts.append(mine)
        return real_handler(request)

    fake_pinboard.posts.append(mine)
    fake_pinboard.handler = handler
    code, out, _ = run("sync", "--write", "--only", "like")
    assert code == 0 and "1 were already there" in out
    assert mine in fake_pinboard.posts


def test_dry_run_against_an_export_needs_no_token(
    run, monkeypatch, fake_pinboard, tmp_path
):
    monkeypatch.delenv("PINBOARD_TOKEN")
    run("import-archive", str(FIXTURES / "like.js"))
    plan_path = tmp_path / "plan.json"
    code, out, _ = run(
        "sync",
        "--pinboard-export",
        str(FIXTURES / "pinboard_export.json"),
        "--plan-out",
        str(plan_path),
        "--show",
        "1",
    )
    assert code == 0 and "Plan: 3 to add, 0 to tag, 1 already in Pinboard." in out
    assert out.count("ADD https://") == 1
    assert fake_pinboard.calls == []
    assert [a["op"] for a in json.loads(plan_path.read_text())].count("add") == 3


def test_usage_errors(run, monkeypatch):
    code, _, err = run("sync")
    assert code == 2 and "snapshot is empty" in err
    run("import-archive", str(FIXTURES / "like.js"))
    code, _, err = run("sync", "--write", "--pinboard-export", "x.json")
    assert code == 2 and "cannot be combined" in err
    monkeypatch.delenv("PINBOARD_TOKEN")
    code, _, err = run("sync")
    assert code == 2 and "set PINBOARD_TOKEN" in err
    code, _, err = run("fetch")
    assert code == 2 and "not signed in to X" in err
    monkeypatch.delenv("X_CLIENT_ID")
    code, _, err = run("fetch")
    assert code == 2 and "X_CLIENT_ID" in err
    code, _, err = run("import-archive", "/no/such/archive.zip")
    assert code == 2 and "no such file" in err
    code, _, err = run("auth", "--redirect-uri", "https://example.com/cb")
    assert code == 2 and "--redirect-uri" in err
    code, out, _ = run("status")
    assert "X sign-in: none" in out and "PINBOARD_TOKEN: not set" in out


def test_failed_saves_give_a_failing_exit_code(run, fake_pinboard):
    run("import-archive", str(FIXTURES / "like.js"))
    run("sync")  # downloads the list first so the failure hits posts/add
    real = fake_pinboard.handler

    def handler(request):
        if request.url.path.endswith("posts/add"):
            return httpx.Response(404, text="nope")
        return real(request)

    fake_pinboard.handler = handler
    code, out, _ = run("sync", "--write", "--limit", "1")
    assert (
        code == 1
        and "0 were already there, 1 failed" in out
        and "failed: https://" in out
    )


def test_damaged_snapshot_is_left_alone(run):
    run.data_dir.mkdir(parents=True)
    path = run.data_dir / "tweets.json"
    path.write_text("{ truncated")
    code, _, err = run("status")
    assert code == 2 and "damaged" in err
    assert path.read_text() == "{ truncated"


def test_enrich_command(run, monkeypatch):
    run("import-archive", str(FIXTURES / "like.js"))
    seen = {}

    def fake_enrich(http, tweets, *, skip, limit, delay):
        tweets = list(tweets)
        seen.update(order=[t.id for t in tweets], skip=set(skip), limit=limit)
        tweets[0].author_handle = "found"
        return {"found": [tweets[0].id], "missing": [tweets[1].id], "failed": []}

    monkeypatch.setattr(cli.oembed, "enrich", fake_enrich)
    code, out, _ = run("enrich", "--limit", "2")
    assert code == 0 and "Authors found for 1; 1 deleted or protected" in out
    assert "2 tweets still have no author" in out
    assert seen["order"] == sorted(seen["order"], reverse=True) and seen["limit"] == 2
    run("enrich")
    assert seen["skip"] == {seen["order"][0]} or len(seen["skip"]) == 1


def test_keyboard_interrupt_is_reported_calmly(run, monkeypatch):
    def interrupted(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_status", interrupted)
    parser = cli.build_parser
    monkeypatch.setattr(
        cli,
        "build_parser",
        lambda: _with_run(parser(), interrupted),
    )
    code, _, err = run("status")
    assert code == 130 and "safe to run the same command again" in err


def _with_run(parser, function):
    original = parser.parse_args

    def parse(argv=None):
        args = original(argv)
        args.run = function
        return args

    parser.parse_args = parse
    return parser


def test_loopback_redirect_listener():
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result: list[str] = []
    thread = threading.Thread(
        target=lambda: result.append(
            cli.wait_for_redirect("127.0.0.1", port, "/callback")
        )
    )
    thread.start()
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(50):
        try:
            opener.open(f"{base}/favicon.ico", timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 404
            break
        except OSError:
            threading.Event().wait(0.05)
    body = opener.open(f"{base}/callback?state=s&code=c", timeout=2).read()
    thread.join(timeout=5)
    assert b"Signed in" in body
    assert result == [f"{base}/callback?state=s&code=c"]


def test_auth_with_the_listener(run, monkeypatch):
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "fixed-state")
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        cli,
        "wait_for_redirect",
        lambda host, port, path: (
            f"http://{host}:{port}{path}?state=fixed-state&code=abc"
        ),
    )
    code, out, _ = run("auth")
    assert code == 0 and "Signed in as @rossshannon" in out
    assert opened and opened[0].startswith("https://x.com/i/oauth2/authorize?")

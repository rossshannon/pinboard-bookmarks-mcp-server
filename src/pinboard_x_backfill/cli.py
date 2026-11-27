"""Command line for the X to Pinboard backfill. See docs/x-backfill.md."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import webbrowser
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from pinboard_x_backfill import __version__, oembed
from pinboard_x_backfill.archive import ArchiveError, read_archive
from pinboard_x_backfill.pinboard_api import PinboardAPI, PinboardError
from pinboard_x_backfill.store import (
    TweetStore,
    default_data_dir,
    read_json,
    write_private_json,
)
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
from pinboard_x_backfill.tweets import BOOKMARK, KINDS, LIKE
from pinboard_x_backfill.xapi import (
    DEFAULT_REDIRECT_URI,
    TokenStore,
    XAuth,
    XClient,
    XError,
    authorize_url,
    code_from_redirect,
    new_pkce_pair,
)

USER_AGENT = f"pinboard-x-backfill/{__version__}"


class UsageError(Exception):
    """Something the person running the tool needs to fix."""


def _http() -> httpx.Client:
    return httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT})


def _store(args: argparse.Namespace) -> TweetStore:
    path = args.data_dir / "tweets.json"
    try:
        return TweetStore(path)
    except ValueError:
        raise UsageError(
            f"{path} is damaged and cannot be read. Move it aside to start again; "
            "it has not been changed."
        ) from None


def _x_auth(args: argparse.Namespace, http: httpx.Client) -> XAuth:
    client_id = os.environ.get("X_CLIENT_ID", "").strip()
    if not client_id:
        raise UsageError("set X_CLIENT_ID to your X app's OAuth 2.0 client id")
    secret = os.environ.get("X_CLIENT_SECRET", "").strip() or None
    return XAuth(http, TokenStore(args.data_dir / "x-token.json"), client_id, secret)


def _kinds(args: argparse.Namespace) -> list[str]:
    return [args.only] if args.only else list(KINDS)


# -- auth ----------------------------------------------------------------------


def wait_for_redirect(host: str, port: int, path: str) -> str:
    """Serve one request on the loopback redirect URI; return its full URL."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
            if urlsplit(self.path).path != path:
                self.send_response(404)
                self.end_headers()
                return
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")

        def log_message(self, format: str, *values: object) -> None:
            pass  # the request line holds the authorisation code

    with HTTPServer((host, port), Handler) as server:
        while not seen:
            server.handle_request()
    return f"http://{host}:{port}{seen[0]}"


def cmd_auth(args: argparse.Namespace) -> int:
    redirect = urlsplit(args.redirect_uri)
    if redirect.hostname not in ("127.0.0.1", "localhost") or not redirect.port:
        raise UsageError("--redirect-uri must be http://127.0.0.1:<port>/<path>")
    with _http() as http:
        auth = _x_auth(args, http)
        verifier, challenge = new_pkce_pair()
        state = secrets.token_urlsafe(24)
        url = authorize_url(auth.client_id, args.redirect_uri, state, challenge)
        print("Open this address and approve access (read-only scopes):\n")
        print(f"  {url}\n")
        if args.paste:
            redirected = input("Paste the address you were sent to: ")
        else:
            webbrowser.open(url)
            print(f"Waiting for X to send you back to {args.redirect_uri} …")
            redirected = wait_for_redirect(
                redirect.hostname, redirect.port, redirect.path or "/"
            )
        code = code_from_redirect(redirected, state)
        auth.exchange_code(code, verifier, args.redirect_uri)
        user = XClient(http, auth).me()
    print(f"Signed in as @{user['username']} (id {user['id']}).")
    return 0


# -- fetch ---------------------------------------------------------------------


def cmd_fetch(args: argparse.Namespace) -> int:
    store = _store(args)
    with _http() as http:
        auth = _x_auth(args, http)
        client = XClient(http, auth)
        saved = (auth.store.load() or {}).get("user") or {}
        user_id = saved.get("id") or client.me()["id"]
        for kind in _kinds(args):
            pages = read = new_total = 0
            for tweets in client.pages(kind, user_id):
                pages += 1
                read += len(tweets)
                unseen = sum(1 for t in tweets if not store.fetched(t.id, kind))
                store.merge(tweets)
                store.save()
                new_total += unseen
                if tweets and not unseen and not args.full:
                    print(f"  {kind}s: reached tweets already fetched; stopping")
                    break
                if args.max_pages and pages >= args.max_pages:
                    print(f"  {kind}s: stopped at --max-pages {args.max_pages}")
                    break
            print(f"{kind}s: read {read} in {pages} page(s), {new_total} new")
    print(f"Snapshot now holds {len(store)} tweets: {store.path}")
    return 0


# -- import-archive ------------------------------------------------------------


def cmd_import_archive(args: argparse.Namespace) -> int:
    store = _store(args)
    tweets = read_archive(args.path)
    counts = Counter(kind for tweet in tweets for kind in tweet.kinds)
    new, changed = store.merge(tweets)
    store.save()
    print(
        f"Archive holds {counts[LIKE]} likes and {counts[BOOKMARK]} bookmarks: "
        f"{new} new, {changed} updated."
    )
    print(f"Snapshot now holds {len(store)} tweets: {store.path}")
    return 0


# -- enrich --------------------------------------------------------------------


def cmd_enrich(args: argparse.Namespace) -> int:
    store = _store(args)
    missed_path = args.data_dir / "enrich-missing.json"
    missed = read_json(missed_path)
    skip = set(missed) if isinstance(missed, list) else set()
    newest_first = sorted(
        store.tweets.values(), key=lambda t: (len(t.id), t.id), reverse=True
    )
    with _http() as http:
        results = oembed.enrich(
            http, newest_first, skip=skip, limit=args.limit, delay=args.delay
        )
    store.save()
    write_private_json(missed_path, sorted(skip | set(results[oembed.MISSING])))
    print(
        f"Authors found for {len(results[oembed.FOUND])}; "
        f"{len(results[oembed.MISSING])} deleted or protected; "
        f"{len(results[oembed.FAILED])} failed (will be retried next time)."
    )
    left = sum(
        1
        for t in store.tweets.values()
        if not t.author_handle
        and t.id not in skip
        and t.id not in results[oembed.MISSING]
    )
    print(f"{left} tweets still have no author looked up.")
    return 0


# -- sync ----------------------------------------------------------------------


def cmd_sync(args: argparse.Namespace) -> int:
    store = _store(args)
    if not len(store):
        raise UsageError("the snapshot is empty; run `fetch` or `import-archive` first")
    if args.write and args.pinboard_export:
        raise UsageError(
            "--write needs the live bookmark list, so it cannot be combined "
            "with --pinboard-export"
        )
    token = os.environ.get("PINBOARD_TOKEN", "").strip()
    with _http() as http:
        api = None
        if args.pinboard_export:
            existing_posts = load_export(args.pinboard_export)
            print(
                f"Comparing against {args.pinboard_export} "
                f"({len(existing_posts)} tweet bookmarks)"
            )
        elif token:
            api = PinboardAPI(token, http)
            existing_posts = load_existing(api, args.data_dir / "pinboard-tweets.json")
        else:
            raise UsageError(
                "set PINBOARD_TOKEN (username:HEXTOKEN, from "
                "pinboard.in/settings/password), or for an offline dry run pass "
                "--pinboard-export with a JSON export from pinboard.in/export/"
            )

        plan = build_plan(
            store.tweets.values(),
            index_by_tweet_id(existing_posts),
            host=args.url_host,
            shared=args.shared,
            extra_tags=args.tag,
            tag_existing=args.tag_existing,
            kinds=_kinds(args),
        )
        counts = Counter(action.op for action in plan)
        print(
            f"Plan: {counts[ADD]} to add, {counts[RETAG]} to tag, "
            f"{counts[SKIP]} already in Pinboard."
        )
        if args.plan_out:
            write_private_json(args.plan_out, [action.to_json() for action in plan])
            print(f"Full plan written to {args.plan_out}")
        for action in [a for a in plan if a.op != SKIP][: args.show]:
            print(f"\n  {action.op.upper()} {action.post['url']}")
            print(f"    title: {action.post['description']}")
            print(f"    tags:  {action.post['tags']}")
            if action.post.get("dt"):
                print(f"    date:  {action.post['dt']}")

        if not args.write:
            print("\nDry run: nothing was sent to Pinboard. Add --write to save.")
            return 0
        assert api is not None
        outcome = execute_plan(plan, api, limit=args.limit)
    print(
        f"\nSaved {outcome.added}, tagged {outcome.retagged}, "
        f"{outcome.already_there} were already there, {outcome.failed} failed."
    )
    for line in outcome.errors[:20]:
        print(f"  failed: {line}")
    return 1 if outcome.failed else 0


# -- status --------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    store = _store(args)
    tweets = list(store.tweets.values())
    print(f"Data folder: {args.data_dir}")
    print(f"Snapshot: {len(tweets)} tweets")
    for kind in KINDS:
        print(f"  {kind}s: {sum(1 for t in tweets if kind in t.kinds)}")
    print(f"  without an author: {sum(1 for t in tweets if not t.author_handle)}")
    tokens = TokenStore(args.data_dir / "x-token.json").load()
    user = (tokens or {}).get("user") or {}
    print(
        f"X sign-in: @{user.get('username')}"
        if user
        else "X sign-in: none (run `auth`)"
    )
    print(f"PINBOARD_TOKEN: {'set' if os.environ.get('PINBOARD_TOKEN') else 'not set'}")
    return 0


# -- entry point ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pinboard-x-backfill",
        description="Save your X likes and bookmarks into Pinboard, idempotently.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir(),
        help="where the snapshot, cache and X sign-in live (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def only(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--only", choices=KINDS, help="just likes or just bookmarks")

    auth = commands.add_parser("auth", help="sign in to X (OAuth 2.0 with PKCE)")
    auth.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    auth.add_argument(
        "--paste",
        action="store_true",
        help="paste the redirected address instead of listening for it",
    )
    auth.set_defaults(run=cmd_auth)

    fetch = commands.add_parser(
        "fetch", help="read likes and bookmarks from the X API into the snapshot"
    )
    only(fetch)
    fetch.add_argument(
        "--full",
        action="store_true",
        help="page all the way back instead of stopping at tweets already fetched",
    )
    fetch.add_argument(
        "--max-pages", type=int, default=0, help="stop after this many pages of 100"
    )
    fetch.set_defaults(run=cmd_fetch)

    archive = commands.add_parser(
        "import-archive", help="read likes from an X data archive into the snapshot"
    )
    archive.add_argument(
        "path", type=Path, help="the archive zip, its folder, or like.js"
    )
    archive.set_defaults(run=cmd_import_archive)

    enrich = commands.add_parser(
        "enrich", help="look up authors for archive likes (free, public oEmbed)"
    )
    enrich.add_argument("--limit", type=int, help="look up at most this many")
    enrich.add_argument(
        "--delay", type=float, default=1.0, help="seconds between lookups"
    )
    enrich.set_defaults(run=cmd_enrich)

    sync = commands.add_parser(
        "sync", help="plan the saves to Pinboard; a dry run unless --write"
    )
    only(sync)
    sync.add_argument("--write", action="store_true", help="actually save to Pinboard")
    sync.add_argument("--limit", type=int, help="with --write, send at most this many")
    sync.add_argument(
        "--pinboard-export",
        type=Path,
        help="compare against a Pinboard JSON export instead of the API (dry run only)",
    )
    sync.add_argument(
        "--tag-existing",
        action="store_true",
        help="add a missing via:twitter-… tag to tweets already in Pinboard "
        "(rewrites those bookmarks with their own title, notes, tags and date)",
    )
    sync.add_argument(
        "--tag", action="append", default=[], help="extra tag for new bookmarks"
    )
    sync.add_argument(
        "--shared", action="store_true", help="save as public (default: private)"
    )
    sync.add_argument("--url-host", choices=("x.com", "twitter.com"), default="x.com")
    sync.add_argument("--show", type=int, default=5, help="sample actions to print")
    sync.add_argument("--plan-out", type=Path, help="write the full plan as JSON")
    sync.set_defaults(run=cmd_sync)

    status = commands.add_parser("status", help="show what is in the data folder")
    status.set_defaults(run=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    # httpx logs full request URLs at INFO, and Pinboard's carry the API token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    try:
        return int(args.run(args))
    except (UsageError, XError, PinboardError, ArchiveError) as error:
        print(f"pinboard-x-backfill: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped. It is safe to run the same command again.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

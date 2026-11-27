"""Work out what to save to Pinboard, and save it without clobbering.

Idempotency rests on Pinboard itself:

1. Before planning, every existing bookmark whose URL is a tweet is indexed
   by tweet id, whatever spelling of the URL it was saved under
   (twitter.com or x.com, with or without a handle, with tracking
   parameters). A tweet already present is skipped.
2. Every new save is sent with ``replace=no``, so even if step 1 missed
   something Pinboard refuses to overwrite a bookmark with the same URL.

Existing bookmarks are never rewritten, with one opt-in exception
(``tag_existing``): adding a missing ``via:twitter-…`` tag. That re-reads the
bookmark from Pinboard immediately beforehand and resends its own title,
notes, tags, time and flags.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from pinboard_x_backfill.pinboard_api import PinboardAPI, PinboardError
from pinboard_x_backfill.store import read_json, write_private_json
from pinboard_x_backfill.tweets import (
    KINDS,
    TAG_FOR_KIND,
    Tweet,
    bookmark_notes,
    bookmark_tags,
    bookmark_title,
    tweet_id_from_url,
)

ADD = "add"
RETAG = "retag"
SKIP = "skip"

_MAX_CONSECUTIVE_FAILURES = 5
_SAVE_EVERY = 25
# Pinboard's API is GET-only; stay under the 8 KB request line servers allow.
_MAX_ENCODED_REQUEST = 7000


def is_tweet(post: dict[str, Any]) -> bool:
    return tweet_id_from_url(str(post.get("href") or "")) is not None


def index_by_tweet_id(posts: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Existing tweet bookmarks keyed by tweet id (first one wins)."""
    index: dict[str, dict[str, Any]] = {}
    for post in posts:
        tweet_id = tweet_id_from_url(str(post.get("href") or ""))
        if tweet_id:
            index.setdefault(tweet_id, post)
    return index


def load_export(path: Path) -> list[dict[str, Any]]:
    """Tweet bookmarks from a Pinboard JSON export (pinboard.in/export/)."""
    data = read_json(path)
    if not isinstance(data, list):
        raise PinboardError(f"{path} is not a Pinboard JSON export")
    return [post for post in data if isinstance(post, dict) and is_tweet(post)]


def _a_day_before(moment: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    earlier = parsed.astimezone(timezone.utc) - timedelta(days=1)
    return earlier.strftime("%Y-%m-%dT%H:%M:%SZ")


class Mirror:
    """A local copy of the account's tweet bookmarks.

    Pinboard allows ``posts/all`` once every five minutes, so the whole list
    is downloaded only once. After that the copy is kept current two ways:
    this tool records its own saves in it, and each run asks Pinboard only
    for bookmarks dated since the day before the copy was last brought up to
    date. A bookmark deleted on Pinboard stays in the copy, so a tweet you
    removed is not saved again; ``refresh(full=True)`` starts over.
    """

    def __init__(self, path: Path):
        self.path = path
        self.update_time = ""
        self.posts: dict[str, dict[str, Any]] = {}
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("posts"), list):
            self.update_time = str(data.get("update_time") or "")
            for post in data["posts"]:
                if isinstance(post, dict) and post.get("href"):
                    self.posts[str(post["href"])] = post

    def save(self) -> None:
        write_private_json(
            self.path,
            {"update_time": self.update_time, "posts": list(self.posts.values())},
        )

    def record(self, post: dict[str, Any]) -> None:
        self.posts[str(post["href"])] = post

    def refresh(
        self, api: PinboardAPI, full: bool = False, log: Callable[[str], None] = print
    ) -> None:
        current = api.last_update()
        if not full and self.update_time and current == self.update_time:
            log(f"Pinboard unchanged since {current}; using the local copy")
            return
        since = None if full else _a_day_before(self.update_time)
        if since:
            log(f"Asking Pinboard for bookmarks dated since {since}…")
        else:
            log(
                "Downloading the whole bookmark list from Pinboard. Pinboard "
                "allows one page every five minutes, so a large account takes "
                "a while; this happens once."
            )
            self.posts = {}
        total = 0
        for post in api.all_posts(fromdt=since):
            total += 1
            if is_tweet(post):
                self.record(post)
        log(f"Read {total} bookmarks; the local copy holds {len(self.posts)} tweets")
        # ``current`` was read before the download, so anything saved during
        # it falls inside the next run's one-day overlap.
        if current:
            self.update_time = current
        self.save()


@dataclass
class Action:
    op: str
    tweet: Tweet
    reason: str = ""
    post: dict[str, str] = field(default_factory=dict)
    add_tags: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "tweet_id": self.tweet.id,
            "reason": self.reason,
            "post": self.post,
        }


def _existing_tags(post: dict[str, Any]) -> list[str]:
    tags = post.get("tags") or ""
    return tags.split() if isinstance(tags, str) else [str(tag) for tag in tags]


def new_post(
    tweet: Tweet, host: str, shared: bool, extra_tags: list[str]
) -> dict[str, str]:
    """The ``posts/add`` parameters for a tweet not yet in Pinboard."""
    post = {
        "url": tweet.url(host),
        "description": bookmark_title(tweet),
        "extended": bookmark_notes(tweet),
        "tags": " ".join(bookmark_tags(tweet, extra_tags)),
        "shared": "yes" if shared else "no",
        "toread": "no",
    }
    if tweet.posted_at:
        post["dt"] = tweet.posted_at
    return post


def retag_post(existing: dict[str, Any], missing: list[str]) -> dict[str, str]:
    """An existing bookmark, resent unchanged apart from extra tags."""
    post = {
        "url": str(existing["href"]),
        "description": str(existing.get("description") or ""),
        "extended": str(existing.get("extended") or ""),
        "tags": " ".join(_existing_tags(existing) + missing),
        "shared": str(existing.get("shared") or "no"),
        "toread": str(existing.get("toread") or "no"),
    }
    if existing.get("time"):
        post["dt"] = str(existing["time"])
    return post


def as_pinboard_post(params: dict[str, str]) -> dict[str, Any]:
    """``posts/add`` parameters in the shape ``posts/all`` returns."""
    return {
        "href": params["url"],
        "description": params.get("description", ""),
        "extended": params.get("extended", ""),
        "tags": params.get("tags", ""),
        "time": params.get("dt", ""),
        "shared": params.get("shared", ""),
        "toread": params.get("toread", ""),
    }


def _missing_tags(tweet: Tweet, post: dict[str, Any]) -> list[str]:
    have = set(_existing_tags(post))
    return [
        TAG_FOR_KIND[kind]
        for kind in KINDS
        if kind in tweet.kinds and TAG_FOR_KIND[kind] not in have
    ]


def build_plan(
    tweets: Iterable[Tweet],
    existing: dict[str, dict[str, Any]],
    *,
    host: str = "x.com",
    shared: bool = False,
    extra_tags: list[str] | None = None,
    tag_existing: bool = False,
    kinds: Iterable[str] = KINDS,
) -> list[Action]:
    """One action per tweet, oldest tweet first."""
    wanted = set(kinds)
    plan = []
    for tweet in sorted(tweets, key=lambda t: (len(t.id), t.id)):
        if not tweet.kinds & wanted:
            continue
        found = existing.get(tweet.id)
        if found is None:
            post = new_post(tweet, host, shared, extra_tags or [])
            plan.append(Action(ADD, tweet, "not in Pinboard", post))
            continue
        missing = _missing_tags(tweet, found)
        if tag_existing and missing and found.get("description"):
            plan.append(
                Action(
                    RETAG,
                    tweet,
                    f"already saved as {found.get('href')}; adding "
                    + " ".join(missing),
                    retag_post(found, missing),
                    missing,
                )
            )
        else:
            plan.append(Action(SKIP, tweet, f"already saved as {found.get('href')}"))
    return plan


@dataclass
class Outcome:
    added: int = 0
    retagged: int = 0
    already_there: int = 0
    failed: int = 0
    stopped_early: bool = False
    errors: list[str] = field(default_factory=list)


def _send(action: Action, api: PinboardAPI) -> tuple[str, dict[str, str] | None]:
    """Send one action. Returns Pinboard's result code and what was saved."""
    post = action.post
    if action.op == RETAG:
        # Work from the bookmark as it is right now, not as it was when the
        # local copy was made: anything edited since must survive.
        fresh = api.get(post["url"])
        if fresh is None:
            return "error: no longer in Pinboard under that address", None
        missing = _missing_tags(action.tweet, fresh)
        if not missing:
            return "item already exists", as_pinboard_post(retag_post(fresh, []))
        if not fresh.get("description"):
            return "error: the bookmark has no title to resend", None
        post = retag_post(fresh, missing)
    if len(urlencode(post)) > _MAX_ENCODED_REQUEST:
        return "error: too long to send in one request; left untouched", None
    return api.add(post, replace=action.op == RETAG), post


def execute_plan(
    plan: list[Action],
    api: PinboardAPI,
    mirror: Mirror | None = None,
    *,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> Outcome:
    """Send the plan's adds and retags to Pinboard, in order.

    ``limit`` caps how many writes are attempted. A run of failures stops
    the whole thing rather than hammering Pinboard. Each save is recorded in
    the mirror, which is written to disk as the run goes.
    """
    outcome = Outcome()
    writes = [action for action in plan if action.op in (ADD, RETAG)]
    if limit is not None:
        writes = writes[:limit]
    streak = 0
    try:
        for number, action in enumerate(writes, start=1):
            sent: dict[str, str] | None = None
            try:
                code, sent = _send(action, api)
            except PinboardError as error:
                code = f"error: {error}"
            if code == "done":
                streak = 0
                if action.op == ADD:
                    outcome.added += 1
                else:
                    outcome.retagged += 1
            elif code == "item already exists":
                streak = 0
                outcome.already_there += 1
            else:
                streak += 1
                outcome.failed += 1
                outcome.errors.append(f"{action.post.get('url')}: {code}")
            if mirror is not None and sent is not None:
                if code == "done":
                    mirror.record(as_pinboard_post(sent))
                elif code == "item already exists" and sent["url"] not in mirror.posts:
                    # Pinboard has it though the local copy did not: note the
                    # address so the next plan skips it.
                    mirror.record({"href": sent["url"]})
            if streak >= _MAX_CONSECUTIVE_FAILURES:
                outcome.stopped_early = True
                log(f"Stopping: {streak} saves failed in a row")
                break
            if number % _SAVE_EVERY == 0:
                log(f"  {number} of {len(writes)} sent")
                if mirror is not None:
                    mirror.save()
    finally:
        if mirror is not None:
            mirror.save()
    return outcome

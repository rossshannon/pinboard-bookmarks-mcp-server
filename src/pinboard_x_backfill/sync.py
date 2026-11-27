"""Work out what to save to Pinboard, and save it without clobbering.

Idempotency rests on Pinboard itself, not on local state:

1. Before planning, every existing bookmark whose URL is a tweet is indexed
   by tweet id, whatever spelling of the URL it was saved under
   (twitter.com or x.com, with or without a handle, with tracking
   parameters). A tweet already present is skipped.
2. Every new save is sent with ``replace=no``, so even if step 1 missed
   something Pinboard refuses to overwrite a bookmark with the same URL.

Existing bookmarks are never rewritten, with one opt-in exception
(``tag_existing``): adding a missing ``via:twitter-…`` tag, which resends the
bookmark's own title, notes, tags, time and flags exactly as Pinboard
returned them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


def tweet_posts(posts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The bookmarks, out of any list of Pinboard posts, that are tweets."""
    return [post for post in posts if tweet_id_from_url(str(post.get("href") or ""))]


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
    return tweet_posts(post for post in data if isinstance(post, dict))


def load_existing(
    api: PinboardAPI, cache_path: Path, log: Callable[[str], None] = print
) -> list[dict[str, Any]]:
    """Existing tweet bookmarks from Pinboard, via a local cache.

    The cache is trusted only while Pinboard's ``posts/update`` time is the
    one it was saved under, so any change to the account, including this
    tool's own saves, causes a fresh download next time.
    """
    update_time = api.last_update()
    cached = read_json(cache_path)
    if (
        isinstance(cached, dict)
        and update_time
        and cached.get("update_time") == update_time
        and isinstance(cached.get("posts"), list)
    ):
        log(f"Pinboard unchanged since {update_time}; using the local copy")
        return cached["posts"]
    log("Downloading the bookmark list from Pinboard (a page at a time)…")
    total = 0
    posts = []
    for post in api.all_posts():
        total += 1
        if tweet_id_from_url(str(post.get("href") or "")):
            posts.append(post)
    log(f"Pinboard has {total} bookmarks, {len(posts)} of them tweets")
    if update_time and api.last_update() == update_time:
        write_private_json(cache_path, {"update_time": update_time, "posts": posts})
    return posts


@dataclass
class Action:
    op: str
    tweet: Tweet
    reason: str = ""
    post: dict[str, str] = field(default_factory=dict)

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
        have = set(_existing_tags(found))
        missing = [
            TAG_FOR_KIND[kind]
            for kind in KINDS
            if kind in tweet.kinds and TAG_FOR_KIND[kind] not in have
        ]
        if tag_existing and missing and found.get("description"):
            plan.append(
                Action(
                    RETAG,
                    tweet,
                    f"already saved as {found.get('href')}; adding "
                    + " ".join(missing),
                    retag_post(found, missing),
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


def execute_plan(
    plan: list[Action],
    api: PinboardAPI,
    *,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> Outcome:
    """Send the plan's adds and retags to Pinboard, in order.

    ``limit`` caps how many writes are attempted. A run of failures stops
    the whole thing rather than hammering Pinboard.
    """
    outcome = Outcome()
    writes = [action for action in plan if action.op in (ADD, RETAG)]
    if limit is not None:
        writes = writes[:limit]
    streak = 0
    for number, action in enumerate(writes, start=1):
        try:
            code = api.add(action.post, replace=action.op == RETAG)
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
            if streak >= _MAX_CONSECUTIVE_FAILURES:
                outcome.stopped_early = True
                log(f"Stopping: {streak} saves failed in a row")
                break
        if number % 25 == 0:
            log(f"  {number} of {len(writes)} sent")
    return outcome

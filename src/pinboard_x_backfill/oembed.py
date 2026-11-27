"""Fill in authors for archive likes using X's public oEmbed endpoint.

The data archive gives a like's id and text but not who wrote it. X's oEmbed
endpoint (publish.twitter.com/oembed) needs no key and costs nothing, and
answers with the author's name and profile URL for any public tweet. A deleted
tweet answers 404 and stays authorless.

Experimental: written from the endpoint's documented behaviour and not yet
run against the live service.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import urlsplit

import httpx

from pinboard_x_backfill.tweets import Tweet, clean_handle

OEMBED_URL = "https://publish.twitter.com/oembed"
FOUND = "found"
MISSING = "missing"
FAILED = "failed"
MAX_FAILURES_IN_A_ROW = 10


def lookup_author(http: httpx.Client, tweet: Tweet) -> str:
    """Set the tweet's author from oEmbed. Returns FOUND, MISSING or FAILED."""
    params = {
        # oEmbed wants a handle in the URL but accepts any; it reports the
        # real author in its answer.
        "url": f"https://twitter.com/x/status/{tweet.id}",
        "omit_script": "1",
        "dnt": "true",
    }
    try:
        response = http.get(OEMBED_URL, params=params)
    except httpx.HTTPError:
        return FAILED
    if response.status_code == 404:
        return MISSING
    # A 403 may be a protected tweet, or X refusing this client altogether;
    # only a 404 is taken as a lasting answer.
    if response.status_code != 200:
        return FAILED
    try:
        data = response.json()
    except ValueError:
        return FAILED
    if not isinstance(data, dict):
        return FAILED
    path = urlsplit(str(data.get("author_url") or "")).path
    handle = clean_handle(path.strip("/").split("/")[0])
    if not handle:
        return FAILED
    tweet.author_handle = handle
    tweet.author_name = str(data.get("author_name") or "").strip()
    return FOUND


def enrich(
    http: httpx.Client,
    tweets: Iterable[Tweet],
    *,
    skip: set[str],
    limit: int | None = None,
    delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[tuple[Tweet, str]]:
    """Look up authors for tweets that lack one, yielding each result.

    ``skip`` holds ids already known to be gone. Stops after ten failures in
    a row (rate limiting, a block, or no network). Results are yielded one
    at a time so the caller can save as it goes.
    """
    streak = 0
    done = 0
    for tweet in tweets:
        if tweet.author_handle or tweet.id in skip:
            continue
        if limit is not None and done >= limit:
            return
        if done:
            sleep(delay)
        done += 1
        result = lookup_author(http, tweet)
        yield tweet, result
        streak = streak + 1 if result == FAILED else 0
        if streak >= MAX_FAILURES_IN_A_ROW:
            return

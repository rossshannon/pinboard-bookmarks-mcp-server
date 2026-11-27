"""The few Pinboard API calls the backfill needs, with careful error text.

Pinboard takes the API token in the query string, so nothing here ever puts a
URL or an httpx exception message into an error or a log line.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

API_BASE = "https://api.pinboard.in/v1"
MIN_INTERVAL = 3.0  # Pinboard asks for at least 3 seconds between calls
_BACKOFF_CAP = 320.0
_ATTEMPTS = 8


class PinboardError(Exception):
    """A Pinboard API failure, safe to show (never holds the token)."""


class PinboardAPI:
    def __init__(
        self,
        token: str,
        http: httpx.Client,
        min_interval: float = MIN_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ):
        if ":" not in token:
            raise PinboardError("PINBOARD_TOKEN should look like username:HEXTOKEN")
        self._token = token
        self.http = http
        self.min_interval = min_interval
        self.sleep = sleep
        self.monotonic = monotonic
        self.log = log
        self._last_call: float | None = None

    def _pace(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval - (self.monotonic() - self._last_call)
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.monotonic()

    def _call(self, method: str, params: dict[str, str] | None = None) -> Any:
        query = {**(params or {}), "auth_token": self._token, "format": "json"}
        # posts/all is limited to one call every five minutes, far tighter
        # than the three seconds that applies elsewhere.
        backoff = 60.0 if method == "posts/all" else self.min_interval * 2
        failure = "no response"
        for attempt in range(_ATTEMPTS):
            self._pace()
            try:
                response = self.http.get(f"{API_BASE}/{method}", params=query)
            except httpx.InvalidURL:
                raise PinboardError(
                    f"Pinboard {method}: the request is too long to send"
                ) from None
            except httpx.HTTPError as error:
                failure = type(error).__name__
            else:
                status = response.status_code
                if status == 200:
                    try:
                        return json.loads(response.text, strict=False)
                    except ValueError:
                        raise PinboardError(
                            f"Pinboard {method} returned something that is not JSON"
                        ) from None
                if status in (401, 403):
                    raise PinboardError(
                        f"Pinboard rejected the API token (HTTP {status})"
                    )
                failure = f"HTTP {status}"
                if status != 429 and status < 500:
                    break
            if attempt == _ATTEMPTS - 1:
                break
            self.log(f"Pinboard {method}: {failure}; waiting {int(backoff)} s")
            self.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP)
        raise PinboardError(f"Pinboard {method} failed: {failure}")

    def last_update(self) -> str:
        """When the account's bookmarks last changed (``posts/update``)."""
        result = self._call("posts/update")
        return str(result.get("update_time") or "") if isinstance(result, dict) else ""

    def all_posts(
        self, page_size: int = 5000, fromdt: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Bookmarks, a page at a time; only those dated after ``fromdt`` if set.

        Pages are fetched by offset and can be minutes apart, so a bookmark
        deleted in between would shift the rest up and let one slip through
        the gap. Each page therefore starts a little before the previous one
        ended: callers may see the same bookmark twice, never miss one.
        """
        overlap = min(100, page_size // 2)
        start = 0
        while True:
            params = {"start": str(start), "results": str(page_size)}
            if fromdt:
                params["fromdt"] = fromdt
            page = self._call("posts/all", params)
            if not isinstance(page, list):
                raise PinboardError("Pinboard posts/all did not return a list")
            yield from (post for post in page if isinstance(post, dict))
            if len(page) < page_size:
                return
            start += len(page) - overlap

    def get(self, url: str) -> dict[str, Any] | None:
        """The bookmark saved under exactly this URL, or None (``posts/get``)."""
        result = self._call("posts/get", {"url": url})
        posts = result.get("posts") if isinstance(result, dict) else None
        if not isinstance(posts, list):
            raise PinboardError("Pinboard posts/get gave no list of posts")
        return next((p for p in posts if isinstance(p, dict)), None)

    def add(self, post: dict[str, str], replace: bool) -> str:
        """Save a bookmark. Returns Pinboard's result code.

        With ``replace`` false Pinboard refuses to touch a URL it already has
        and answers ``item already exists``.
        """
        params = {**post, "replace": "yes" if replace else "no"}
        result = self._call("posts/add", params)
        code = result.get("result_code") if isinstance(result, dict) else None
        if not code:
            raise PinboardError("Pinboard posts/add gave no result code")
        return str(code)

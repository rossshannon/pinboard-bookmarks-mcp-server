"""Fakes for the X API and Pinboard, served through httpx.MockTransport.

Nothing in these tests touches the network: both services are small
in-memory stand-ins, so "writes" land in a Python list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def ids() -> dict[str, str]:
    return load("ids.json")


class FakePinboard:
    """Pinboard's posts/update, posts/all and posts/add, with replace=no."""

    def __init__(self, posts: list[dict[str, Any]] | None = None):
        self.posts: list[dict[str, Any]] = list(posts or [])
        self.update_time = "2026-11-26T00:00:00Z"
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail_next: list[int] = []
        self._tick = 0

    def _touch(self) -> None:
        self._tick += 1
        self.update_time = f"2026-11-27T00:00:{self._tick:02d}Z"

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pinboard.in"
        method = request.url.path.removeprefix("/v1/")
        params = dict(request.url.params)
        assert params.pop("auth_token") == "ross:ABC123"
        assert params.pop("format") == "json"
        self.calls.append((method, params))
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), text="nope")
        if method == "posts/update":
            return httpx.Response(200, json={"update_time": self.update_time})
        if method == "posts/all":
            start, count = int(params["start"]), int(params["results"])
            return httpx.Response(200, json=self.posts[start : start + count])
        if method == "posts/add":
            found = next((p for p in self.posts if p["href"] == params["url"]), None)
            if found and params["replace"] == "no":
                return httpx.Response(200, json={"result_code": "item already exists"})
            post = {
                "href": params["url"],
                "description": params["description"],
                "extended": params.get("extended", ""),
                "tags": params.get("tags", ""),
                "time": params.get("dt", "2026-11-27T00:00:00Z"),
                "shared": params.get("shared", "yes"),
                "toread": params.get("toread", "no"),
            }
            if found:
                self.posts[self.posts.index(found)] = post
            else:
                self.posts.insert(0, post)
            self._touch()
            return httpx.Response(200, json={"result_code": "done"})
        return httpx.Response(404, text="unknown method")

    def adds(self) -> list[dict[str, str]]:
        return [params for method, params in self.calls if method == "posts/add"]


class FakeX:
    """X's token endpoint, users/me, liked_tweets and bookmarks."""

    def __init__(self) -> None:
        self.pages = {
            "liked_tweets": {
                None: load("likes_page1.json"),
                "NEXT1": load("likes_page2.json"),
            },
            "bookmarks": {None: load("bookmarks_page1.json")},
        }
        self.access_token = "access-1"
        self.refresh_token = "refresh-1"
        self.requests: list[httpx.Request] = []
        self.token_forms: list[dict[str, str]] = []
        self.reject_tweet_fields = False
        self.queue: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/2/oauth2/token":
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_forms.append(form)
            if form["grant_type"] == "refresh_token":
                if form["refresh_token"] != self.refresh_token:
                    return httpx.Response(400, json={"error": "invalid_request"})
            number = len(self.token_forms) + 1
            self.access_token = f"access-{number}"
            self.refresh_token = f"refresh-{number}"
            return httpx.Response(
                200,
                json={
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "expires_in": 7200,
                    "scope": "tweet.read users.read like.read bookmark.read",
                },
            )
        if self.queue:
            return self.queue.pop(0)
        if request.headers.get("Authorization") != f"Bearer {self.access_token}":
            return httpx.Response(401, json={"title": "Unauthorized"})
        if path == "/2/users/me":
            return httpx.Response(
                200, json={"data": {"id": "999", "username": "rossshannon"}}
            )
        params = request.url.params
        if self.reject_tweet_fields and "tweet.fields" in params:
            return httpx.Response(400, json={"title": "Invalid Request"})
        for endpoint, pages in self.pages.items():
            if path == f"/2/users/999/{endpoint}":
                return httpx.Response(200, json=pages[params.get("pagination_token")])
        return httpx.Response(404, json={"title": "Not Found"})


@pytest.fixture
def fake_x() -> FakeX:
    return FakeX()


@pytest.fixture
def fake_pinboard() -> FakePinboard:
    return FakePinboard(load("pinboard_export.json"))


@pytest.fixture
def no_sleep() -> list[float]:
    """A sleep stand-in that records what it was asked to wait."""
    return []

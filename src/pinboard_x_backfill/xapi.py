"""A small X API v2 client: OAuth 2.0 with PKCE, likes and bookmarks.

Facts below were read from docs.x.com on 2026-11-27:

- ``GET /2/users/{id}/bookmarks`` needs scopes ``bookmark.read tweet.read
  users.read``; ``max_results`` 1 to 100; 180 requests per 15 minutes.
- ``GET /2/users/{id}/liked_tweets`` needs ``like.read tweet.read
  users.read``; ``max_results`` 5 to 100; 75 requests per 15 minutes.
- Both are "owned reads" when ``{id}`` is the signed-in user who also owns
  the app. ``{id}`` must be the numeric id from ``GET /2/users/me``.
- The reference names the field parameter ``post.fields`` while the bookmarks
  guide still uses ``tweet.fields``. The client starts with ``tweet.fields``
  and switches to ``post.fields`` if X rejects it with a 400.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from pinboard_x_backfill.store import read_json, write_private_json
from pinboard_x_backfill.tweets import (
    BOOKMARK,
    LIKE,
    Tweet,
    clean_handle,
    normalise_time,
    tidy_text,
)

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_BASE = "https://api.x.com"
SCOPES = ("tweet.read", "users.read", "like.read", "bookmark.read", "offline.access")
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8739/callback"

_ENDPOINT = {LIKE: "liked_tweets", BOOKMARK: "bookmarks"}
_POST_FIELDS = "created_at,author_id,entities,{note}"
_FIELD_STYLES = (
    # (name of the fields parameter, name of the long-text field)
    ("tweet.fields", "note_tweet"),
    ("post.fields", "note_post"),
)
_MAX_RATE_LIMIT_WAIT = 16 * 60
_RETRIES = 4


class XError(Exception):
    """An X API or sign-in failure, safe to show (never holds a token)."""


# -- OAuth 2.0 authorisation code with PKCE ---------------------------------


def new_pkce_pair() -> tuple[str, str]:
    """A fresh (verifier, S256 challenge) pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def code_from_redirect(redirected_url: str, expected_state: str) -> str:
    """The authorisation code in the URL X redirected to, after checks."""
    query = parse_qs(urlsplit(redirected_url.strip()).query)
    if "error" in query:
        raise XError(f"X refused the sign-in: {query['error'][0]}")
    state = query.get("state", [""])[0]
    if not state or not secrets.compare_digest(state, expected_state):
        raise XError("sign-in state did not match; start `auth` again")
    code = query.get("code", [""])[0]
    if not code:
        raise XError("the redirect carried no authorisation code")
    return code


class TokenStore:
    """The OAuth tokens on disk, in a file only the owner can read."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any] | None:
        data = read_json(self.path)
        return data if isinstance(data, dict) and data.get("access_token") else None

    def save(self, tokens: dict[str, Any]) -> None:
        write_private_json(self.path, tokens)


class XAuth:
    """Exchanges and refreshes tokens against X's token endpoint."""

    def __init__(
        self,
        http: httpx.Client,
        store: TokenStore,
        client_id: str,
        client_secret: str | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.http = http
        self.store = store
        self.client_id = client_id
        self.client_secret = client_secret
        self.now = now

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        # A confidential client authenticates with Basic auth; a public client
        # puts its client id in the body. Sending the id in both is accepted.
        form = {**form, "client_id": self.client_id}
        auth: httpx.Auth | httpx._client.UseClientDefault = httpx.USE_CLIENT_DEFAULT
        if self.client_secret:
            auth = httpx.BasicAuth(self.client_id, self.client_secret)
        try:
            response = self.http.post(TOKEN_URL, data=form, auth=auth)
        except httpx.HTTPError as error:
            raise XError(
                f"could not reach X's token endpoint: {type(error).__name__}"
            ) from None
        if response.status_code != 200:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("error_description") or body.get("error") or "")
            except ValueError:
                pass
            raise XError(
                f"X token request failed: HTTP {response.status_code} {detail}".strip()
            )
        tokens = response.json()
        if not tokens.get("access_token"):
            raise XError("X token response held no access token")
        return tokens

    def _keep(
        self, tokens: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        kept = {
            "access_token": tokens["access_token"],
            # X rotates refresh tokens; keep the old one only if none came back.
            "refresh_token": tokens.get("refresh_token")
            or (previous or {}).get("refresh_token", ""),
            "expires_at": self.now() + float(tokens.get("expires_in") or 7200),
            "scope": tokens.get("scope", ""),
            "user": (previous or {}).get("user"),
        }
        self.store.save(kept)
        return kept

    def exchange_code(
        self, code: str, verifier: str, redirect_uri: str
    ) -> dict[str, Any]:
        tokens = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }
        )
        return self._keep(tokens, None)

    def refresh(self) -> dict[str, Any]:
        current = self.store.load()
        if not current or not current.get("refresh_token"):
            raise XError("no refresh token saved; run `pinboard-x-backfill auth`")
        tokens = self._token_request(
            {"grant_type": "refresh_token", "refresh_token": current["refresh_token"]}
        )
        return self._keep(tokens, current)

    def access_token(self) -> str:
        current = self.store.load()
        if not current:
            raise XError("not signed in to X; run `pinboard-x-backfill auth`")
        if float(current.get("expires_at") or 0) - 60 <= self.now():
            current = self.refresh()
        return str(current["access_token"])

    def remember_user(self, user: dict[str, str]) -> None:
        current = self.store.load()
        if current:
            current["user"] = user
            self.store.save(current)


# -- Reading likes and bookmarks ---------------------------------------------


def _expand_links(text: str, entities: dict[str, Any] | None) -> str:
    """Swap t.co short links for the addresses they point at."""
    for link in (entities or {}).get("urls") or []:
        short = link.get("url")
        full = link.get("unwound_url") or link.get("expanded_url")
        if short and full:
            text = text.replace(short, full)
    return text


def parse_page(payload: dict[str, Any], kind: str) -> list[Tweet]:
    """The tweets in one page of a likes or bookmarks response."""
    users = {
        str(user.get("id")): user
        for user in (payload.get("includes") or {}).get("users") or []
    }
    tweets = []
    for post in payload.get("data") or []:
        tweet_id = str(post.get("id") or "")
        if not tweet_id.isdigit():
            continue
        note = post.get("note_tweet") or post.get("note_post") or {}
        if note.get("text"):
            text = _expand_links(note["text"], note.get("entities"))
        else:
            text = _expand_links(post.get("text") or "", post.get("entities"))
        author = users.get(str(post.get("author_id") or ""), {})
        tweets.append(
            Tweet(
                id=tweet_id,
                kinds={kind},
                text=tidy_text(text),
                author_handle=clean_handle(author.get("username") or ""),
                author_name=(author.get("name") or "").strip(),
                created_at=normalise_time(post.get("created_at") or ""),
                source="api",
            )
        )
    return tweets


class XClient:
    """Pages through the signed-in user's likes and bookmarks."""

    def __init__(
        self,
        http: httpx.Client,
        auth: XAuth,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        log: Callable[[str], None] = print,
    ):
        self.http = http
        self.auth = auth
        self.sleep = sleep
        self.now = now
        self.log = log
        self._style = 0

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        """GET with sign-in refresh, rate-limit waits and retries.

        Returns the response for 200 and 400 (the caller reads 400s); raises
        XError for anything else once retries run out.
        """
        refreshed = False
        failure = "no response"
        for attempt in range(_RETRIES):
            headers = {"Authorization": f"Bearer {self.auth.access_token()}"}
            try:
                response = self.http.get(
                    f"{API_BASE}{path}", params=params, headers=headers
                )
            except httpx.HTTPError as error:
                failure = type(error).__name__
                self.sleep(2.0**attempt)
                continue
            status = response.status_code
            if status in (200, 400):
                return response
            failure = f"HTTP {status}"
            if status == 401 and not refreshed:
                refreshed = True
                self.auth.refresh()
            elif status == 429:
                reset = response.headers.get("x-rate-limit-reset", "")
                wait = float(reset) - self.now() + 2 if reset.isdigit() else 60.0
                wait = min(max(wait, 1.0), _MAX_RATE_LIMIT_WAIT)
                self.log(f"X rate limit reached; waiting {int(wait)} s")
                self.sleep(wait)
            elif status >= 500:
                self.sleep(2.0**attempt)
            else:
                break
        raise XError(f"X API request to {path} failed: {failure}")

    def me(self) -> dict[str, str]:
        """The signed-in user's numeric id and handle."""
        response = self._get("/2/users/me", {})
        data = response.json().get("data") if response.status_code == 200 else None
        if not data or not data.get("id"):
            raise XError(
                f"X did not say who is signed in (HTTP {response.status_code})"
            )
        user = {"id": str(data["id"]), "username": str(data.get("username") or "")}
        self.auth.remember_user(user)
        return user

    def pages(self, kind: str, user_id: str) -> Iterator[list[Tweet]]:
        """Each page of the user's likes or bookmarks, newest first."""
        path = f"/2/users/{user_id}/{_ENDPOINT[kind]}"
        token: str | None = None
        while True:
            fields_name, note_name = _FIELD_STYLES[self._style]
            params = {
                "max_results": "100",
                fields_name: _POST_FIELDS.format(note=note_name),
                "expansions": "author_id",
                "user.fields": "username,name",
            }
            if token:
                params["pagination_token"] = token
            response = self._get(path, params)
            if response.status_code == 400:
                if self._style + 1 < len(_FIELD_STYLES):
                    self._style += 1
                    continue
                raise XError(f"X rejected the {kind}s request: HTTP 400")
            payload = response.json()
            yield parse_page(payload, kind)
            token = (payload.get("meta") or {}).get("next_token")
            if not token:
                return

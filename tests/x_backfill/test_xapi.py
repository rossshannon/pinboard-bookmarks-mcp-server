"""The X client: PKCE sign-in, token refresh, paging, rate limits."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from pinboard_x_backfill import oembed
from pinboard_x_backfill.tweets import BOOKMARK, LIKE, Tweet
from pinboard_x_backfill.xapi import (
    SCOPES,
    TokenStore,
    XAuth,
    XClient,
    XError,
    authorize_url,
    code_from_redirect,
    new_pkce_pair,
    parse_page,
)

from .conftest import load


def make(fake_x, tmp_path, secret=None, now=lambda: 1000.0):
    http = httpx.Client(transport=httpx.MockTransport(fake_x.handler))
    auth = XAuth(http, TokenStore(tmp_path / "x-token.json"), "client-1", secret, now)
    waits: list[float] = []
    client = XClient(http, auth, sleep=waits.append, now=now, log=lambda _: None)
    return auth, client, waits


def test_pkce_pair_and_authorize_url():
    verifier, challenge = new_pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    url = authorize_url("client-1", "http://127.0.0.1:8739/callback", "st", challenge)
    query = parse_qs(urlsplit(url).query)
    assert url.startswith("https://x.com/i/oauth2/authorize?")
    assert query["scope"] == [" ".join(SCOPES)]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8739/callback"]
    # Read-only: nothing in the scopes can post, like or bookmark.
    assert not any(scope.endswith(".write") for scope in SCOPES)


def test_code_from_redirect():
    good = "http://127.0.0.1:8739/callback?state=st&code=abc"
    assert code_from_redirect(good, "st") == "abc"
    with pytest.raises(XError, match="state"):
        code_from_redirect(good, "other")
    with pytest.raises(XError, match="access_denied"):
        code_from_redirect("http://x/callback?error=access_denied&state=st", "st")
    with pytest.raises(XError, match="no authorisation code"):
        code_from_redirect("http://x/callback?state=st", "st")


def test_exchange_then_refresh_keeps_the_rotated_token(fake_x, tmp_path):
    auth, client, _ = make(fake_x, tmp_path)
    with pytest.raises(XError, match="not signed in"):
        auth.access_token()
    auth.exchange_code("code-1", "verifier-1", "http://127.0.0.1:8739/callback")
    form = fake_x.token_forms[0]
    assert form["code_verifier"] == "verifier-1" and form["client_id"] == "client-1"
    assert "Authorization" not in fake_x.requests[0].headers  # public client
    assert (tmp_path / "x-token.json").stat().st_mode & 0o777 == 0o600
    assert client.me() == {"id": "999", "username": "rossshannon"}
    assert auth.store.load()["user"]["id"] == "999"

    # Two hours on, the access token has expired: it is refreshed once, and
    # the new refresh token replaces the old (X's refresh tokens rotate).
    auth.now = lambda: 1000.0 + 7300
    assert auth.access_token() == fake_x.access_token
    assert fake_x.token_forms[1]["refresh_token"] == "refresh-2"
    saved = auth.store.load()
    assert saved["refresh_token"] == fake_x.refresh_token
    assert saved["user"]["username"] == "rossshannon"


def test_confidential_client_uses_basic_auth(fake_x, tmp_path):
    auth, _, _ = make(fake_x, tmp_path, secret="shh")
    auth.exchange_code("code-1", "v", "http://127.0.0.1:8739/callback")
    expected = "Basic " + base64.b64encode(b"client-1:shh").decode()
    assert fake_x.requests[0].headers["Authorization"] == expected


def test_token_errors_are_reported_without_secrets(fake_x, tmp_path):
    auth, _, _ = make(fake_x, tmp_path)
    with pytest.raises(XError, match="no refresh token"):
        auth.refresh()
    auth.store.save({"access_token": "a", "refresh_token": "stale", "expires_at": 0})
    with pytest.raises(XError, match="HTTP 400 invalid_request") as caught:
        auth.access_token()
    assert "stale" not in str(caught.value)


def test_paging_likes_and_bookmarks(fake_x, tmp_path, ids):
    auth, client, _ = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    listing = list(client.pages(LIKE, "999"))
    assert [token for _, token in listing] == ["NEXT1", None]
    pages = [tweets for tweets, _ in listing]
    assert [len(page) for page in pages] == [2, 2]
    both, collison = pages[0]
    assert (
        both.text == "SQLite FTS5 is absurdly good & fast https://sqlite.org/fts5.html"
    )
    assert (collison.author_handle, collison.author_name) == (
        "patrickc",
        "Patrick Collison",
    )
    assert collison.created_at == "2026-11-20T14:03:00Z" and collison.source == "api"
    long_post = pages[1][0]
    assert (
        long_post.text.startswith("A very long post") and "whole body" in long_post.text
    )
    request = fake_x.requests[-1]
    assert request.url.params["pagination_token"] == "NEXT1"
    assert request.url.params["max_results"] == "100"
    assert "note_tweet" in request.url.params["tweet.fields"]

    bookmarks = [tweets for tweets, _ in client.pages(BOOKMARK, "999")]
    assert [[t.id for t in page] for page in bookmarks] == [[ids["both"]]]
    assert bookmarks[0][0].kinds == {BOOKMARK}

    resumed = list(client.pages(LIKE, "999", "NEXT1"))
    assert len(resumed) == 1 and resumed[0][1] is None


def test_switches_to_post_fields_when_tweet_fields_is_rejected(fake_x, tmp_path):
    auth, client, _ = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    fake_x.reject_tweet_fields = True
    assert sum(len(page) for page, _ in client.pages(LIKE, "999")) == 4
    assert "note_post" in fake_x.requests[-1].url.params["post.fields"]


def test_gives_up_when_both_field_styles_are_rejected(fake_x, tmp_path):
    auth, client, _ = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    fake_x.queue = [httpx.Response(400), httpx.Response(400)]
    with pytest.raises(XError, match="HTTP 400"):
        list(client.pages(LIKE, "999"))


def test_waits_out_rate_limits_and_server_errors(fake_x, tmp_path):
    auth, client, waits = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    fake_x.queue = [
        httpx.Response(429, headers={"x-rate-limit-reset": "1300"}),
        httpx.Response(503),
    ]
    assert client.me()["id"] == "999"
    assert waits == [302.0, 2.0]


def test_rate_limit_wait_is_capped_and_defaults(fake_x, tmp_path):
    auth, client, waits = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    fake_x.queue = [
        httpx.Response(429, headers={"x-rate-limit-reset": "999999"}),
        httpx.Response(429),
    ]
    client.me()
    assert waits == [960, 60.0]


def test_refreshes_once_on_401_then_fails_cleanly(fake_x, tmp_path):
    auth, client, _ = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    fake_x.queue = [httpx.Response(401)]
    assert client.me()["id"] == "999"
    assert len(fake_x.token_forms) == 2

    fake_x.queue = [httpx.Response(401), httpx.Response(401)]
    with pytest.raises(XError, match="HTTP 401"):
        client.me()
    fake_x.queue = [httpx.Response(403)]
    with pytest.raises(XError, match="/2/users/me failed: HTTP 403"):
        client.me()


def test_network_errors_are_retried_then_reported(tmp_path, fake_x):
    def handler(request):
        if request.url.path == "/2/oauth2/token":
            return fake_x.handler(request)
        raise httpx.ConnectError("boom")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    auth = XAuth(http, TokenStore(tmp_path / "t.json"), "client-1")
    auth.exchange_code("c", "v", "r")
    waits: list[float] = []
    client = XClient(http, auth, sleep=waits.append)
    with pytest.raises(XError, match="ConnectError"):
        client.me()
    assert waits == [1.0, 2.0, 4.0]  # no pointless wait after the last try


def test_token_endpoint_unreachable(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    auth = XAuth(http, TokenStore(tmp_path / "t.json"), "client-1")
    with pytest.raises(XError, match="could not reach"):
        auth.exchange_code("c", "v", "r")


def test_parse_page_tolerates_gaps():
    assert parse_page({}, LIKE) == []
    payload = {"data": [{"id": "x"}, {"id": "5", "author_id": "404"}]}
    (tweet,) = parse_page(payload, LIKE)
    assert (tweet.id, tweet.author_handle, tweet.text) == ("5", "", "")
    assert len(parse_page(load("likes_page1.json"), LIKE)) == 2


def test_oembed_enrich():
    answers = {
        "1": httpx.Response(
            200,
            json={
                "author_name": "Simon Willison",
                "author_url": "https://twitter.com/simonw",
            },
        ),
        "2": httpx.Response(404),
        "3": httpx.Response(403),
        "4": httpx.Response(200, text="not json"),
        "5": httpx.Response(200, json={"author_url": "https://twitter.com/"}),
        "7": httpx.Response(200, json=["a list"]),
        "8": httpx.Response(404),
    }
    asked = []

    def handler(request):
        assert request.url.host == "publish.twitter.com"
        tweet_id = request.url.params["url"].rsplit("/", 1)[1]
        asked.append(tweet_id)
        return answers[tweet_id]

    http = httpx.Client(transport=httpx.MockTransport(handler))
    tweets = [Tweet(id=str(n), kinds={LIKE}) for n in range(1, 9)]
    tweets[5].author_handle = "known"
    waits: list[float] = []
    results = [
        (tweet.id, result)
        for tweet, result in oembed.enrich(http, tweets, skip={"2"}, sleep=waits.append)
    ]
    assert asked == ["1", "3", "4", "5", "7", "8"]
    # Only a 404 is a lasting "missing"; a 403 or an odd answer is retried.
    assert results == [
        ("1", "found"),
        ("3", "failed"),
        ("4", "failed"),
        ("5", "failed"),
        ("7", "failed"),
        ("8", "missing"),
    ]
    assert (tweets[0].author_handle, tweets[0].author_name) == (
        "simonw",
        "Simon Willison",
    )
    assert waits == [1.0] * 5

    limited = list(oembed.enrich(http, tweets, skip=set(), limit=1, sleep=waits.append))
    assert [(t.id, r) for t, r in limited] == [("2", "missing")]


def test_oembed_stops_after_repeated_failures():
    def handler(request):
        raise httpx.ConnectError("offline")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    tweets = [Tweet(id=str(n), kinds={LIKE}) for n in range(20)]
    results = list(oembed.enrich(http, tweets, skip=set(), sleep=lambda _: None))
    assert len(results) == oembed.MAX_FAILURES_IN_A_ROW == 10


def test_a_200_that_is_not_json_is_a_clean_error(fake_x, tmp_path):
    auth, client, _ = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    html = httpx.Response(200, text="<html>captive portal</html>")
    fake_x.queue = [html]
    with pytest.raises(XError, match="did not answer with JSON"):
        client.me()
    fake_x.queue = [httpx.Response(200, json=["a", "list"])]
    with pytest.raises(XError, match="did not answer with JSON"):
        list(client.pages(LIKE, "999"))
    fake_x.queue = [httpx.Response(200, json={"data": "nope"})]
    with pytest.raises(XError, match="did not say who is signed in"):
        client.me()

    def portal(request):
        return httpx.Response(200, text="<html>")

    http = httpx.Client(transport=httpx.MockTransport(portal))
    with pytest.raises(XError, match="token endpoint did not answer with JSON"):
        XAuth(http, TokenStore(tmp_path / "t2.json"), "c").exchange_code("c", "v", "r")

    def refused(request):
        return httpx.Response(502, text="bad gateway")

    http = httpx.Client(transport=httpx.MockTransport(refused))
    with pytest.raises(XError, match="HTTP 502$"):
        XAuth(http, TokenStore(tmp_path / "t3.json"), "c").exchange_code("c", "v", "r")


def test_no_wait_after_the_last_attempt(fake_x, tmp_path):
    auth, client, waits = make(fake_x, tmp_path)
    auth.exchange_code("c", "v", "r")
    fake_x.queue = [httpx.Response(429)] * 4
    with pytest.raises(XError, match="HTTP 429"):
        client.me()
    assert waits == [60.0, 60.0, 60.0]
    waits.clear()
    fake_x.queue = [httpx.Response(500)] * 4
    with pytest.raises(XError, match="HTTP 500"):
        client.me()
    assert waits == [1.0, 2.0, 4.0]

"""Link previews for the bookmarks card, fetched from a pinboard-bridge instance.

The bookmarks card draws each row as a link card: a favicon and site name, the
page's description, a thumbnail. None of that is in Pinboard, and none of it
belongs in the model's context, so the card asks for it itself, after it has
rendered, through the app-only ``get_link_previews`` tool (see ``main.py``).
That tool is this module: it asks a pinboard-bridge instance to scrape each
page, and hands the card back a small, sanitised summary with the images
inlined as ``data:`` URIs, so the card never loads anything from the network.

The feature is opt-in. Looking a page up means sending its URL to the bridge,
and a Pinboard account holds private bookmarks too, so nothing is sent
anywhere unless ``PINBOARD_PREVIEW_BRIDGE_URL`` names a bridge the user has
chosen to trust with that. Unset, the tool is not registered at all and the
card draws its rows from the bookmark alone.

What comes back from the bridge is derived from arbitrary web pages, so every
field is validated here before the card sees it, and the card validates the
images again. Remote image URLs are never forwarded.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable, Container, Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx2
from cachetools import LRUCache, TLRUCache

logger = logging.getLogger(__name__)

#: Environment variable naming the pinboard-bridge instance. Unset or empty
#: leaves the feature off.
BRIDGE_URL_ENV_VAR = "PINBOARD_PREVIEW_BRIDGE_URL"

#: The most URLs one ``get_link_previews`` call takes: the rows the card draws
#: before "Show all".
MAX_URLS_PER_CALL = 8

#: A URL longer than this is not one a browser would open either.
MAX_URL_LENGTH = 2048

#: Seconds one page's lookup may take, connection to last byte, counted from
#: the moment it is sent. Time spent queued behind other lookups is not
#: charged to it. The bridge answers a thumbnail request inside a deadline of
#: its own, seven seconds, so this is that and a margin for the network: an
#: answer the bridge was always going to give is never counted as a failure
#: of the bridge.
REQUEST_TIMEOUT_SECONDS = 8.5

#: Seconds a ``get_link_previews`` call waits before answering with what it
#: has. The card gives a tool call about fifteen seconds before it stops
#: waiting, so the answer has to leave before then. A lookup not finished by
#: then is not cut off: it carries on into the cache, and its entry goes back
#: ``pending`` so the card asks once more.
BATCH_TIMEOUT_SECONDS = 12.0

#: Lookups in flight at once, across every call: a whole call's worth, so the
#: eight URLs of one call go out together, in one wave, and each has its full
#: time inside the call's twelve seconds. It is also the number of jobs the
#: bridge runs at once for one client, so there are never more in flight
#: than it will take. Only a second card asking at the same moment queues.
MAX_CONCURRENCY = 8

#: How many transport failures in a row (the bridge unreachable, timing out,
#: answering 5xx) open the circuit breaker, and for how long. Open, lookups
#: fail at once without touching the bridge. A failure of this kind says
#: nothing about the page, so it is never cached against the page's URL; the
#: breaker is what keeps a dead bridge from being hammered.
BREAKER_FAILURES = 3
BREAKER_OPEN_SECONDS = 30.0

#: How many bookmark URLs the server remembers having returned; see
#: ``OfferedUrls``.
OFFERED_URLS = 5000

#: A bridge response larger than this is refused unread. Two thumbnails and a
#: few strings come to well under a tenth of it.
MAX_RESPONSE_BYTES = 1_000_000

CACHE_ENTRIES = 500
POSITIVE_TTL_SECONDS = 6 * 60 * 60
NEGATIVE_TTL_SECONDS = 10 * 60

MAX_TITLE_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 500
MAX_SITE_NAME_LENGTH = 100

#: Ceilings on a whole ``data:`` URI, in characters.
MAX_IMAGE_DATA_URI_LENGTH = 60 * 1024
MAX_FAVICON_DATA_URI_LENGTH = 12 * 1024

#: The longest side a thumbnail may claim.
MAX_IMAGE_DIMENSION = 1024

#: What the card is told to wait when the bridge gave no ``Retry-After``, and
#: the range a value it did give is held to.
DEFAULT_RETRY_AFTER_SECONDS = 30
MAX_RETRY_AFTER_SECONDS = 120

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"
STATUS_ERROR = "error"
STATUS_RATE_LIMITED = "rate_limited"
#: The lookup was still running when the call had to answer. It carries on,
#: and asking again shortly will find its result.
STATUS_PENDING = "pending"

#: The bridge's ``thumbnailsStatus``: whether the thumbnails in a response are
#: all there are going to be. An older bridge sends none, which means they are.
THUMBNAILS_COMPLETE = "complete"
#: The bridge was too busy to make the thumbnails this time, and expects to
#: have them in a few seconds.
THUMBNAILS_BUSY = "busy"
#: The other values, "transient" (a timeout, a network failure or a 5xx,
#: fetching the page or its images, that may not happen again) and
#: "unavailable", are handled alike: what arrived is served and kept only for
#: the short lifetime. A page that failed for good (not HTML, a 4xx, blocked)
#: comes back "complete" with nothing in it, and is an ordinary ``no_data`` or
#: ``error`` entry.

#: Raster formats only: an SVG is a document, and can carry script.
_DATA_URI = re.compile(r"^data:image/(webp|png|jpeg);base64,([A-Za-z0-9+/=]+)$")
_HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def bridge_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The configured bridge's base URL, without a trailing slash, or ``None`` when the feature is off.

    ``https`` only, because every request carries the Pinboard token; plain
    ``http`` is allowed for a bridge on this machine. Anything else is ignored
    with a warning, which leaves the feature off rather than half on.
    """
    raw = (environ if environ is not None else os.environ).get(BRIDGE_URL_ENV_VAR, "")
    value = raw.strip()
    if not value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a port that is not a number
    except ValueError:
        host = None
    problem = None
    if not host:
        problem = "it is not an absolute URL"
    elif parts.scheme not in {"http", "https"}:
        problem = "it is not an http(s) URL"
    elif parts.scheme == "http" and host not in _LOCAL_HOSTS:
        problem = "plain http is only allowed for localhost"
    elif parts.username is not None or parts.password is not None:
        problem = "it carries credentials"
    elif parts.query or parts.fragment:
        problem = "it carries a query or fragment"
    if problem is not None:
        # The reason, never the value: a URL with credentials in it is exactly
        # what would otherwise end up in a log.
        logger.warning(
            "Ignoring %s: %s. Link previews are off.", BRIDGE_URL_ENV_VAR, problem
        )
        return None
    return value.rstrip("/")


def requested_urls(urls: Any) -> list[str]:
    """Check a ``get_link_previews`` request's shape and return its URLs, deduplicated, in order.

    Raises ``ValueError`` only for a request that is the wrong shape: not a
    list of one to eight strings. Whether each string is a URL worth looking
    up is decided per URL (``is_web_url``, ``OfferedUrls``), so one bad URL
    costs its own row a preview and not the other seven theirs.
    """
    if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS_PER_CALL:
        raise ValueError(f"Must provide 1-{MAX_URLS_PER_CALL} URLs")
    unique: list[str] = []
    for url in urls:
        if not isinstance(url, str):
            raise ValueError("Every URL must be a string")
        if url not in unique:
            unique.append(url)
    return unique


def is_web_url(url: str) -> bool:
    """Whether a URL is one the bridge may be asked about: absolute ``http(s)``, with a host, of a sane length.

    The card applies the same rule before it asks (``preview-loader.ts``).
    """
    if len(url) > MAX_URL_LENGTH or url != url.strip():
        return False
    if any(character.isspace() for character in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


class OfferedUrls:
    """The bookmark URLs this server has recently returned: the only ones it will look up.

    ``get_link_previews`` is marked for the card alone, but a host that
    ignores that shows it to the model, and a model can be talked into calling
    a tool with anything. Without this, the tool would make the bridge fetch
    any URL at all, with the user's token attached to the request for it. So
    the bookmark tools record each URL they return, and a preview is only ever
    fetched for one of those: the card asks about nothing else, and nothing
    else gets further than an ``error`` entry.

    Bounded, least recently used out first, and in memory: a card still open
    after a server restart gets no previews for rows it has not been sent
    again, which costs it nothing but the previews.
    """

    def __init__(self, maxsize: int = OFFERED_URLS) -> None:
        self._urls: LRUCache = LRUCache(maxsize=maxsize)

    def add(self, urls: Iterable[Any]) -> None:
        for url in urls:
            if isinstance(url, str):
                self._urls[url] = True

    def __contains__(self, url: object) -> bool:
        # `get`, not `in`: being asked about counts as use.
        return self._urls.get(url) is True

    def __len__(self) -> int:
        return len(self._urls)


def _clean_text(value: Any, limit: int) -> str | None:
    """A string trimmed, on one line and no longer than ``limit``, or ``None``."""
    if not isinstance(value, str):
        return None
    text = " ".join(_CONTROL_CHARACTERS.sub(" ", value).split())
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _clean_colour(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    colour = value.strip()
    return colour.lower() if _HEX_COLOUR.fullmatch(colour) else None


def _clean_dimension(value: Any) -> int | None:
    # A bool is an int in Python; `True` is not a width.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 < value <= MAX_IMAGE_DIMENSION else None


def _clean_thumbnail(value: Any, max_length: int) -> dict[str, Any] | None:
    """One thumbnail as the card takes it, or ``None`` if any part of it is off.

    All or nothing: a card that drew an image without its dimensions would
    reflow as it decoded, so an image missing either is dropped whole.
    """
    if not isinstance(value, dict):
        return None
    data_uri = value.get("dataUri")
    if not isinstance(data_uri, str) or len(data_uri) > max_length:
        return None
    match = _DATA_URI.fullmatch(data_uri)
    if match is None:
        return None
    try:
        base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        return None
    width = _clean_dimension(value.get("width"))
    height = _clean_dimension(value.get("height"))
    if width is None or height is None:
        return None
    return {"data_uri": data_uri, "width": width, "height": height}


def _entry(url: str, status: str) -> dict[str, Any]:
    """A preview with nothing in it: the shape every entry has, whatever became of the lookup."""
    return {
        "url": url,
        "status": status,
        "title": None,
        "description": None,
        "site_name": None,
        "theme_color": None,
        "image": None,
        "favicon": None,
    }


def _thumbnails_status(payload: Any) -> str:
    """The bridge's ``thumbnailsStatus``; absent, as on an older bridge, it is "complete"."""
    if not isinstance(payload, dict):
        return THUMBNAILS_COMPLETE
    status = payload.get("thumbnailsStatus", THUMBNAILS_COMPLETE)
    # Anything unrecognised is treated as not complete: the cautious reading,
    # which only costs asking again sooner.
    return status if isinstance(status, str) else "unknown"


def sanitise_preview(url: str, payload: Any) -> dict[str, Any]:
    """Narrow one bridge response to what the card is allowed to draw.

    ``url`` is the URL as requested, never the bridge's own (which may be a
    redirect's target): the card matches previews to rows by it. The bridge's
    ``imageUrl`` and ``faviconUrl`` are deliberately not read.

    When the bridge says it was too busy to make the thumbnails
    (``thumbnailsStatus: "busy"``), the entry carries ``retry: True``: the card
    draws what did arrive and asks about that URL once more a few seconds on.
    The key is absent otherwise.
    """
    entry = _sanitise(url, payload)
    if (
        _thumbnails_status(payload) == THUMBNAILS_BUSY
        and entry["status"] != STATUS_ERROR
    ):
        entry["retry"] = True
    return entry


def _sanitise(url: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _entry(url, STATUS_ERROR)
    preview = payload.get("preview")
    thumbnails = payload.get("thumbnails")
    if not isinstance(thumbnails, dict):
        thumbnails = {}  # an older bridge sends none
    entry = _entry(url, STATUS_OK)
    if isinstance(preview, dict):
        entry["title"] = _clean_text(preview.get("title"), MAX_TITLE_LENGTH)
        entry["description"] = _clean_text(
            preview.get("description"), MAX_DESCRIPTION_LENGTH
        )
        entry["site_name"] = _clean_text(preview.get("siteName"), MAX_SITE_NAME_LENGTH)
        entry["theme_color"] = _clean_colour(preview.get("themeColor"))
    entry["image"] = _clean_thumbnail(
        thumbnails.get("image"), MAX_IMAGE_DATA_URI_LENGTH
    )
    entry["favicon"] = _clean_thumbnail(
        thumbnails.get("favicon"), MAX_FAVICON_DATA_URI_LENGTH
    )
    drawable = ("title", "description", "site_name", "image", "favicon")
    if all(entry[key] is None for key in drawable):
        # Nothing the card could show. The bridge says whether that is a page
        # with no metadata or a page it could not read.
        failed = payload.get("previewStatus") == "error"
        return _entry(url, STATUS_ERROR if failed else STATUS_NO_DATA)
    return entry


def _retry_after(response: httpx2.Response) -> int:
    try:
        seconds = int(response.headers.get("Retry-After", ""))
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS
    return max(1, min(seconds, MAX_RETRY_AFTER_SECONDS))


class LinkPreviews:
    """An async client for a pinboard-bridge instance's ``/preview``, with a cache in front.

    One instance per server. ``transport`` and ``timer`` are for tests.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timer: Callable[[], float] = time.monotonic,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
        batch_timeout: float = BATCH_TIMEOUT_SECONDS,
    ) -> None:
        self._endpoint = f"{base_url.rstrip('/')}/preview"
        self._request_timeout = request_timeout
        self._batch_timeout = batch_timeout
        self._timer = timer
        self._http = httpx2.AsyncClient(
            # The token goes to the bridge and nowhere else: a redirect is not
            # followed, to this host or any other.
            follow_redirects=False,
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=httpx2.Timeout(request_timeout),
            transport=transport,
        )
        # One cache, two lifetimes: a finished preview keeps for hours, and a
        # page-level answer short of that (no metadata, a page the bridge
        # could not read, thumbnails yet to be made) is worth asking about
        # again in a few minutes. Each value is the entry and the lifetime it
        # was stored with. Failures of the bridge itself are never in here.
        self._cache: TLRUCache = TLRUCache(
            maxsize=CACHE_ENTRIES,
            ttu=lambda _url, cached, now: now + cached[1],
            timer=timer,
        )
        self._semaphore: asyncio.Semaphore | None = None
        # Lookups under way, by URL: a second call asking about the same page
        # joins the first's lookup rather than starting another.
        self._in_flight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        # Until when the bridge's rate limit is taken to be in force.
        self._limited_until = 0.0
        self._transport_failures = 0
        self._breaker_open_until = 0.0
        self._warned_unauthorised = False

    async def aclose(self) -> None:
        for task in list(self._in_flight.values()):
            task.cancel()
        await self._http.aclose()

    async def fetch_many(
        self, urls: list[str], *, allowed: Container[str] | None = None
    ) -> dict[str, Any]:
        """Look up every URL and return the ``get_link_previews`` result, entries in request order.

        A URL that is not a web URL, or not in ``allowed`` when that is given,
        gets an ``error`` entry and the bridge never hears of it. Nothing one
        URL does fails the others: every lookup ends as an entry.

        The call answers within the batch timeout. A lookup still running then
        is left to finish into the cache, and its entry is ``pending``. When
        the bridge's rate limit is in force the entries not looked up are
        ``rate_limited`` and the result carries ``retry_after_seconds``.
        """
        lookups: dict[str, asyncio.Task[dict[str, Any]]] = {}
        refused: set[str] = set()
        for url in urls:
            if not is_web_url(url) or (allowed is not None and url not in allowed):
                refused.add(url)
            elif url not in lookups:
                lookups[url] = self._lookup_task(url)
        if refused:
            logger.info("Link previews: refused %d of %d URLs", len(refused), len(urls))
        unfinished: set[asyncio.Task[dict[str, Any]]] = set()
        if lookups:
            _, unfinished = await asyncio.wait(
                lookups.values(), timeout=self._batch_timeout
            )
        if unfinished:
            logger.info(
                "Link previews: %d of %d lookups still running at the deadline",
                len(unfinished),
                len(lookups),
            )

        previews: list[dict[str, Any]] = []
        for url in urls:
            task = lookups.get(url)
            if task is None:
                previews.append(_entry(url, STATUS_ERROR))
            elif task in unfinished:
                previews.append(_entry(url, STATUS_PENDING))
            else:
                # A copy: the cache holds the original.
                previews.append(dict(task.result()))
        result: dict[str, Any] = {"previews": previews}
        if any(entry["status"] == STATUS_RATE_LIMITED for entry in previews):
            remaining = math.ceil(self._limited_until - self._timer())
            result["retry_after_seconds"] = max(
                1, min(remaining, MAX_RETRY_AFTER_SECONDS)
            )
        return result

    def _lookup_task(self, url: str) -> asyncio.Task[dict[str, Any]]:
        task = self._in_flight.get(url)
        if task is None:
            task = asyncio.ensure_future(self._lookup(url))
            self._in_flight[url] = task
            task.add_done_callback(lambda _done: self._in_flight.pop(url, None))
        return task

    async def _lookup(self, url: str) -> dict[str, Any]:
        """One URL's entry, from the cache or the bridge. Never raises."""
        try:
            cached = self._cache.get(url)
            if cached is not None:
                return cached[0]
            # Made on first use, so it belongs to the loop the server runs in.
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
            async with self._semaphore:
                now = self._timer()
                if now < self._limited_until:
                    return _entry(url, STATUS_RATE_LIMITED)
                if now < self._breaker_open_until:
                    return _entry(url, STATUS_ERROR)
                entry, lifetime = await self._request(url)
            if lifetime is not None:
                self._cache[url] = (entry, lifetime)
            return entry
        except Exception as error:  # noqa: BLE001 - one lookup must not fail its batch
            # Whatever it was (a response nested too deep to parse, a URL the
            # HTTP client will not send), it is this URL's problem alone. The
            # kind of failure only: the message can quote the request.
            logger.warning("Link preview lookup failed: %s", type(error).__name__)
            return _entry(url, STATUS_ERROR)

    def _transport_failed(self) -> None:
        """Count a failure of the bridge itself, and open the breaker on a run of them."""
        self._transport_failures += 1
        if self._transport_failures >= BREAKER_FAILURES:
            self._open_breaker()

    def _open_breaker(self) -> None:
        self._transport_failures = 0
        self._breaker_open_until = self._timer() + BREAKER_OPEN_SECONDS
        logger.warning(
            "The preview bridge is not answering; leaving it alone for %d seconds",
            BREAKER_OPEN_SECONDS,
        )

    async def _request(self, url: str) -> tuple[dict[str, Any], float | None]:
        """One lookup: the entry, and how long to keep it (``None`` for not at all).

        Only an answer about the page is kept. A timeout, a refused
        connection, a 401 or a 5xx is the bridge's trouble, not the page's,
        and the next ask may well go through.
        """
        try:
            status, body, retry_after = await asyncio.wait_for(
                self._get(url), timeout=self._request_timeout
            )
        except (asyncio.TimeoutError, httpx2.HTTPError, _BadResponse) as error:
            # The kind of failure only. The message can quote the request, and
            # the request is somebody's bookmark.
            logger.info("Link preview lookup failed: %s", type(error).__name__)
            self._transport_failed()
            return _entry(url, STATUS_ERROR), None
        if status == 429:
            self._limited_until = self._timer() + retry_after
            return _entry(url, STATUS_RATE_LIMITED), None
        if status == 400:
            # The bridge's answer for a page it cannot reach or will not
            # fetch: a verdict on the URL, and the bridge is plainly alive.
            self._transport_failures = 0
            return _entry(url, STATUS_ERROR), NEGATIVE_TTL_SECONDS
        if status != 200:
            logger.info("Link preview lookup failed: HTTP %d", status)
            if status == 401:
                if not self._warned_unauthorised:
                    self._warned_unauthorised = True
                    logger.warning(
                        "The preview bridge rejected the Pinboard token (401); link previews will not load"
                    )
                # No retry will change the token.
                self._open_breaker()
            else:
                self._transport_failed()
            return _entry(url, STATUS_ERROR), None
        self._transport_failures = 0
        entry = sanitise_preview(url, body)
        if entry.get("retry"):
            # The card is about to ask again, and an answer from the cache
            # would be this one: a busy bridge's reply is not kept at all.
            return entry, None
        finished = (
            entry["status"] == STATUS_OK
            and _thumbnails_status(body) == THUMBNAILS_COMPLETE
        )
        return entry, POSITIVE_TTL_SECONDS if finished else NEGATIVE_TTL_SECONDS

    async def _get(self, url: str) -> tuple[int, Any, int]:
        """One request: the status, the parsed body of a 200, and the wait a 429 asked for."""
        async with self._http.stream(
            "GET", self._endpoint, params={"url": url, "thumbnails": "1"}
        ) as response:
            if response.status_code != 200:
                return response.status_code, None, _retry_after(response)
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise _BadResponse("preview response too large")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise _BadResponse("preview response too large")
                chunks.append(chunk)
        try:
            return 200, json.loads(b"".join(chunks)), 0
        except ValueError as error:
            raise _BadResponse("preview response is not JSON") from error


class _BadResponse(Exception):
    """A 200 from the bridge that cannot be used: too large, or not JSON."""

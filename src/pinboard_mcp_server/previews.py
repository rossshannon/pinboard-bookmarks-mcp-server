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

The tool looks up only what this server returned as a bookmark. Each bookmark
carries a short token, an HMAC of its URL keyed from the Pinboard token, and
the card hands the pair back; a URL without its own token is refused before
the bridge hears of it (``preview_token``).

What comes back from the bridge is derived from arbitrary web pages, so every
field is validated here before the card sees it, and the card validates the
images again. Remote image URLs are never forwarded.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import math
import os
import re
import struct
import time
from collections.abc import Callable, Container, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx2
from cachetools import TLRUCache

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

#: How many failures to reach the bridge at all, in a row (no connection, no
#: DNS answer, no TLS), open the circuit breaker, and for how long. Open,
#: lookups fail at once without touching the bridge. A bridge that rejects the
#: token (401) opens it at once. One page timing out or answering 5xx is that
#: page's trouble and does not count, unless a whole wave of lookups failed
#: that way together, which is a bridge in trouble whatever it says.
BREAKER_FAILURES = 3
BREAKER_OPEN_SECONDS = 30.0

#: The fewest lookups that make a wave for that purpose: one slow page on its
#: own must not lock every other page out.
BREAKER_WAVE_SIZE = 2

#: Bytes of the HMAC kept in a preview token: 80 bits, which is 14 characters
#: of base64url on every bookmark.
PREVIEW_TOKEN_BYTES = 10

#: Mixed into the HMAC key, so a preview token is good for nothing else that
#: might one day be derived from the Pinboard token.
_PREVIEW_KEY_CONTEXT = b"pinboard-mcp-preview-v1\0"

#: A bridge response larger than this is refused unread. Two thumbnails and a
#: few strings come to well under a tenth of it.
MAX_RESPONSE_BYTES = 1_000_000

CACHE_ENTRIES = 500
#: An answer the bridge calls settled: a finished preview, and equally a page
#: with nothing to show or one it will never be able to read.
SETTLED_TTL_SECONDS = 6 * 60 * 60
#: An answer that may be better next time: thumbnails that failed to render,
#: a URL the bridge refused.
UNSETTLED_TTL_SECONDS = 10 * 60
#: One page's lookup timing out or failing on the bridge's side. Long enough
#: that every card showing the page does not wait out the timeout again,
#: short enough to be gone when the page is next looked at.
FAILED_LOOKUP_TTL_SECONDS = 60

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

#: The two formats the bridge makes, WebP for a page's picture and PNG for a
#: favicon, and nothing else: an SVG is a document, and can carry script.
_DATA_URI = re.compile(r"^data:image/(webp|png);base64,([A-Za-z0-9+/=]+)$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
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


def requested_items(items: Any) -> list[tuple[str, Any]]:
    """Check a ``get_link_previews`` request's shape and return its ``(url, token)`` pairs, one per URL, in order.

    Raises ``ValueError`` only for a request that is the wrong shape: not a
    list of one to eight objects, each with a string ``url``. Whether each
    URL is worth looking up, and whether its token is the right one, is
    decided per item (``is_web_url``, ``token_matches``), so one bad item
    costs its own row a preview and not the other seven theirs. A URL sent
    twice is kept once, with the token it first came with.
    """
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_URLS_PER_CALL:
        raise ValueError(f"Must provide 1-{MAX_URLS_PER_CALL} items")
    unique: dict[str, Any] = {}
    for item in items:
        url = item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str):
            raise ValueError("Every item must have a string url")
        unique.setdefault(url, item.get("token"))
    return list(unique.items())


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


def preview_key(pinboard_token: str) -> bytes:
    """The key preview tokens are made with, derived from the Pinboard token.

    Derived, so the server keeps no secret of its own and every worker of one
    account makes the same tokens, before and after a restart.
    """
    return hashlib.sha256(
        _PREVIEW_KEY_CONTEXT + pinboard_token.encode("utf-8")
    ).digest()


def preview_token(key: bytes, url: str) -> str:
    """The token that says this server returned ``url`` as a bookmark.

    ``get_link_previews`` is marked for the card alone, but a host that
    ignores that shows it to the model, and a model can be talked into calling
    a tool with anything. Without a check, the tool would make the bridge
    fetch any URL at all, with the user's token attached to the request for
    it. So each bookmark a tool returns carries this token, the card hands it
    back with the URL, and a preview is only ever fetched for a pair that
    matches. Nothing is remembered: any worker can check any card's request.

    The first ten bytes of HMAC-SHA256(key, url), as unpadded base64url.
    Never logged: it is derived from the Pinboard token.
    """
    digest = hmac.new(key, url.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:PREVIEW_TOKEN_BYTES]).rstrip(b"=").decode()


def token_matches(key: bytes, url: str, token: Any) -> bool:
    """Whether ``token`` is this server's token for ``url``, compared in constant time."""
    if not isinstance(token, str):
        return False
    try:
        offered = token.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(offered, preview_token(key, url).encode("ascii"))


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


def _png_size(data: bytes) -> tuple[int, int] | None:
    """A PNG's width and height, from its IHDR chunk, which is always first."""
    if len(data) < 24 or data[:8] != _PNG_SIGNATURE or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """A WebP's width and height, from whichever of its three headers it starts with."""
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    kind = data[12:16]
    if kind == b"VP8 ":
        # Lossy: a three-byte frame tag, a start code, then two 14-bit sizes.
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if kind == b"VP8L":
        # Lossless: a signature byte, then both sizes less one in 28 bits.
        if data[20] != 0x2F:
            return None
        (bits,) = struct.unpack("<I", data[21:25])
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        # Extended: flags, then the canvas's sizes less one in 24 bits each.
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def image_size(kind: str, data: bytes) -> tuple[int, int] | None:
    """The real width and height of a ``png`` or ``webp`` image, read from its bytes.

    ``None`` when the bytes are not an image of the kind they were declared
    to be. Only the header is read; nothing is decoded.
    """
    if kind == "png":
        return _png_size(data)
    if kind == "webp":
        return _webp_size(data)
    return None


def _clean_thumbnail(value: Any, max_length: int) -> dict[str, Any] | None:
    """One thumbnail as the card takes it, or ``None`` if any part of it is off.

    All or nothing: a card that drew an image without its dimensions would
    reflow as it decoded, so an image missing either is dropped whole. The
    dimensions claimed for it have to be the ones in its own header, so the
    card reserves the space the picture will really take, and the bytes have
    to be the kind of image the URI says they are.
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
        data = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        return None
    width = _clean_dimension(value.get("width"))
    height = _clean_dimension(value.get("height"))
    if width is None or height is None:
        return None
    if image_size(match.group(1), data) != (width, height):
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


#: What became of one lookup, beside its entry: whether the bridge was asked,
#: and if so whether the failure, if any, was the page's or the bridge's.
_FROM_CACHE = "cached"
_NOT_SENT = "not_sent"
_ANSWERED = "answered"
_LOOKUP_FAILED = "lookup_failed"
_UNREACHABLE = "unreachable"

_Lookup = tuple[dict[str, Any], str]


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
        # One cache, three lifetimes: an answer the bridge calls settled keeps
        # for hours, one that may be better next time for minutes, and one
        # page's failed lookup for a minute. Each value is the entry and the
        # lifetime it was stored with. A bridge that could not be reached at
        # all is never in here: that says nothing about any page.
        self._cache: TLRUCache = TLRUCache(
            maxsize=CACHE_ENTRIES,
            ttu=lambda _url, cached, now: now + cached[1],
            timer=timer,
        )
        self._semaphore: asyncio.Semaphore | None = None
        # Lookups under way, by URL: a second call asking about the same page
        # joins the first's lookup rather than starting another.
        self._in_flight: dict[str, asyncio.Task[_Lookup]] = {}
        # Until when the bridge's rate limit is taken to be in force.
        self._limited_until = 0.0
        self._unreachable_in_a_row = 0
        self._breaker_open_until = 0.0
        self._warned_unauthorised = False

    async def aclose(self) -> None:
        """Stop the lookups still running, wait for them to stop, and close the connection pool."""
        running = list(self._in_flight.values())
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
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
        lookups: dict[str, asyncio.Task[_Lookup]] = {}
        refused: set[str] = set()
        for url in urls:
            if not is_web_url(url) or (allowed is not None and url not in allowed):
                refused.add(url)
            elif url not in lookups:
                lookups[url] = self._lookup_task(url)
        if refused:
            logger.info("Link previews: refused %d of %d URLs", len(refused), len(urls))
        unfinished: set[asyncio.Task[_Lookup]] = set()
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
        finished = {
            url: task.result()
            for url, task in lookups.items()
            if task not in unfinished and not task.cancelled()
        }
        self._judge_wave(finished)

        previews: list[dict[str, Any]] = []
        for url in urls:
            if url not in lookups:
                previews.append(_entry(url, STATUS_ERROR))
            elif url in finished:
                # A copy: the cache holds the original.
                previews.append(dict(finished[url][0]))
            elif lookups[url] in unfinished:
                previews.append(_entry(url, STATUS_PENDING))
            else:
                # Cancelled under us: the server is shutting down.
                previews.append(_entry(url, STATUS_ERROR))
        result: dict[str, Any] = {"previews": previews}
        if any(entry["status"] == STATUS_RATE_LIMITED for entry in previews):
            remaining = math.ceil(self._limited_until - self._timer())
            result["retry_after_seconds"] = max(
                1, min(remaining, MAX_RETRY_AFTER_SECONDS)
            )
        return result

    def _judge_wave(self, finished: Mapping[str, _Lookup]) -> None:
        """Open the breaker when every lookup a call sent failed on the bridge's side.

        One page timing out is that page. All of several timing out together
        is the bridge, so it is left alone for a while, and those failures
        are taken back out of the cache: they were never about the pages.
        """
        sent = {
            url: outcome
            for url, (_entry_, outcome) in finished.items()
            if outcome in {_ANSWERED, _LOOKUP_FAILED, _UNREACHABLE}
        }
        if len(sent) < BREAKER_WAVE_SIZE:
            return
        if any(outcome != _LOOKUP_FAILED for outcome in sent.values()):
            return
        for url in sent:
            self._cache.pop(url, None)
        self._open_breaker()

    def _lookup_task(self, url: str) -> asyncio.Task[_Lookup]:
        task = self._in_flight.get(url)
        if task is None:
            task = asyncio.ensure_future(self._lookup(url))
            self._in_flight[url] = task
            task.add_done_callback(lambda _done: self._in_flight.pop(url, None))
        return task

    async def _lookup(self, url: str) -> _Lookup:
        """One URL's entry, from the cache or the bridge, and what became of the lookup. Never raises."""
        try:
            cached = self._cache.get(url)
            if cached is not None:
                return cached[0], _FROM_CACHE
            # Made on first use, so it belongs to the loop the server runs in.
            if self._semaphore is None:
                self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
            async with self._semaphore:
                now = self._timer()
                if now < self._limited_until:
                    return _entry(url, STATUS_RATE_LIMITED), _NOT_SENT
                if now < self._breaker_open_until:
                    return _entry(url, STATUS_ERROR), _NOT_SENT
                entry, lifetime, outcome = await self._request(url)
            if lifetime is not None:
                self._cache[url] = (entry, lifetime)
            return entry, outcome
        except Exception as error:  # noqa: BLE001 - one lookup must not fail its batch
            # Whatever it was (a response nested too deep to parse, a URL the
            # HTTP client will not send), it is this URL's problem alone. The
            # kind of failure only: the message can quote the request.
            logger.warning("Link preview lookup failed: %s", type(error).__name__)
            return _entry(url, STATUS_ERROR), _NOT_SENT

    def _unreachable(self) -> None:
        """Count a failure to reach the bridge at all, and open the breaker on a run of them."""
        self._unreachable_in_a_row += 1
        if self._unreachable_in_a_row >= BREAKER_FAILURES:
            self._open_breaker()

    def _open_breaker(self) -> None:
        self._unreachable_in_a_row = 0
        self._breaker_open_until = self._timer() + BREAKER_OPEN_SECONDS
        logger.warning(
            "The preview bridge is not answering; leaving it alone for %d seconds",
            BREAKER_OPEN_SECONDS,
        )

    async def _request(self, url: str) -> tuple[dict[str, Any], float | None, str]:
        """One lookup: the entry, how long to keep it (``None`` for not at all), and what became of it.

        Three kinds of failure, kept apart. The bridge could not be reached
        (no connection, no DNS, no TLS) or rejected the token: nothing is
        kept, and it counts towards the breaker. The bridge was reached and
        this lookup timed out or came back 5xx or unusable: that is kept
        against the URL for a minute, so the next card does not wait it out
        again, and it does not count. The bridge answered about the page:
        kept for as long as the answer is good for.
        """
        failed = _entry(url, STATUS_ERROR)
        try:
            status, body, retry_after = await asyncio.wait_for(
                self._get(url), timeout=self._request_timeout
            )
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as error:
            # The kind of failure only. The message can quote the request, and
            # the request is somebody's bookmark.
            logger.info("Link preview bridge unreachable: %s", type(error).__name__)
            self._unreachable()
            return failed, None, _UNREACHABLE
        except (asyncio.TimeoutError, httpx2.HTTPError, _BadResponse) as error:
            logger.info("Link preview lookup failed: %s", type(error).__name__)
            return failed, FAILED_LOOKUP_TTL_SECONDS, _LOOKUP_FAILED
        # Any response at all means the bridge can be reached.
        self._unreachable_in_a_row = 0
        if status == 429:
            self._limited_until = self._timer() + retry_after
            return _entry(url, STATUS_RATE_LIMITED), None, _ANSWERED
        if status == 400:
            # The bridge's answer for a URL it will not fetch. It might, with
            # a newer bridge, so not kept as long as a settled answer.
            return failed, UNSETTLED_TTL_SECONDS, _ANSWERED
        if status == 401:
            if not self._warned_unauthorised:
                self._warned_unauthorised = True
                logger.warning(
                    "The preview bridge rejected the Pinboard token (401); link previews will not load"
                )
            # No retry will change the token.
            self._open_breaker()
            return failed, None, _UNREACHABLE
        if status != 200:
            logger.info("Link preview lookup failed: HTTP %d", status)
            return failed, FAILED_LOOKUP_TTL_SECONDS, _LOOKUP_FAILED
        entry = sanitise_preview(url, body)
        if entry.get("retry"):
            # The card is about to ask again, and an answer from the cache
            # would be this one: a busy bridge's reply is not kept at all.
            return entry, None, _ANSWERED
        # "complete" is the bridge saying this is all there will be, whether
        # that is a preview, a page with nothing to show, or one it will
        # never be able to read.
        settled = _thumbnails_status(body) == THUMBNAILS_COMPLETE
        lifetime = SETTLED_TTL_SECONDS if settled else UNSETTLED_TTL_SECONDS
        return entry, lifetime, _ANSWERED

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

"""Link previews: the bridge client, what it lets through to the card, and the app-only tool.

The bridge is mocked with ``httpx2.MockTransport`` throughout; nothing here
touches the network.
"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest

from pinboard_mcp_server import previews, ui
from pinboard_mcp_server.previews import LinkPreviews

BRIDGE = "https://bridge.example"
TOKEN = "testuser:1234567890ABCDEF"
FIXTURES = Path(__file__).resolve().parent.parent / "cards" / "fixtures"

#: A real one-pixel PNG, so the base64 is something a decoder accepts.
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def bridge_payload(**overrides: Any) -> dict[str, Any]:
    """A whole, healthy ``/preview`` response; ``overrides`` replace top-level keys."""
    payload: dict[str, Any] = {
        "preview": {
            "url": "https://example.com/article",
            "title": "An article",
            "description": "What the article is about.",
            "imageUrl": "https://cdn.example.com/og.png",
            "siteName": "Example",
            "siteHandle": "@example",
            "siteDomain": "example.com",
            "cardType": "summary_large_image",
            "themeColor": "#1A2B3C",
            "faviconUrl": "https://example.com/favicon.ico",
            "fetchedAt": "2026-11-01T10:00:00Z",
            "status": "ok",
        },
        "previewStatus": "fresh",
        "thumbnails": {
            "image": {"dataUri": PIXEL, "width": 240, "height": 126},
            "favicon": {"dataUri": PIXEL, "width": 32, "height": 32},
        },
    }
    payload.update(overrides)
    return payload


def with_preview(**fields: Any) -> dict[str, Any]:
    payload = bridge_payload()
    payload["preview"].update(fields)
    return payload


def with_image(**fields: Any) -> dict[str, Any]:
    payload = bridge_payload()
    payload["thumbnails"]["image"].update(fields)
    return payload


class Bridge:
    """A stand-in bridge: answers from ``respond`` and remembers what it was asked."""

    def __init__(self, respond: Any = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.respond = respond or (
            lambda request: httpx2.Response(200, json=bridge_payload())
        )
        self.in_flight = 0
        self.most_in_flight = 0
        self.hold: asyncio.Event | None = None

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            if self.hold is not None:
                await self.hold.wait()
            else:
                await asyncio.sleep(0)
            response = self.respond(request)
            if asyncio.iscoroutine(response):
                response = await response
            return response
        finally:
            self.in_flight -= 1

    def asked(self) -> list[str]:
        return [request.url.params["url"] for request in self.requests]

    def client(self, **options: Any) -> LinkPreviews:
        return LinkPreviews(
            BRIDGE, TOKEN, transport=httpx2.MockTransport(self.handle), **options
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestBridgeUrl:
    @pytest.mark.parametrize("value", ["", "   "])
    def test_unset_or_empty_leaves_the_feature_off(self, value, caplog):
        with caplog.at_level("WARNING"):
            assert previews.bridge_url({previews.BRIDGE_URL_ENV_VAR: value}) is None
            assert previews.bridge_url({}) is None
        assert caplog.text == ""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("https://bridge.example", "https://bridge.example"),
            ("https://bridge.example/", "https://bridge.example"),
            (" https://bridge.example/api/ ", "https://bridge.example/api"),
            ("http://localhost:3000", "http://localhost:3000"),
            ("http://127.0.0.1:3000/", "http://127.0.0.1:3000"),
            ("http://[::1]:3000", "http://[::1]:3000"),
        ],
    )
    def test_an_https_or_local_url_is_taken(self, value, expected):
        assert previews.bridge_url({previews.BRIDGE_URL_ENV_VAR: value}) == expected

    @pytest.mark.parametrize(
        "value",
        [
            "http://bridge.example",
            "http://localhost.evil.example",
            "ftp://bridge.example",
            "bridge.example",
            "https://",
            "https://user:secret@bridge.example",
            "https://bridge.example/?token=secret",
            "https://bridge.example/#fragment",
            "https://bridge.example:port",
        ],
    )
    def test_anything_else_is_ignored_with_a_warning(self, value, caplog):
        with caplog.at_level("WARNING", logger="pinboard_mcp_server.previews"):
            assert previews.bridge_url({previews.BRIDGE_URL_ENV_VAR: value}) is None

        assert previews.BRIDGE_URL_ENV_VAR in caplog.text
        # The reason, not the value: it may carry a credential.
        assert "secret" not in caplog.text

    def test_reads_the_process_environment_by_default(self, monkeypatch):
        monkeypatch.setenv(previews.BRIDGE_URL_ENV_VAR, "https://bridge.example")
        assert previews.bridge_url() == "https://bridge.example"


class TestRequestedUrls:
    def test_keeps_order_and_drops_repeats(self):
        assert previews.requested_urls(
            ["https://b.example/", "http://a.example/x", "https://b.example/"]
        ) == ["https://b.example/", "http://a.example/x"]

    @pytest.mark.parametrize(
        "urls",
        [
            [],
            [f"https://example.com/{n}" for n in range(9)],
            "https://example.com",
            None,
            {"url": "https://example.com"},
            [42],
            [None],
            ["https://ok.example", ["https://nested.example"]],
        ],
    )
    def test_rejects_a_request_of_the_wrong_shape(self, urls):
        with pytest.raises(ValueError):
            previews.requested_urls(urls)

    def test_takes_exactly_eight(self):
        urls = [f"https://example.com/{n}" for n in range(8)]
        assert previews.requested_urls(urls) == urls

    def test_does_not_judge_the_urls_themselves(self):
        # One bad URL is its own row's problem (an `error` entry from
        # fetch_many), not a reason to refuse the other seven.
        urls = ["https://ok.example/", "javascript:alert(1)", ""]
        assert previews.requested_urls(urls) == urls


class TestIsWebUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com",
            "http://example.com/a?b=c#d",
            "HTTPS://EXAMPLE.COM/",
            "https://example.com/" + "a" * (2048 - len("https://example.com/")),
        ],
    )
    def test_takes_an_absolute_http_or_https_url(self, url):
        assert previews.is_web_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "ftp://example.com/",
            "//example.com/protocol-relative",
            "/relative",
            "example.com",
            "https://",
            "https:///path-only",
            " https://example.com",
            "https://example.com ",
            "https://example.com/a b",
            "https://example.com/a\nb",
            "https://[not-an-address/",
            "",
            "https://example.com/" + "a" * 2100,
        ],
    )
    def test_refuses_anything_else(self, url):
        assert not previews.is_web_url(url)


class TestOfferedUrls:
    def test_knows_what_it_was_given_and_nothing_else(self):
        offered = previews.OfferedUrls()
        offered.add(["https://a.example/", "https://b.example/"])

        assert "https://a.example/" in offered
        assert "https://b.example/" in offered
        assert "https://a.example" not in offered
        assert "https://c.example/" not in offered
        assert None not in offered

    def test_ignores_what_is_not_a_string(self):
        offered = previews.OfferedUrls()
        offered.add([None, 42, "https://a.example/"])

        assert len(offered) == 1

    def test_is_bounded_and_forgets_the_least_recently_used(self):
        offered = previews.OfferedUrls(maxsize=3)
        offered.add(["https://1.example/", "https://2.example/", "https://3.example/"])
        # Being asked about counts as use.
        assert "https://1.example/" in offered

        offered.add(["https://4.example/"])

        assert len(offered) == 3
        assert "https://1.example/" in offered
        assert "https://2.example/" not in offered
        assert "https://4.example/" in offered

    def test_holds_five_thousand_by_default(self):
        offered = previews.OfferedUrls()
        offered.add(f"https://example.com/{n}" for n in range(5100))

        assert len(offered) == 5000


class TestSanitisePreview:
    URL = "https://example.com/as-requested"

    def test_a_healthy_response_becomes_the_card_s_shape(self):
        assert previews.sanitise_preview(self.URL, bridge_payload()) == {
            "url": self.URL,
            "status": "ok",
            "title": "An article",
            "description": "What the article is about.",
            "site_name": "Example",
            "theme_color": "#1a2b3c",
            "image": {"data_uri": PIXEL, "width": 240, "height": 126},
            "favicon": {"data_uri": PIXEL, "width": 32, "height": 32},
        }

    def test_remote_image_urls_never_reach_the_card(self):
        entry = previews.sanitise_preview(self.URL, bridge_payload())

        assert "cdn.example.com" not in json.dumps(entry)
        assert "favicon.ico" not in json.dumps(entry)

    def test_the_url_is_the_one_requested_not_the_bridge_s(self):
        assert previews.sanitise_preview(self.URL, bridge_payload())["url"] == self.URL

    def test_strings_are_trimmed_and_put_on_one_line(self):
        entry = previews.sanitise_preview(
            self.URL,
            with_preview(
                title="  A title\n\tover  lines\x00 ",
                description="  spaced \x1b[31m out ",
                siteName=" Site ",
            ),
        )

        assert entry["title"] == "A title over lines"
        assert entry["description"] == "spaced [31m out"
        assert entry["site_name"] == "Site"

    @pytest.mark.parametrize(
        ("field", "key", "limit"),
        [
            ("title", "title", 300),
            ("description", "description", 500),
            ("siteName", "site_name", 100),
        ],
    )
    def test_strings_are_capped(self, field, key, limit):
        entry = previews.sanitise_preview(self.URL, with_preview(**{field: "x" * 5000}))

        assert len(entry[key]) == limit
        assert entry[key].endswith("…")

    @pytest.mark.parametrize("value", [None, "", "   ", 42, ["a"], {"a": 1}])
    def test_a_string_that_is_not_one_drops_out(self, value):
        entry = previews.sanitise_preview(self.URL, with_preview(title=value))

        assert entry["title"] is None
        assert entry["status"] == "ok"

    def test_markup_is_left_as_text_for_the_card_to_escape(self):
        title = "<img src=x onerror=alert(1)>"
        entry = previews.sanitise_preview(self.URL, with_preview(title=title))

        assert entry["title"] == title

    @pytest.mark.parametrize(
        "colour", ["#fff", "#FFFFFF", " #0a0B0c "], ids=["short", "long", "padded"]
    )
    def test_a_hex_theme_colour_is_kept_lower_cased(self, colour):
        entry = previews.sanitise_preview(self.URL, with_preview(themeColor=colour))

        assert entry["theme_color"] == colour.strip().lower()

    @pytest.mark.parametrize(
        "colour",
        [
            "red",
            "#ffff",
            "#12345g",
            "rgb(0,0,0)",
            "#fff;background:url(https://evil.example)",
            "url(javascript:alert(1))",
            "",
            None,
            0xFFFFFF,
        ],
    )
    def test_any_other_theme_colour_is_dropped(self, colour):
        entry = previews.sanitise_preview(self.URL, with_preview(themeColor=colour))

        assert entry["theme_color"] is None

    @pytest.mark.parametrize("mime", ["webp", "png", "jpeg"])
    def test_raster_data_uris_are_kept(self, mime):
        uri = f"data:image/{mime};base64,AAAA"
        entry = previews.sanitise_preview(self.URL, with_image(dataUri=uri))

        assert entry["image"] == {"data_uri": uri, "width": 240, "height": 126}

    @pytest.mark.parametrize(
        "uri",
        [
            "https://evil.example/tracker.png",
            "//evil.example/tracker.png",
            "javascript:alert(1)",
            "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
            "data:image/gif;base64,AAAA",
            "data:text/html;base64,AAAA",
            "data:image/png,AAAA",
            "data:image/png;base64,",
            "data:image/png;base64,AAAA AAAA",
            "data:image/png;base64,AAAA\n",
            'data:image/png;base64,AAAA"><script>',
            "data:image/png;base64,AAA",
            "DATA:IMAGE/PNG;BASE64,AAAA",
            " data:image/png;base64,AAAA",
            "data:image/png;charset=utf-8;base64,AAAA",
            "",
            None,
            42,
        ],
    )
    def test_any_other_image_is_dropped(self, uri):
        entry = previews.sanitise_preview(self.URL, with_image(dataUri=uri))

        assert entry["image"] is None
        # One bad image does not cost the row everything else.
        assert entry["status"] == "ok"
        assert entry["favicon"] is not None

    def test_an_oversized_image_is_dropped(self):
        prefix = "data:image/png;base64,"
        # Whole base64 quads, so only the length separates the two.
        quads = (previews.MAX_IMAGE_DATA_URI_LENGTH - len(prefix)) // 4
        at_limit = prefix + "AAAA" * quads
        over = prefix + "AAAA" * (quads + 1)
        assert len(at_limit) <= previews.MAX_IMAGE_DATA_URI_LENGTH < len(over)

        kept = previews.sanitise_preview(self.URL, with_image(dataUri=at_limit))
        dropped = previews.sanitise_preview(self.URL, with_image(dataUri=over))

        assert kept["image"] is not None
        assert dropped["image"] is None

    def test_a_favicon_has_a_smaller_ceiling(self):
        payload = bridge_payload()
        big = "data:image/png;base64," + "AAAA" * (
            previews.MAX_FAVICON_DATA_URI_LENGTH // 4
        )
        assert len(big) > previews.MAX_FAVICON_DATA_URI_LENGTH
        assert len(big) < previews.MAX_IMAGE_DATA_URI_LENGTH
        payload["thumbnails"]["favicon"]["dataUri"] = big

        entry = previews.sanitise_preview(self.URL, payload)

        assert entry["favicon"] is None
        assert entry["image"] is not None

    @pytest.mark.parametrize(
        "dimensions",
        [
            {"width": 0},
            {"width": -5},
            {"height": 1025},
            {"width": 12.5},
            {"width": "240"},
            {"width": True},
            {"height": None},
        ],
    )
    def test_an_image_without_usable_dimensions_is_dropped(self, dimensions):
        entry = previews.sanitise_preview(self.URL, with_image(**dimensions))

        assert entry["image"] is None

    def test_the_largest_allowed_dimensions_pass(self):
        entry = previews.sanitise_preview(self.URL, with_image(width=1024, height=1024))

        assert entry["image"]["width"] == 1024

    def test_an_older_bridge_without_thumbnails_still_gives_text(self):
        payload = bridge_payload()
        del payload["thumbnails"]

        entry = previews.sanitise_preview(self.URL, payload)

        assert entry["status"] == "ok"
        assert entry["title"] == "An article"
        assert entry["image"] is None
        assert entry["favicon"] is None

    @pytest.mark.parametrize("thumbnails", [None, [], "nope", {"image": None}])
    def test_malformed_thumbnails_are_treated_as_none(self, thumbnails):
        entry = previews.sanitise_preview(
            self.URL, bridge_payload(thumbnails=thumbnails)
        )

        assert entry["status"] == "ok"
        assert entry["image"] is None

    def test_no_preview_is_no_data(self):
        payload = bridge_payload(preview=None, previewStatus="no_data", thumbnails=None)

        assert previews.sanitise_preview(self.URL, payload) == {
            "url": self.URL,
            "status": "no_data",
            "title": None,
            "description": None,
            "site_name": None,
            "theme_color": None,
            "image": None,
            "favicon": None,
        }

    def test_no_preview_with_an_error_is_an_error(self):
        payload = bridge_payload(
            preview=None,
            previewStatus="error",
            previewError="upstream timed out",
            thumbnails=None,
        )

        assert previews.sanitise_preview(self.URL, payload)["status"] == "error"

    def test_a_preview_with_nothing_drawable_is_no_data(self):
        payload = bridge_payload(
            preview={"url": "https://example.com", "themeColor": "#fff"},
            thumbnails=None,
        )

        entry = previews.sanitise_preview(self.URL, payload)

        assert entry["status"] == "no_data"
        assert entry["theme_color"] is None

    def test_a_favicon_alone_is_worth_drawing(self):
        payload = bridge_payload(preview=None, previewStatus="no_data")
        payload["thumbnails"]["image"] = None

        entry = previews.sanitise_preview(self.URL, payload)

        assert entry["status"] == "ok"
        assert entry["favicon"] is not None

    @pytest.mark.parametrize("payload", [None, [], "text", 7])
    def test_a_body_that_is_not_an_object_is_an_error(self, payload):
        assert previews.sanitise_preview(self.URL, payload)["status"] == "error"

    @pytest.mark.parametrize(
        "status", ["complete", "transient", "unavailable", "something-new", None, 3]
    )
    def test_only_a_busy_bridge_asks_the_card_to_retry(self, status):
        entry = previews.sanitise_preview(
            self.URL, bridge_payload(thumbnailsStatus=status)
        )

        assert "retry" not in entry
        assert entry["status"] == "ok"

    def test_a_busy_bridge_s_entry_keeps_what_arrived_and_says_retry(self):
        payload = bridge_payload(thumbnailsStatus="busy")
        payload["thumbnails"]["image"] = None

        entry = previews.sanitise_preview(self.URL, payload)

        assert entry["retry"] is True
        assert entry["status"] == "ok"
        assert entry["title"] == "An article"
        assert entry["image"] is None
        assert entry["favicon"] is not None

    def test_a_busy_bridge_with_no_metadata_is_still_worth_a_retry(self):
        payload = bridge_payload(
            preview=None,
            previewStatus="no_data",
            thumbnails=None,
            thumbnailsStatus="busy",
        )

        entry = previews.sanitise_preview(self.URL, payload)

        assert entry["status"] == "no_data"
        assert entry["retry"] is True


class TestFetchMany:
    async def test_asks_the_bridge_as_its_contract_says(self):
        bridge = Bridge()
        client = bridge.client()

        result = await client.fetch_many(["https://example.com/a%20b?x=1&y=2"])

        (request,) = bridge.requests
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "bridge.example"
        assert request.url.path == "/preview"
        assert dict(request.url.params) == {
            "url": "https://example.com/a%20b?x=1&y=2",
            "thumbnails": "1",
        }
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert result["previews"][0]["status"] == "ok"
        assert result["previews"][0]["url"] == "https://example.com/a%20b?x=1&y=2"
        assert "retry_after_seconds" not in result
        await client.aclose()

    async def test_a_base_url_with_a_path_keeps_it(self):
        bridge = Bridge()
        client = LinkPreviews(
            "https://bridge.example/api/",
            TOKEN,
            transport=httpx2.MockTransport(bridge.handle),
        )

        await client.fetch_many(["https://example.com"])

        assert bridge.requests[0].url.path == "/api/preview"

    async def test_entries_come_back_in_request_order(self):
        def respond(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200, json=with_preview(title=request.url.params["url"])
            )

        urls = [f"https://example.com/{n}" for n in range(8)]
        result = await Bridge(respond).client().fetch_many(urls)

        assert [entry["url"] for entry in result["previews"]] == urls
        assert [entry["title"] for entry in result["previews"]] == urls

    async def test_a_second_ask_is_served_from_the_cache(self):
        bridge = Bridge()
        client = bridge.client()

        first = await client.fetch_many(["https://example.com/a"])
        second = await client.fetch_many(["https://example.com/a"])

        assert len(bridge.requests) == 1
        assert second == first

    async def test_a_cached_entry_cannot_be_changed_by_its_reader(self):
        client = Bridge().client()

        first = await client.fetch_many(["https://example.com/a"])
        first["previews"][0]["title"] = "scribbled on"
        second = await client.fetch_many(["https://example.com/a"])

        assert second["previews"][0]["title"] == "An article"

    async def test_a_preview_is_kept_for_six_hours(self):
        clock = Clock()
        bridge = Bridge()
        client = bridge.client(timer=clock)

        await client.fetch_many(["https://example.com/a"])
        clock.now += 6 * 60 * 60 - 1
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 1

        clock.now += 2
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 2

    @pytest.mark.parametrize(
        "response",
        [
            httpx2.Response(200, json={"preview": None, "previewStatus": "no_data"}),
            httpx2.Response(
                200,
                json={
                    "preview": None,
                    "previewStatus": "error",
                    "thumbnailsStatus": "transient",
                },
            ),
            httpx2.Response(400, json={"error": "unreachable"}),
        ],
        ids=["no_data", "page-fetch-failed", "bad-url"],
    )
    async def test_a_page_level_miss_is_kept_for_ten_minutes_only(self, response):
        clock = Clock()
        bridge = Bridge(lambda request: response)
        client = bridge.client(timer=clock)

        first = await client.fetch_many(["https://example.com/a"])
        assert first["previews"][0]["status"] in {"no_data", "error"}
        clock.now += 10 * 60 - 1
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 1

        clock.now += 2
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 2

    async def test_an_older_bridge_without_thumbnails_status_is_kept_six_hours(self):
        clock = Clock()
        bridge = Bridge()
        client = bridge.client(timer=clock)
        assert "thumbnailsStatus" not in bridge_payload()

        await client.fetch_many(["https://example.com/a"])
        clock.now += 5 * 60 * 60
        await client.fetch_many(["https://example.com/a"])

        assert len(bridge.requests) == 1

    @pytest.mark.parametrize("status", ["transient", "unavailable", "something-new"])
    async def test_unfinished_thumbnails_are_asked_about_again_in_ten_minutes(
        self, status
    ):
        clock = Clock()
        payload = bridge_payload(thumbnailsStatus=status)
        payload["thumbnails"]["image"] = None
        bridge = Bridge(lambda request: httpx2.Response(200, json=payload))
        client = bridge.client(timer=clock)

        first = await client.fetch_many(["https://example.com/a"])
        # The text and the thumbnail that did arrive are served as usual.
        assert first["previews"][0]["status"] == "ok"
        assert first["previews"][0]["title"] == "An article"
        assert first["previews"][0]["favicon"] is not None
        assert "retry" not in first["previews"][0]

        clock.now += 10 * 60 - 1
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 1

        bridge.respond = lambda request: httpx2.Response(
            200, json=bridge_payload(thumbnailsStatus="complete")
        )
        clock.now += 2
        second = await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 2
        assert second["previews"][0]["image"] is not None

        # And now that they are complete, the long lifetime applies.
        clock.now += 5 * 60 * 60
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 2

    async def test_a_busy_bridge_s_answer_is_not_cached_so_the_retry_is_real(self):
        payload = bridge_payload(thumbnailsStatus="busy", thumbnails=None)
        bridge = Bridge(lambda request: httpx2.Response(200, json=payload))
        client = bridge.client()

        first = await client.fetch_many(["https://example.com/a"])
        assert first["previews"][0]["retry"] is True
        assert first["previews"][0]["title"] == "An article"
        assert "retry_after_seconds" not in first

        bridge.respond = lambda request: httpx2.Response(
            200, json=bridge_payload(thumbnailsStatus="complete")
        )
        second = await client.fetch_many(["https://example.com/a"])

        assert len(bridge.requests) == 2
        assert "retry" not in second["previews"][0]
        assert second["previews"][0]["image"] is not None

    async def test_the_cache_holds_five_hundred_entries(self):
        client = Bridge().client()

        for start in range(0, 504, 8):
            await client.fetch_many(
                [f"https://example.com/{n}" for n in range(start, start + 8)]
            )

        assert len(client._cache) == 500

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502, 301])
    async def test_a_failed_lookup_is_an_error_entry(self, status):
        bridge = Bridge(
            lambda request: httpx2.Response(
                status, headers={"Location": "https://elsewhere.example/preview"}
            )
        )

        result = await bridge.client().fetch_many(["https://example.com/a"])

        assert result["previews"][0]["status"] == "error"
        # In particular a redirect is not followed, so the token goes nowhere else.
        assert len(bridge.requests) == 1

    async def test_a_transport_failure_is_an_error_entry(self):
        def respond(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("connection refused")

        result = await Bridge(respond).client().fetch_many(["https://example.com/a"])

        assert result["previews"][0]["status"] == "error"

    async def test_a_body_that_is_not_json_is_an_error_entry(self):
        bridge = Bridge(lambda request: httpx2.Response(200, text="<html>oops</html>"))

        result = await bridge.client().fetch_many(["https://example.com/a"])

        assert result["previews"][0]["status"] == "error"

    async def test_an_oversized_body_is_refused(self):
        huge = json.dumps(with_preview(title="x" * 1_100_000))
        bridge = Bridge(lambda request: httpx2.Response(200, text=huge))

        result = await bridge.client().fetch_many(["https://example.com/a"])

        assert result["previews"][0]["status"] == "error"

    async def test_an_oversized_body_is_refused_even_when_it_does_not_say_so(self):
        async def chunks():
            for _ in range(11):
                yield b" " * 100_000

        bridge = Bridge(lambda request: httpx2.Response(200, content=chunks()))

        result = await bridge.client().fetch_many(["https://example.com/a"])

        assert result["previews"][0]["status"] == "error"

    async def test_a_slow_lookup_times_out_as_an_error(self):
        async def respond(request: httpx2.Request) -> httpx2.Response:
            await asyncio.sleep(5)
            return httpx2.Response(200, json=bridge_payload())

        client = Bridge(respond).client(request_timeout=0.05)

        result = await client.fetch_many(["https://example.com/a"])

        assert result["previews"][0]["status"] == "error"

    async def test_a_call_answers_in_time_and_marks_what_is_still_running_pending(
        self,
    ):
        release = asyncio.Event()

        async def respond(request: httpx2.Request) -> httpx2.Response:
            if "slow" in request.url.params["url"]:
                await release.wait()
            return httpx2.Response(200, json=bridge_payload())

        bridge = Bridge(respond)
        client = bridge.client(batch_timeout=0.05)

        result = await client.fetch_many(
            ["https://example.com/fast", "https://example.com/slow"]
        )

        assert [entry["status"] for entry in result["previews"]] == ["ok", "pending"]
        assert result["previews"][1] == {
            "url": "https://example.com/slow",
            "status": "pending",
            "title": None,
            "description": None,
            "site_name": None,
            "theme_color": None,
            "image": None,
            "favicon": None,
        }

        # The lookup was not cut off. Asking again while it runs joins it
        # rather than sending a second request...
        again = asyncio.ensure_future(client.fetch_many(["https://example.com/slow"]))
        await asyncio.sleep(0)
        release.set()
        assert (await again)["previews"][0]["status"] == "ok"
        assert bridge.asked().count("https://example.com/slow") == 1

        # ...and its answer went into the cache.
        await client.fetch_many(["https://example.com/slow"])
        assert bridge.asked().count("https://example.com/slow") == 1

    async def test_time_spent_queued_is_not_charged_to_a_lookup(self):
        # Sixteen lookups, eight at a time, each taking most of its own
        # timeout: the second eight wait longer than that in the queue, and
        # still succeed, because the clock starts when a lookup is sent.
        async def respond(request: httpx2.Request) -> httpx2.Response:
            await asyncio.sleep(0.15)
            return httpx2.Response(200, json=bridge_payload())

        client = Bridge(respond).client(request_timeout=0.25, batch_timeout=5)

        first, second = await asyncio.gather(
            client.fetch_many([f"https://example.com/{n}" for n in range(8)]),
            client.fetch_many([f"https://example.org/{n}" for n in range(8)]),
        )

        assert all(
            entry["status"] == "ok"
            for result in (first, second)
            for entry in result["previews"]
        )

    async def test_the_defaults_let_one_call_finish_inside_its_deadline(self):
        # A whole call goes out in one wave, and one wave's timeout fits in
        # the deadline with room to spare; the deadline in turn fits inside
        # the fifteen seconds the card waits.
        assert previews.MAX_CONCURRENCY >= previews.MAX_URLS_PER_CALL
        assert previews.REQUEST_TIMEOUT_SECONDS < previews.BATCH_TIMEOUT_SECONDS
        # Longer than the bridge's own seven-second deadline, so its slowest
        # honest answer still arrives, and one wave of the bridge's eight jobs.
        assert previews.REQUEST_TIMEOUT_SECONDS == 8.5
        assert previews.MAX_CONCURRENCY == previews.MAX_URLS_PER_CALL == 8
        assert previews.BATCH_TIMEOUT_SECONDS < 15

    async def test_a_pending_lookup_that_then_fails_is_not_left_dangling(self):
        release = asyncio.Event()

        async def respond(request: httpx2.Request) -> httpx2.Response:
            await release.wait()
            raise httpx2.ConnectError("gone")

        client = Bridge(respond).client(batch_timeout=0.05)

        first = await client.fetch_many(["https://example.com/a"])
        assert first["previews"][0]["status"] == "pending"
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)

        assert client._in_flight == {}

    async def test_closing_cancels_lookups_still_running(self):
        async def respond(request: httpx2.Request) -> httpx2.Response:
            await asyncio.sleep(30)
            return httpx2.Response(200, json=bridge_payload())

        client = Bridge(respond).client(batch_timeout=0.05)
        await client.fetch_many(["https://example.com/a"])
        (task,) = client._in_flight.values()

        await client.aclose()
        await asyncio.gather(task, return_exceptions=True)

        assert task.cancelled()

    async def test_a_whole_call_goes_out_at_once_and_a_second_call_queues(self):
        bridge = Bridge()
        bridge.hold = asyncio.Event()
        client = bridge.client()

        first = asyncio.ensure_future(
            client.fetch_many([f"https://example.com/{n}" for n in range(8)])
        )
        # A second call shares the same bound.
        second = asyncio.ensure_future(
            client.fetch_many([f"https://example.org/{n}" for n in range(8)])
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert bridge.in_flight == 8

        bridge.hold.set()
        results = await asyncio.gather(first, second)

        assert bridge.most_in_flight == 8
        assert len(bridge.requests) == 16
        assert all(
            entry["status"] == "ok"
            for result in results
            for entry in result["previews"]
        )

    async def test_two_calls_about_the_same_url_share_one_lookup(self):
        bridge = Bridge()
        bridge.hold = asyncio.Event()
        client = bridge.client()

        first = asyncio.ensure_future(client.fetch_many(["https://example.com/a"]))
        second = asyncio.ensure_future(client.fetch_many(["https://example.com/a"]))
        for _ in range(5):
            await asyncio.sleep(0)
        bridge.hold.set()
        results = await asyncio.gather(first, second)

        assert len(bridge.requests) == 1
        assert [r["previews"][0]["status"] for r in results] == ["ok", "ok"]


class TestRefusedUrls:
    """A URL the server will not look up costs its own entry, never the batch."""

    async def test_a_url_that_is_not_a_web_url_is_an_error_entry_and_is_not_sent(self):
        bridge = Bridge()

        result = await bridge.client().fetch_many(
            [
                "https://example.com/a",
                "javascript:alert(1)",
                "https://example.com/" + "a" * 2100,
                "https://example.com/b",
            ]
        )

        assert [entry["status"] for entry in result["previews"]] == [
            "ok",
            "error",
            "error",
            "ok",
        ]
        assert [entry["url"] for entry in result["previews"]][1] == (
            "javascript:alert(1)"
        )
        assert bridge.asked() == ["https://example.com/a", "https://example.com/b"]

    async def test_only_offered_urls_reach_the_bridge(self):
        bridge = Bridge()
        offered = previews.OfferedUrls()
        offered.add(["https://bookmarked.example/a"])

        result = await bridge.client().fetch_many(
            [
                "https://bookmarked.example/a",
                "http://169.254.169.254/latest/meta-data/",
                "https://attacker.example/collect?secret=1",
            ],
            allowed=offered,
        )

        assert [entry["status"] for entry in result["previews"]] == [
            "ok",
            "error",
            "error",
        ]
        assert bridge.asked() == ["https://bookmarked.example/a"]

    async def test_a_refused_url_is_not_cached_as_a_verdict(self):
        bridge = Bridge()
        client = bridge.client()
        offered = previews.OfferedUrls()

        await client.fetch_many(["https://example.com/a"], allowed=offered)
        offered.add(["https://example.com/a"])
        result = await client.fetch_many(["https://example.com/a"], allowed=offered)

        assert result["previews"][0]["status"] == "ok"

    async def test_a_call_with_nothing_to_look_up_answers_at_once(self):
        bridge = Bridge()

        result = await bridge.client().fetch_many(
            ["ftp://example.com/"], allowed=previews.OfferedUrls()
        )

        assert result == {
            "previews": [
                {
                    "url": "ftp://example.com/",
                    "status": "error",
                    "title": None,
                    "description": None,
                    "site_name": None,
                    "theme_color": None,
                    "image": None,
                    "favicon": None,
                }
            ]
        }
        assert bridge.requests == []


class TestOneLookupCannotFailTheBatch:
    async def test_json_nested_too_deep_to_parse_is_one_error_entry(self, caplog):
        nested = "[" * 100_000 + "]" * 100_000

        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.url.params["url"].endswith("/deep"):
                return httpx2.Response(200, text=nested)
            return httpx2.Response(200, json=bridge_payload())

        with caplog.at_level("INFO", logger="pinboard_mcp_server.previews"):
            result = (
                await Bridge(respond)
                .client()
                .fetch_many(["https://example.com/a", "https://example.com/deep"])
            )

        assert [entry["status"] for entry in result["previews"]] == ["ok", "error"]
        assert "deep" not in caplog.text
        assert TOKEN not in caplog.text

    @pytest.mark.parametrize(
        "error",
        [
            httpx2.InvalidURL("no"),
            RecursionError("maximum recursion depth exceeded"),
            RuntimeError("anything at all"),
            KeyError("x"),
        ],
        ids=lambda error: type(error).__name__,
    )
    async def test_an_unexpected_exception_is_one_error_entry(self, error, caplog):
        def respond(request: httpx2.Request) -> httpx2.Response:
            if request.url.params["url"].endswith("/bad"):
                raise error
            return httpx2.Response(200, json=bridge_payload())

        bridge = Bridge(respond)
        client = bridge.client()
        with caplog.at_level("INFO", logger="pinboard_mcp_server.previews"):
            result = await client.fetch_many(
                ["https://example.com/bad", "https://example.com/good"]
            )

        assert [entry["status"] for entry in result["previews"]] == ["error", "ok"]
        # The kind of failure is logged; the bookmark and the token are not.
        assert type(error).__name__ in caplog.text
        assert "example.com" not in caplog.text
        assert TOKEN not in caplog.text
        # And it is not held against the URL.
        bridge.respond = lambda request: httpx2.Response(200, json=bridge_payload())
        again = await client.fetch_many(["https://example.com/bad"])
        assert again["previews"][0]["status"] == "ok"


class TestBridgeFailuresAreNotThePagesFault:
    """A timeout, a refused connection, a 401 or a 5xx says nothing about the page."""

    @staticmethod
    def failing(kind: str) -> Any:
        async def respond(request: httpx2.Request) -> httpx2.Response:
            if kind == "timeout":
                await asyncio.sleep(5)
            if kind == "connect":
                raise httpx2.ConnectError("connection refused")
            if kind == "not-json":
                return httpx2.Response(200, text="<html>bad gateway</html>")
            return httpx2.Response(int(kind))

        return respond

    @pytest.mark.parametrize(
        "kind", ["timeout", "connect", "500", "502", "503", "301", "not-json"]
    )
    async def test_a_transport_failure_is_not_cached_against_the_url(self, kind):
        bridge = Bridge(self.failing(kind))
        client = bridge.client(request_timeout=0.05)

        first = await client.fetch_many(["https://example.com/a"])
        assert first["previews"][0]["status"] == "error"

        bridge.respond = lambda request: httpx2.Response(200, json=bridge_payload())
        second = await client.fetch_many(["https://example.com/a"])

        assert second["previews"][0]["status"] == "ok"
        assert len(bridge.requests) == 2

    async def test_a_run_of_failures_opens_the_breaker_for_thirty_seconds(self, caplog):
        clock = Clock()
        bridge = Bridge(self.failing("503"))
        client = bridge.client(timer=clock)

        with caplog.at_level("WARNING", logger="pinboard_mcp_server.previews"):
            for n in range(3):
                await client.fetch_many([f"https://example.com/{n}"])
        assert len(bridge.requests) == 3
        assert "not answering" in caplog.text

        # Open: nothing is sent, the answer is immediate, and nothing is cached.
        bridge.respond = lambda request: httpx2.Response(200, json=bridge_payload())
        clock.now += 29
        result = await client.fetch_many(
            ["https://example.com/0", "https://example.com/new"]
        )
        assert [entry["status"] for entry in result["previews"]] == ["error", "error"]
        assert len(bridge.requests) == 3

        clock.now += 2
        result = await client.fetch_many(
            ["https://example.com/0", "https://example.com/new"]
        )
        assert [entry["status"] for entry in result["previews"]] == ["ok", "ok"]

    async def test_a_success_between_failures_keeps_the_breaker_shut(self):
        bridge = Bridge()
        client = bridge.client()
        answers = iter([503, 503, 200, 503, 503, 200])

        def respond(request: httpx2.Request) -> httpx2.Response:
            status = next(answers)
            return httpx2.Response(status, json=bridge_payload())

        bridge.respond = respond
        for n in range(6):
            await client.fetch_many([f"https://example.com/{n}"])

        assert len(bridge.requests) == 6

    async def test_a_400_is_about_the_page_and_does_not_count(self):
        bridge = Bridge(lambda request: httpx2.Response(400))
        client = bridge.client()

        for n in range(5):
            await client.fetch_many([f"https://example.com/{n}"])

        assert len(bridge.requests) == 5

    async def test_a_page_that_failed_for_good_is_about_the_page_too(self):
        # The bridge reports a page it will never read (not HTML, a 4xx,
        # blocked) as a finished answer with nothing in it.
        payload = {
            "preview": None,
            "previewStatus": "error",
            "thumbnails": {"image": None, "favicon": None},
            "thumbnailsStatus": "complete",
        }
        bridge = Bridge(lambda request: httpx2.Response(200, json=payload))
        client = bridge.client()

        for n in range(5):
            result = await client.fetch_many([f"https://example.com/{n}"])
            assert result["previews"][0]["status"] == "error"
        again = await client.fetch_many(["https://example.com/0"])

        # Every one was sent (no breaker), and the verdict on a page is kept.
        assert len(bridge.requests) == 5
        assert again["previews"][0]["status"] == "error"

    async def test_a_slow_answer_inside_the_timeout_is_not_a_failure(self):
        async def respond(request: httpx2.Request) -> httpx2.Response:
            await asyncio.sleep(0.05)
            return httpx2.Response(200, json=bridge_payload())

        bridge = Bridge(respond)
        client = bridge.client(request_timeout=1.0)

        for n in range(5):
            result = await client.fetch_many([f"https://example.com/{n}"])
            assert result["previews"][0]["status"] == "ok"

        assert len(bridge.requests) == 5

    async def test_a_rejected_token_opens_the_breaker_at_once(self):
        clock = Clock()
        bridge = Bridge(lambda request: httpx2.Response(401))
        client = bridge.client(timer=clock)

        await client.fetch_many(["https://example.com/a"])
        await client.fetch_many(["https://example.com/b"])
        assert len(bridge.requests) == 1

        clock.now += 31
        await client.fetch_many(["https://example.com/b"])
        assert len(bridge.requests) == 2


class TestRateLimit:
    @staticmethod
    def limited_after(allowed: int, headers: dict[str, str] | None = None) -> Bridge:
        bridge = Bridge()
        answered = 0

        def respond(request: httpx2.Request) -> httpx2.Response:
            nonlocal answered
            answered += 1
            if answered > allowed:
                return httpx2.Response(429, headers=headers or {})
            return httpx2.Response(200, json=bridge_payload())

        bridge.respond = respond
        return bridge

    async def test_a_429_marks_the_unanswered_urls_rate_limited(self):
        bridge = self.limited_after(2, {"Retry-After": "17"})
        urls = [f"https://example.com/{n}" for n in range(8)]

        result = await bridge.client().fetch_many(urls)

        statuses = [entry["status"] for entry in result["previews"]]
        assert statuses[:2] == ["ok", "ok"]
        assert statuses[2:] == ["rate_limited"] * 6
        assert result["retry_after_seconds"] == 17

    async def test_nothing_more_is_sent_while_the_limit_is_in_force(self):
        clock = Clock()
        bridge = self.limited_after(0, {"Retry-After": "17"})
        client = bridge.client(timer=clock)
        await client.fetch_many(["https://example.com/a"])
        assert len(bridge.requests) == 1
        bridge.respond = lambda request: httpx2.Response(200, json=bridge_payload())

        clock.now += 10
        result = await client.fetch_many(
            ["https://example.com/a", "https://example.com/b"]
        )

        assert [entry["status"] for entry in result["previews"]] == [
            "rate_limited",
            "rate_limited",
        ]
        assert len(bridge.requests) == 1
        # What is left of the wait, not the whole of it again.
        assert result["retry_after_seconds"] == 7

    async def test_lookups_queued_behind_a_429_are_not_sent(self):
        # Sixteen URLs across two calls: the second eight are still queued
        # when the first wave is told to stop, and never go out.
        bridge = self.limited_after(0, {"Retry-After": "5"})
        client = bridge.client()

        first, second = await asyncio.gather(
            client.fetch_many([f"https://example.com/{n}" for n in range(8)]),
            client.fetch_many([f"https://example.org/{n}" for n in range(8)]),
        )

        assert len(bridge.requests) == 8
        assert all(
            entry["status"] == "rate_limited"
            for result in (first, second)
            for entry in result["previews"]
        )

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, 30),
            ("soon", 30),
            ("Wed, 21 Oct 2026 07:28:00 GMT", 30),
            ("0", 1),
            ("-3", 1),
            ("9999", 120),
        ],
    )
    async def test_the_wait_is_the_bridge_s_within_reason(self, header, expected):
        bridge = self.limited_after(
            0, {} if header is None else {"Retry-After": header}
        )

        result = await bridge.client(timer=Clock()).fetch_many(
            ["https://example.com/a"]
        )

        assert result["retry_after_seconds"] == expected

    async def test_a_rate_limited_url_is_not_cached(self):
        clock = Clock()
        bridge = self.limited_after(1, {"Retry-After": "5"})
        client = bridge.client(timer=clock)

        first = await client.fetch_many(
            ["https://example.com/a", "https://example.com/b"]
        )
        assert [entry["status"] for entry in first["previews"]] == [
            "ok",
            "rate_limited",
        ]

        bridge.respond = lambda request: httpx2.Response(200, json=bridge_payload())
        clock.now += 5
        second = await client.fetch_many(
            ["https://example.com/a", "https://example.com/b"]
        )

        assert [entry["status"] for entry in second["previews"]] == ["ok", "ok"]
        # /a came from the cache; /b was asked for again.
        assert bridge.asked() == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/b",
        ]

    async def test_cached_urls_are_still_answered_while_limited(self):
        bridge = Bridge()
        client = bridge.client()
        await client.fetch_many(["https://example.com/cached"])
        bridge.respond = lambda request: httpx2.Response(429)

        result = await client.fetch_many(
            ["https://example.com/new", "https://example.com/cached"]
        )

        assert [entry["status"] for entry in result["previews"]] == [
            "rate_limited",
            "ok",
        ]

    async def test_a_429_is_not_a_failure_of_the_bridge(self):
        clock = Clock()
        bridge = self.limited_after(0, {"Retry-After": "1"})
        client = bridge.client(timer=clock)

        for n in range(5):
            await client.fetch_many([f"https://example.com/{n}"])
            clock.now += 2

        # Each was sent once its wait was over: the breaker never opened.
        assert len(bridge.requests) == 5


class TestTokenStaysOutOfTheLogs:
    async def test_no_outcome_logs_the_token_or_a_bookmark(self, caplog):
        secret_url = "https://private.example/secret-bookmark"
        responses = iter(
            [
                httpx2.Response(200, json=bridge_payload()),
                httpx2.Response(401, json={"error": f"bad token {TOKEN}"}),
                httpx2.Response(429, headers={"Retry-After": "5"}),
                httpx2.Response(500, text=TOKEN),
                httpx2.Response(200, text="not json " + TOKEN),
            ]
        )
        calls = 0

        def respond(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            if calls == 6:
                raise httpx2.ConnectError(f"could not reach {request.url}")
            return next(responses)

        client = Bridge(respond).client()
        with caplog.at_level(logging.DEBUG):
            for n in range(6):
                await client.fetch_many([f"{secret_url}/{n}"])
            await client.aclose()

        def text_of(records: list[logging.LogRecord]) -> str:
            return "\n".join(f"{r.getMessage()} {r.args!r}" for r in records)

        ours = [r for r in caplog.records if r.name.startswith("pinboard_mcp_server")]
        assert ours
        # Not from this module, and not from the HTTP client under it either.
        everything = text_of(caplog.records)
        assert TOKEN not in everything
        assert TOKEN.split(":")[1] not in everything
        assert "Bearer" not in everything
        # This module does not name the bookmark it was asked about. (The
        # HTTP client's own INFO line does, as it would for any request.)
        assert "secret-bookmark" not in text_of(ours)

    async def test_a_rejected_token_is_warned_about_once(self, caplog):
        clock = Clock()
        client = Bridge(lambda request: httpx2.Response(401)).client(timer=clock)

        with caplog.at_level("WARNING", logger="pinboard_mcp_server.previews"):
            for n in range(3):
                await client.fetch_many([f"https://example.com/{n}"])
                clock.now += 60

        rejections = [r for r in caplog.records if "401" in r.getMessage()]
        assert len(rejections) == 1
        assert rejections[0].levelno == logging.WARNING

    def test_the_token_is_not_in_the_client_s_repr(self):
        client = Bridge().client()

        assert TOKEN not in repr(client)
        assert TOKEN not in repr(vars(client))


def _run_server_script(script: str, **env: str) -> dict[str, Any]:
    """Run ``script`` in a fresh interpreter: the tools read their switches at import."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {previews.BRIDGE_URL_ENV_VAR, ui.APP_UI_ENV_VAR}
    }
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=True,
        env={**environment, **env},
        text=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


LIST_TOOLS = """
import asyncio, json
from fastmcp import Client
from pinboard_mcp_server import main

async def go():
    async with Client(main.mcp) as client:
        tools = await client.list_tools()
        print(json.dumps({
            "enabled": main.PREVIEWS_ENABLED,
            "tools": {tool.name: tool.meta for tool in tools},
            "descriptions": {tool.name: tool.description for tool in tools},
        }))

asyncio.run(go())
"""

CALL_TOOLS = """
import asyncio, json
from unittest.mock import AsyncMock, Mock
from fastmcp import Client
from pinboard_mcp_server import main
from pinboard_mcp_server.models import Bookmark

class FakePreviews:
    def __init__(self):
        self.asked = []

    async def fetch_many(self, urls, *, allowed=None):
        offered = [url for url in urls if allowed is None or url in allowed]
        self.asked.append(offered)
        return {"previews": [
            {"url": url, "status": "no_data" if url in offered else "error"}
            for url in urls
        ]}

async def call(client, name, arguments):
    result = await client.call_tool_mcp(name, arguments)
    return {
        "is_error": result.is_error,
        "structured": result.structured_content,
        "texts": [block.text for block in result.content],
    }

async def go():
    bookmarks = [
        Bookmark.from_pinboard({
            "href": "https://example.com/" + name, "description": name, "extended": "",
            "tags": "python", "time": "2024-01-15T10:30:00Z",
        })
        for name in ("a", "b")
    ]
    main.client = Mock()
    for method in ("search_bookmarks", "search_bookmarks_extended",
                   "get_recent_bookmarks", "get_bookmarks_by_tags"):
        setattr(main.client, method, AsyncMock(return_value=bookmarks))
    fake = FakePreviews()
    report = {}
    a, b = "https://example.com/a", "https://example.com/b"
    async with Client(main.mcp) as client:
        report["unconfigured"] = await call(client, "get_link_previews", {"urls": [a]})
        main.link_previews = fake
        report["before_any_bookmarks"] = await call(
            client, "get_link_previews", {"urls": [a]})
        report["bookmarks"] = {
            "search_bookmarks": await call(client, "search_bookmarks", {"query": "a"}),
            "search_bookmarks_extended": await call(
                client, "search_bookmarks_extended", {"query": "a"}),
            "list_recent_bookmarks": await call(client, "list_recent_bookmarks", {}),
            "list_bookmarks_by_tags": await call(
                client, "list_bookmarks_by_tags", {"tags": ["python"]}),
        }
        report["ok"] = await call(client, "get_link_previews", {"urls": [a, b, a]})
        report["mixed"] = await call(client, "get_link_previews", {"urls": [
            a, "javascript:alert(1)", "https://elsewhere.example/secret"]})
        report["too_many"] = await call(client, "get_link_previews", {
            "urls": ["https://example.com/%d" % n for n in range(9)]})
        report["none"] = await call(client, "get_link_previews", {"urls": []})
    report["asked"] = fake.asked
    print(json.dumps(report))

asyncio.run(go())
"""

BOOKMARK_TOOLS = (
    "search_bookmarks",
    "search_bookmarks_extended",
    "list_recent_bookmarks",
    "list_bookmarks_by_tags",
)


class TestToolRegistration:
    def test_without_a_bridge_the_tool_does_not_exist(self):
        report = _run_server_script(LIST_TOOLS)

        assert report["enabled"] is False
        assert "get_link_previews" not in report["tools"]

    def test_an_unusable_bridge_url_leaves_it_off_too(self):
        report = _run_server_script(
            LIST_TOOLS, **{previews.BRIDGE_URL_ENV_VAR: "http://bridge.example"}
        )

        assert report["enabled"] is False
        assert "get_link_previews" not in report["tools"]

    def test_with_the_cards_off_the_tool_does_not_exist(self):
        report = _run_server_script(
            LIST_TOOLS,
            **{
                previews.BRIDGE_URL_ENV_VAR: BRIDGE,
                ui.APP_UI_ENV_VAR: "false",
            },
        )

        assert report["enabled"] is False
        assert "get_link_previews" not in report["tools"]

    def test_with_a_bridge_the_tool_is_registered_for_the_app_only(self):
        report = _run_server_script(LIST_TOOLS, **{previews.BRIDGE_URL_ENV_VAR: BRIDGE})

        assert report["enabled"] is True
        meta = report["tools"]["get_link_previews"]
        assert meta["ui"] == {"visibility": ["app"]}
        assert meta["openai/widgetAccessible"] is True
        assert meta["openai/visibility"] == "private"
        # A host without MCP Apps shows the tool to the model regardless, so
        # the description has to wave it off.
        description = report["descriptions"]["get_link_previews"]
        assert "result card" in description
        assert "not useful to call directly" in description
        # The bookmark tools still point at their card, and nothing else changed.
        for name in BOOKMARK_TOOLS:
            assert report["tools"][name]["ui"] == {
                "resourceUri": ui.card_uri(ui.BOOKMARKS_CARD)
            }

    def test_the_app_only_meta_is_what_the_spec_and_chatgpt_ask_for(self):
        assert ui.APP_ONLY_TOOL_META == {
            # MCP Apps: callable by the app, hidden from the model.
            "ui": {"visibility": ["app"]},
            # ChatGPT's own keys for the same two things.
            "openai/widgetAccessible": True,
            "openai/visibility": "private",
        }


class TestToolCalls:
    @pytest.fixture(scope="class")
    @classmethod
    def report(cls) -> dict[str, Any]:
        return _run_server_script(CALL_TOOLS, **{previews.BRIDGE_URL_ENV_VAR: BRIDGE})

    def test_urls_are_deduplicated_and_answered_in_order(self, report):
        assert report["asked"][1] == ["https://example.com/a", "https://example.com/b"]
        assert report["ok"]["is_error"] is False
        assert [p["url"] for p in report["ok"]["structured"]["previews"]] == [
            "https://example.com/a",
            "https://example.com/b",
        ]

    def test_only_urls_a_bookmark_tool_returned_reach_the_bridge(self, report):
        # Asked before any bookmark tool ran, the same URL is refused.
        assert report["asked"][0] == []
        before = report["before_any_bookmarks"]
        assert before["is_error"] is False
        assert [p["status"] for p in before["structured"]["previews"]] == ["error"]

    def test_a_url_the_tool_will_not_look_up_costs_only_its_own_row(self, report):
        mixed = report["mixed"]

        assert mixed["is_error"] is False
        assert [p["status"] for p in mixed["structured"]["previews"]] == [
            "no_data",
            "error",
            "error",
        ]
        assert report["asked"][2] == ["https://example.com/a"]

    def test_the_text_block_is_one_line_and_not_the_payload_again(self, report):
        for case in ("ok", "mixed"):
            texts = report[case]["texts"]
            assert len(texts) == 1
            assert "\n" not in texts[0]
            assert len(texts[0]) < 100
            assert "previews" not in texts[0].lower().replace("link previews", "")
            assert "example.com" not in texts[0]

    @pytest.mark.parametrize("case", ["too_many", "none"])
    def test_a_bad_request_is_refused_before_the_bridge_is_asked(self, report, case):
        assert report[case]["is_error"] is True
        assert len(report["asked"]) == 3

    def test_a_server_started_without_its_bridge_client_says_so(self, report):
        assert report["unconfigured"]["is_error"] is True
        assert "not configured" in report["unconfigured"]["texts"][0]

    def test_bookmark_tools_tell_the_card_previews_are_available(self, report):
        for name in BOOKMARK_TOOLS:
            assert report["bookmarks"][name]["structured"]["previews_available"] is True

    async def test_without_a_bridge_bookmark_results_are_unchanged(self, mock_client):
        from fastmcp import Client

        from pinboard_mcp_server import main

        main.client = mock_client
        async with Client(main.mcp) as client:
            result = await client.call_tool("search_bookmarks", {"query": "python"})

        assert set(result.structured_content) == {"bookmarks", "total", "query"}


class TestFixtureContract:
    """The card's preview fixtures have to be what ``get_link_previews`` returns."""

    @staticmethod
    def _fixtures() -> dict[str, Any]:
        return json.loads((FIXTURES / "previews.json").read_text(encoding="utf-8"))

    async def _real_entries(self) -> list[dict[str, Any]]:
        responses = iter(
            [
                httpx2.Response(200, json=bridge_payload()),
                httpx2.Response(
                    200, json={"preview": None, "previewStatus": "no_data"}
                ),
                httpx2.Response(400),
                httpx2.Response(429),
            ]
        )
        client = Bridge(lambda request: next(responses)).client()
        entries = []
        for n in range(4):
            result = await client.fetch_many([f"https://example.com/{n}"])
            entries.extend(result["previews"])
        assert [entry["status"] for entry in entries] == [
            "ok",
            "no_data",
            "error",
            "rate_limited",
        ]
        return entries

    async def test_preview_fixtures_have_the_tool_s_shape(self):
        real_keys = set((await self._real_entries())[0])

        for scenario, result in self._fixtures().items():
            content = result["structuredContent"]
            assert set(content) <= {"previews", "retry_after_seconds"}, scenario
            limited = any(p["status"] == "rate_limited" for p in content["previews"])
            assert ("retry_after_seconds" in content) == limited, scenario
            for entry in content["previews"]:
                # `retry` is there only when true: a busy bridge.
                assert set(entry) - {"retry"} == real_keys, scenario
                assert entry.get("retry", True) is True, scenario
                for key in ("image", "favicon"):
                    if entry[key] is not None and scenario != "hostile":
                        assert set(entry[key]) == {"data_uri", "width", "height"}

    def test_the_well_behaved_fixtures_would_pass_the_sanitiser(self):
        # Everything in them is something the server could really send: each
        # entry survives a round trip through the bridge's shape unchanged.
        for scenario, result in self._fixtures().items():
            if scenario == "hostile":
                continue
            for entry in result["structuredContent"]["previews"]:
                if entry["status"] != "ok":
                    assert all(
                        entry[key] is None
                        for key in entry
                        if key not in {"url", "status", "retry"}
                    ), scenario
                    continue

                def thumbnail(value: Any) -> Any:
                    return value and {
                        "dataUri": value["data_uri"],
                        "width": value["width"],
                        "height": value["height"],
                    }

                payload = {
                    "preview": {
                        "title": entry["title"],
                        "description": entry["description"],
                        "siteName": entry["site_name"],
                        "themeColor": entry["theme_color"],
                    },
                    "previewStatus": "fresh",
                    "thumbnails": {
                        "image": thumbnail(entry["image"]),
                        "favicon": thumbnail(entry["favicon"]),
                    },
                    "thumbnailsStatus": "busy" if entry.get("retry") else "complete",
                }
                assert previews.sanitise_preview(entry["url"], payload) == entry, (
                    f"{scenario}: {entry['url']}"
                )

    def test_the_hostile_fixture_is_hostile(self):
        # It stands for a server that failed to sanitise, so the card's own
        # guards are what the card suites exercise.
        entries = self._fixtures()["hostile"]["structuredContent"]["previews"]
        images = [
            entry[key]["data_uri"]
            for entry in entries
            for key in ("image", "favicon")
            if isinstance(entry[key], dict)
        ]

        assert any(uri.startswith("https://") for uri in images)
        assert any(uri.startswith("javascript:") for uri in images)
        assert any("svg" in uri for uri in images)

    def test_fixture_images_are_real_images(self):
        magic = {"png": b"\x89PNG", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}
        seen = 0
        for scenario, result in self._fixtures().items():
            if scenario == "hostile":
                continue
            for entry in result["structuredContent"]["previews"]:
                for key in ("image", "favicon"):
                    if entry[key] is None:
                        continue
                    header, _, body = entry[key]["data_uri"].partition(";base64,")
                    kind = header.removeprefix("data:image/")
                    assert base64.b64decode(body).startswith(magic[kind]), scenario
                    seen += 1
        assert seen > 0

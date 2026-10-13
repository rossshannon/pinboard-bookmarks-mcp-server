"""MCP Apps result cards: how the tools point at them and how they are served.

The cards' own behaviour is tested in ``cards/`` (jsdom and a real browser);
this suite covers the server's half of the contract — the ``_meta`` on each
tool, the ``ui://`` resources, the off switch — and checks that the fixtures
the cards are tested against have the shape the tools really return.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from pinboard_mcp_server import main, ui

FIXTURES = Path(__file__).resolve().parent.parent / "cards" / "fixtures"

BOOKMARK_TOOLS = (
    "search_bookmarks",
    "search_bookmarks_extended",
    "list_recent_bookmarks",
    "list_bookmarks_by_tags",
)


@pytest.fixture
async def mcp_client(mock_client):
    """An in-memory MCP client against the real server, its Pinboard client mocked."""
    main.client = mock_client
    async with Client(main.mcp) as client:
        yield client


def _json_type(value: Any) -> str:
    """A value's JSON type, with an array's element types: ``array<string>``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        elements = sorted({_json_type(item) for item in value})
        return f"array<{'|'.join(elements)}>" if elements else "array"
    return "object"


def _types_by_key(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Every JSON type each key takes across ``records``; an empty array matches any array."""
    types: dict[str, set[str]] = {}
    for record in records:
        for key, value in record.items():
            types.setdefault(key, set()).add(_json_type(value))
    return types


def _assert_types_within(
    fixture: dict[str, Any], allowed: dict[str, set[str]], where: str
) -> None:
    for key, value in fixture.items():
        assert key in allowed, f"{where}: {key} is not a key the tools send"
        kind = _json_type(value)
        if kind == "array" and any(t.startswith("array") for t in allowed[key]):
            continue
        assert kind in allowed[key], (
            f"{where}: {key} is {kind}, tools send {allowed[key]}"
        )


def _fixtures(card: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{card}.json").read_text(encoding="utf-8"))


class TestToolBindings:
    async def test_bookmark_tools_point_at_the_bookmarks_card(self, mcp_client):
        tools = {tool.name: tool for tool in await mcp_client.list_tools()}

        for name in BOOKMARK_TOOLS:
            assert tools[name].meta is not None
            assert tools[name].meta["ui"] == {
                "resourceUri": ui.card_uri(ui.BOOKMARKS_CARD)
            }

    async def test_list_tags_points_at_the_tags_card(self, mcp_client):
        tools = {tool.name: tool for tool in await mcp_client.list_tools()}

        assert tools["list_tags"].meta is not None
        assert tools["list_tags"].meta["ui"] == {
            "resourceUri": ui.card_uri(ui.TAGS_CARD)
        }

    async def test_every_pointer_names_a_resource_the_server_lists(self, mcp_client):
        uris = {str(resource.uri) for resource in await mcp_client.list_resources()}

        for tool in await mcp_client.list_tools():
            assert tool.meta is not None
            assert tool.meta["ui"]["resourceUri"] in uris


class TestCardResources:
    async def test_each_card_is_listed_as_an_mcp_app(self, mcp_client):
        resources = {
            str(resource.uri): resource
            for resource in await mcp_client.list_resources()
        }

        for card in ui.CARDS:
            resource = resources[ui.card_uri(card)]
            assert resource.mime_type == "text/html;profile=mcp-app"
            assert resource.meta is not None
            assert resource.meta["ui"] == ui.CARD_RESOURCE_META["ui"]
            assert resource.meta["ui"]["prefersBorder"] is True
            # Spelled out, not read back from HOST_FONT_ORIGINS, so a typo
            # there fails here. These are where Claude's styles.css.fonts
            # point: its own asset host and Google Fonts' CSS and files.
            assert resource.meta["ui"]["csp"] == {
                "resourceDomains": [
                    "https://assets.anthropic.com",
                    "https://fonts.googleapis.com",
                    "https://fonts.gstatic.com",
                ]
            }

    async def test_reading_a_card_serves_its_built_document(self, mcp_client):
        for card in ui.CARDS:
            result = await mcp_client.read_resource_mcp(ui.card_uri(card))

            (content,) = result.contents
            assert content.mime_type == "text/html;profile=mcp-app"
            assert content.text == ui.card_html(card)
            assert content.text.startswith("<!doctype html>")

    async def test_a_stale_fingerprint_still_resolves_to_the_running_build(
        self, mcp_client
    ):
        # Served exactly as the current URI is: a host renders only an MCP App
        # MIME type, and a fallback that came back as text/plain would be a
        # card that silently never draws.
        for card in ui.CARDS:
            result = await mcp_client.read_resource_mcp(
                f"ui://pinboard/{card.name}@0000"
            )

            (content,) = result.contents
            assert content.mime_type == "text/html;profile=mcp-app"
            assert content.meta == ui.CARD_RESOURCE_META
            assert content.text == ui.card_html(card)

    async def test_an_unknown_card_is_not_found(self, mcp_client):
        with pytest.raises(Exception, match="not found"):
            await mcp_client.read_resource_mcp("ui://pinboard/nonesuch@0000")

    def test_the_uri_fingerprint_is_the_built_bytes(self):
        for card in ui.CARDS:
            digest = hashlib.sha256(ui.card_html(card).encode("utf-8")).hexdigest()
            assert ui.card_uri(card) == f"ui://pinboard/{card.name}@{digest[:16]}"

    def test_documents_load_nothing_from_the_network(self):
        # The CSP admits only the host's font origins, so anything the card
        # itself tried to load would be blocked: everything must be inline.
        for card in ui.CARDS:
            html = ui.card_html(card)
            assert not re.search(r"<script[^>]+\bsrc=", html)
            assert not re.search(r"<link[^>]+\bhref=", html)
            assert "@import" not in html
            assert not re.search(r"url\(\s*['\"]?\s*(?:https?:)?//", html, re.I)


class TestMissingBuild:
    def test_a_card_that_is_not_installed_drops_out_without_failing(
        self, caplog, monkeypatch
    ):
        monkeypatch.delenv(ui.APP_UI_ENV_VAR, raising=False)
        missing = ui.Card(name="not-built", title="Missing", description="Never built.")
        ui.card_available.cache_clear()

        with caplog.at_level("WARNING", logger="pinboard_mcp_server.ui"):
            assert ui.tool_meta(missing) is None
        assert "not-built" in caplog.text

        registered: list[str] = []

        class RecordingMcp:
            def resource(self, uri: str, **_: Any) -> Any:
                registered.append(uri)
                return lambda fn: fn

        monkeypatch.setattr(ui, "CARDS", (missing, ui.TAGS_CARD))
        ui.register_card_resources(RecordingMcp())
        assert registered and all(
            uri.startswith("ui://pinboard/tags@") for uri in registered
        )

    def test_a_card_that_cannot_be_decoded_drops_out_too(self, monkeypatch):
        def undecodable(card: ui.Card) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        monkeypatch.delenv(ui.APP_UI_ENV_VAR, raising=False)
        monkeypatch.setattr(ui, "card_html", undecodable)
        ui.card_available.cache_clear()
        try:
            assert ui.tool_meta(ui.TAGS_CARD) is None
        finally:
            ui.card_available.cache_clear()


class TestOffSwitch:
    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", " false "])
    def test_falsey_values_switch_the_cards_off(self, value):
        assert not ui.ui_enabled({ui.APP_UI_ENV_VAR: value})

    @pytest.mark.parametrize("value", ["", "true", "1", "yes"])
    def test_cards_are_on_by_default(self, value):
        assert ui.ui_enabled({ui.APP_UI_ENV_VAR: value})
        assert ui.ui_enabled({})

    def test_tool_meta_is_none_when_off(self):
        assert ui.tool_meta(ui.BOOKMARKS_CARD, {ui.APP_UI_ENV_VAR: "false"}) is None

    def test_switched_off_the_server_advertises_no_cards(self):
        # A fresh interpreter, because the tools read the switch as they are
        # registered, at import.
        script = """
import asyncio, json
from fastmcp import Client
from pinboard_mcp_server import main

async def go():
    async with Client(main.mcp) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        print(json.dumps({
            "metas": [(tool.meta or {}).get("ui") for tool in tools],
            "resources": [str(r.uri) for r in resources],
            "templates": [t.uri_template for t in templates],
        }))

asyncio.run(go())
"""
        env = {**os.environ, ui.APP_UI_ENV_VAR: "false"}
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            env=env,
            text=True,
        )
        report = json.loads(completed.stdout.strip().splitlines()[-1])

        assert report == {"metas": [None] * 5, "resources": [], "templates": []}


class TestFixtureContract:
    """The cards are tested against ``cards/fixtures``; those have to be what the tools return."""

    async def _real_outputs(self, client) -> dict[str, dict[str, Any]]:
        calls = {
            "search_bookmarks": {"query": "python"},
            "search_bookmarks_extended": {"query": "python"},
            "list_recent_bookmarks": {},
            "list_bookmarks_by_tags": {
                "tags": ["python"],
                "from_date": "2024-01-01",
                "to_date": "2024-12-31",
            },
            # No dates: from_date and to_date come back null.
            "list_bookmarks_by_tags#undated": {"tags": ["python"]},
            "list_tags": {},
        }
        outputs = {}
        for name, arguments in calls.items():
            result = await client.call_tool(name.split("#")[0], arguments)
            assert result.structured_content is not None
            outputs[name] = result.structured_content
        return outputs

    async def test_bookmark_fixtures_have_a_real_tool_s_shape(self, mcp_client):
        outputs = await self._real_outputs(mcp_client)
        real_shapes = {frozenset(outputs[name]) for name in BOOKMARK_TOOLS}
        real_bookmark_keys = set(outputs["search_bookmarks"]["bookmarks"][0])

        for scenario, result in _fixtures("bookmarks").items():
            if result.get("isError"):
                continue
            content = result["structuredContent"]
            assert frozenset(content) in real_shapes, scenario
            for bookmark in content["bookmarks"]:
                assert set(bookmark) == real_bookmark_keys, scenario

    async def test_bookmark_fixtures_have_a_real_tool_s_types(self, mcp_client):
        # Names alone let a fixture send `total: "4"` or `tags: "a,b"` that no
        # tool ever would, and the cards would be tested against it.
        outputs = await self._real_outputs(mcp_client)
        bookmark_outputs = [v for k, v in outputs.items() if k != "list_tags"]
        top_level = _types_by_key(bookmark_outputs)
        rows = _types_by_key(
            [row for out in bookmark_outputs for row in out["bookmarks"]]
        )

        for scenario, result in _fixtures("bookmarks").items():
            if result.get("isError"):
                continue
            content = result["structuredContent"]
            _assert_types_within(content, top_level, scenario)
            for bookmark in content["bookmarks"]:
                _assert_types_within(bookmark, rows, scenario)

    async def test_tag_fixtures_have_list_tags_shape(self, mcp_client):
        outputs = await self._real_outputs(mcp_client)
        real_keys = set(outputs["list_tags"]["result"][0])

        assert set(outputs["list_tags"]) == {"result"}
        for scenario, result in _fixtures("tags").items():
            if result.get("isError") or scenario == "hostile":
                continue
            assert set(result["structuredContent"]) == {"result"}, scenario
            for entry in result["structuredContent"]["result"]:
                assert set(entry) == real_keys, scenario
                _assert_types_within(
                    entry, _types_by_key(outputs["list_tags"]["result"]), scenario
                )

    async def test_error_fixtures_match_how_fastmcp_reports_a_failure(self, mcp_client):
        result = await mcp_client.call_tool_mcp(
            "search_bookmarks", {"query": "x", "limit": 500}
        )

        assert result.is_error
        assert (
            result.content[0].text
            == _fixtures("bookmarks")["error"]["content"][0]["text"]
        )

    async def test_saved_at_reaches_the_card_as_an_iso_string(self, mcp_client):
        result = await mcp_client.call_tool("search_bookmarks", {"query": "python"})

        saved_at = result.structured_content["bookmarks"][0]["saved_at"]
        assert re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(Z|[+-]\d\d:\d\d)", saved_at
        )

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


def _field(model: Any, name: str, legacy: str) -> Any:
    """A protocol field by its MCP SDK v2 name, or its v1 camelCase one: FastMCP 3 ships the v1 SDK."""
    return (
        getattr(model, name)
        if name in type(model).model_fields
        else getattr(model, legacy)
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
            assert (
                _field(resource, "mime_type", "mimeType") == "text/html;profile=mcp-app"
            )
            assert resource.meta is not None
            assert resource.meta["ui"] == {"prefersBorder": True}

    async def test_reading_a_card_serves_its_built_document(self, mcp_client):
        for card in ui.CARDS:
            result = await mcp_client.read_resource_mcp(ui.card_uri(card))

            (content,) = result.contents
            assert (
                _field(content, "mime_type", "mimeType") == "text/html;profile=mcp-app"
            )
            assert content.text == ui.card_html(card)
            assert content.text.startswith("<!doctype html>")

    async def test_a_stale_fingerprint_still_resolves_to_the_running_build(
        self, mcp_client
    ):
        result = await mcp_client.read_resource_mcp("ui://pinboard/bookmarks@0000")

        assert result.contents[0].text == ui.card_html(ui.BOOKMARKS_CARD)

    async def test_an_unknown_card_is_not_found(self, mcp_client):
        with pytest.raises(Exception, match="not found"):
            await mcp_client.read_resource_mcp("ui://pinboard/nonesuch@0000")

    def test_the_uri_fingerprint_is_the_built_bytes(self):
        for card in ui.CARDS:
            digest = hashlib.sha256(ui.card_html(card).encode("utf-8")).hexdigest()
            assert ui.card_uri(card) == f"ui://pinboard/{card.name}@{digest[:16]}"

    def test_documents_load_nothing_from_the_network(self):
        # The resources declare no CSP origins, so the host would block any
        # external script, stylesheet, font or image a card tried to load.
        for card in ui.CARDS:
            html = ui.card_html(card)
            assert not re.search(r"<script[^>]+\bsrc=", html)
            assert not re.search(r"<link[^>]+\bhref=", html)
            assert "@import" not in html


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
            "list_bookmarks_by_tags": {"tags": ["python"], "from_date": "2024-01-01"},
            "list_tags": {},
        }
        outputs = {}
        for name, arguments in calls.items():
            result = await client.call_tool(name, arguments)
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

    async def test_error_fixtures_match_how_fastmcp_reports_a_failure(self, mcp_client):
        result = await mcp_client.call_tool_mcp(
            "search_bookmarks", {"query": "x", "limit": 500}
        )

        assert _field(result, "is_error", "isError")
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

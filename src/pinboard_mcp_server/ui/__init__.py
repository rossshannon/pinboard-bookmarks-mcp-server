"""MCP Apps result cards for the Pinboard tools.

MCP hosts that support MCP Apps (SEP-1865: Claude, ChatGPT and others) render a
tool's result as an interactive card instead of a block of JSON. The mechanism:

1. The tool descriptor carries ``_meta.ui.resourceUri``, pointing at a ``ui://``
   resource.
2. That resource is a self-contained HTML document served with the
   ``text/html;profile=mcp-app`` MIME type.
3. The host fetches it once, renders it in a sandboxed iframe, and hands it the
   tool call's ``structuredContent`` over a ``postMessage`` bridge.

Hosts without MCP Apps support ignore the ``_meta`` and see exactly what they
saw before, so the layer is additive. ``PINBOARD_MCP_APPS_UI=false`` turns it
off entirely: no ``_meta`` on any tool and no ``ui://`` resources.

The cards themselves are React, built by ``cards/`` (see ``docs/mcp-apps-ui.md``)
into the committed documents under ``generated/``, which ship as package data.
Nothing here builds anything; it serves bytes.

Each card's URI carries a fingerprint of its built bytes
(``ui://pinboard/bookmarks@<hash>``), so a host that caches documents by URI
picks up a new build when it next lists tools. The resource is also registered
as a template on the fingerprint, so a host still holding an old URI resolves
to the running build rather than failing.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

logger = logging.getLogger(__name__)

#: Environment variable that switches the cards off when set to ``false``.
APP_UI_ENV_VAR = "PINBOARD_MCP_APPS_UI"

#: The MCP Apps resource MIME type (SEP-1865).
MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"

#: The scheme and namespace every card is served under.
CARD_URI_PREFIX = "ui://pinboard/"


@dataclass(frozen=True)
class Card:
    """One built card: its name (the ``cards/src/<name>`` directory) and what the host is told about it."""

    name: str
    title: str
    description: str


BOOKMARKS_CARD = Card(
    name="bookmarks",
    title="Pinboard bookmarks",
    description=(
        "Card for search_bookmarks, search_bookmarks_extended, "
        "list_recent_bookmarks and list_bookmarks_by_tags results: each "
        "bookmark's title as a link, its site and save date, notes and tags."
    ),
)

TAGS_CARD = Card(
    name="tags",
    title="Pinboard tags",
    description=(
        "Card for list_tags results: every tag with its count, most-used "
        "first, drawn as a bar chart with a filter."
    ),
)

CARDS: tuple[Card, ...] = (BOOKMARKS_CARD, TAGS_CARD)


def ui_enabled(environ: os._Environ[str] | dict[str, str] | None = None) -> bool:
    """Whether the cards are on. On unless ``PINBOARD_MCP_APPS_UI`` says ``false`` (or ``0``/``no``/``off``)."""
    value = (environ if environ is not None else os.environ).get(APP_UI_ENV_VAR, "")
    return value.strip().lower() not in {"false", "0", "no", "off"}


@cache
def card_html(card: Card) -> str:
    """The card's built document, read once from package data."""
    return (
        resources.files(__package__)
        .joinpath("generated")
        .joinpath(f"{card.name}.html")
        .read_text(encoding="utf-8")
    )


@cache
def card_available(card: Card) -> bool:
    """Whether the card's built document is installed. A missing one switches off that card only, never the server."""
    try:
        card_html(card)
    except (OSError, ValueError):  # missing, unreadable, or not UTF-8
        logger.warning(
            "MCP Apps card %r is not installed (ui/generated/%s.html); its tools will return plain results",
            card.name,
            card.name,
        )
        return False
    return True


@cache
def card_hash(card: Card) -> str:
    """A fingerprint of the built bytes: the first 16 hex digits of their SHA-256."""
    return hashlib.sha256(card_html(card).encode("utf-8")).hexdigest()[:16]


def card_uri(card: Card) -> str:
    """The running build's fingerprinted URI: what a tool points at and the resource list advertises."""
    return f"{CARD_URI_PREFIX}{card.name}@{card_hash(card)}"


def card_uri_template(card: Card) -> str:
    """The URI matched on read: any fingerprint of this card resolves to the running build."""
    return f"{CARD_URI_PREFIX}{card.name}@{{fingerprint}}"


def tool_meta(
    card: Card, environ: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """The ``_meta`` a tool rendered by ``card`` carries, or ``None`` when the cards are off."""
    if not ui_enabled(environ) or not card_available(card):
        return None
    return {"ui": {"resourceUri": card_uri(card)}}


#: The ``_meta`` every card resource is served with. A card loads nothing from
#: the network — no fonts, no images, no fetches — so it declares no CSP origins
#: at all, and the host's default (block everything) is exactly right.
#: ``prefersBorder`` asks the host to frame the card, which reads better in a
#: transcript than a borderless panel.
CARD_RESOURCE_META: dict[str, Any] = {"ui": {"prefersBorder": True}}


def register_card_resources(mcp: Any) -> None:
    """Register every card's ``ui://`` resource on ``mcp``: the concrete URI, listed, and the any-fingerprint template."""
    for card in CARDS:
        if card_available(card):
            _register_card(mcp, card)


def _register_card(mcp: Any, card: Card) -> None:
    def read_card() -> str:
        return card_html(card)

    def read_card_at(fingerprint: str) -> str:
        # Any fingerprint resolves to the running build: a host holding a stale
        # URI still gets a card, and the next tools/list hands it the new one.
        del fingerprint
        return card_html(card)

    common = {
        "description": card.description,
        "meta": CARD_RESOURCE_META,
        "mime_type": MCP_APP_MIME_TYPE,
        "title": card.title,
    }
    mcp.resource(card_uri(card), name=f"{card.name}_card", **common)(read_card)
    mcp.resource(card_uri_template(card), name=f"{card.name}_card_any_build", **common)(
        read_card_at
    )

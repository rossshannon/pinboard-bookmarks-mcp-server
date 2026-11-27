"""Backfill X (Twitter) likes and bookmarks into Pinboard, idempotently.

See docs/x-backfill.md. This package is separate from ``pinboard_mcp_server``
on purpose: the MCP server stays read-only, and this is the only code in the
repository that writes to Pinboard.
"""

__version__ = "0.1.0"

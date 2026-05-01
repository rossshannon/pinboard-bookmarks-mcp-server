"""Pinboard MCP Server - Read-only access to Pinboard bookmarks for LLMs."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pinboard-mcp-server")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__author__ = "Ross Shannon"
__description__ = (
    "Pinboard MCP Server - Read-only access to Pinboard bookmarks for LLMs"
)

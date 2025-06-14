"""Main entry point for the Pinboard MCP Server."""

import os
import sys
from typing import Any

from fastmcp import FastMCP  # type: ignore

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.tools import (
    list_bookmarks_by_tags,
    list_recent_bookmarks,
    list_tags,
    search_bookmarks,
)


def create_server() -> Any:
    """Create and configure the FastMCP server."""
    server: Any = FastMCP("Pinboard MCP Server")

    # Get Pinboard API token
    token = os.getenv("PINBOARD_TOKEN")
    if not token:
        print("Error: PINBOARD_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)

    # Initialize Pinboard client
    client = PinboardClient(token)

    # Register tools
    server.add_tool(search_bookmarks(client))
    server.add_tool(list_recent_bookmarks(client))
    server.add_tool(list_bookmarks_by_tags(client))
    server.add_tool(list_tags(client))

    return server


def main() -> None:
    """Main entry point."""
    try:
        server = create_server()
        server.run()
    except KeyboardInterrupt:
        print("\nServer stopped", file=sys.stderr)
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

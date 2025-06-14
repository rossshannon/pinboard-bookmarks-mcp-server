"""Main entry point for the Pinboard MCP Server."""

import asyncio
import os
from typing import Any

from fastmcp import FastMCP

from pinboard_mcp_server.client import PinboardClient
from pinboard_mcp_server.tools import (
    list_recent_bookmarks,
    list_tags,
    search_bookmarks,
    list_bookmarks_by_tags,
)


def create_server() -> FastMCP:
    """Create and configure the FastMCP server."""
    server = FastMCP("Pinboard MCP Server")
    
    # Get Pinboard API token
    token = os.getenv("PINBOARD_TOKEN")
    if not token:
        raise ValueError("PINBOARD_TOKEN environment variable is required")
    
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
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\nServer stopped")
    except Exception as e:
        print(f"Error starting server: {e}")
        raise


if __name__ == "__main__":
    main()
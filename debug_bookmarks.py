#!/usr/bin/env python3
"""Debug script to check what bookmarks are actually being retrieved."""

import asyncio
import os
import sys
from datetime import datetime

# Add src to path
sys.path.insert(0, "src")

from pinboard_mcp_server.client import PinboardClient


async def debug_bookmarks():
    """Debug what bookmarks are being retrieved."""
    token = os.getenv("PINBOARD_TOKEN")
    if not token:
        print("Error: PINBOARD_TOKEN environment variable is required")
        return

    client = PinboardClient(token)
    
    try:
        print("=== DEBUG: Checking bookmarks ===")
        
        # Get initial bookmarks (recent 100)
        bookmarks = await client.get_all_bookmarks()
        print(f"Initial bookmarks retrieved (recent): {len(bookmarks)}")
        
        if bookmarks:
            print(f"Oldest bookmark: {bookmarks[-1].saved_at} - {bookmarks[-1].title}")
            print(f"Newest bookmark: {bookmarks[0].saved_at} - {bookmarks[0].title}")
        
        print(f"Has expanded data: {client._has_expanded_data}")
        
        # Check for synology in titles, notes, and tags
        synology_matches = []
        for bookmark in bookmarks:
            if (
                "synology" in bookmark.title.lower()
                or "synology" in bookmark.notes.lower()
                or any("synology" in tag.lower() for tag in bookmark.tags)
            ):
                synology_matches.append(bookmark)
        
        print(f"\nSynology matches in current cache: {len(synology_matches)}")
        for match in synology_matches:
            print(f"  - {match.title} (tags: {match.tags}) - {match.saved_at}")
        
        # Get all tags to see if synology tag exists
        tags = await client.get_all_tags()
        synology_tags = [tag for tag in tags if "synology" in tag.tag.lower()]
        print(f"\nSynology tags: {synology_tags}")
        
        # Search using the current search function (this will auto-expand if needed)
        search_results = await client.search_bookmarks("synology", limit=50)
        print(f"\nSearch results for 'synology': {len(search_results)}")
        for result in search_results[:3]:  # Show first 3
            print(f"  - {result.title} (tags: {result.tags}) - {result.saved_at}")
        
        # Search by tags using current function (this will auto-expand if needed)
        tag_results = await client.get_bookmarks_by_tags(["synology"], limit=50)
        print(f"\nTag-based search results for 'synology': {len(tag_results)}")
        for result in tag_results[:3]:  # Show first 3
            print(f"  - {result.title} (tags: {result.tags}) - {result.saved_at}")
        
        # Check if we expanded the search
        print(f"\nExpanded search was used: {client._has_expanded_data}")
        if client._has_expanded_data:
            print(f"Total bookmarks after expansion: {len(await client.get_all_bookmarks())}")
        
    finally:
        await client.close()


if __name__ == "__main__":
    if not os.getenv("PINBOARD_TOKEN"):
        print("Please set PINBOARD_TOKEN environment variable")
        print("Example: export PINBOARD_TOKEN='username:API_TOKEN'")
        sys.exit(1)
    
    asyncio.run(debug_bookmarks())
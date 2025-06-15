# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Pinboard MCP Server built on FastMCP 2.0 that provides read-only access to Pinboard.in bookmarks for LLMs. The server exposes bookmark data via MCP tools, enabling LLMs to search, filter, and retrieve bookmark metadata during inference.

## Architecture & Design

Based on the PRD (docs/pinboard_mcp_server_prd.md), the system follows this design:

- **FastMCP 2.0** provides the MCP scaffolding with Tool abstraction, async FastAPI server, and JSON-Schema validation
- **pinboard.py** wrapper simplifies Pinboard API calls and error handling
- **Read-only** design with four main tools: `searchBookmarks`, `listRecentBookmarks`, `listBookmarksByTags`, `listTags`
- **In-memory caching** using LRU cache (1000 query results) with `posts/update` polling for cache invalidation
- **Rate limiting** respects Pinboard's 3-second guideline between requests

## Key Implementation Details

- **Field mapping**: Pinboard's `description` → `title`, `extended` → `notes` in MCP responses
- **Authentication**: Uses `PINBOARD_TOKEN` environment variable (format: `username:hex`)
- **Performance targets**: P50 < 250ms cached, P95 < 600ms cold
- **Cache strategy**: Seeds from `posts/all` API call, invalidates using `posts/update` timestamp checks

## Tech Stack (Planned)

- FastMCP 2.0, FastAPI, Uvicorn/Gunicorn
- pinboard.py ≥ 2.0.0
- Poetry for dependency management
- pytest, pytest-asyncio, responses, vcr.py for testing
- ruff, mypy for linting and type checking
- Optional: redis-lite for cache backend, OpenTelemetry for observability

## Development Notes

- Target ≥90% test coverage with mocked `pinboard.Pinboard` using pytest-monkeypatch
- Integration tests use vcr.py cassettes for API replay
- Load testing with k6 targeting 30 RPS
- Never log Pinboard API tokens for security
- All dates returned in ISO-8601 Zulu format
- When introducing or upgrading dependencies, update the `pyproject.toml` file with the new version and run `poetry update` to update the lock file. Do a search online for appropriate version numbers rather than relying on your memory.
- **Test Coverage Requirements**: The CI pipeline requires 75% test coverage to pass. Current coverage exceeds requirements with comprehensive testing of search methods, error handling, and edge cases.

## Virtual Environment

The project uses a Python virtual environment at `~/.venvs/pinboard-bookmarks-mcp-server/`. Always activate before running tests or development commands:

```bash
source ~/.venvs/pinboard-bookmarks-mcp-server/bin/activate
```

Common commands:
- `python -m pytest -v` - Run all tests
- `ruff check src/ tests/` - Run linting
- `ruff format src/ tests/` - Format code
- `mypy src/` - Run type checking

## CRITICAL: Pinboard API Usage

- **NEVER use `posts/all` without filters** - This endpoint can return hundreds of megabytes of data for active users
- Use `posts/recent` for recent bookmarks (limited to ~100 posts)
- Use `posts/all` ONLY with additional parameters like `tag=` or `fromdt=` to filter results
- Always implement pagination and limits when possible

## Generous Search Strategy

The client implements a comprehensive search strategy designed to provide rich data for LLM analysis:

1. **Tag-Based Search**: Uses `posts.all(tag=...)` to get ALL bookmarks with specific tags (most efficient)
2. **Recent Search**: Uses `posts.recent(count=100)` for fast searches of latest content
3. **Extended Search**: Uses `posts.all(fromdt=...)` with 6-month auto-expansion, up to 2-year manual lookback
4. **Intelligent Optimization**: Automatically detects exact tag matches for efficient retrieval

**Philosophy**: Be generous with data while respecting server resources. Tag-based searches are preferred for historical access, with time-based searches limited to reasonable ranges.

**Available Tools**:
- `search_bookmarks()` - Smart search with 6-month auto-expansion (up to 100 results)
- `search_bookmarks_extended()` - Configurable 1-year default search (up to 200 results)
- `list_bookmarks_by_tags()` - ALL bookmarks with specific tags (up to 200 results) - **Most efficient for historical data**
- `list_tags()` - All available tags for discovery

## Using FastMCP

### 1. Tool Registration: Use @mcp.tool Decorators, NOT Factory Functions

**WRONG** ❌ (What we initially tried):
```python
def search_bookmarks(client: PinboardClient):
    async def _search_bookmarks(params: SearchBookmarksParams, context: Context) -> SearchResult:
        # tool implementation
        pass
    return _search_bookmarks

# Register tools
mcp.add_tool(search_bookmarks(client))
```

**RIGHT** ✅ (What actually works):
```python
@mcp.tool
async def search_bookmarks(query: str, limit: int = 20) -> dict[str, Any]:
    """Search bookmarks by query string across titles, notes, and tags."""
    bookmarks = await client.search_bookmarks(query=query, limit=limit)
    return {
        "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
        "total": len(bookmarks),
        "query": query
    }
```

**Why this matters:**
- FastMCP 2.0 requires the `@mcp.tool` decorator pattern from https://gofastmcp.com/servers/tools
- Factory functions create objects without the `name` attribute that FastMCP expects
- The decorator automatically handles parameter parsing and validation
- Parameters are passed directly to the function, not wrapped in objects

### 2. Async vs Sync: NEVER Use asyncio.run() Inside MCP Tools

**WRONG** ❌ (Causes "Already running asyncio in this thread"):
```python
@mcp.tool
async def search_bookmarks(query: str) -> dict[str, Any]:
    # This breaks because MCP server already has an event loop running
    bookmarks = asyncio.run(client.search_bookmarks(query))
    return {"bookmarks": bookmarks}
```

**RIGHT** ✅ (Use await in async functions):
```python
@mcp.tool
async def search_bookmarks(query: str) -> dict[str, Any]:
    # Use await since we're already in an async context
    bookmarks = await client.search_bookmarks(query)
    return {"bookmarks": [bookmark.model_dump() for bookmark in bookmarks]}
```

**Why this matters:**
- MCP servers run inside an existing asyncio event loop
- `asyncio.run()` tries to create a new event loop, which conflicts
- Use `await` for async operations within MCP tools
- Make all MCP tool functions `async` if they need to call async APIs

### 3. Return Types: Use dict[str, Any], Not Custom Pydantic Models

**WRONG** ❌ (MCP can't serialize custom objects):
```python
@mcp.tool
async def search_bookmarks(query: str) -> SearchResult:
    bookmarks = await client.search_bookmarks(query)
    return SearchResult(bookmarks=bookmarks, query=query, total=len(bookmarks))
```

**RIGHT** ✅ (Return serializable dictionaries):
```python
@mcp.tool
async def search_bookmarks(query: str) -> dict[str, Any]:
    bookmarks = await client.search_bookmarks(query)
    return {
        "bookmarks": [bookmark.model_dump() for bookmark in bookmarks],
        "total": len(bookmarks),
        "query": query
    }
```

**Why this matters:**
- MCP expects JSON-serializable return values
- Use `.model_dump()` on Pydantic models to convert to dictionaries
- Return plain dictionaries, lists, and primitive types from MCP tools

### 4. Error Symptoms and Solutions

**"'function' object has no attribute 'name'"**
- Caused by: Using factory functions instead of `@mcp.tool` decorators
- Solution: Switch to the decorator pattern

**"Already running asyncio in this thread"**
- Caused by: Using `asyncio.run()` inside MCP tools
- Solution: Use `await` instead, make tool functions `async`

**"HTTP 500 errors from Pinboard API"**
- Caused by: Using `posts.all()` which downloads massive amounts of data
- Solution: Use `posts.recent(count=100)` or filtered `posts.all()` calls

**"'list' object has no attribute 'items'"**
- Caused by: Expecting dict when API returns list (e.g., tags API)
- Solution: Check API response format and handle both list and dict returns

### 5. Testing with FastMCP

- Mock the client, not the MCP tools themselves
- Test client methods directly in integration tests
- Use `@patch("pinboard_mcp_server.client.pinboard.Pinboard")` to mock the underlying API
- Convert test data to match pinboard.py object format (not raw dicts)

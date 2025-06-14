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
- Current implementation needs refactoring to avoid full dataset downloads
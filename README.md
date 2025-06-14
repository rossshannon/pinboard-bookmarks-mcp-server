# Pinboard MCP Server

Read-only access to Pinboard.in bookmarks for LLMs via Model Context Protocol (MCP).

## Overview

This server provides LLMs with the ability to search, filter, and retrieve bookmark metadata from Pinboard.in at inference time. Built on FastMCP 2.0, it offers four core tools for bookmark interaction while respecting Pinboard's rate limits and implementing intelligent caching.

## Features

- **Read-only access** to Pinboard bookmarks
- **Four MCP tools**: `searchBookmarks`, `listRecentBookmarks`, `listBookmarksByTags`, `listTags`
- **Smart caching** with LRU cache and automatic invalidation
- **Rate limiting** respects Pinboard's 3-second guideline
- **Field mapping** converts Pinboard's legacy field names to intuitive ones

## Installation

```bash
pip install -e .
```

## Usage

1. Get your Pinboard API token from https://pinboard.in/settings/password
2. Set the `PINBOARD_TOKEN` environment variable:
   ```bash
   export PINBOARD_TOKEN="username:token"
   ```
3. Start the server:
   ```bash
   pinboard-mcp-server
   ```

## Development

Install development dependencies:
```bash
pip install -e ".[dev]"
```

Run tests:
```bash
pytest
```

Run type checking:
```bash
mypy src/
```

Run linting:
```bash
ruff check src/ tests/
ruff format src/ tests/
```

## License

MIT
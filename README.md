# Pinboard MCP Server

[![CI](https://github.com/rossshannon/pinboard-bookmarks-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/rossshannon/pinboard-bookmarks-mcp-server/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Read-only access to Pinboard.in bookmarks for LLMs via Model Context Protocol (MCP).

## Overview

This server provides LLMs with the ability to search, filter, and retrieve bookmark metadata from Pinboard.in at inference time. Built on FastMCP, it offers five tools for bookmark interaction while respecting Pinboard's rate limits and implementing intelligent caching.

## Features

- **Read-only access** to Pinboard bookmarks
- **Five MCP tools**: `search_bookmarks`, `search_bookmarks_extended`, `list_recent_bookmarks`, `list_bookmarks_by_tags`, `list_tags`
- **Smart caching** with LRU cache and automatic invalidation using `posts/update` endpoint
- **Rate limiting** respects Pinboard's 3-second guideline between API calls
- **Field mapping** converts Pinboard's legacy field names to intuitive ones (description→title, extended→notes)
- **Result cards** for MCP Apps hosts: bookmarks and tags render as interactive cards in Claude, ChatGPT and other hosts that support [MCP Apps](https://modelcontextprotocol.io/docs/extensions/apps); other hosts see plain results as before
- **Link previews** on those cards, optionally: a favicon, site name, description and thumbnail for each bookmark, fetched through a [pinboard-bridge](#link-previews) instance you name
- **Comprehensive testing** with integration test harnesses and CI validation

## Installation

### Via pip (recommended)
```bash
pip install pinboard-bookmarks-mcp-server
```

### From source
```bash
git clone https://github.com/rossshannon/pinboard-bookmarks-mcp-server.git
cd pinboard-bookmarks-mcp-server
pip install -e .
```

## Quick Start

1. **Get your Pinboard API token** from https://pinboard.in/settings/password
2. **Set environment variable**:
   ```bash
   export PINBOARD_TOKEN="username:1234567890ABCDEF"
   ```
3. **Start the server**:
   ```bash
   pinboard-mcp-server
   ```
4. **Verify it's working**:
   ```bash
   # Test help command (works without token)
   pinboard-mcp-server --help

   # Server should show "Starting MCP server" when run with token
   ```

## Usage with Claude Desktop

Add this configuration to your Claude Desktop settings:

```json
{
  "mcpServers": {
    "pinboard": {
      "command": "pinboard-mcp-server",
      "env": {
        "PINBOARD_TOKEN": "your-username:your-token-here"
      }
    }
  }
}
```

## Available Tools

### 1. `search_bookmarks`
Search bookmarks by query string across titles, notes, and tags. Recent-focused with automatic expansion.

**Parameters:**
- `query` (string): Search query
- `limit` (int, optional): Maximum results (default: 20, max: 100)

**Example:**
```
Search for "python testing" bookmarks
```

### 2. `search_bookmarks_extended`
Extended search for comprehensive historical results across titles, notes, and tags.

**Parameters:**
- `query` (string): Search query
- `days_back` (int, optional): How many days back to search (default: 365, max: 730)
- `limit` (int, optional): Maximum results (default: 100, max: 200)

**Example:**
```
Search the last 2 years for "kubernetes" bookmarks
```

### 3. `list_recent_bookmarks`
List bookmarks saved in the last N days.

**Parameters:**
- `days` (int, optional): Days to look back (default: 7, max: 30)
- `limit` (int, optional): Maximum results (default: 20, max: 100)

**Example:**
```
Show me bookmarks from the last 3 days
```

### 4. `list_bookmarks_by_tags`
List ALL bookmarks filtered by tags with optional date range. Most efficient for historical access.

**Parameters:**
- `tags` (array): List of tags to filter by (1-3 tags)
- `from_date` (string, optional): Start date in ISO format (YYYY-MM-DD)
- `to_date` (string, optional): End date in ISO format (YYYY-MM-DD)
- `limit` (int, optional): Maximum results (default: 100, max: 200)

**Example:**
```
Find bookmarks tagged with "python" and "api" from January 2024
```

### 5. `list_tags`
List all tags with their usage counts.

**Example:**
```
What are my most used tags?
```

## Configuration

### Environment Variables

| Variable | Required | What it does |
|---|---|---|
| `PINBOARD_TOKEN` | Yes | Your Pinboard API token, in the format `username:token`. |
| `PINBOARD_MCP_APPS_UI` | No | Set to `false` (or `0`, `no`, `off`) to turn off the result cards. On by default. |
| `PINBOARD_PREVIEW_BRIDGE_URL` | No | Base URL of a pinboard-bridge instance, for example `https://example-bridge.herokuapp.com`. Turns on [link previews](#link-previews) on the bookmarks card. Off when unset. Must be `https`, except for `http://localhost` and `http://127.0.0.1`. |

### Result cards (MCP Apps)

Each tool links to a `ui://pinboard/…` card. The four bookmark tools share one that draws each bookmark as a link card: the site and date saved, the title as a link, your notes, and the tags, with the tags you searched for highlighted. `list_tags` gets a bar chart of your most-used tags with a filter. The cards follow the host's light or dark theme, use the host's fonts, open links through the host, and load nothing else from the network. Hosts without MCP Apps support ignore them.

The cards are React, built into self-contained HTML under `src/pinboard_mcp_server/ui/generated/` and shipped with the package, so running the server needs no Node. See [docs/mcp-apps-ui.md](docs/mcp-apps-ui.md) for how they work and how to change them.

### Link previews

With `PINBOARD_PREVIEW_BRIDGE_URL` set, each row of the bookmarks card also shows what the page says about itself: its favicon and site name, its description where the bookmark has no notes of its own, and a thumbnail. The bookmark's title stays the one you gave it.

The previews come from a pinboard-bridge instance, which fetches each page and reads its metadata. The card asks for them after it has rendered, through a tool only the card can call (`get_link_previews`), so they never enter the model's context. Images arrive inline in the tool result, so the card still loads nothing from the network.

**This is off by default, and you should know what turning it on does.** To preview a bookmark the server sends its URL to the bridge, along with your Pinboard token to authenticate. That includes the URLs of your private bookmarks whenever they appear in a result. The bridge then requests those pages. Only point this at a bridge you run or trust.

A row always shows the host its link goes to. The page's own name for itself appears beside the host, never in place of it.

If the bridge is slow, rate-limited or down, or the host can't call tools for a card, the card shows the bookmarks without previews. Nothing else changes.

Hosts without MCP Apps support may list `get_link_previews` to the model like any other tool. Its description says it isn't useful to call directly, and it only looks up URLs that a bookmark tool has returned in the current server process, so it can't be used to make the bridge fetch an arbitrary URL with your token.

### Rate Limiting

The server automatically enforces a 3-second delay between Pinboard API calls to respect their guidelines. Cached responses are returned immediately.

### Caching Strategy

- **Query cache**: LRU cache with 1000 entries for search results
- **Bookmark cache**: Full bookmark list cached for 1 hour
- **Cache invalidation**: Uses `posts/update` endpoint to detect changes
- **Tag cache**: Tag list cached until manually refreshed

## Testing

The project includes comprehensive test coverage with multiple test strategies:

### Run all tests
```bash
# Activate virtual environment first
source ~/.venvs/pinboard-bookmarks-mcp-server/bin/activate

# Run all tests with coverage
pytest --cov=src --cov-report=term-missing
```

### Real API testing
```bash
# Set your Pinboard token
export PINBOARD_TOKEN="username:token"

# Run debug utility to test search functionality (development only)
PINBOARD_TOKEN="username:token" python tests/debug_bookmarks.py
```

### Mock API testing
```bash
# Run comprehensive test suite (development only)
python -m pytest tests/ -v
```

## Development

### Setup
```bash
# Clone and setup
git clone https://github.com/rossshannon/pinboard-bookmarks-mcp-server.git
cd pinboard-bookmarks-mcp-server

# Quick development setup
./scripts/dev-setup.sh
```

### Code Quality
```bash
# Activate environment
source ~/.venvs/pinboard-bookmarks-mcp-server/bin/activate

# Linting and formatting
ruff check src/ tests/
ruff format src/ tests/

# Type checking
mypy src/

# Run tests
pytest -v

# Build package
./scripts/build.sh
```

### Architecture

- **FastMCP**: MCP scaffolding with Tool abstraction and async FastAPI server
- **pinboard.py**: Pinboard API client wrapper with error handling
- **Pydantic**: Data validation and serialization with JSON Schema
- **ThreadPoolExecutor**: Bridges async MCP with sync pinboard.py library
- **LRU Cache**: In-memory caching with intelligent invalidation

### Key Files

- `src/pinboard_mcp_server/main.py` - MCP server entry point and tool implementations
- `src/pinboard_mcp_server/client.py` - Pinboard API client with caching
- `src/pinboard_mcp_server/models.py` - Pydantic data models
- `src/pinboard_mcp_server/ui/` - MCP Apps card resources and tool metadata
- `cards/` - Source, fixtures and tests for the result cards (Node; see `docs/mcp-apps-ui.md`)
- `tests/` - Comprehensive test suite
- `tests/debug_bookmarks.py` - Debug utility for testing search functionality
- `docs/TEST_HARNESS.md` - Documentation for test harnesses

## Performance

- **P50 response time**: <250ms (cached responses)
- **P95 response time**: <600ms (cold cache)
- **Rate limiting**: 3-second intervals between API calls
- **Cache hit ratio**: >90% for typical usage patterns

## Security

- API tokens are never logged or exposed in error messages
- Read-only access to Pinboard data
- Input validation on all tool parameters
- Secure environment variable handling

## Troubleshooting

### Common Issues

**"PINBOARD_TOKEN environment variable is required"**
- Make sure you've set your token: `export PINBOARD_TOKEN="username:token"`
- Get your token from https://pinboard.in/settings/password
- Token format should be: `username:1234567890ABCDEF`

**"Command not found: pinboard-mcp-server"**
- Ensure you've installed the package: `pip install pinboard-bookmarks-mcp-server`
- Check your Python environment is activated
- Try reinstalling: `pip uninstall pinboard-bookmarks-mcp-server && pip install pinboard-bookmarks-mcp-server`

**Server starts but Claude Desktop can't connect**
- Verify the MCP configuration in Claude Desktop settings
- Check that the `command` path is correct: `pinboard-mcp-server`
- Ensure the `PINBOARD_TOKEN` is set in the `env` section

**"Permission denied" or "Access denied" errors**
- Verify your Pinboard token is valid and active
- Check you have internet connectivity to reach pinboard.in
- Test your token manually at https://pinboard.in/api/v1/posts/recent

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with tests
4. Ensure all tests pass and code is formatted
5. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) file for details.

# Pinboard MCP Server

[![CI](https://github.com/rossshannon/pinboard-bookmarks-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/rossshannon/pinboard-bookmarks-mcp-server/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Search your Pinboard.in bookmarks from an LLM via Model Context Protocol (MCP): full-text and semantic search over the whole account, from a local index.

## Overview

Pinboard stays the place you save to. This server mirrors the account into one SQLite file on your machine and answers every question from there, so an LLM can ask "what was that Patrick Collison tweet last week about building a feedback tool?" and get an answer in milliseconds instead of paging through Pinboard's API. It never writes to Pinboard.

## Features

- **A local search index** ([docs/search-index.md](docs/search-index.md)): SQLite with FTS5, kept in step with Pinboard in the background. Words in any order, stemming, BM25 ranking, across titles, notes, tags, URLs and tweet authors
- **Filters on every search**: date range (saved or posted), tweet author, site, tags, and source (liked tweet, bookmarked tweet, hand-saved bookmark)
- **Semantic search** (optional extra): a local embedding model finds bookmarks by meaning, fused with keyword results
- **Tweets understood**: author handle and name, and the date a tweet was posted, are read from the bookmark's URL and title
- **Seven MCP tools**: `search_bookmarks`, `list_recent_bookmarks`, `list_bookmarks_by_tags`, `list_tags`, `count_bookmarks`, `get_index_status`, and `search_bookmarks_extended` for compatibility
- **Polite to Pinboard**: one cheap call to notice a change, date-bounded `posts/all` calls to fetch it, the documented rate limits honoured
- **Result cards** for MCP Apps hosts: bookmarks and tags render as interactive cards in Claude, ChatGPT and other hosts that support [MCP Apps](https://modelcontextprotocol.io/docs/extensions/apps); other hosts see plain results as before
- **Read-only access** to Pinboard, with field names mapped to intuitive ones (description→title, extended→notes)

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
The main tool: search everything in the account, with filters. Results that contain every word rank first, then partial matches and (with the semantic extra) matches by meaning.

**Parameters** (all optional):
- `query` (string): words in any order; `"double quotes"` for an exact phrase. Empty lists everything matching the filters, newest first
- `limit` (int): maximum results (default 20, max 100); `offset` (int) pages through more when `has_more` is true
- `from_date`, `to_date` (string): ISO dates (`YYYY-MM-DD`) or datetimes, UTC. A bare `to_date` includes that whole day
- `date_field`: `saved` (default) or `posted` (when a tweet was written)
- `author` (string): tweet author by handle or name (`patrickc`, `@patrickc`, `Patrick Collison`)
- `domain` (string): site, subdomains included; `x.com` and `twitter.com` are the same
- `tags` (array): only bookmarks carrying all of these
- `source` (string): `twitter-like`, `twitter-bookmark`, `twitter` (any tweet) or `bookmark`
- `mode`: `auto` (default), `keyword`, `semantic` or `hybrid`

Each result carries `id` (stable: Pinboard's hash of the URL), `url`, `title`, `notes`, `tags`, `saved_at`, `private`, `to_read`, `domain` and `source`, plus `author_handle`, `author_name` and `posted_at` for tweets.

**Examples:**
```
What was that Patrick Collison tweet last week about building a feedback tool?
  → search_bookmarks(query="feedback tool", author="patrickc", from_date="2026-11-19")
What did I bookmark on X about SQLite this year?
  → search_bookmarks(query="sqlite", source="twitter-bookmark", from_date="2026-01-01")
```

### 2. `list_recent_bookmarks`
Everything saved in the last N days, newest first.

**Parameters:**
- `days` (int, optional): days to look back (default 7, max 365)
- `limit` (int, optional): maximum results (default 20, max 200)

### 3. `list_bookmarks_by_tags`
Bookmarks carrying all of the given tags, however old, with an optional date range.

**Parameters:**
- `tags` (array): 1-3 tags
- `from_date`, `to_date` (string, optional): ISO dates
- `limit` (int, optional): maximum results (default 100, max 200)

### 4. `list_tags`
All tags with their usage counts.

### 5. `count_bookmarks`
Counts in groups, with the same filters as `search_bookmarks`: "whose tweets do I like most?", "what was I reading about in March?".

**Parameters:**
- `group_by`: `day`, `month`, `year`, `domain`, `author`, `tag` or `source`
- `query`, `from_date`, `to_date`, `date_field`, `author`, `domain`, `tags`, `source`: as for `search_bookmarks`
- `limit` (int, optional): maximum groups (default 50, max 500)

### 6. `get_index_status`
How many bookmarks the index holds, when it last synced, whether a sync is running and whether semantic search is available. `refresh=true` asks for a sync now.

### 7. `search_bookmarks_extended`
Kept for compatibility: `search_bookmarks` limited to the last `days_back` days (default 365, max 730), up to 200 results.

## The search index

The first time the server starts it reads the whole account from Pinboard into a SQLite file, newest bookmarks first, and from then on checks for changes every five minutes. Nothing needs setting up. A sync costs one cheap API call when nothing has changed.

For a large account the first sync takes a while, because Pinboard allows one `posts/all` call every five minutes and the account is read in windows of up to 10,000 bookmarks. To skip the wait, seed the index from an export file:

```bash
# Download https://pinboard.in/export/format:json/ in your browser, then:
pinboard-mcp-index import ~/Downloads/pinboard_export.json
```

The `pinboard-mcp-index` command also runs a sync by hand, shows status and searches from a terminal:

```bash
pinboard-mcp-index status
pinboard-mcp-index sync              # needs PINBOARD_TOKEN
pinboard-mcp-index search feedback tool --author patrickc --from 2026-11-19
```

### Semantic search (optional)

```bash
pip install "pinboard-bookmarks-mcp-server[semantic]"
```

This adds a small embedding model that runs on your machine ([fastembed](https://github.com/qdrant/fastembed), `BAAI/bge-small-en-v1.5`, about 70 MB, downloaded once from Hugging Face). Bookmarks are embedded in the background after each sync, and `search_bookmarks` then combines keyword and by-meaning results. Nothing is sent anywhere.

How it all works, and why: [docs/search-index.md](docs/search-index.md).

## Configuration

### Environment Variables

- `PINBOARD_TOKEN` (required): Your Pinboard API token in format `username:token`
- `PINBOARD_INDEX` (optional): set to `off` to disable the local index; the tools then ask Pinboard directly and cover recent bookmarks only
- `PINBOARD_INDEX_PATH` (optional): where the index file lives. Default: `~/Library/Application Support/pinboard-mcp-server/bookmarks.sqlite3` on macOS, `$XDG_DATA_HOME/pinboard-mcp-server/` (or `~/.local/share/…`) on Linux, `%LOCALAPPDATA%` on Windows
- `PINBOARD_INDEX_SYNC_INTERVAL` (optional): seconds between checks for changes (default 300, minimum 60)
- `PINBOARD_SEMANTIC` (optional): set to `off` to disable semantic search even when the extra is installed
- `PINBOARD_EMBEDDING_MODEL` (optional): another fastembed model name
- `PINBOARD_MCP_APPS_UI` (optional): set to `false` to turn off the result cards; on by default

The index file holds your private bookmarks too, so it is created readable by you alone (mode 600).

### Result cards (MCP Apps)

Each tool links to a `ui://pinboard/…` card: a list of bookmarks (title, site, date, notes and tags, with the tags you searched for highlighted) for the four bookmark tools, and a bar chart of your most-used tags with a filter for `list_tags`. The cards follow the host's light or dark theme, use the host's fonts, open links through the host, and load nothing else from the network. Hosts without MCP Apps support ignore them.

The cards are React, built into self-contained HTML under `src/pinboard_mcp_server/ui/generated/` and shipped with the package, so running the server needs no Node. See [docs/mcp-apps-ui.md](docs/mcp-apps-ui.md) for how they work and how to change them.

### Rate Limiting

The server enforces Pinboard's limits: three seconds between API calls, and five minutes between `posts/all` calls. Searches never wait on either, because they run against the local index.

### Without the index

With `PINBOARD_INDEX=off`, or before the first sync has fetched anything, the tools ask Pinboard directly: the most recent 100 bookmarks, widening to six months when those do not fill the page. Every word of the query must appear (in any order) in the title, notes, tags or URL. The `author`, `domain`, `tags`, `source` and `offset` arguments and `count_bookmarks` need the index. Answers are remembered for five minutes and dropped as soon as Pinboard reports a change.

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

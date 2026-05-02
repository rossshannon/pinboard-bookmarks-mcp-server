# Pinboard MCP Server

[![CI](https://github.com/rossshannon/pinboard-bookmarks-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/rossshannon/pinboard-bookmarks-mcp-server/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Read-only access to Pinboard.in bookmarks for LLMs via Model Context Protocol (MCP).

## Overview

This server provides LLMs with the ability to search, filter, and retrieve bookmark metadata from Pinboard.in at inference time. Built on FastMCP 2.0, it offers four core tools for bookmark interaction while respecting Pinboard's rate limits and implementing intelligent caching.

## Features

- **Read-only access** to Pinboard bookmarks
- **Five MCP tools**: `search_bookmarks`, `search_bookmarks_extended`, `list_recent_bookmarks`, `list_bookmarks_by_tags`, `list_tags`
- **Smart caching** with LRU cache and automatic invalidation using `posts/update` endpoint
- **Rate limiting** respects Pinboard's 3-second guideline between API calls
- **Field mapping** converts Pinboard's legacy field names to intuitive ones (description→title, extended→notes)
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

- `PINBOARD_TOKEN` (required): Your Pinboard API token in format `username:token`

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

- **FastMCP 2.0**: MCP scaffolding with Tool abstraction and async FastAPI server
- **pinboard.py**: Pinboard API client wrapper with error handling
- **Pydantic**: Data validation and serialization with JSON Schema
- **ThreadPoolExecutor**: Bridges async MCP with sync pinboard.py library
- **LRU Cache**: In-memory caching with intelligent invalidation

### Key Files

- `src/pinboard_mcp_server/main.py` - MCP server entry point and tool implementations
- `src/pinboard_mcp_server/client.py` - Pinboard API client with caching
- `src/pinboard_mcp_server/models.py` - Pydantic data models
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

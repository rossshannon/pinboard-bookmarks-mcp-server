# Release Notes

## v1.2.0 - Local search index

The server now mirrors the Pinboard account into a local SQLite index and answers from it. See [docs/search-index.md](docs/search-index.md).

**New**
- Full-text search over the whole account: words in any order, stemming, BM25 ranking, across titles, notes, tags, URLs and tweet authors.
- Filters on `search_bookmarks`: `from_date`, `to_date`, `date_field` (saved or posted), `author`, `domain`, `tags`, `source`, plus `mode` and `offset`.
- Optional semantic search (`pip install "pinboard-bookmarks-mcp-server[semantic]"`): a local embedding model, fused with keyword results.
- Tweets saved to Pinboard are understood: author handle and name, source (`via:twitter-like`, `via:twitter-bookmark`) and the date the tweet was posted.
- New tools: `count_bookmarks` (counts by month, site, author, tag or source) and `get_index_status`.
- New command: `pinboard-mcp-index` (`sync`, `import`, `status`, `search`, `embed`).
- Background sync that finds changes anywhere in the account's history, including backdated imports, and honours Pinboard's five-minute `posts/all` limit.

**Fixed** (also when the index is switched off)
- Search matches each word separately and looks at URLs, instead of needing the whole query as one phrase in the title, notes or tags.
- Search widens beyond the most recent 100 bookmarks whenever those do not fill the page, not only when they match nothing.
- `list_recent_bookmarks` is no longer capped at the most recent 100 bookmarks.
- Searching for two or three tags reaches back through the whole account.
- Remembered answers are dropped when Pinboard changes, and expire after five minutes.
- A bookmark's `id` is stable (Pinboard's hash of the URL) instead of random on every fetch.
- A bookmark with no timestamp has no date, instead of 1 January 2024.
- `private` and `to_read` are returned.

**Changed**
- `list_recent_bookmarks` accepts up to 365 days and 200 results.
- `saved_at` may be `null`.
- Set `PINBOARD_INDEX=off` to keep the old ask-Pinboard-directly behaviour.

## v1.0.0 - Initial Release

### 🎉 **First Stable Release**

Pinboard MCP Server v1.0.0 provides production-ready, read-only access to Pinboard.in bookmarks for Large Language Models via the Model Context Protocol (MCP).

### ✨ **Features**

**Core MCP Tools:**
- `searchBookmarks` - Smart search with automatic expansion across titles, notes, and tags
- `searchBookmarksExtended` - Configurable historical search with up to 2-year lookback
- `listRecentBookmarks` - Recent bookmarks from last N days
- `listBookmarksByTags` - Efficient tag-based filtering with comprehensive results
- `listTags` - All available tags with usage counts

**Performance & Reliability:**
- **Smart caching** with LRU cache and automatic invalidation
- **Rate limiting** respects Pinboard's 3-second API guidelines
- **Optimized search strategies** for large bookmark collections (100k+)
- **Comprehensive error handling** with graceful API failure recovery

**Developer Experience:**
- **87% test coverage** with comprehensive unit and integration tests
- **CI/CD pipeline** with automated linting, type checking, and testing
- **Pre-commit hooks** for code quality enforcement
- **FastMCP 2.0** integration with proper async handling

### 🔧 **Technical Highlights**

- **Field mapping**: Converts Pinboard's legacy field names (description→title, extended→notes)
- **Multi-tier search**: Tag-optimized, recent, and extended search modes
- **Thread-safe async operations** bridging sync pinboard.py with async MCP
- **Secure authentication** via PINBOARD_TOKEN environment variable

### 📦 **Installation**

```bash
pip install pinboard-bookmarks-mcp-server
```

### 🚀 **Quick Start**

1. Get your Pinboard API token from https://pinboard.in/settings/password
2. Set environment variable: `export PINBOARD_TOKEN="username:token"`
3. Start the server: `pinboard-mcp-server`

### 🔗 **Claude Desktop Integration**

Add to your Claude Desktop configuration:

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

### 📈 **Performance**

- **Response times**: <250ms cached, <600ms cold
- **Cache hit ratio**: >90% for typical usage patterns
- **Large collection support**: Tested with 119k+ bookmarks
- **Memory efficient**: Smart pagination and filtering

### 🛡️ **Security**

- Read-only access to Pinboard data
- API tokens never logged or exposed
- Input validation on all parameters
- Secure environment variable handling

### 🧪 **Testing**

- Comprehensive test suite with pytest
- Mock-based integration tests
- Real API validation utilities
- 87% code coverage

### 📚 **Documentation**

- Complete API documentation
- Integration guides for MCP clients
- Development setup instructions
- Troubleshooting guides

---

**Full Changelog**: https://github.com/rossshannon/pinboard-bookmarks-mcp-server/commits/v1.0.0

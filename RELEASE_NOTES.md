# Release Notes

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
pip install pinboard-mcp-server
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

# Test Harness Documentation

This project includes several test harnesses to validate the MCP functionality at different levels.

## Test Harnesses

### 1. `test_mcp_harness.py` - Real API Testing

Tests the MCP tools against the actual Pinboard API with real data.

**Requirements:**
- Set `PINBOARD_TOKEN` environment variable
- Active internet connection
- Valid Pinboard account

**Usage:**
```bash
export PINBOARD_TOKEN="username:token"
python test_mcp_harness.py
```

**What it tests:**
- Real API integration with rate limiting
- Actual bookmark data retrieval
- Search functionality with real content
- Tag filtering with user's actual tags
- Cache behavior with real API responses

### 2. `test_mcp_harness_mock.py` - Mocked Testing

Tests the MCP tools with mocked Pinboard data for fast validation.

**Requirements:**
- No API credentials needed
- No internet connection required

**Usage:**
```bash
python test_mcp_harness_mock.py
```

**What it tests:**
- MCP tool function signatures
- Data structure validation
- Field mapping (description→title, extended→notes)
- Search logic with known test data
- Tool integration without external dependencies

### 3. `tests/test_integration.py` - Integration Test Suite

Comprehensive pytest-based integration tests with detailed mocking.

**Usage:**
```bash
python -m pytest tests/test_integration.py -v
```

**What it tests:**
- All MCP tool functions with comprehensive scenarios
- Error handling and edge cases
- Caching behavior validation
- Field mapping verification
- Environment variable handling
- Date filtering and sorting logic

## Regular Test Suite

The standard unit tests can be run with:
```bash
python -m pytest
```

These test individual components in isolation with comprehensive mocking.

## Testing Strategy

1. **Development**: Use `test_mcp_harness_mock.py` for quick validation
2. **Integration**: Run `tests/test_integration.py` for comprehensive testing
3. **Production validation**: Use `test_mcp_harness.py` with real credentials
4. **CI/CD**: All tests except the real API harness (no credentials in CI)

## Sample Output

### Mock Harness Output:
```
Pinboard MCP Server Mock Test Harness
=============================================
Testing with mocked data (no API credentials required)

1. Testing basic client functionality...
   ✓ Retrieved 3 bookmarks and 8 tags

2. Testing searchBookmarks tool...
   ✓ Found 2 bookmarks matching 'python'
     - Python Testing Best Practices
     - FastAPI Tutorial

...

✅ All mocked tests passed!
```

### Real API Harness Output:
```
Pinboard MCP Server Test Harness
========================================
Using API token: username:********

Testing basic client functionality...
✓ Successfully retrieved 1,247 total bookmarks
✓ Successfully retrieved 156 total tags

Testing searchBookmarks tool...
✓ Found 23 bookmarks matching 'python'

...

✅ All tests completed successfully!
```
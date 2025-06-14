# Claude Desktop Configuration

This directory contains example configurations for integrating the Pinboard MCP Server with Claude Desktop.

## Setup Instructions

1. **Install the Pinboard MCP Server** (if not already installed):
   ```bash
   pip install pinboard-mcp-server
   ```

2. **Get your Pinboard API token**:
   - Visit https://pinboard.in/settings/password
   - Copy your API token (format: `username:hexadecimal-token`)

3. **Configure Claude Desktop**:
   - Open Claude Desktop settings
   - Navigate to the MCP servers configuration
   - Add the configuration from `config.json` below
   - Replace `your-username:your-token-here` with your actual Pinboard token

## Configuration Files

### Basic Configuration (`config.json`)
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

### Configuration with Custom Port (`config-custom-port.json`)
```json
{
  "mcpServers": {
    "pinboard": {
      "command": "pinboard-mcp-server",
      "args": ["--port", "8001"],
      "env": {
        "PINBOARD_TOKEN": "your-username:your-token-here"
      }
    }
  }
}
```

### Development Configuration (`config-dev.json`)
For development with verbose logging:
```json
{
  "mcpServers": {
    "pinboard": {
      "command": "pinboard-mcp-server",
      "args": ["--debug"],
      "env": {
        "PINBOARD_TOKEN": "your-username:your-token-here",
        "LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

## Testing the Integration

Once configured, you can test the integration by asking Claude:

1. **Search bookmarks**:
   ```
   Search my Pinboard bookmarks for "python testing"
   ```

2. **List recent bookmarks**:
   ```
   Show me my recent bookmarks from the last 7 days
   ```

3. **Find bookmarks by tags**:
   ```
   Find bookmarks tagged with "javascript" and "react"
   ```

4. **List all tags**:
   ```
   What are my most used bookmark tags?
   ```

## Troubleshooting

### Common Issues

1. **"Command not found" error**:
   - Ensure `pinboard-mcp-server` is installed: `pip install pinboard-mcp-server`
   - Check that the installation directory is in your PATH

2. **Authentication errors**:
   - Verify your Pinboard token is correct
   - Ensure the token format is `username:hexadecimal-token`
   - Check that your Pinboard account is active

3. **Connection timeouts**:
   - The server respects Pinboard's 3-second rate limit
   - First requests may be slower as cache is populated
   - Subsequent requests should be much faster

4. **Permission errors**:
   - Ensure Claude Desktop has permission to run the server command
   - Check that environment variables are properly set

### Debug Mode

Enable debug mode by adding `"--debug"` to the args array:
```json
{
  "mcpServers": {
    "pinboard": {
      "command": "pinboard-mcp-server",
      "args": ["--debug"],
      "env": {
        "PINBOARD_TOKEN": "your-username:your-token-here"
      }
    }
  }
}
```

### Log Files

Server logs are typically available in:
- macOS: `~/Library/Logs/Claude/mcp-server-pinboard.log`
- Windows: `%APPDATA%\Claude\Logs\mcp-server-pinboard.log`
- Linux: `~/.local/share/claude/logs/mcp-server-pinboard.log`

## Security Notes

- Never commit your actual Pinboard token to version control
- Consider using environment variable substitution if your system supports it
- The server only requests read-only access to your Pinboard data
- Tokens are never logged or exposed in error messages

## Advanced Configuration

### Using Environment Files

You can create a `.env` file with your token:
```bash
# .env file
PINBOARD_TOKEN=your-username:your-token-here
```

Then reference it in your Claude Desktop configuration:
```json
{
  "mcpServers": {
    "pinboard": {
      "command": "pinboard-mcp-server",
      "env": {
        "PINBOARD_TOKEN": "${PINBOARD_TOKEN}"
      }
    }
  }
}
```

### Custom Cache Settings

Configure cache behavior via environment variables:
```json
{
  "mcpServers": {
    "pinboard": {
      "command": "pinboard-mcp-server",
      "env": {
        "PINBOARD_TOKEN": "your-username:your-token-here",
        "CACHE_SIZE": "2000",
        "CACHE_TTL": "7200"
      }
    }
  }
}
```
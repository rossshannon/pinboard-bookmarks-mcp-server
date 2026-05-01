#!/bin/bash
# Development setup script for Pinboard MCP Server
# Usage: ./scripts/dev-setup.sh

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}🛠️  Setting up development environment${NC}"

# Ensure we're in the project root
cd "$(dirname "$0")/.."

# Create virtual environment if it doesn't exist
VENV_PATH=~/.venvs/pinboard-bookmarks-mcp-server
if [ ! -d "$VENV_PATH" ]; then
    echo -e "${YELLOW}📦 Creating virtual environment${NC}"
    python -m venv "$VENV_PATH"
fi

# Activate virtual environment
echo -e "${YELLOW}📦 Activating virtual environment${NC}"
source "$VENV_PATH/bin/activate"

# Install the package in development mode
echo -e "${YELLOW}📦 Installing package in development mode${NC}"
pip install -e ".[dev]"

# Install pre-commit hooks
echo -e "${YELLOW}🪝 Installing pre-commit hooks${NC}"
pre-commit install

# Run tests to ensure everything works
echo -e "${YELLOW}🧪 Running tests${NC}"
pytest --cov=src --cov-report=term-missing

echo -e "${GREEN}✅ Development environment ready!${NC}"
echo -e "${YELLOW}💡 To activate: source ~/.venvs/pinboard-bookmarks-mcp-server/bin/activate${NC}"
echo -e "${YELLOW}💡 To build: ./scripts/build.sh${NC}"
echo -e "${YELLOW}💡 To test: pytest -v${NC}"

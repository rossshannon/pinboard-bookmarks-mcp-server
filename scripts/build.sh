#!/bin/bash
# Build script for Pinboard MCP Server
# Usage: ./scripts/build.sh [--test-pypi]

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🔨 Building Pinboard MCP Server${NC}"

# Ensure we're in the project root
cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d ~/.venvs/pinboard-bookmarks-mcp-server ]; then
    echo -e "${YELLOW}📦 Activating virtual environment${NC}"
    source ~/.venvs/pinboard-bookmarks-mcp-server/bin/activate
fi

# Install build tools if not present (need both: build module and twine)
if ! command -v twine &> /dev/null || ! python -c "import build" &> /dev/null; then
    echo -e "${YELLOW}🛠️  Installing build tools${NC}"
    pip install build twine
fi

# Clean previous builds
echo -e "${YELLOW}🧹 Cleaning previous builds${NC}"
rm -rf dist/ build/ src/*.egg-info/

# Build the package
echo -e "${YELLOW}🏗️  Building package${NC}"
python -m build

# Validate the build
echo -e "${YELLOW}✅ Validating build${NC}"
twine check dist/*

# Show what was built
echo -e "${GREEN}📦 Built packages:${NC}"
ls -la dist/

# Test installation in a temporary environment
echo -e "${YELLOW}🧪 Testing installation${NC}"
TEMP_ENV=$(mktemp -d)
python -m venv "$TEMP_ENV"
source "$TEMP_ENV/bin/activate"

# Install the wheel
pip install dist/*.whl

# Test the CLI
echo -e "${YELLOW}🚀 Testing CLI${NC}"
if pinboard-mcp-server --help &> /dev/null; then
    echo -e "${GREEN}✅ CLI test passed${NC}"
else
    echo -e "${RED}❌ CLI test failed${NC}"
    deactivate
    rm -rf "$TEMP_ENV"
    exit 1
fi

# Clean up test environment
deactivate
rm -rf "$TEMP_ENV"

echo -e "${GREEN}🎉 Build completed successfully!${NC}"

# Check if user wants to upload
if [ "$1" == "--test-pypi" ]; then
    echo -e "${YELLOW}🚀 Uploading to Test PyPI${NC}"
    twine upload --repository testpypi dist/*
elif [ "$1" == "--pypi" ]; then
    echo -e "${YELLOW}🚀 Uploading to PyPI${NC}"
    read -p "Are you sure you want to upload to production PyPI? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        twine upload dist/*
    else
        echo -e "${YELLOW}Upload cancelled${NC}"
    fi
else
    echo -e "${YELLOW}💡 To upload to Test PyPI: ./scripts/build.sh --test-pypi${NC}"
    echo -e "${YELLOW}💡 To upload to PyPI: ./scripts/build.sh --pypi${NC}"
fi

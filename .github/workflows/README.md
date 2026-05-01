# GitHub Actions Workflows

## Automated PyPI Publishing

This repository includes GitHub Actions workflows for automated package building and publishing.

### Package vs Repository Names
- **GitHub Repository**: `rossshannon/pinboard-bookmarks-mcp-server`
- **PyPI Package**: `pinboard-mcp-server` (https://pypi.org/project/pinboard-mcp-server/)
- **Python Import**: `import pinboard_mcp_server`

### Current Workflows

- **`ci.yml`** - Runs lint, type-check, and tests across Python 3.10–3.13 on PRs and pushes
- **`publish-trusted.yml`** - Automated PyPI publishing with trusted publishing (no API tokens)

### Setting Up Automated Publishing

#### Trusted Publishing Setup

1. **Configure PyPI Trusted Publisher**:
   - Go to https://pypi.org/manage/account/publishing/
   - Add a new trusted publisher for the **pinboard-mcp-server** package:
     - **Owner**: `rossshannon`
     - **Repository**: `pinboard-bookmarks-mcp-server` (GitHub repository name)
     - **Workflow**: `publish-trusted.yml`
     - **Environment**: `pypi`

2. **Enable the workflow**:
   - The `publish-trusted.yml` workflow is ready to use
   - Create a GitHub environment called `pypi` in repository settings
   - No API tokens needed!

### How It Works

#### Manual Process (using `scripts/build.sh`):
```bash
# 1. Update version in pyproject.toml, commit
git commit -am "Bump version to X.Y.Z"
git push origin main

# 2. Build and upload to Test PyPI first
./scripts/build.sh --test-pypi

# 3. Build and upload to production PyPI
./scripts/build.sh --pypi
```

#### With Automation (preferred):
```bash
# 1. Update version in pyproject.toml and commit
git commit -am "Bump version to X.Y.Z"
git push origin main

# 2. Create GitHub release (triggers everything automatically)
gh release create vX.Y.Z --generate-notes

# ✅ Done! GitHub Actions handles:
# - Building packages
# - Verifying integrity (twine check)
# - Testing installation
# - Publishing to PyPI via trusted publishing
# - Uploading wheel + sdist to the GitHub release
```

### Benefits of Automation

- **Consistency**: Same build process every time
- **Security**: No local API tokens needed with trusted publishing
- **Testing**: Automated installation testing before publishing
- **Reliability**: Reduces human error in release process
- **Audit Trail**: All releases tracked in GitHub Actions logs

### Workflow Triggers

- **`on: release: types: [published]`** - Triggers when you create a GitHub release
- Alternative: switch to `on: push: tags: ['v*']` if you want tags alone to publish
- Manual trigger: add `workflow_dispatch` for ad-hoc runs

### Environment Protection

The `pypi` environment can be configured with:
- **Required reviewers**: Require approval before publishing
- **Branch restrictions**: Only allow publishing from main branch
- **Wait timer**: Add delay before publishing

This gives you an extra safety net for important releases.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a GitHub Action called **fresh-eyes-only-action** that dismisses stale PR approvals when new code changes are pushed. It ensures reviewers must approve the latest version of code changes before merging.

## Architecture

### Composite Action Structure

The action is defined in `action.yml` as a composite action with three main steps:

1. **Parameter Resolution**: Handles two operational modes:
   - PR synchronize events (automatic trigger on PR updates)
   - Manual dispatch events (requires explicit inputs: pr-number, repository, base-ref, before-sha, after-sha, actor)

2. **Python Environment Setup**: Uses `astral-sh/setup-uv@v6` to set up Python 3.12 with uv package manager

3. **Approval Dismissal Logic**: Executes `utils/dismiss_approvals.py` which:
   - Detects meaningful code changes between before/after SHAs
   - Fetches approved reviews via GitHub API
   - Dismisses approvals if non-exempt actors pushed changes
   - Optionally re-requests reviews from dismissed reviewers

### Core Logic (`utils/dismiss_approvals.py`)

The script uses a sophisticated diff analysis approach:
- Compares normalized content hashes of changes (strips whitespace, comments)
- Uses GitHub CLI (`gh`) to fetch commit data and diffs
- Handles merge commits by analyzing parent commits
- Skips dismissal if actor is in exempt list (e.g., "SherpanyDevOps")

Key classes:
- `GitHubClient`: Handles all GitHub API interactions (uses httpx library)
  - `get_approved_reviews()`: Fetches paginated list of approved reviews
  - `dismiss_review()`: Dismisses a specific review by ID
  - `request_reviews()`: Re-requests reviews from dismissed reviewers

## Development Commands

### Environment Setup
```bash
cd utils
uv venv .venv
uv pip install -r requirements-dev.txt
```

### Running Tests
```bash
# Run all unit tests (excludes integration tests)
cd utils
.venv/bin/python -m pytest -v -k "not integration" --cov=dismiss_approvals --cov-report=term-missing

# Run specific test
.venv/bin/python -m pytest test_dismiss_approvals.py::TestClassName::test_method_name -v

# Run integration tests (makes real API calls - requires auth)
.venv/bin/python -m pytest -v -m integration
```

### Linting and Formatting
```bash
cd utils
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ruff format .  # Auto-fix formatting
```

## Testing Strategy

Tests are marked with pytest markers:
- `@pytest.mark.integration`: Real API tests (excluded from CI unit tests)
- Unit tests: Use mocks for GitHub API and subprocess calls

Test file: `utils/test_dismiss_approvals.py`

## Dependencies

- **Runtime** (`requirements.txt`): httpx, click
- **Development** (`requirements-dev.txt`): pytest, pytest-mock, pytest-cov, ruff

## Important Configuration

- Python version: 3.12
- GitHub API version: 2022-11-28
- Default exempt actor: "SherpanyDevOps" (hardcoded in `dismiss_approvals.py:759`)
- API pagination: MAX_PAGES=20, PER_PAGE=100
- Timeout: 30 seconds for API calls

## Action Inputs

Required for dispatch mode only:
- `pr-number`, `repository`, `base-ref`, `before-sha`, `after-sha`, `actor`

Optional inputs:
- `exempt-actors`: Comma-separated list of GitHub logins to skip dismissal
- `dismissal-message`: Custom dismissal message
- `rerequest-review`: Whether to re-request reviews (default: true)

## Code Style Requirements

- Use **ruff** for Python code formatting and linting
- **CRITICAL**: Always add a newline at the end of ALL files (Python, YAML, TOML, etc.) - this is mandatory
- **NEVER use tabs** - always use spaces for indentation (2 spaces for YAML, 4 spaces for Python)

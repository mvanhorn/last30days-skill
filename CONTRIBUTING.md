# Contributing to last30days-skill

Thank you for your interest in contributing to **last30days-skill** — the deep research engine that scans Reddit, X, Bluesky, Truth Social, YouTube, TikTok, Instagram, Hacker News, Polymarket, and the web from the last 30 days to synthesize grounded, cited reports.

This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Project Overview](#project-overview)
- [How to Contribute](#how-to-contribute)
  - [Reporting Bugs](#reporting-bugs)
  - [Suggesting Enhancements](#suggesting-enhancements)
  - [Pull Requests](#pull-requests)
- [Development Setup](#development-setup)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Adding New Data Sources](#adding-new-data-sources)
- [Security Considerations](#security-considerations)
- [License](#license)
- [Contact](#contact)

---

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainer.

---

## Project Overview

**last30days-skill** is a multi-source research tool that:

- Searches **10+ platforms** (Reddit, X/Twitter, YouTube, TikTok, Instagram, Hacker News, Polymarket, Bluesky, Truth Social, web)
- Enforces a **30-day recency window** (configurable via `--days=N`)
- Applies **multi-signal quality-ranked relevance scoring** across all sources
- Synthesizes findings into **grounded, cited reports** with real engagement data

### Technology Stack

- **Pure Python stdlib** — No pip dependencies required for core functionality
- **Python 3.10+** — Uses modern type hints and dataclasses
- **Node.js 22+** — Required for bundled X/Twitter GraphQL client (vendored)

The skill runs as a Claude Code plugin, Codex CLI skill, or standalone Python script.

---

## How to Contribute

### Reporting Bugs

If you find a bug, please [open an issue](https://github.com/mvanhorn/last30days-skill/issues) with:

1. **A clear title** — Summarize the problem concisely
2. **Steps to reproduce** — Exact commands and inputs that trigger the bug
3. **Expected behavior** — What you expected to happen
4. **Actual behavior** — What actually happened, including error messages
5. **Environment details**:
   - Python version (`python3 --version`)
   - Node.js version (`node --version`, if using X search)
   - Operating system
   - Relevant environment variables (sanitize API keys!)

**Bug report template:**

```markdown
## Bug Description
[Clear description of the bug]

## Steps to Reproduce
1. Run: `python3 scripts/last30days.py "topic"`
2. Observe: [what happens]

## Expected Behavior
[What should happen]

## Actual Behavior
[What actually happens]

## Environment
- Python: 3.12.0
- Node.js: 22.1.0
- OS: macOS 14.0

## Debug Output
```
[Paste relevant output, sanitize any API keys]
```
```

### Suggesting Enhancements

We welcome feature requests and enhancements! Please [open an issue](https://github.com/mvanhorn/last30days-skill/issues) with:

1. **A clear title** — Describe the enhancement
2. **Use case** — Why is this feature needed? What problem does it solve?
3. **Proposed solution** — How might this be implemented?
4. **Alternatives considered** — Other approaches you've thought about
5. **Additional context** — Any relevant examples, mockups, or references

**Feature request template:**

```markdown
## Feature Description
[Clear description of the proposed feature]

## Use Case
[Why is this needed? What problem does it solve?]

## Proposed Solution
[How might this be implemented?]

## Alternatives Considered
[Other approaches you've thought about]

## Additional Context
[Any relevant examples, mockups, or references]
```

### Pull Requests

We actively welcome pull requests! Here's how to submit one:

#### 1. Fork the Repository

```bash
# Fork via GitHub UI, then clone your fork
git clone https://github.com/YOUR_USERNAME/last30days-skill.git
cd last30days-skill
git remote add upstream https://github.com/mvanhorn/last30days-skill.git
```

#### 2. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

#### 3. Make Your Changes

- Follow the [Code Style Guidelines](#code-style-guidelines)
- Add or update tests as needed
- Update documentation if you change behavior
- Test your changes thoroughly

#### 4. Commit Your Changes

Write clear, descriptive commit messages:

```
feat: Add support for [new feature]

- Add X module for [purpose]
- Update Y function to handle [case]
- Add tests for Z

Fixes #123
```

Follow conventional commit prefixes:
- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation changes
- `test:` — Adding or updating tests
- `refactor:` — Code refactoring
- `chore:` — Maintenance tasks

#### 5. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then [create a pull request](https://github.com/mvanhorn/last30days-skill/compare) with:

- **Title**: Clear description of the change
- **Description**: What changed, why, and any relevant context
- **Testing**: How you tested your changes
- **Related issues**: Link any related issues (e.g., "Fixes #123")

---

## Development Setup

### Prerequisites

- **Python 3.10+** — The project uses modern type hints and dataclasses
- **Node.js 22+** — Required for X/Twitter search (bundled client)
- **yt-dlp** (optional) — For YouTube search + transcript extraction
- **API keys** — See below

### Clone and Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/last30days-skill.git
cd last30days-skill

# Create config directory
mkdir -p ~/.config/last30days

# Create .env file with your API keys
cat > ~/.config/last30days/.env << 'EOF'
# Primary: ScrapeCreators (covers Reddit + TikTok + Instagram)
SCRAPECREATORS_API_KEY=your_key_here

# Optional: X/Twitter search
AUTH_TOKEN=your_auth_token
CT0=your_ct0_token
# Or use xAI as fallback
XAI_API_KEY=xai-...

# Optional: Bluesky search
BSKY_HANDLE=you.bsky.social
BSKY_APP_PASSWORD=xxxx-xxxx-xxxx

# Optional: Web search backends
BRAVE_API_KEY=...
PARALLEL_API_KEY=...
OPENROUTER_API_KEY=...

# Optional: Legacy Reddit fallback
OPENAI_API_KEY=sk-...
EOF

chmod 600 ~/.config/last30days/.env
```

### Verify Setup

```bash
# Check source availability
python3 scripts/last30days.py --diagnose

# Run a quick test
python3 scripts/last30days.py "test topic" --quick
```

### Run Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_score.py -v

# Run with coverage
python3 -m pytest tests/ -v --cov=scripts/lib
```

---

## Code Style Guidelines

### Python Style

This project uses **pure Python stdlib** — no external dependencies for core functionality. Follow these guidelines:

#### Type Hints

Use type hints for all function signatures:

```python
from typing import Optional, List, Dict, Any

def process_results(
    items: List[Dict[str, Any]],
    min_score: float = 0.5,
    max_items: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Process and filter results by score."""
    ...
```

#### Dataclasses

Use `dataclasses` for structured data:

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class RedditItem:
    """A Reddit thread with engagement metrics."""
    id: str
    title: str
    subreddit: str
    score: int
    num_comments: int
    created_utc: datetime
    url: str
    top_comment: Optional[str] = None
    top_comment_score: int = 0
```

#### Error Handling

Use explicit error types and handle edge cases:

```python
import json
from pathlib import Path

def load_config(path: Path) -> Dict[str, str]:
    """Load environment config from file."""
    if not path.exists():
        return {}
    
    try:
        content = path.read_text()
        return dict(
            line.strip().split("=", 1)
            for line in content.splitlines()
            if "=" in line and not line.startswith("#")
        )
    except Exception as e:
        # Log the error but don't crash
        print(f"Warning: Could not load config from {path}: {e}", file=sys.stderr)
        return {}
```

#### Logging

Use the `logging` module, not `print()`:

```python
import logging

logger = logging.getLogger(__name__)

def search_reddit(query: str) -> List[Dict]:
    logger.debug(f"Searching Reddit for: {query}")
    results = _execute_search(query)
    logger.info(f"Found {len(results)} results")
    return results
```

#### Documentation

Write docstrings for all public functions and classes:

```python
def calculate_engagement_score(item: RedditItem) -> float:
    """
    Calculate composite engagement score for a Reddit item.
    
    Uses a weighted formula:
    - 50% log-scaled upvote score
    - 35% log-scaled comment count
    - 5% upvote ratio
    - 10% log-scaled top comment score
    
    Args:
        item: RedditItem with engagement metrics
    
    Returns:
        Float score normalized to [0, 1] range
    """
    ...
```

### Code Organization

- **One module per concern** — Each source has its own module (`reddit.py`, `bluesky.py`, etc.)
- **Keep scripts/ focused** — Main orchestration in `last30days.py`, utilities in `lib/`
- **Separate concerns** — Discovery, enrichment, scoring, and rendering are separate modules

### No External Dependencies

**Critical**: The core codebase must remain pure Python stdlib. Do not add pip dependencies.

If you need functionality that requires external packages:
1. Make it optional (check at runtime)
2. Provide a graceful fallback
3. Document the optional dependency

```python
# Example: Optional dependency handling
try:
    import requests  # Optional
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

def fetch_with_retry(url: str) -> Optional[str]:
    if HAS_REQUESTS:
        return requests.get(url).text
    else:
        # Fallback to stdlib
        return _stdlib_fetch(url)
```

---

## Testing

The project has **455+ tests** across all modules. All contributions should include appropriate tests.

### Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific module tests
python3 -m pytest tests/test_score.py -v

# Run with pattern matching
python3 -m pytest tests/ -v -k "polymarket"

# Run with coverage
python3 -m pytest tests/ -v --cov=scripts/lib --cov-report=term-missing
```

### Writing Tests

Follow these patterns:

```python
import pytest
from datetime import datetime, timezone
from scripts.lib.score import calculate_reddit_score

class TestRedditScoring:
    """Tests for Reddit item scoring."""
    
    def test_high_engagement_item_gets_high_score(self):
        """Items with high upvotes should score higher."""
        item = {
            "id": "test123",
            "score": 1000,
            "num_comments": 100,
            "upvote_ratio": 0.95,
            "top_comment_score": 500,
        }
        score = calculate_reddit_score(item)
        assert score > 0.5
    
    def test_low_engagement_item_gets_low_score(self):
        """Items with low engagement should score lower."""
        item = {
            "id": "test456",
            "score": 1,
            "num_comments": 0,
            "upvote_ratio": 0.5,
            "top_comment_score": 0,
        }
        score = calculate_reddit_score(item)
        assert score < 0.3
    
    def test_missing_fields_handled_gracefully(self):
        """Missing optional fields should not crash."""
        item = {"id": "test789"}
        # Should not raise
        score = calculate_reddit_score(item)
        assert score >= 0
```

### Test Categories

- **Unit tests** — Test individual functions in isolation
- **Integration tests** — Test module interactions
- **Smoke tests** — Quick sanity checks for basic functionality
- **Mock tests** — Use `--mock` flag for API-free testing

```python
# Use mock mode for CI testing
def test_search_with_mock():
    """Test search using local fixtures."""
    result = subprocess.run(
        ["python3", "scripts/last30days.py", "test topic", "--mock"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
```

---

## Project Structure

```
last30days-skill/
├── README.md              # Full documentation (71KB)
├── SKILL.md               # Agent skill definition (35KB)
├── CHANGELOG.md           # Version history
├── SPEC.md                # Architecture specification
├── LICENSE                # MIT License
├── scripts/
│   ├── last30days.py      # Main orchestrator (80KB)
│   ├── watchlist.py       # Watchlist management
│   ├── store.py           # SQLite persistence
│   ├── briefing.py        # Briefing generation
│   └── lib/               # Core modules
│       ├── __init__.py
│       ├── bird_x.py      # X/Twitter GraphQL client
│       ├── bluesky.py     # Bluesky/AT Protocol search
│       ├── brave_search.py # Brave Search API
│       ├── cache.py       # 24-hour TTL caching
│       ├── dates.py       # Date range calculation
│       ├── dedupe.py      # Near-duplicate detection
│       ├── entity_extract.py # Handle/entity extraction
│       ├── env.py         # Config and auth loading
│       ├── hackernews.py  # HN Algolia search
│       ├── http.py        # stdlib HTTP client
│       ├── instagram.py   # Instagram Reels (ScrapeCreators)
│       ├── models.py      # Model selection logic
│       ├── normalize.py   # Response normalization
│       ├── openai_reddit.py # Reddit via OpenAI
│       ├── polymarket.py  # Prediction market search
│       ├── query.py       # Query construction
│       ├── query_type.py  # Intent classification
│       ├── reddit.py      # Reddit (ScrapeCreators)
│       ├── reddit_enrich.py # Thread enrichment
│       ├── relevance.py   # Text similarity scoring
│       ├── render.py      # Output formatting
│       ├── schema.py      # Type definitions
│       ├── score.py       # Engagement scoring
│       ├── tiktok.py      # TikTok (ScrapeCreators)
│       ├── truthsocial.py # Truth Social search
│       ├── ui.py          # Terminal UI helpers
│       ├── websearch.py   # Web search backends
│       ├── xai_x.py       # X via xAI API
│       └── youtube_yt.py  # YouTube via yt-dlp
├── tests/                 # Test suite (455+ tests)
│   ├── test_*.py          # Unit tests per module
│   └── __init__.py
├── variants/              # Skill variants
│   └── open/              # Open Claw watchlist mode
├── docs/                  # Additional documentation
├── fixtures/              # Test fixtures
└── assets/                # Images and media
```

---

## Adding New Data Sources

To add a new data source, follow this checklist:

### 1. Create the Module

Create `scripts/lib/newsource.py`:

```python
"""New source search implementation."""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

@dataclass
class NewSourceItem:
    """Item from new source."""
    id: str
    title: str
    url: str
    created_at: datetime
    # Add source-specific fields

def search_newsource(
    query: str,
    days: int = 30,
    api_key: Optional[str] = None,
) -> List[NewSourceItem]:
    """
    Search the new source for items matching query.
    
    Args:
        query: Search query string
        days: Lookback window in days
        api_key: Optional API key for authenticated search
    
    Returns:
        List of NewSourceItem objects
    """
    # Implementation
    ...
```

### 2. Add Scoring

Update `scripts/lib/score.py`:

```python
def calculate_newsource_score(item: NewSourceItem) -> float:
    """Calculate engagement score for new source item."""
    # Use consistent scoring formula
    ...
```

### 3. Add Normalization

Update `scripts/lib/normalize.py`:

```python
def normalize_newsource(item: NewSourceItem) -> NormalizedItem:
    """Convert new source item to canonical format."""
    ...
```

### 4. Add to Orchestrator

Update `scripts/last30days.py`:

```python
# Import your module
from lib.newsource import search_newsource

# Add to search pipeline
def run_searches(query: str, ...):
    futures = {
        # ... existing sources
        "newsource": executor.submit(search_newsource, query, days),
    }
```

### 5. Add Rendering

Update `scripts/lib/render.py`:

```python
def render_newsource_items(items: List[NewSourceItem]) -> str:
    """Render new source items to markdown."""
    ...
```

### 6. Add Tests

Create `tests/test_newsource.py`:

```python
import pytest
from scripts.lib.newsource import search_newsource, NewSourceItem

class TestNewSource:
    def test_search_returns_items(self):
        ...
    
    def test_item_scoring(self):
        ...
```

### 7. Update Documentation

- Update `README.md` with new source
- Update `SKILL.md` stats template
- Update `SPEC.md` architecture

---

## Security Considerations

### API Key Handling

- **Never commit API keys** to the repository
- Keys are stored in `~/.config/last30days/.env` with `chmod 600`
- Each key is transmitted only to its respective endpoint
- Keys are never logged or written to output files

### Data Privacy

- User queries are sent to external APIs (documented in README)
- No user data is stored beyond local caching
- Research briefings are saved locally only (user's machine)

### When Contributing

- Sanitize API keys in bug reports and test output
- Don't add logging that outputs credentials
- Don't add dependencies that transmit data to undocumented endpoints

---

## License

By contributing to this project, you agree that your contributions will be licensed under the [MIT License](LICENSE).

```
MIT License

Copyright (c) 2026 Matt Van Horn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

## Contact

- **Issues**: [GitHub Issues](https://github.com/mvanhorn/last30days-skill/issues)
- **Pull Requests**: [GitHub PRs](https://github.com/mvanhorn/last30days-skill/pulls)
- **Author**: [@mvanhorn](https://github.com/mvanhorn)

---

Thank you for contributing to last30days-skill! 🎉
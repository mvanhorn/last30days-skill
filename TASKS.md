# last30days Implementation Tasks

> Historical checklist from the original implementation. Current runtime code
> lives under `skills/last30days/scripts/`.

## Setup & Configuration
- [x] Create directory structure
- [x] Write SPEC.md
- [x] Write TASKS.md
- [x] Write SKILL.md with proper frontmatter

## Core Library Modules
- [x] scripts/lib/env.py - Environment and API key loading
- [x] scripts/lib/dates.py - Date range and confidence utilities
- [x] scripts/lib/cache.py - TTL-based caching
- [x] scripts/lib/http.py - HTTP client with retry
- [x] scripts/lib/models.py - Auto model selection
- [x] scripts/lib/schema.py - Data structures
- [x] scripts/lib/openai_reddit.py - OpenAI Responses API
- [x] scripts/lib/xai_x.py - xAI Responses API
- [x] scripts/lib/reddit_enrich.py - Reddit thread JSON fetcher
- [x] scripts/lib/normalize.py - Schema normalization
- [x] scripts/lib/score.py - Popularity scoring
- [x] scripts/lib/dedupe.py - Near-duplicate detection
- [x] scripts/lib/render.py - Output rendering

## Main Script
- [x] scripts/last30days.py - CLI orchestrator

## Fixtures
- [x] fixtures/openai_sample.json
- [x] fixtures/xai_sample.json
- [x] fixtures/reddit_thread_sample.json
- [x] fixtures/models_openai_sample.json
- [x] fixtures/models_xai_sample.json

## Tests
- [x] tests/test_dates.py
- [x] tests/test_cache.py
- [x] tests/test_models.py
- [x] tests/test_score.py
- [x] tests/test_dedupe.py
- [x] tests/test_normalize.py
- [x] tests/test_render.py

## Validation
- [x] Run tests in mock mode
- [x] Demo --emit=compact
- [x] Demo --emit=context
- [x] Verify file tree

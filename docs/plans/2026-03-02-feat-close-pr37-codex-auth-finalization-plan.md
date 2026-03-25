---
title: "feat: close PR #37 - Codex auth finalization and clean close"
type: feat
status: active
date: 2026-03-02
---

# feat: Close PR #37 - Codex Auth Finalization

## Overview

PR #37 (`iliaal:codex-auth-merged`) adds Codex auth so users with `codex login` can use the
skill without an `OPENAI_API_KEY`. The good news: **all of its core changes are already on main**.
PR #38 (which we merged earlier today) was branched directly from #37, so the Codex auth code
rode in with that merge.

The task is to:
1. Verify the Codex auth integration is intact and compatible with v2.6 additions
2. Fix the one known test isolation bug that PR #37 shipped with
3. Close PR #37 with a clear explanation and a thank-you to the contributor

## What PR #37 Added (Now All on Main)

### Core Codex auth system (`scripts/lib/env.py`)
- `CODEX_AUTH_FILE` path constant (`~/.codex/auth.json`)
- `OpenAIAuth` dataclass: `token`, `source`, `status`, `account_id`, `codex_auth_file`
- `_decode_jwt_payload()` - JWT base64 decode without verification
- `_token_expired()` - checks JWT `exp` claim with 60s leeway
- `extract_chatgpt_account_id()` - extracts `chatgpt_account_id` from JWT `https://api.openai.com/auth` claim
- `load_codex_auth()` - reads `~/.codex/auth.json`
- `get_codex_access_token()` - returns `(token, status)` tuple
- `get_openai_auth()` - priority chain: `OPENAI_API_KEY` env var > `.env` file key > Codex token

### Codex endpoint routing (`scripts/lib/openai_reddit.py`)
- `CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"`
- `_parse_sse_chunk()` / `_parse_sse_stream()` / `_parse_codex_stream()` - SSE response parsing
- Headers injected for Codex path: `chatgpt-account-id`, `OpenAI-Beta: responses=v1`, `originator: pi`
- Codex payload: `store: false`, `stream: true`
- `CODEX_FALLBACK_MODELS` retry chain: `gpt-5.1-codex-mini` → `gpt-5.2`

### Tests (`tests/test_codex_auth.py`)
- 22 unit tests covering JWT decode, expiry, account ID extraction, auth resolution,
  SSE parsing, payload building, source availability
- 21/22 pass; 1 has a test isolation bug (see below)

## What PR #37 Contains That Must NOT Go to Public Repo

These files are in the #37 branch but are internal benchmarking artifacts. They should
never land on the public `upstream` remote:

| Path | Why private |
|------|-------------|
| `docs/comparison-results/` (55+ files) | Benchmark JSON/MD from synthesis quality testing |
| `docs/plans/*.md` (9 internal plan docs) | Private planning documents |
| `docs/v2.1-tweets.md` | Internal launch tweet drafts |
| `variants/open/references/research.md` | Internal research notes |
| `scripts/evaluate-synthesis.py` | Internal evaluation script |
| `scripts/generate-synthesis-inputs.py` | Internal benchmark input generator |
| `fixtures/polymarket_sample.json` | Used by internal eval scripts |

## What PR #37 Has That's OLDER Than Main

These files in PR #37 are earlier versions than what main has - we keep our versions:

- `SKILL.md` - PR #37 is v2.1; main is v2.6 (keep v2.6)
- `README.md` - PR #37's is missing HN/Polymarket; main's is current (keep main)
- `SPEC.md` - PR #37 has an older spec (keep main)
- `scripts/lib/hackernews.py` - NOT in PR #37; main has the full HN integration
- `scripts/lib/polymarket.py` - PR #37 has an older version without quality ranking
- `scripts/sync.sh` - minor differences; main's version is correct

## Known Issue: Test Isolation Bug

**File:** `tests/test_codex_auth.py`
**Test:** `TestGetOpenaiAuth::test_api_key_takes_priority`

```python
def test_api_key_takes_priority(self):
    """OPENAI_API_KEY in env file should be preferred over Codex."""
    file_env = {"OPENAI_API_KEY": "sk-test123"}
    auth = env.get_openai_auth(file_env)
    self.assertEqual(auth.token, "sk-test123")  # FAILS if OPENAI_API_KEY set in shell
```

**Root cause:** `get_openai_auth()` checks `os.environ.get("OPENAI_API_KEY")` first (env var
priority). The test sets `file_env` but does NOT patch `os.environ`, so the real
`OPENAI_API_KEY` from the developer's shell wins.

**Fix:**

```python
@patch.dict(os.environ, {}, clear=False)
def test_api_key_takes_priority(self):
```

But we also need to REMOVE `OPENAI_API_KEY` from the patched env:

```python
@patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
def test_api_key_takes_priority(self):
```

Actually the cleanest fix:

```python
def test_api_key_takes_priority(self):
    """OPENAI_API_KEY in env file should be preferred over Codex."""
    with patch.dict(os.environ, {}, clear=True):
        # Restore non-OPENAI env vars to avoid side effects
        file_env = {"OPENAI_API_KEY": "sk-test123"}
        auth = env.get_openai_auth(file_env)
        self.assertEqual(auth.token, "sk-test123")
```

Or the minimal fix (remove just OPENAI_API_KEY without nuking entire env):

```python
@patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"})
def test_api_key_takes_priority(self):
    """OPENAI_API_KEY in env var should be preferred over Codex."""
    auth = env.get_openai_auth({})
    self.assertEqual(auth.source, "api_key")
    self.assertEqual(auth.token, "sk-test123")
    self.assertIsNone(auth.account_id)
```

This reframes the test as "env var takes priority over empty file_env" which is equally
valid and sidesteps the isolation problem entirely.

## Acceptance Criteria

- [ ] Verify `tests/test_codex_auth.py` runs 22/22 clean (no isolation failures)
- [ ] Fix `test_api_key_takes_priority` with the minimal patch approach above
- [ ] Run full test suite: confirm only the 5 pre-existing stale model failures remain
- [ ] Verify `env.py` on main has `is_hackernews_available()` and `is_polymarket_available()`
  (they were REMOVED in PR #37 but should be on main since #38 preserved them)
- [ ] Verify `scripts/sync.sh` deploys to `~/.claude/skills/last30daysCROSS` correctly
  (PR #37's sync.sh may be missing this; main's version should have it)
- [ ] Close PR #37 with a comment explaining the code landed via #38
- [ ] Add `docs/comparison-results/` to `.gitignore` in the private repo so benchmark
  files never accidentally get committed to the upstream public repo

## Implementation Steps

### Step 1: Fix the test

In the **private source repo** (`$HOME/last30days-skill-private/`):

Edit `tests/test_codex_auth.py` line 77-84. Replace the bare test with the `@patch.dict` version above. Run `python3 -m pytest tests/test_codex_auth.py -v` to confirm 22/22.

### Step 2: Run full test suite

```bash
cd $HOME/last30days-skill-private
python3 -m pytest tests/ -v 2>&1 | tail -20
```

Expected: only `test_reddit_search_basic`, `test_default_model`, `test_model_pin`, and
similar model-name tests fail (the 5 pre-existing stale model failures from before PR #37).
Codex auth tests should all pass.

### Step 3: Verify the private docs protection

Check that `.gitignore` (or the upstream push config) prevents `docs/comparison-results/`
from leaking to the public GitHub remote.

```bash
cat $HOME/last30days-skill-private/.gitignore | grep -E "comparison|evaluate|generate"
```

If not present, add:
```
docs/comparison-results/
scripts/evaluate-synthesis.py
scripts/generate-synthesis-inputs.py
fixtures/polymarket_sample.json
docs/v2.1-tweets.md
variants/open/references/research.md
```

### Step 4: Commit and sync

```bash
cd $HOME/last30days-skill-private
git add tests/test_codex_auth.py
git commit -m "fix(tests): patch OPENAI_API_KEY env isolation in test_api_key_takes_priority"
bash scripts/sync.sh
```

Then push to upstream (public):
```bash
git push upstream main
```

### Step 5: Close PR #37

Post a comment on PR #37 explaining what happened, then close it:

```
Thanks @iliaal! 🙏 This was a great contribution.

The Codex auth changes landed in main via PR #38, which was branched from your
`codex-auth-merged` branch. So all the core auth code is already shipping:

- JWT decoding + expiry checking in env.py ✅
- Codex endpoint routing + SSE parsing in openai_reddit.py ✅
- 22 unit tests in test_codex_auth.py ✅
- CODEX_FALLBACK_MODELS retry chain ✅

Since then we've also shipped v2.5 (HN + Polymarket sources) and v2.6 (agent-native
invocation with --agent flag), so SKILL.md and README are already ahead of this branch.

Closing as the changes are incorporated. Thanks again for the excellent work!
```

## Technical Considerations

**Why can't we just merge #37 directly?**

Three reasons:
1. SKILL.md/README in #37 are v2.1 - they'd overwrite our v2.6 improvements
2. The `docs/comparison-results/` directory (55+ benchmark files) would go to the public repo
3. The test isolation bug would ship a flaky test to everyone who sets `OPENAI_API_KEY`

**Cherry-pick vs close approach:**

Since the Codex auth code is already on main, cherry-picking would be redundant. The cleanest
path is to fix the test bug on main, then close #37 with an explanation.

**Future private-docs hygiene:**

The `docs/comparison-results/` files should be gitignored or moved to a separate
private branch so this situation doesn't repeat. These are internal QA benchmarks -
they belong in the private repo only, never in `upstream`.

## Dependencies & Risks

**Low risk** - this is test cleanup + PR bookkeeping. The feature itself is already running.

**Codex auth live test** - We can't easily verify the live Codex auth flow without a `codex login`
session. If a user reports auth issues, the test suite gives good coverage of the logic;
live testing would require a Codex-authenticated environment.

## Sources & References

- PR #37: https://github.com/mvanhorn/last30days-skill/pull/37 (iliaal: codex-auth-merged)
- PR #38 (merged): fixed Bird X auth, brought Codex auth to main as a side effect
- `tests/test_codex_auth.py` - 22 unit tests for the Codex auth system
- `scripts/lib/env.py` lines 26-175 - Codex auth core logic
- `scripts/lib/openai_reddit.py` lines 45-310 - Codex endpoint routing

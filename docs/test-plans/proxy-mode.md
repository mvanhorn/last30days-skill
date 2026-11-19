# Proxy mode: live test plan

Manual end-to-end check for `LAST30DAYS_API_MODE=proxy` against the hosted service. It proves that one API key and no other provider keys produce a full local report, that free sources stay local and free, and that what the skill reports as charged matches what the service billed. Run it before merging any change to proxy mode.

It spends real credits: about one quick and one default run.

## Setup

Use a shell where no provider key, config file, or Keychain item can leak in.

```bash
# From the repo root, on the branch under test.
export L30D_TEST_HOME="$(mktemp -d)"
mkdir -p "$L30D_TEST_HOME/config"

run_proxy() {
  env -i \
    PATH="$PATH" \
    HOME="$L30D_TEST_HOME" \
    LAST30DAYS_CONFIG_DIR="$L30D_TEST_HOME/config" \
    LAST30DAYS_SKIP_KEYCHAIN=1 \
    LAST30DAYS_API_MODE=proxy \
    LAST30DAYS_API_BASE="<hosted API base, ending in /api/v1>" \
    LAST30DAYS_API_KEY="$(cat <path to the test key file>)" \
    uv run python skills/last30days/scripts/last30days.py "$@"
}
```

- The config dir is empty, so no `.env` is read. `LAST30DAYS_SKIP_KEYCHAIN=1` keeps Keychain keys out. `env -i` drops every other variable, including provider keys and `LAST30DAYS_PASS_PREFIX`.
- Never paste the key into the command line, a log, or this file.
- Confirm the isolation first: `run_proxy doctor` should show "credentials present: none" and `paid lanes via API key (LAST30DAYS_API_MODE=proxy): SCRAPECREATORS_API_KEY, XAI_API_KEY, SERPER_API_KEY, PARALLEL_API_KEY`, with the ScrapeCreators sources and the X auth path reported "via API key".

## Balance snapshots

Before and after each run, record:

- `GET {base}/credits` with the key: `credits`, `reserved`, `available_credits`, and the `skill_sessions` entry for the run's session id.
- `GET {base}/credits/activity`: the session entry for the run.
- The account's `proxy_credit_carry` value. The API does not expose it; read it from the profile row (operator, read only).

`reserved` should be back to its starting value after each run. A non-zero leftover means a hold was not settled or released.

## Runs

Pick topics with recent activity on X, TikTok, YouTube, and the web, and on at least one free source (Hacker News, GitHub, or Polymarket).

1. Quick: `run_proxy "<topic A>" --quick --emit=compact 2> quick.stderr > quick.out`
2. Default: `run_proxy "<topic B>" --emit=compact 2> default.stderr > default.out`

For each run, keep:

- The footer's `💳 API key:` line from stdout.
- The last stderr line, `[last30days] API key session <id>: <credits> credits across <n> paid calls`.
- Per-source item counts from the footer.
- Any stderr line with `/proxy/` in it (`LAST30DAYS_DEBUG=1` logs each request URL; add it to `run_proxy` for one run if you need the per-call list).

Optional comparison: run topic B once in hosted mode (same key and base, `LAST30DAYS_API_MODE` unset) and note its per-source counts and charge.

## Checks

1. Footer total equals the activity entry: the `💳 API key:` credits equal the session's charged credits in `/credits/activity` (and in `skill_sessions`), to six decimal places, and the call counts match.
2. Balance delta plus carry change equals the total: `(credits before - credits after) + (carry after - carry before)` equals the footer total.
3. Free sources show zero proxy calls: no `/proxy/` request for Hacker News, Reddit public, GitHub, Polymarket, or any other keyless source, and their items appear in the footer.
4. Paid lanes return results: X (via xAI), TikTok, Instagram, YouTube, and web search each return items, and the planner and rerank ran on xAI rather than the deterministic fallback.
5. No secrets leaked: `grep -c "<first 12 characters of the key>" quick.* default.*` returns 0 for every file.

Out-of-credits check (optional, needs a key with 0 available credits): run one quick topic. Expect one stderr line saying the key is out of credits with the billing link, no further `/proxy/` calls, a report built from free sources, and no charge.

## Results

Fill in after the run.

| Field | Quick run | Default run |
| --- | --- | --- |
| Date and skill commit | 2026-11-19, web fallback fix on top of `6526abbf` | same |
| Topic | OpenAI Codex | Claude Code skills |
| Session id | `skill-16c060a58fbd4c38b87c55b536b0a277` | `skill-4f44ef1ae6c749b79a1cba065146ea1c` |
| Footer `💳 API key:` line | 9.383246 credits across 17 paid calls | 50.030618 credits across 25 paid calls |
| Activity entry credits and calls | 9.38 credits, 17 calls (`skill_sessions`, rounded to cents) | 50.03 credits, 25 calls |
| Credits before / after | 71907 / 71897 | 71897 / 71847 |
| Reserved before / after | 0 / 0 | 0 / 0 |
| Carry before / after | 0.820316 / 0.203562 (derived from the debits; not read from the row) | 0.203562 / 0.234180 (derived) |
| Balance delta + carry change | 10 + (0.203562 - 0.820316) = 9.383246 | 50 + (0.234180 - 0.203562) = 50.030618 |
| Free-source items (HN / Reddit / GitHub / Polymarket) | HN 3, Reddit 6 | HN 16, Reddit 29, arXiv 13, Techmeme 10 |
| Free-source proxy calls | 0 (HN, Arctic Shift, keyless site search went direct) | 0 |
| Paid-lane items (X / TikTok / Instagram / YouTube / web) | planner picked Reddit, HN, web, jobs; Web 5 | X 13, TikTok 25, Instagram 7, YouTube 2 (2/2 transcripts), Web 21 |
| Planner and rerank provider | xAI through the proxy (3 calls) | xAI through the proxy (4 calls) |
| Hosted-mode comparison (items, charge) | not run | not run |
| Key found in any output file | no (0 matches in stdout and stderr) | no |
| Check 1 pass | yes | yes |
| Check 2 pass | yes | yes |
| Check 3 pass | yes | yes |
| Check 4 pass | yes (lanes the planner chose) | yes |
| Notes | A first run on `6526abbf` found the service holds no Serper key: every proxied web search failed with `provider_unavailable`. Fixed in this PR: web search now moves to the next backend (Parallel). Each run still makes up to 3 free 503 Serper calls before the lane is marked off. | Reddit search and comments also went through the ScrapeCreators lane, as R5 expects; Reddit's keyless path still ran locally. |

Out-of-credits check:

| Field | Result |
| --- | --- |
| Hint printed once with billing link | |
| Proxy calls after the 402 | |
| Report rendered from free sources | |
| Credits charged | |

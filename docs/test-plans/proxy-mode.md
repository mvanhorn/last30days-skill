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
| Date and skill commit | | |
| Topic | | |
| Session id | | |
| Footer `💳 API key:` line | | |
| Activity entry credits and calls | | |
| Credits before / after | | |
| Reserved before / after | | |
| Carry before / after | | |
| Balance delta + carry change | | |
| Free-source items (HN / Reddit / GitHub / Polymarket) | | |
| Free-source proxy calls | | |
| Paid-lane items (X / TikTok / Instagram / YouTube / web) | | |
| Planner and rerank provider | | |
| Hosted-mode comparison (items, charge) | | |
| Key found in any output file | | |
| Check 1 pass | | |
| Check 2 pass | | |
| Check 3 pass | | |
| Check 4 pass | | |
| Notes | | |

Out-of-credits check:

| Field | Result |
| --- | --- |
| Hint printed once with billing link | |
| Proxy calls after the 402 | |
| Report rendered from free sources | |
| Credits charged | |

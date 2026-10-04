# Security

This document is the trust model for the `last30days` research skill. It
explains what the skill does on your machine, where it sends data, how it
handles credentials, and how to report a security issue — so you can evaluate
it *before* you install or run it, including when an installer's security scan
flags it.

The skill is open source (MIT). Every script it ships lives in this repository
under `skills/last30days/`, so every claim below is verifiable by reading the
code. The agent-facing behavior contract is the "Security & Permissions"
section of `skills/last30days/SKILL.md`; the knob-by-knob reference is
`CONFIGURATION.md`.

## What the skill does

`/last30days` researches what people are saying about a topic across public
sources (Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, Bluesky,
TruthSocial, Digg, jobs boards, and the web) and writes a cited research
briefing.

Mechanically, it:

- runs a local Python engine (`skills/last30days/scripts/last30days.py`) and,
  for X search, a vendored Node client that is a search-only subset of the MIT
  `@steipete/bird` CLI (`scripts/lib/vendor/bird-search/`);
- makes outbound HTTPS calls only to the platforms being researched and to
  providers you configure (see "Network destinations");
- runs local binaries when present: `yt-dlp` (YouTube transcripts), the `gh`
  CLI (GitHub search), `digg-pp-cli` (Digg), and optionally the `xurl` CLI
  (official X API v2, OAuth2);
- reads optional credentials from environment variables, `.env` files, the
  macOS Keychain, or `pass` (see "Credentials");
- writes briefings to `LAST30DAYS_MEMORY_DIR`, which defaults to `~/Documents/Last30Days/`, and, in watchlist mode only, keeps local state in
  a SQLite database.

The engine contains no telemetry, analytics, or phone-home code.

## What the skill does not do

- It never posts, likes, replies, or modifies anything on any platform.
- It never accesses your accounts beyond performing searches — it cannot
  publish as you.
- It never shares a key across providers: the OpenAI key only goes to
  `api.openai.com`, the xAI key only to `api.x.ai`, and so on.
- It never logs or writes API keys into report files, and debug output redacts
  request keys (including keys a provider echoes back in an error body).
- It never sends data to endpoints other than the ones listed below.

## Trust boundary

The skill runs with the same permissions as the agent that invokes it: an
agent running it can read any file and run any program the user account can.
Treat "review before use" as applying to the agent runtime as much as to the
skill. Install through a CLI you trust (`npx skills add
mvanhorn/last30days-skill`), pin the version you reviewed, and avoid running
the skill under accounts that hold credentials you are not willing to expose
to the agent.

## Why installer security scanners flag this skill

The `npx skills` installer runs three independent scanners — Gen Agent Trust
Hub, Socket, and Snyk — and shows their results at install time. Research
aggregators look unusual to supply-chain scanners by design: the skill's core
job is outbound network access, subprocess execution (Python, Node, `yt-dlp`,
`gh`), and optional use of API-key environment variables, all behaviors
scanners conservatively rate as risky. Current results are public at
<https://skills.sh/mvanhorn/last30days-skill> and change as both the skill and
the scanners evolve.

If a scan flags this repository, check the specific finding rather than the
headline risk: the repo's own CI (see "Security posture") runs dependency
audits and SAST on every commit, and the engine's network behavior is the
documented list below, not an open pipe.

## Credentials

Several sources need no credentials at all: Hacker News, Polymarket, public
Reddit, YouTube, GitHub (uses the `gh` CLI's existing auth), and Digg. Optional
keys unlock the remaining sources and are resolved in this priority order:

1. process environment variables;
2. project-scoped `.claude/last30days.env`, then the global
   `~/.config/last30days/.env`;
3. macOS Keychain items prefixed `last30days-` (`scripts/setup-keychain.sh`);
4. a `pass`(1) store (`scripts/setup-pass.sh`) as the lowest-priority source,
   decrypted transiently so secrets stay encrypted at rest.

Rules the engine enforces:

- secret files must be `0600` on POSIX hosts; the engine warns on every run if
  they are not (`lib/env.py`, `_check_file_permissions`);
- keys stay with their provider and are never written into output files;
- `--diagnose` reports which sources are *available*, not the keys themselves.

The source-by-source key table lives in `CONFIGURATION.md`.

### Browser-cookie authentication for X

When no explicit X credential is configured, the engine by default probes
Firefox and Safari cookie stores for X session cookies, reading local files
silently. Chromium-family browsers are only probed when you opt in, because
their cookie stores require a macOS Keychain prompt. Control this explicitly:

```bash
FROM_BROWSER=off      # never touch browser cookie stores (recommended for strict setups)
FROM_BROWSER=firefox  # only a specific browser
FROM_BROWSER=auto     # also try Chromium-family browsers (may prompt for Keychain access)
```

Cookie reads stay on your machine — extracted cookies are sent only to X
endpoints for the search being run. If you prefer zero ambient access, set
`FROM_BROWSER=off` and provide `XAI_API_KEY` or another explicit X credential
instead.

## Network destinations

Outbound calls go to the platforms being researched and to providers you
configure. Grouped by purpose:

| Purpose | Hosts |
|---|---|
| X / Twitter search | `x.com`, `twitter.com`, `upload.twitter.com` (cookie auth), `api.x.ai` (xAI), `xquik.com`, `api.scrapecreators.com` |
| Reddit | `reddit.com`, `www.reddit.com` (public data; ScrapeCreators only as a backup) |
| YouTube / TikTok / Instagram / Threads / Pinterest | `www.youtube.com` (via local `yt-dlp`), `www.tiktok.com`, `www.instagram.com`, `www.threads.net`, `www.pinterest.com`, `api.scrapecreators.com` |
| Hacker News | `hn.algolia.com`, `news.ycombinator.com` |
| Polymarket | `polymarket.com`, `gamma-api.polymarket.com` |
| GitHub | `github.com`, `api.github.com` (via `gh`) |
| Bluesky / TruthSocial / Digg | `bsky.app`, `truthsocial.com`, `di.gg` |
| Web search & grounding | `api.openai.com`, `openrouter.ai`, `api.x.ai`, `generativelanguage.googleapis.com`, `r.jina.ai`, `html.duckduckgo.com`, plus Brave/Parallel/Exa/Serper APIs when configured |
| Jobs / hiring signals | `boards.greenhouse.io`, `apply.workable.com`, `jobs.smartrecruiters.com` |
| Reasoning provider | `api.openai.com`, `chatgpt.com/backend-api` (Codex login), `api.x.ai`, `openrouter.ai`, `generativelanguage.googleapis.com` |

## Security posture

Every commit and pull request runs through CI that includes: a secret scan, a
Semgrep SAST scan, a dependency audit and dependency review, zizmor (GitHub
Actions hardening), OpenSSF Scorecard tracking, and the full test suite —
which contains security-boundary tests (e.g. credential-source precedence,
secret-file permission warnings, and setup-store key masking).

## Reporting a vulnerability

The repository accepts private vulnerability reports through GitHub (Settings
→ Security → "Report a vulnerability" on the repo page). Please include:

- the affected version (plugin version and install method);
- a minimal reproduction and the platform it applies to;
- the impact as concretely as you can.

Scanner findings (Gen/Socket/Snyk) are usually not vulnerabilities — if a
scanner flags a specific dependency or behavior, open a regular issue with the
scanner name, the finding, and the scanned version, and the maintainers will
triage it. Do not post details of a confirmed exploit publicly before it is
addressed.

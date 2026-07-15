"""Unified `doctor` health surface: aggregate, tier rollup, render (U4).

One command answers "what's broken, what's serving, and what do I run to
fix it" by composing the existing health layers instead of replacing them:

- U1 ``lib/health.py``      dependency probes (ok/missing/broken/timeout)
- U2 ``lib/backends.py``    chain descriptors + "will use" prediction
- U3 ``lib/prescriptions.py`` the single remediation vocabulary
- ``lib/pipeline.diagnose`` + ``lib/permission_preflight`` for the
  engine-level availability and permission summary

The legacy ``--diagnose`` / ``--preflight`` flags keep their frozen JSON
shapes (see ``tests/test_diagnose_compat.py``); anything new appears ONLY
in ``doctor --json``.

Tier rollup (the R1 machine contract). Per source, ``tier`` is the
four-value rollup and ``status`` preserves the most specific state:

| condition                                            | status        | tier  |
|------------------------------------------------------|---------------|-------|
| probes pass, credentials (if any) present            | ok            | ok    |
| usable but degraded (fallback serving, partial)      | degraded      | warn  |
| opt-in not enabled / key-gated unconfigured          | opt-in /      | off   |
|                                                      | unconfigured  |       |
| configured but missing / broken / timeout / error    | that status   | error |

Semantics and guarantees:

- ``active_backend`` is a PREDICTION ("will use"), never an observation
  (KTD 4). Reddit is conditional mode: honest wording, no single winner.
- On a native-search host with no web keys, engine-side web search is
  intentionally off — doctor reports tier ``off`` with a host-native note,
  never a false-alarm error. Web search has NO env pin, only the
  ``--web-backend`` flag; the record says so.
- No cookie reads (plan-only, like ``--diagnose``); no secret values
  anywhere — key presence is booleans only.
- Per-source exception isolation: one failing probe becomes that source's
  ``error`` record; it can never blank the report.
- Reporting problems is a successful run: the exit code is always 0.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import backends, env, health, prescriptions
from .backends import TIER_ERROR, TIER_OK, TIER_WARN

# Rollup tiers (R1). ok/warn/error are U2's; only "off" is doctor's own.
TIER_OFF = "off"

# Specific statuses and the rollup row each maps to. The doctor-local
# statuses "opt-in" and "unconfigured" have no health constant.
TIER_BY_STATUS: Dict[str, str] = {
    health.OK: TIER_OK,
    health.DEGRADED: TIER_WARN,
    "opt-in": TIER_OFF,
    "unconfigured": TIER_OFF,
    health.MISSING: TIER_ERROR,
    health.BROKEN: TIER_ERROR,
    health.TIMEOUT: TIER_ERROR,
    health.ERROR: TIER_ERROR,
}

# Tier -> glyph, still used by the cached-report shape validator to confirm a
# record carries a known tier. The user-facing render uses AUDIT_GLYPHS below.
GLYPHS = {TIER_OK: "✓", TIER_WARN: "!", TIER_OFF: "○", TIER_ERROR: "✗"}

# Four-state audit (R1). A presentation layer derived from the tier rollup +
# last-run evidence + optional live probe - it augments the per-source records,
# it does not replace them (the tier/status fields stay in the record and JSON).
AUDIT_WORKING = "working"
AUDIT_UNVERIFIED = "unverified"
AUDIT_NOT_WORKING = "not-working"
AUDIT_COULD_BE_ON = "could-be-on"

AUDIT_GLYPHS = {
    AUDIT_WORKING: "●",       # ●
    AUDIT_UNVERIFIED: "◐",    # ◐
    AUDIT_NOT_WORKING: "✕",   # ✕
    AUDIT_COULD_BE_ON: "○",   # ○
}

AUDIT_GROUPS = (
    (AUDIT_WORKING, "WORKING"),
    (AUDIT_UNVERIFIED, "TURNED ON - UNVERIFIED"),
    (AUDIT_NOT_WORKING, "NOT WORKING"),
    (AUDIT_COULD_BE_ON, "COULD BE ON"),
)

# Sources that need neither credentials nor a CLI: they always serve, so with
# no run evidence and no probe they are WORKING, not UNVERIFIED.
KEYLESS_ALWAYS_ON = frozenset(
    {"reddit", "hackernews", "polymarket", "github", "library"}
)

# Fresh-run outcome states -> audit bucket for a tier-ok source. Anything not
# listed here (error / timeout / rate-limited / auth-failed / unreachable /
# schema-drift) means the source ran and failed -> NOT WORKING.
_RUN_WORKING_STATES = frozenset({health.OK, health.NO_RESULTS})
_RUN_UNVERIFIED_STATES = frozenset({health.PARTIAL, health.SKIPPED_UNCONFIGURED})


def audit_state(
    name: str,
    record: Dict[str, Any],
    run_outcome: Optional[Dict[str, Any]] = None,
    probe_result: Optional[Dict[str, Any]] = None,
) -> str:
    """Map a source's (tier, run evidence, probe) to one of four audit states.

    Precedence: a failing/degraded tier is NOT WORKING regardless of history;
    an off tier (opt-in / unconfigured) is COULD BE ON. For a tier-ok source,
    fresh run evidence wins (ok/no-results -> WORKING; partial/skipped ->
    UNVERIFIED; any error/timeout/rate-limit/etc -> NOT WORKING), then a live
    probe, then the keyless-always-on fallback, else UNVERIFIED.
    """
    tier = record.get("tier")
    if tier in (TIER_ERROR, TIER_WARN):
        return AUDIT_NOT_WORKING
    if tier == TIER_OFF:
        return AUDIT_COULD_BE_ON
    # tier ok
    if run_outcome:
        state = run_outcome.get("state")
        if state in _RUN_WORKING_STATES:
            return AUDIT_WORKING
        if state in _RUN_UNVERIFIED_STATES:
            return AUDIT_UNVERIFIED
        return AUDIT_NOT_WORKING
    if probe_result is not None:
        return AUDIT_WORKING if probe_result.get("ok") else AUDIT_NOT_WORKING
    if name in KEYLESS_ALWAYS_ON:
        return AUDIT_WORKING
    return AUDIT_UNVERIFIED

# Report order: chained sources first, then free, then key-gated/opt-in.
SOURCE_ORDER = (
    "reddit",
    "x",
    "youtube",
    "web",
    "hackernews",
    "polymarket",
    "github",
    "digg",
    "techmeme",
    "arxiv",
    "trustpilot",
    "tiktok",
    "instagram",
    "threads",
    "bluesky",
    "truthsocial",
    "perplexity",
    "linkedin",
    "pinterest",
    "xiaohongshu",
    "jobs",
    "library",
)

# Sources whose availability depends on a downloaded CLI binary. doctor probes
# each (installed AND functional, via health.probe_dependency) and surfaces a
# per-source marker plus a dedicated CLI-health block (R2). Everything not
# listed is keyless - it needs no CLI. gh is OPTIONAL for GitHub (the REST tier
# works without it), so its absence is a note, never a failure.
CLI_DEPENDENCIES = {
    "youtube": "yt-dlp",
    "digg": "digg-pp-cli",
    "techmeme": "techmeme-pp-cli",
    "arxiv": "arxiv-pp-cli",
    "trustpilot": "trustpilot-pp-cli",
    "github": "gh",
}
_OPTIONAL_CLI_SOURCES = frozenset({"github"})

# Key-presence booleans for the setup block. NEVER values.
KEY_PRESENCE_VARS = (
    "SCRAPECREATORS_API_KEY",
    "XAI_API_KEY",
    "XQUIK_API_KEY",
    "BRAVE_API_KEY",
    "EXA_API_KEY",
    "SERPER_API_KEY",
    "PARALLEL_API_KEY",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "PERPLEXITY_API_KEY",
    "GITHUB_TOKEN",
    "TRUTHSOCIAL_TOKEN",
    "BSKY_APP_PASSWORD",
)

# Failing statuses ranked most-specific-first for chained rollups: a broken
# shim outranks a timeout outranks a generic error when naming the source's
# status (all three roll up to tier error regardless).
_SPECIFIC_FAILURES = (health.BROKEN, health.TIMEOUT, health.ERROR)


def _fix_text(entry: prescriptions.Prescription) -> str:
    """Render a registry entry as one actionable fix line (NL + CLI forms)."""
    if entry.fix_cli and entry.fix_cli not in entry.fix_nl:
        return f"{entry.fix_nl} (cli: {entry.fix_cli})"
    return entry.fix_nl


def _record(
    *,
    status: str,
    mode: str = "single",
    backends_list: Optional[List[Dict[str, Any]]] = None,
    active_backend: Optional[str] = None,
    fix: str = "",
    requires: str = "",
    note: str = "",
    detail: str = "",
    pin_var: Optional[str] = None,
    pin_flag: Optional[str] = None,
    pinned: bool = False,
) -> Dict[str, Any]:
    return {
        "tier": TIER_BY_STATUS[status],
        "status": status,
        "mode": mode,
        "backends": backends_list,
        "active_backend": active_backend,
        "fix": fix,
        "requires": requires,
        "note": note,
        "detail": detail,
        "pin_var": pin_var,
        "pin_flag": pin_flag,
        "pinned": pinned,
    }


# ---------------------------------------------------------------------------
# Chained sources (via U2 descriptors)
# ---------------------------------------------------------------------------

def _finding_json(finding: backends.BackendFinding) -> Dict[str, Any]:
    return {
        "name": finding.name,
        "status": finding.status,
        "detail": finding.detail,
        "requires": finding.requires,
        "fix": finding.prescription,
    }


def _host_native_web_note(config: Dict[str, Any]) -> str:
    """Doctor-local note when the host's own web search serves this run.

    Keys on LAST30DAYS_NATIVE_SEARCH (via env.is_native_search) AND on
    CLAUDECODE as a host signal - Claude Code always exposes a web-search tool,
    but `doctor` run in a plain shell never sees the LAST30DAYS_NATIVE_SEARCH
    the engine exports only for its own run, so without the CLAUDECODE signal it
    would mislabel a fine setup as "degraded/keyless". Messaging only: it does
    not change env.is_native_search or the engine's keyless-floor behavior. The
    note names the signal actually detected so it never cites an env var the
    user did not set.
    """
    if env.is_native_search(config):
        return (
            "host-native search active (LAST30DAYS_NATIVE_SEARCH): the host's "
            "own web search serves this run; set a web key only if you want "
            "engine-side web search"
        )
    if config.get("CLAUDECODE") or os.environ.get("CLAUDECODE"):
        return (
            "host-native web search active (Claude Code): the host's own web "
            "search serves this run; set a web key only if you want "
            "engine-side web search"
        )
    return ""


def _chained_record(source: str, config: Dict[str, Any]) -> Dict[str, Any]:
    descriptor = backends.get_descriptor(source)
    res = backends.resolve(source, config)
    findings_json = [_finding_json(f) for f in res.findings]
    common = dict(
        mode=res.mode,
        backends_list=findings_json,
        active_backend=res.active_backend,
        pin_var=descriptor.pin_var,
        pin_flag=descriptor.pin_flag,
        pinned=res.pinned,
    )

    if res.mode == backends.MODE_CONDITIONAL:
        # Reddit: honest conditional wording (U2, verbatim), never a winner.
        return _record(status=health.OK, note=res.conditional,
                       requires=res.findings[0].requires if res.findings else "",
                       **common)

    by_name = {f.name: f for f in res.findings}
    if res.tier == backends.TIER_OK:
        active = by_name.get(res.active_backend)
        return _record(status=health.OK, note=res.summary,
                       requires=active.requires if active else "", **common)

    # Doctor-local (KTD-3): on a host that brings its own web search, the
    # engine's web lanes (keyless floor or nothing configured) are intentionally
    # dormant - report that, not an alarming "degraded/keyless". This must run
    # before the WARN branch, because the keyless floor resolves to WARN and
    # would otherwise return first. Messaging only; it never touches
    # env.is_native_search or the engine's keyless-floor runtime behavior.
    if source == "web":
        host_note = _host_native_web_note(config)
        if host_note:
            return _record(
                status="unconfigured",
                note=host_note,
                requires=res.findings[0].requires if res.findings else "",
                **common,
            )

    if res.tier == backends.TIER_WARN:
        active = by_name.get(res.active_backend)
        return _record(status=health.DEGRADED, note=res.summary,
                       detail=active.detail if active else "",
                       fix=res.prescription,
                       requires=active.requires if active else "", **common)

    # res.tier == error: separate "nothing configured" (tier off) from
    # "configured but broken" (tier error).
    if res.findings and all(f.status == health.MISSING for f in res.findings):
        return _record(
            status="unconfigured",
            fix=res.prescription,
            requires=res.findings[0].requires if res.findings else "",
            note=f"no backend configured (chain: {' -> '.join(res.chain)})",
            **common,
        )
    # Something IS configured/installed but won't serve: name the most
    # specific failure in chain order.
    status = health.ERROR
    fix = res.prescription
    detail = ""
    failed: Optional[backends.BackendFinding] = None
    for wanted in _SPECIFIC_FAILURES:
        failed = next((f for f in res.findings if f.status == wanted), None)
        if failed is not None:
            status = wanted
            fix = failed.prescription or res.prescription
            detail = failed.detail
            break
    # Mirror the OK/WARN branches: the requirement named is the FAILED
    # backend's, not chain[0]'s (which may be a different, merely-missing
    # backend when the failure came from later in the chain).
    return _record(status=status, fix=fix, detail=detail,
                   requires=failed.requires if failed
                   else (res.findings[0].requires if res.findings else ""),
                   **common)


# ---------------------------------------------------------------------------
# Single-backend sources
# ---------------------------------------------------------------------------

def _sc_fix() -> str:
    return _fix_text(prescriptions.get("scrapecreators", "key_missing"))


def _sc_gated_record(config: Dict[str, Any], purpose: str) -> Dict[str, Any]:
    if config.get("SCRAPECREATORS_API_KEY"):
        return _record(status=health.OK, requires="SCRAPECREATORS_API_KEY",
                       detail=f"SCRAPECREATORS_API_KEY present ({purpose})")
    return _record(status="unconfigured", requires="SCRAPECREATORS_API_KEY",
                   fix=_sc_fix())


def _sc_optin_record(config: Dict[str, Any], source: str, purpose: str) -> Dict[str, Any]:
    """SC-gated source that ALSO requires an INCLUDE_SOURCES opt-in to run.

    Unlike ``_sc_gated_record`` (used by the on-by-default TikTok/Instagram),
    a key alone is not enough here: the pipeline only fires this source when it
    is in INCLUDE_SOURCES. Reporting a bare key as Ready is the Threads
    false-Ready bug - this mirrors ``_linkedin_record``'s correct gating so
    doctor and the pipeline cannot disagree.
    """
    requires = f"SCRAPECREATORS_API_KEY + INCLUDE_SOURCES={source}"
    if not config.get("SCRAPECREATORS_API_KEY"):
        return _record(status="unconfigured", requires=requires, fix=_sc_fix())
    if source in env.include_sources(config):
        return _record(status=health.OK, requires=requires,
                       detail=f"SCRAPECREATORS_API_KEY present ({purpose})")
    return _record(
        status="opt-in", requires=requires,
        fix=f"add {source} to INCLUDE_SOURCES (or request it via --search {source})",
        note="key present; opt-in, never auto-activates",
    )


def _reddit_record(config):
    return _chained_record("reddit", config)


def _x_record(config):
    record = _chained_record("x", config)
    # Diagnose/doctor load config in plan_only mode, so browser cookies are not
    # extracted and every X backend reads as statically missing -> unconfigured.
    # But if bird is installed and FROM_BROWSER will authenticate X at run time,
    # a normal run serves X fine (this is how the reporting user pulled 29 posts
    # while doctor said "Off"). Reuse the existing shared predicate so doctor and
    # diagnose cannot drift. It reads no cookie *values*, so it confirms a run
    # will *attempt* browser auth, not that the session is currently valid -
    # keep the note honest and point at the verified key-backed path.
    if record["status"] == "unconfigured" and env.x_pending_browser_auth(
        config, local_only=True
    ):
        record["status"] = health.OK
        record["tier"] = TIER_BY_STATUS[health.OK]
        record["note"] = (
            "will use: bird (browser cookies; session not verified until a run "
            "- add XAI_API_KEY for a verified, cookie-free path)"
        )
        record["fix"] = ""
    return record


def _youtube_record(config):
    record = _chained_record("youtube", config)
    if record["status"] != health.OK:
        return record
    notes: List[str] = []
    # yt-dlp already provides search + transcripts. A transcription key only
    # backfills captions for the occasional caption-free video - an enhancement,
    # not a sign YouTube is broken.
    if not env.transcription_providers(config):
        entry = prescriptions.get("youtube", "transcription_key_missing")
        notes.append(
            "search + transcripts work; a transcription key only adds "
            "captions for caption-free videos"
        )
        record["fix"] = _fix_text(entry)
    # Comment *text* comes from ScrapeCreators, never yt-dlp (yt-dlp yields
    # search, transcripts, and a comment count only). Say so accurately so a
    # user does not expect yt-dlp to surface comment text.
    if not env.is_youtube_comments_available(config):
        notes.append(
            "comment text needs a ScrapeCreators key + youtube_comments opt-in"
        )
        # Actionable fix, matching the transcription branch. The transcription
        # fix takes precedence when both caveats fire (one fix line per record).
        if not record["fix"]:
            if not config.get("SCRAPECREATORS_API_KEY"):
                record["fix"] = _sc_fix()
            else:
                record["fix"] = (
                    "add youtube_comments to INCLUDE_SOURCES in "
                    "~/.config/last30days/.env to enable YouTube comment text"
                )
    if notes:
        joined = "; ".join(notes)
        record["note"] = (record["note"] + "; " + joined) if record["note"] else joined
    return record


def _web_record(config):
    return _chained_record("web", config)


def _hackernews_record(config):
    return _record(status=health.OK, requires="none (free Algolia API)")


def _polymarket_record(config):
    return _record(status=health.OK, requires="none (public API)")


def _github_record(config):
    authed = bool(env.read_secret_env("GITHUB_TOKEN") or shutil.which("gh"))
    detail = (
        "authenticated tier (GITHUB_TOKEN or gh CLI)"
        if authed
        else "unauthenticated REST tier (lower rate limits; GITHUB_TOKEN or gh raises them)"
    )
    return _record(status=health.OK, detail=detail,
                   requires="none (GITHUB_TOKEN or gh CLI optional)")


def _digg_record(config):
    probe = health.probe_dependency("digg-pp-cli")
    requires = "digg-pp-cli on the agent-subprocess PATH"
    if probe.ok:
        return _record(status=health.OK, detail=probe.detail, requires=requires)
    entry = prescriptions.for_dependency_probe(probe)
    fix = _fix_text(entry) if entry else probe.prescription
    if probe.status == health.MISSING and not probe.off_path:
        # Never installed: an optional source that simply isn't enabled.
        return _record(status="opt-in", fix=fix, detail=probe.detail, requires=requires)
    # Installed but off-PATH, broken, or timing out: configured-but-broken.
    return _record(status=probe.status, fix=fix, detail=probe.detail, requires=requires)


def _cli_gated_record(config, cli_name: str, purpose: str):
    """A source gated purely on a keyless downloaded CLI (mirrors _digg_record).

    ok -> installed and functional; opt-in -> never installed (an optional
    source simply not enabled); its failing status -> installed off-PATH,
    broken, or timing out (configured-but-broken).
    """
    probe = health.probe_dependency(cli_name)
    requires = f"{cli_name} on the agent-subprocess PATH"
    if probe.ok:
        return _record(status=health.OK, detail=probe.detail, requires=requires)
    entry = prescriptions.for_dependency_probe(probe)
    fix = _fix_text(entry) if entry else probe.prescription
    if probe.status == health.MISSING and not probe.off_path:
        return _record(status="opt-in", fix=fix, detail=probe.detail, requires=requires)
    return _record(status=probe.status, fix=fix, detail=probe.detail, requires=requires)


def _techmeme_record(config):
    return _cli_gated_record(config, "techmeme-pp-cli", "techmeme")


def _arxiv_record(config):
    return _cli_gated_record(config, "arxiv-pp-cli", "arxiv")


def _trustpilot_record(config):
    return _cli_gated_record(config, "trustpilot-pp-cli", "trustpilot")


def _tiktok_record(config):
    return _sc_gated_record(config, "tiktok")


def _instagram_record(config):
    return _sc_gated_record(config, "instagram")


def _threads_record(config):
    # Threads needs the key AND an INCLUDE_SOURCES=threads opt-in to run, so it
    # is opt-in-gated (not on-by-default like TikTok/Instagram).
    return _sc_optin_record(config, "threads", "threads")


def _bluesky_record(config):
    if env.is_bluesky_available(config):
        return _record(status=health.OK, requires="BSKY_HANDLE + BSKY_APP_PASSWORD")
    return _record(
        status="unconfigured",
        requires="BSKY_HANDLE + BSKY_APP_PASSWORD",
        fix=_fix_text(prescriptions.get("bluesky", "app_password_missing")),
    )


def _truthsocial_record(config):
    if env.is_truthsocial_available(config):
        return _record(status=health.OK, requires="TRUTHSOCIAL_TOKEN")
    return _record(
        status="unconfigured",
        requires="TRUTHSOCIAL_TOKEN",
        fix=_fix_text(prescriptions.get("truthsocial", "token_missing")),
    )


def _perplexity_record(config):
    requires = "PERPLEXITY_API_KEY or OPENROUTER_API_KEY + INCLUDE_SOURCES=perplexity"
    has_key = bool(config.get("PERPLEXITY_API_KEY") or config.get("OPENROUTER_API_KEY"))
    include = env.include_sources(config)
    if not has_key:
        return _record(
            status="unconfigured", requires=requires,
            fix=(
                "set PERPLEXITY_API_KEY or OPENROUTER_API_KEY in "
                "~/.config/last30days/.env, then add perplexity to INCLUDE_SOURCES"
            ),
        )
    if "perplexity" in include:
        return _record(status=health.OK, requires=requires)
    return _record(
        status="opt-in", requires=requires,
        fix="add perplexity to INCLUDE_SOURCES (or request it via --search perplexity)",
        note="key present; source runs only when opted in",
    )


def _linkedin_record(config):
    requires = "SCRAPECREATORS_API_KEY + INCLUDE_SOURCES=linkedin"
    if not config.get("SCRAPECREATORS_API_KEY"):
        return _record(status="unconfigured", requires=requires, fix=_sc_fix())
    if "linkedin" in env.include_sources(config):
        return _record(status=health.OK, requires=requires)
    return _record(
        status="opt-in", requires=requires,
        fix="add linkedin to INCLUDE_SOURCES (or request it via --search linkedin)",
        note="key present; power-user opt-in, never auto-activates",
    )


def _pinterest_record(config):
    requires = "SCRAPECREATORS_API_KEY; requested-only (--search pinterest)"
    if not config.get("SCRAPECREATORS_API_KEY"):
        return _record(status="unconfigured", requires=requires, fix=_sc_fix())
    return _record(
        status="opt-in", requires=requires,
        fix="request it explicitly via --search pinterest (or INCLUDE_SOURCES)",
        note="key present; runs only when requested",
    )


def _xiaohongshu_record(config):
    requires = (
        "logged-in Xiaohongshu browser-session service; requested-only "
        "(--search xhs)"
    )
    entry = prescriptions.get("xiaohongshu", "service_unreachable")
    if config.get("XIAOHONGSHU_API_BASE"):
        return _record(
            status=health.OK, requires=requires,
            note=(
                "XIAOHONGSHU_API_BASE configured; service reachability is not "
                "probed (doctor makes no network calls)"
            ),
        )
    return _record(
        status="opt-in",
        requires=requires,
        fix=_fix_text(entry),
        note=(
            "auto-probes http://localhost:18060 first, then "
            "http://host.docker.internal:18060"
        ),
    )


def _jobs_record(config):
    return _record(
        status="opt-in",
        requires="none; activates for company topics or --hiring-signals",
        note="on-demand source: no configuration needed",
    )


def _count_saved_briefs(memory_dir) -> int:
    """Cheap count of saved research briefs (directory listing, no file parse).

    Globs the ``*-raw*.md`` artifacts the engine writes, deliberately avoiding
    library.scan_library's read_text+parse of every file - a count does not
    need the parsed content, and the full scan adds real latency to every
    `doctor` run on a large library.
    """
    path = Path(memory_dir).expanduser()
    return sum(1 for _ in path.glob("*-raw*.md"))


def _library_record(config):
    """Local research library that feeds the report's 'From your library' block.

    This is not a network source - it reports how many saved briefs are indexed
    so the 'From your library' block's presence is explained on the health
    surface. Read-only and never fails the run: an empty store, a missing store,
    or a SQLite build without FTS5 all resolve to an informational OK line.
    """
    from . import library, library_index

    if not library_index.fts5_available():
        return _record(
            status=health.OK,
            requires="none (local SQLite)",
            note=(
                "search index unavailable (this SQLite build lacks FTS5); "
                "saved briefs still render, `library search` is disabled"
            ),
        )
    try:
        count = _count_saved_briefs(
            config.get("LAST30DAYS_MEMORY_DIR") or library.DEFAULT_MEMORY_DIR
        )
    except Exception:
        return _record(
            status=health.OK,
            requires="none (local SQLite)",
            note="local research library (powers the 'From your library' block)",
        )
    if count == 0:
        note = "no saved briefs yet - runs you save build this over time"
    else:
        plural = "brief" if count == 1 else "briefs"
        note = (
            f"{count} saved {plural}; powers the 'From your library' block "
            "(LAST30DAYS_LIBRARY_CONTEXT=off to hide)"
        )
    return _record(status=health.OK, requires="none (local SQLite)", note=note)


_SOURCE_BUILDERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "reddit": _reddit_record,
    "x": _x_record,
    "youtube": _youtube_record,
    "web": _web_record,
    "hackernews": _hackernews_record,
    "polymarket": _polymarket_record,
    "github": _github_record,
    "digg": _digg_record,
    "techmeme": _techmeme_record,
    "arxiv": _arxiv_record,
    "trustpilot": _trustpilot_record,
    "tiktok": _tiktok_record,
    "instagram": _instagram_record,
    "threads": _threads_record,
    "bluesky": _bluesky_record,
    "truthsocial": _truthsocial_record,
    "perplexity": _perplexity_record,
    "linkedin": _linkedin_record,
    "pinterest": _pinterest_record,
    "xiaohongshu": _xiaohongshu_record,
    "jobs": _jobs_record,
    "library": _library_record,
}


# ---------------------------------------------------------------------------
# Run-evidence overlay (U1): read the engine's last-report.json
#
# doctor predicts config health; a research run records what ACTUALLY happened
# per source in Report.source_status. Reading the last run lets doctor tell
# "configured" from "working" (the four-state audit) and powers --postmortem.
# This is a read-only reuse of the engine's existing report cache - no new
# writer. The schema stamp + filename mirror REPORT_CACHE_VERSION /
# _last_report_cache_path() in last30days.py (the same mirror pattern the
# doctor-cache block below already uses for its own schema stamp).
# ---------------------------------------------------------------------------

REPORT_CACHE_SCHEMA_VERSION = "last30days-report-cache/v1"
REPORT_CACHE_FILENAME = "last-report.json"
DEFAULT_REPORT_CACHE_TTL_SECONDS = 3600


def _last_report_path() -> Optional[Path]:
    """The engine's last-report.json, beside the doctor cache (None in clean mode)."""
    if env.CONFIG_DIR is None:
        return None
    return env.CONFIG_DIR / REPORT_CACHE_FILENAME


def load_run_evidence(
    config: Dict[str, Any], ttl_seconds: int = DEFAULT_REPORT_CACHE_TTL_SECONDS
) -> Dict[str, Any]:
    """Return the last research run's per-source outcomes, read-only.

    Shape: ``{"outcomes": {source: {state, items_returned, detail, fix_hint,
    at}}, "topic": str|None, "at": str|None, "fresh": bool, "present": bool}``.

    Any failure mode - absent file, unreadable, invalid JSON, schema mismatch,
    wrong shape - yields the empty, not-present result and never raises
    (doctor's exit-0 contract is absolute). ``fresh`` reflects the report TTL:
    ``--postmortem`` reads regardless of freshness (labeling the age), while the
    plain-``doctor`` overlay consumes only fresh evidence so a week-old run
    cannot mislabel a source as WORKING today.
    """
    empty = {"outcomes": {}, "topic": None, "at": None, "fresh": False, "present": False}
    path = _last_report_path()
    if path is None or not path.exists():
        return empty
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty
    if not isinstance(payload, dict):
        return empty
    if payload.get("schema") != REPORT_CACHE_SCHEMA_VERSION:
        return empty
    reports = payload.get("reports") or []
    if not reports or not isinstance(reports[0], dict):
        return empty
    report = reports[0].get("report")
    if not isinstance(report, dict):
        return empty
    raw_status = report.get("source_status") or {}
    outcomes: Dict[str, Any] = {}
    if isinstance(raw_status, dict):
        for source, outcome in raw_status.items():
            if not isinstance(outcome, dict):
                continue
            state = outcome.get("state")
            if not isinstance(state, str):
                continue
            outcomes[source] = {
                "state": state,
                "items_returned": int(outcome.get("items_returned") or 0),
                "detail": outcome.get("detail"),
                "fix_hint": outcome.get("fix_hint"),
                "at": outcome.get("at"),
            }
    timestamp = payload.get("timestamp")
    return {
        "outcomes": outcomes,
        "topic": payload.get("topic"),
        "at": timestamp or report.get("generated_at"),
        "fresh": bool(env.is_timestamp_fresh(timestamp, ttl_seconds)),
        "present": True,
    }


# ---------------------------------------------------------------------------
# Backup + comment sub-lanes (U7 / R8, R9)
#
# Backups (Reddit's SC backfill, YouTube's SC transcript/search backstop, X's
# cookie-vs-key dual path) and comment lanes (youtube/tiktok/instagram) are not
# independent sources - they are capabilities of their parent. doctor surfaces
# them as indented sub-lines so "is a backup armed when yt-dlp is rate-limited?"
# is answerable at a glance without inventing fake sources.
# ---------------------------------------------------------------------------

def _sub_lanes_for(source: str, config: Dict[str, Any]):
    """Return (backups, comments) metadata for a source, or ([], None)."""
    backups: List[Dict[str, Any]] = []
    comments: Optional[Dict[str, Any]] = None
    has_sc = bool(config.get("SCRAPECREATORS_API_KEY"))
    if source == "reddit":
        backups.append({
            "name": "ScrapeCreators backfill", "armed": has_sc,
            "note": "fills in when the free public path returns nothing",
        })
    elif source == "youtube":
        backups.append({
            "name": "ScrapeCreators transcript/search backstop", "armed": has_sc,
            "note": "used when yt-dlp is rate-limited or bot-gated",
        })
        comments = {"enabled": bool(env.is_youtube_comments_available(config))}
    elif source == "x":
        has_key = bool(config.get("XAI_API_KEY") or config.get("XQUIK_API_KEY"))
        cookie = bool(env.x_pending_browser_auth(config, local_only=True))
        if has_key:
            note = "XAI_API_KEY key-backed path (verified, cookie-free)"
        elif cookie:
            note = (
                "browser-cookie path primary; add XAI_API_KEY for a verified "
                "cookie-free backup"
            )
        else:
            note = "no auth path armed"
        backups.append({"name": "X auth path", "armed": has_key or cookie, "note": note})
    elif source == "tiktok":
        comments = {"enabled": bool(env.is_tiktok_comments_available(config))}
    elif source == "instagram":
        comments = {"enabled": bool(env.is_instagram_comments_available(config))}
    return backups, comments


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _engine_version() -> str:
    try:
        from . import render

        version = render._skill_version()
    except Exception:
        version = None
    # render's parent-walking helper falls back to "?"; doctor says "unknown".
    if not version or version == "?":
        return "unknown"
    return version


def _setup_block(config: Dict[str, Any]) -> Dict[str, Any]:
    keys_present = {var: bool(config.get(var)) for var in KEY_PRESENCE_VARS}
    keys_present["x_browser_cookies"] = bool(
        config.get("AUTH_TOKEN") and config.get("CT0")
    )
    keys_present["bluesky_app_password"] = bool(
        config.get("BSKY_HANDLE") and config.get("BSKY_APP_PASSWORD")
    )
    return {
        "setup_complete": env.is_setup_complete(config),
        "keys_present": keys_present,
    }


def _permissions_block(config: Dict[str, Any]) -> Dict[str, Any]:
    """Secret-free permission summary via the existing preflight provider."""
    from . import pipeline

    diag = pipeline.diagnose(config, None, safe=True)
    return diag["permission_preflight"]


def build_report(config: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate every health provider into one report dict.

    Per-source exceptions are isolated: a failing builder yields an
    ``error`` record for that source and the rest of the report survives.
    """
    def _build_one(name: str) -> Dict[str, Any]:
        try:
            return _SOURCE_BUILDERS[name](config)
        except Exception as exc:  # one bad probe must not blank the report
            return _record(
                status=health.ERROR,
                detail=f"probe failed: {type(exc).__name__}: {exc}",
                fix=_fix_text(prescriptions.get(name, "probe_error")),
            )

    # Builders are independent probes (subprocess/filesystem bound), so run
    # them concurrently. ``pool.map`` preserves SOURCE_ORDER, keeping the
    # sources dict insertion order — and render grouping — deterministic.
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(8, len(SOURCE_ORDER))
    ) as pool:
        sources: Dict[str, Dict[str, Any]] = dict(
            zip(SOURCE_ORDER, pool.map(_build_one, SOURCE_ORDER))
        )

    # U1: overlay the last research run's per-source outcome onto each record
    # so the audit layer can tell "configured" from "actually working".
    # U2: derive each record's four-state audit bucket (probe evidence, when a
    # live probe runs, is layered on in run() before render).
    evidence = load_run_evidence(config)
    for source, record in sources.items():
        record["run_outcome"] = evidence["outcomes"].get(source) if evidence["fresh"] else None
        record["audit_state"] = audit_state(source, record, record["run_outcome"])

    # U3: annotate each CLI-dependent source with its binary's health so the
    # per-source marker and the dedicated CLI-health block can render (R2).
    for source, cli_name in CLI_DEPENDENCIES.items():
        record = sources.get(source)
        if record is None:
            continue
        try:
            probe = health.probe_dependency(cli_name)
        except Exception:
            continue
        record["cli"] = {
            "name": cli_name,
            "status": probe.status,
            "off_path": bool(getattr(probe, "off_path", False)),
            "detail": probe.detail,
            "optional": source in _OPTIONAL_CLI_SOURCES,
        }

    # U7: attach backup + comment sub-lanes to their parent source.
    for source, record in sources.items():
        backups, comments = _sub_lanes_for(source, config)
        if backups:
            record["backups"] = backups
        if comments is not None:
            record["comments"] = comments

    # Sequential on purpose: the permission preflight composes pipeline
    # diagnostics and must not race the source builders.
    try:
        permissions = _permissions_block(config)
    except Exception as exc:
        permissions = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}

    return {
        "engine_version": _engine_version(),
        "config": {
            "global_env": str(env.CONFIG_FILE) if env.CONFIG_FILE else None,
            "config_source": config.get("_CONFIG_SOURCE"),
        },
        "setup": _setup_block(config),
        "permissions": permissions,
        "sources": sources,
        "mode": "config",
        "run_evidence": {
            "present": evidence["present"],
            "fresh": evidence["fresh"],
            "topic": evidence["topic"],
            "at": evidence["at"],
        },
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _cli_marker(record: Dict[str, Any]) -> str:
    """Inline `[CLI: name ✓]` / `[keyless]` marker (populated by U3)."""
    cli = record.get("cli")
    if not cli:
        return ""
    name = cli.get("name")
    if cli.get("status") == health.OK:
        return f" [CLI: {name} ✓]"
    if cli.get("off_path"):
        return f" [CLI: {name} ✗ off-PATH]"
    return f" [CLI: {name} ✗ {cli.get('status')}]"


def _run_evidence_suffix(record: Dict[str, Any], state: str) -> str:
    """Last-run outcome tail for a source line (R4)."""
    ro = record.get("run_outcome")
    if not ro:
        if state == AUDIT_UNVERIFIED:
            return "  [no recent run]"
        return ""
    st = ro.get("state")
    count = ro.get("items_returned") or 0
    detail = ro.get("detail")
    if st in _RUN_WORKING_STATES:
        if count:
            return f"  [✓ {count} items last run]"
        return "  [✓ ran clean, 0 matches last run]"
    if st == health.PARTIAL:
        tail = f" ({detail})" if detail else ""
        return f"  [⚠ partial last run{tail}]"
    tail = detail or st
    return f"  [✕ {tail} last run]"


def _audit_source_line(name: str, record: Dict[str, Any], state: str) -> str:
    glyph = AUDIT_GLYPHS.get(state, "?")
    parts = [f"  {glyph} {name}{_cli_marker(record)}"]
    descriptors: List[str] = []
    if record.get("status") not in (health.OK,):
        descriptors.append(record["status"])
    if record.get("note"):
        descriptors.append(record["note"])
    elif record.get("detail") and record.get("tier") != TIER_OK:
        descriptors.append(record["detail"])
    if descriptors:
        parts.append(" — " + "; ".join(descriptors))
    evidence = _run_evidence_suffix(record, state)
    if evidence:
        parts.append(evidence)
    # fix is only ever populated when there is something actionable, so
    # render it whenever present — an ok-tier record can carry one (the
    # youtube transcription-key note) and must not lose it in text mode.
    if record.get("fix"):
        parts.append(f"; fix: {record['fix']}")
    # Backup / comment sub-lanes render on their own indented lines (U7),
    # after the primary line (with its fix) is complete.
    for sub in _sub_lane_lines(record):
        parts.append("\n" + sub)
    return "".join(parts)


def _sub_lane_lines(record: Dict[str, Any]) -> List[str]:
    """Indented backup/comment sub-lane lines under a source (R8, R9)."""
    lines: List[str] = []
    for backup in record.get("backups") or []:
        state = "armed" if backup.get("armed") else "off"
        note = f" - {backup['note']}" if backup.get("note") else ""
        lines.append(f"      backup: {backup['name']} — {state}{note}")
    comments = record.get("comments")
    if comments is not None:
        state = "on" if comments.get("enabled") else "off"
        lines.append(f"      comments: {state}")
    return lines


def _cli_health_lines(report: Dict[str, Any]) -> List[str]:
    """Dedicated CLI-health block (R2): one row per CLI-dependent source,
    plus a note naming the keyless sources that need no CLI at all.
    """
    sources = report.get("sources") or {}
    rows: List[str] = []
    for source in SOURCE_ORDER:
        cli = (sources.get(source) or {}).get("cli")
        if not cli:
            continue
        ok = cli.get("status") == health.OK
        glyph = "✓" if ok else "✗"
        detail = cli.get("detail") or cli.get("status")
        tail = ""
        if not ok:
            if cli.get("off_path"):
                tail = " (installed off-PATH)"
            elif cli.get("optional"):
                tail = " (optional)"
        rows.append(f"  {glyph} {cli['name']} — {source}{tail}: {detail}")
    if not rows:
        return []
    return (
        ["CLI health (downloaded binaries):"]
        + rows
        + ["  · Reddit, Hacker News, Polymarket need no CLI (keyless)"]
    )


def render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = [f"last30days doctor — engine v{report['engine_version']}"]
    config_block = report.get("config") or {}
    if config_block.get("global_env"):
        line = f"config: {config_block['global_env']}"
        if config_block.get("config_source"):
            line += f" (source: {config_block['config_source']})"
        lines.append(line)

    setup = report.get("setup") or {}
    present = sorted(
        name for name, is_set in (setup.get("keys_present") or {}).items() if is_set
    )
    setup_state = "complete" if setup.get("setup_complete") else "not recorded"
    lines.append(
        f"setup: {setup_state}; credentials present: "
        + (", ".join(present) if present else "none")
        + " (values never shown)"
    )

    permissions = report.get("permissions") or {}
    if permissions.get("status"):
        browser = ((permissions.get("local_reads") or {}).get("browser_cookies") or {})
        lines.append(
            f"permissions: {permissions['status']}"
            + (f"; browser cookies: {browser.get('status')}" if browser else "")
        )

    run_ev = report.get("run_evidence") or {}
    if run_ev.get("present") and run_ev.get("fresh"):
        topic = run_ev.get("topic") or "last run"
        lines.append(
            f"last run: {topic} - overlaying actual source outcomes below"
        )
    elif run_ev.get("present"):
        lines.append(
            "last run: found but stale - run `doctor --postmortem` to inspect it"
        )

    grouped: Dict[str, List[str]] = {state: [] for state, _ in AUDIT_GROUPS}
    for name, record in (report.get("sources") or {}).items():
        state = record.get("audit_state") or audit_state(
            name, record, record.get("run_outcome")
        )
        grouped.setdefault(state, []).append(_audit_source_line(name, record, state))

    for state, header in AUDIT_GROUPS:
        entries = grouped.get(state) or []
        lines.append("")
        lines.append(f"{AUDIT_GLYPHS.get(state, '')} {header}:")
        if entries:
            lines.extend(entries)
        else:
            lines.append("  (none)")

    cli_block = _cli_health_lines(report)
    if cli_block:
        lines.append("")
        lines.extend(cli_block)

    lines.append("")
    lines.append(
        "doctor reports problems without failing; run the printed fixes, "
        "then re-run doctor"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Post-mortem (U4 / R5): what actually happened on the last run
#
# Unlike plain doctor (config prediction), --postmortem reads the last run's
# per-source SourceOutcome and reports what broke, at any age (labeled). It is
# a reader of the same last-report.json the overlay uses - no new persistence.
# ---------------------------------------------------------------------------

def _age_label(iso: Any) -> str:
    if not iso:
        return ""
    try:
        ts = datetime.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    secs = int(
        (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
    )
    if secs < 0:
        return ""
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def build_postmortem(config: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the last run's per-source outcomes (any age) for --postmortem."""
    evidence = load_run_evidence(config)
    return {
        "engine_version": _engine_version(),
        "mode": "postmortem",
        "present": evidence["present"],
        "topic": evidence["topic"],
        "at": evidence["at"],
        "outcomes": evidence["outcomes"],
    }


def render_postmortem_text(pm: Dict[str, Any]) -> str:
    lines = [f"last30days post-mortem — engine v{pm['engine_version']}"]
    if not pm.get("present"):
        lines.append("")
        lines.append(
            "No saved run found - run `/last30days <topic>` first, or "
            "`doctor --probe` for a live check."
        )
        return "\n".join(lines) + "\n"
    topic = pm.get("topic") or "last run"
    age = _age_label(pm.get("at"))
    lines.append(f"last run: {topic}" + (f" ({age})" if age else ""))

    failed, partial, succeeded, skipped = [], [], [], []
    for source, outcome in (pm.get("outcomes") or {}).items():
        state = outcome.get("state")
        if state in _RUN_WORKING_STATES:
            succeeded.append((source, outcome))
        elif state == health.PARTIAL:
            partial.append((source, outcome))
        elif state == health.SKIPPED_UNCONFIGURED:
            skipped.append((source, outcome))
        else:
            failed.append((source, outcome))

    if failed:
        lines.append("")
        lines.append("Failed:")
        for source, outcome in failed:
            detail = outcome.get("detail") or outcome.get("state")
            lines.append(f"  ✕ {source} — {outcome.get('state')}: {detail}")
            if outcome.get("fix_hint"):
                lines.append(f"    fix: {outcome['fix_hint']}")
    if partial:
        lines.append("")
        lines.append("Partial:")
        for source, outcome in partial:
            count = outcome.get("items_returned") or 0
            detail = outcome.get("detail")
            tail = f" — {detail}" if detail else ""
            lines.append(f"  ⚠ {source} ({count} items){tail}")
            if outcome.get("fix_hint"):
                lines.append(f"    fix: {outcome['fix_hint']}")
    if succeeded:
        lines.append("")
        names = ", ".join(
            f"{s} ({o.get('items_returned') or 0})" for s, o in succeeded
        )
        lines.append(f"Succeeded: {names}")
    if skipped:
        lines.append("")
        lines.append(
            "Skipped (not configured): " + ", ".join(s for s, _ in skipped)
        )
    if not failed and not partial:
        lines.append("")
        lines.append("No failures on the last run.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Cross-invocation cache (U5 / R5, KTD 8)
#
# Doctor writes its JSON result beside the existing ``last-run.json``
# convention (env.CONFIG_DIR) so the SKILL.md standing rule's pre-research
# check costs one file read on the healthy path instead of a dozen probe
# subprocesses. ``--cached`` serves the stored report within the TTL and
# falls through to a live run (rewriting the cache) when the file is stale,
# absent, or corrupt — corruption is treated as absence, never a crash.
# An explicit ``doctor`` (no ``--cached``) always runs live and refreshes.
#
# The payload carries a schema stamp (mirrors REPORT_CACHE_VERSION in
# last30days.py) and a config fingerprint — a sha256 over the same
# non-secret signals doctor already reports (key-presence booleans, backend
# pin values, INCLUDE_SOURCES). A schema or fingerprint mismatch is treated
# as stale, so a credential or pin change can never serve yesterday's
# conclusions. Served reports carry ``from_cache`` + ``generated_at`` so
# consumers can see staleness instead of inferring it.
# ---------------------------------------------------------------------------

CACHE_FILENAME = "doctor-cache.json"

# Bump when the cached payload/report shape changes incompatibly; a
# mismatched (or absent) stamp is treated as an absent cache.
DOCTOR_CACHE_SCHEMA_VERSION = "last30days-doctor-cache/v1"

# TTL in SECONDS (env-tunable via LAST30DAYS_DOCTOR_TTL; registered in
# lib/env.py's get_config key list so a .env-set value is not swallowed).
DEFAULT_CACHE_TTL_SECONDS = 900

# Config vars whose values must never land in the cache file. Doctor output
# carries no secrets by design (key presence is booleans only); this belt-and-
# suspenders check refuses to persist the cache if a seeded value ever leaks.
_SECRET_CONFIG_VARS = KEY_PRESENCE_VARS + (
    "AUTH_TOKEN", "CT0", "APIFY_API_TOKEN", "GOOGLE_GENAI_API_KEY",
)

# Backend pin vars folded into the config fingerprint. Pin values are
# backend names (e.g. "bird"), never secrets.
_FINGERPRINT_PIN_VARS = (env.X_BACKEND_PIN_VAR, env.REDDIT_BACKEND_PIN_VAR)

# Top-level report keys the renderers read unguarded; a cached report
# missing any of them is treated as corrupt (absent), never rendered.
_REQUIRED_REPORT_KEYS = ("engine_version", "config", "setup", "permissions", "sources")


def cache_path() -> Optional[Path]:
    """The doctor cache file, beside last-run.json (None in clean mode)."""
    if env.CONFIG_DIR is None:
        return None
    return env.CONFIG_DIR / CACHE_FILENAME


def cache_ttl_seconds(config: Dict[str, Any]) -> int:
    """LAST30DAYS_DOCTOR_TTL in seconds; process env > config; default 900."""
    raw: Any = os.environ.get("LAST30DAYS_DOCTOR_TTL")
    if raw is None:
        raw = (config or {}).get("LAST30DAYS_DOCTOR_TTL")
    if raw is None or raw == "":
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL_SECONDS


def _is_fresh(timestamp: Any, ttl_seconds: int) -> bool:
    return env.is_timestamp_fresh(timestamp, ttl_seconds)


def _config_fingerprint(config: Dict[str, Any]) -> str:
    """sha256 over the non-secret config signals doctor already reports.

    Inputs are key-presence BOOLEANS (never credential values — the same
    ``keys_present`` set the setup block renders), backend pin values
    (backend names, not secrets), and INCLUDE_SOURCES (not a secret).
    Adding or removing a credential, changing a pin, or toggling an opt-in
    source yields a new fingerprint, so ``read_cached_report`` treats the
    old cache as stale instead of serving pre-change conclusions.
    """
    config = config or {}
    signals = {
        "keys_present": _setup_block(config)["keys_present"],
        "pins": {var: str(config.get(var) or "") for var in _FINGERPRINT_PIN_VARS},
        "include_sources": str(config.get("INCLUDE_SOURCES") or ""),
    }
    canonical = json.dumps(signals, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report_shape_ok(report: Any) -> bool:
    """True when a cached report satisfies the render contract.

    Validates everything the renderers read unguarded: the required
    top-level keys exist (dict-valued where render calls ``.get`` on them),
    and every sources record is a dict carrying a known tier and a str
    status. Anything else is corrupt — treated as absent, never rendered.
    """
    if not isinstance(report, dict):
        return False
    if any(key not in report for key in _REQUIRED_REPORT_KEYS):
        return False
    if any(
        not isinstance(report[key], dict)
        for key in ("config", "setup", "permissions", "sources")
    ):
        return False
    sources = report["sources"]
    if not sources:
        return False
    for record in sources.values():
        if not isinstance(record, dict):
            return False
        if record.get("tier") not in GLYPHS:
            return False
        if not isinstance(record.get("status"), str):
            return False
    return True


def read_cached_report(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the cached report when present, well-formed, and within TTL.

    Any failure mode — unreadable file, invalid JSON, schema mismatch,
    config-fingerprint mismatch, wrong shape, bad or stale timestamp —
    returns None (cache treated as absent, never a crash).

    A served report is stamped with ``from_cache: True`` and
    ``generated_at`` (the cache write time) so consumers see staleness.
    """
    path = cache_path()
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != DOCTOR_CACHE_SCHEMA_VERSION:
        return None  # absent or mismatched schema stamp: treat as absent
    if payload.get("fingerprint") != _config_fingerprint(config):
        return None  # credentials/pins/opt-ins changed: cache is stale
    report = payload.get("report")
    if not _report_shape_ok(report):
        return None
    if not _is_fresh(payload.get("timestamp"), cache_ttl_seconds(config)):
        return None
    report["generated_at"] = payload.get("timestamp")
    report["from_cache"] = True
    return report


def _write_cache(report: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """Best-effort cache write; refuses to persist any secret value.

    Never fatal: any failure returns False after a one-line stderr warning
    (doctor's exit-0 contract is unaffected; only ``--cached`` reuse is).
    """
    try:
        path = cache_path()
        if path is None:
            return False
        payload = {
            "schema": DOCTOR_CACHE_SCHEMA_VERSION,
            "fingerprint": _config_fingerprint(config),
            "timestamp": report.get("generated_at")
            or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "report": report,
        }
        raw = json.dumps(payload, indent=2, sort_keys=True)
        for var in _SECRET_CONFIG_VARS:
            value = (config or {}).get(var)
            if isinstance(value, str) and value and value in raw:
                return False  # never write a cache containing a secret
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw, encoding="utf-8")
        return True
    except Exception as exc:
        sys.stderr.write(
            f"[last30days] WARNING: could not write doctor cache: "
            f"{type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
        return False


# ---------------------------------------------------------------------------
# Live probe (U5 / R6)
#
# When there is no fresh run to learn from (or on explicit --probe), doctor
# runs a BOUNDED live test so WORKING is verified, not guessed. Scope is
# deliberate: free HTTP endpoints + keyless CLIs only. Credit-gated /
# session-gated sources (x, tiktok, instagram, threads, ...) are NOT
# live-probed - a health check must never spend ScrapeCreators credits or trip
# auth rate limits; they stay UNVERIFIED with that noted. Every probe is capped
# by a per-source deadline so a single slow source (YouTube's 120s search) can
# never hang doctor.
# ---------------------------------------------------------------------------

# Free, keyless liveness endpoints (reachability check, tiny payload).
_HTTP_PROBE_URLS = {
    "reddit": "https://www.reddit.com/r/all/hot.json?limit=1",
    "hackernews": "https://hn.algolia.com/api/v1/search?query=test&hitsPerPage=1",
    "polymarket": "https://gamma-api.polymarket.com/events?limit=1",
    "github": "https://api.github.com/rate_limit",
}

DEFAULT_PROBE_TIMEOUT_SECONDS = 10


def probe_timeout_seconds(config: Dict[str, Any]) -> int:
    """Per-source probe deadline; process env > config > default 10s."""
    raw: Any = os.environ.get("LAST30DAYS_DOCTOR_PROBE_TIMEOUT")
    if raw is None:
        raw = (config or {}).get("LAST30DAYS_DOCTOR_PROBE_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_PROBE_TIMEOUT_SECONDS
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_PROBE_TIMEOUT_SECONDS


def _probeable_sources() -> tuple:
    """Sources doctor will live-probe: free HTTP endpoints + keyless CLIs.

    github is HTTP-probed (its REST tier works without gh), so it is excluded
    from the CLI-probe path even though gh is in CLI_DEPENDENCIES.
    """
    cli_only = [s for s in CLI_DEPENDENCIES if s not in _HTTP_PROBE_URLS]
    return tuple(dict.fromkeys(list(_HTTP_PROBE_URLS) + cli_only))


def _http_ok(url: str, timeout: float) -> tuple:
    """Reachability check: a 4xx still means the endpoint responded; 5xx or a
    connection/timeout error means it did not."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "last30days-doctor"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200) or 200
        return code < 500, f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _probe_source(name: str, config: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
    url = _HTTP_PROBE_URLS.get(name)
    if url:
        ok, detail = _http_ok(url, timeout)
        return {"ok": ok, "detail": detail, "probed": True}
    cli = CLI_DEPENDENCIES.get(name)
    if cli:
        try:
            probe = health.probe_dependency(cli)
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}", "probed": True}
        return {"ok": bool(probe.ok), "detail": probe.detail, "probed": True}
    return None


def _probe_sources(config: Dict[str, Any], timeout: int) -> Dict[str, Dict[str, Any]]:
    """Probe the probeable sources concurrently, each capped at ``timeout``.

    A source that blows its deadline resolves to a probe-failure for that
    source only (never a hung command); other probes are unaffected.
    """
    names = _probeable_sources()
    results: Dict[str, Dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(8, len(names) or 1)
    ) as pool:
        futures = {
            name: pool.submit(_probe_source, name, config, timeout) for name in names
        }
        for name, fut in futures.items():
            try:
                res = fut.result(timeout=timeout + 1)
            except concurrent.futures.TimeoutError:
                res = {"ok": False, "detail": "probe exceeded deadline", "probed": True}
            except Exception as exc:
                res = {
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "probed": True,
                }
            if res is not None:
                results[name] = res
    return results


def _apply_probe(report: Dict[str, Any], probe_results: Dict[str, Dict[str, Any]]) -> None:
    """Attach probe results and re-derive audit_state for probed sources."""
    for name, res in probe_results.items():
        record = (report.get("sources") or {}).get(name)
        if record is None:
            continue
        record["probe"] = res
        record["audit_state"] = audit_state(
            name, record, record.get("run_outcome"), res
        )


def run(
    config: Dict[str, Any],
    *,
    emit_json: bool = False,
    cached: bool = False,
    postmortem: bool = False,
    probe: bool = False,
) -> int:
    """Build (or serve the cached) doctor report and print it. Always exits 0
    (reporting problems is a successful run).

    ``postmortem=True`` reads the last run's per-source outcomes (any age) and
    reports what broke, instead of predicting config health.
    ``probe=True`` (or no fresh run) runs a bounded live probe (U5) so WORKING
    is verified, not guessed.
    ``cached=True`` serves the stored report within the TTL; stale, absent,
    corrupt, schema-mismatched, or fingerprint-mismatched caches fall
    through to a live run that rewrites the cache — as does ANY exception
    raised while serving the cache (never-crash contract, KTD 8).
    ``cached=False`` (explicit ``doctor``) always runs live and refreshes.
    """
    if postmortem:
        pm = build_postmortem(config)
        if emit_json:
            print(json.dumps(pm, indent=2, sort_keys=True))
        else:
            print(render_postmortem_text(pm), end="")
        return 0

    def _emit(report: Dict[str, Any]) -> None:
        if emit_json:
            # generated_at/from_cache ride the report dict, so they appear
            # at the JSON top level for free.
            print(render_json(report))
        else:
            # The cache-status line is printed here (run() owns this print)
            # because render_text's header belongs to the render layer, not
            # the cache layer.
            origin = "cached" if report.get("from_cache") else "live"
            print(render_text(report), end="")
            print(f"generated: {report.get('generated_at')} ({origin})")

    if cached:
        try:
            cached_report = read_cached_report(config)
            if cached_report is not None:
                _emit(cached_report)
                return 0
        except Exception:
            # Belt-and-suspenders for shapes the validator misses: any
            # failure serving the cache falls through to a live run.
            pass
    report = build_report(config)
    report["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    report["from_cache"] = False

    # U5: verify WORKING with a bounded live probe when asked (--probe) or when
    # there is no fresh run to learn from ("if no recent runs, run a live
    # test"). Scoped to free/CLI sources; credit-gated sources stay UNVERIFIED.
    fresh_run = bool((report.get("run_evidence") or {}).get("fresh"))
    if probe or not fresh_run:
        timeout = probe_timeout_seconds(config)
        probeable = _probeable_sources()
        sys.stderr.write(
            f"[last30days] doctor live probe: checking {len(probeable)} free/CLI "
            f"sources ({timeout}s each; no credit-gated sources - x/tiktok/"
            f"instagram/threads stay unverified)\n"
        )
        sys.stderr.flush()
        try:
            probe_results = _probe_sources(config, timeout)
        except Exception:
            probe_results = {}
        _apply_probe(report, probe_results)
        report["mode"] = "probe"
        report["probe"] = {"ran": True, "timeout": timeout, "sources": list(probeable)}

    _write_cache(report, config)
    _emit(report)
    return 0

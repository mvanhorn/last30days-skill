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

GLYPHS = {TIER_OK: "✓", TIER_WARN: "!", TIER_OFF: "○", TIER_ERROR: "✗"}

GROUP_HEADERS = (
    (TIER_OK, "Ready"),
    (TIER_WARN, "Degraded"),
    (TIER_OFF, "Off"),
    (TIER_ERROR, "Errors"),
)

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
)

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
    if res.tier == backends.TIER_WARN:
        active = by_name.get(res.active_backend)
        return _record(status=health.DEGRADED, note=res.summary,
                       detail=active.detail if active else "",
                       fix=res.prescription,
                       requires=active.requires if active else "", **common)

    # res.tier == error: separate "nothing configured" (tier off) from
    # "configured but broken" (tier error).
    if source == "web" and env.is_native_search(config):
        # The engine's web lanes are all unavailable BECAUSE the host brings
        # its own (better) native search — intentionally off, no false alarm.
        return _record(
            status="unconfigured",
            note=(
                "host-native search active (LAST30DAYS_NATIVE_SEARCH): the "
                "host's own web search serves this run; set a web key only "
                "if you want engine-side web search"
            ),
            requires=res.findings[0].requires if res.findings else "",
            **common,
        )
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


def _reddit_record(config):
    return _chained_record("reddit", config)


def _x_record(config):
    return _chained_record("x", config)


def _youtube_record(config):
    record = _chained_record("youtube", config)
    if record["status"] == health.OK and not env.transcription_providers(config):
        # Usable, but the caption-free transcript backstop is unavailable.
        entry = prescriptions.get("youtube", "transcription_key_missing")
        record["note"] = (record["note"] + "; " if record["note"] else "") + (
            "no transcription key for caption-free videos"
        )
        record["fix"] = _fix_text(entry)
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


def _tiktok_record(config):
    return _sc_gated_record(config, "tiktok")


def _instagram_record(config):
    return _sc_gated_record(config, "instagram")


def _threads_record(config):
    return _sc_gated_record(config, "threads")


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
    requires = "xiaohongshu-mcp service (XIAOHONGSHU_API_BASE); requested-only"
    entry = prescriptions.get("xiaohongshu", "service_unreachable")
    if config.get("XIAOHONGSHU_API_BASE"):
        return _record(
            status=health.OK, requires=requires,
            note=(
                "XIAOHONGSHU_API_BASE configured; service reachability is not "
                "probed (doctor makes no network calls)"
            ),
        )
    return _record(status="opt-in", requires=requires, fix=_fix_text(entry))


def _jobs_record(config):
    return _record(
        status="opt-in",
        requires="none; activates for company topics or --hiring-signals",
        note="on-demand source: no configuration needed",
    )


_SOURCE_BUILDERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "reddit": _reddit_record,
    "x": _x_record,
    "youtube": _youtube_record,
    "web": _web_record,
    "hackernews": _hackernews_record,
    "polymarket": _polymarket_record,
    "github": _github_record,
    "digg": _digg_record,
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
}


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
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _source_line(name: str, record: Dict[str, Any]) -> str:
    glyph = GLYPHS.get(record["tier"], "?")
    parts = [f"  {glyph} {name}"]
    descriptors: List[str] = []
    if record["status"] not in (health.OK,):
        descriptors.append(record["status"])
    if record.get("note"):
        descriptors.append(record["note"])
    elif record.get("detail") and record["tier"] != TIER_OK:
        descriptors.append(record["detail"])
    if descriptors:
        parts.append(" — " + "; ".join(descriptors))
    # fix is only ever populated when there is something actionable, so
    # render it whenever present — an ok-tier record can carry one (the
    # youtube transcription-key note) and must not lose it in text mode.
    if record.get("fix"):
        parts.append(f"; fix: {record['fix']}")
    return "".join(parts)


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

    grouped: Dict[str, List[str]] = {tier: [] for tier, _ in GROUP_HEADERS}
    for name, record in (report.get("sources") or {}).items():
        grouped.setdefault(record["tier"], []).append(_source_line(name, record))

    for tier, header in GROUP_HEADERS:
        entries = grouped.get(tier) or []
        lines.append("")
        lines.append(f"{header}:")
        if entries:
            lines.extend(entries)
        else:
            lines.append("  (none)")

    lines.append("")
    lines.append(
        "doctor reports problems without failing; run the printed fixes, "
        "then re-run doctor"
    )
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


def run(config: Dict[str, Any], *, emit_json: bool = False, cached: bool = False) -> int:
    """Build (or serve the cached) doctor report and print it. Always exits 0
    (reporting problems is a successful run).

    ``cached=True`` serves the stored report within the TTL; stale, absent,
    corrupt, schema-mismatched, or fingerprint-mismatched caches fall
    through to a live run that rewrites the cache — as does ANY exception
    raised while serving the cache (never-crash contract, KTD 8).
    ``cached=False`` (explicit ``doctor``) always runs live and refreshes.
    """
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
    _write_cache(report, config)
    _emit(report)
    return 0

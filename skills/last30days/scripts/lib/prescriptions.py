"""Fix-prescription registry: the single remediation vocabulary (KTD 7).

Each (source, failure mode) entry carries a cause line, a natural-language
fix, an exact CLI fix, and an optional CONFIGURATION.md anchor. Two real
consumers keep the vocabulary honest from day one:

- ``lib/quality_nudge.py`` builds its post-research fix text from these
  entries (only the fix strings migrated here; trigger logic is untouched).
- The doctor aggregator (U4) looks entries up per failed source/backend.

Because both surfaces read the same entry, the nudge a user sees after a
degraded run and the prescription doctor prints for the same failure can
never drift apart.

Composition with the other health layers (reference, don't restate):

- U1 (``lib/health.py``) owns the machine-aware package-manager strings
  (brew/pipx/apt/npx install-vs-reinstall, off-PATH PATH edits). Binary-class
  entries here pull their static defaults from U1's tables, and
  ``for_dependency_probe`` lets a live probe's machine-specific prescription
  win the CLI form while the registry supplies cause/NL/anchor vocabulary.
- U2 (``lib/backends.py``) embeds this registry's CLI forms inside its
  chain-failure prescriptions, so a backend finding and a registry lookup
  agree on the command to run.

No secrets: CLI forms use obvious ``<placeholder>`` values only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

from . import health

# Direct engine invocation prefix (scripting fallback; the slash-command UX
# is "ask the agent to run setup ...", which is the natural-language form).
ENGINE_CLI = "python3 skills/last30days/scripts/last30days.py"
SETUP_BROWSER_COOKIES_CLI = f"{ENGINE_CLI} setup --allow-browser-cookies"
SETUP_GITHUB_CLI = f"{ENGINE_CLI} setup --github"

# U1 owns these remediation strings; reference them instead of restating.
_YTDLP_BREW_INSTALL, _YTDLP_BREW_REINSTALL = health.static_prescription("yt-dlp", "brew")
_YTDLP_PIPX_REINSTALL = health.static_prescription("yt-dlp", "pipx")[1]
_DIGG_PP_INSTALL_CLI = health.pp_install_cmd("digg")

GENERIC_FIX_NL = "see CONFIGURATION.md for setup options for this source"


@dataclass(frozen=True)
class Prescription:
    """Remediation for one (source, failure mode).

    ``fix_nl`` is the natural-language form ("ask the agent to run setup
    with browser-cookie consent"); ``fix_cli`` is the exact command.
    ``alt_cli`` carries per-platform alternates (Windows/pip) when the
    primary CLI form is macOS/brew. ``anchor`` is a CONFIGURATION.md
    heading anchor ("" when the doc has no dedicated section).
    """

    source: str
    failure: str
    cause: str
    fix_nl: str
    fix_cli: str
    alt_cli: Tuple[str, ...] = ()
    anchor: str = ""


def _entry(source: str, failure: str, **kwargs) -> Tuple[Tuple[str, str], Prescription]:
    return (source, failure), Prescription(source=source, failure=failure, **kwargs)


REGISTRY: Dict[Tuple[str, str], Prescription] = dict((
    _entry(
        "x", "cookies_missing",
        cause="X browser cookies (AUTH_TOKEN/CT0) are not configured",
        fix_nl=(
            "log into x.com in your browser and re-run (cookies detected "
            "automatically), or add XAI_API_KEY to your .env (get key at "
            "api.x.ai), or add XQUIK_API_KEY to your .env (get key at xquik.com)"
        ),
        fix_cli=SETUP_BROWSER_COOKIES_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "x", "cookies_expired",
        cause="X errored this run: cookies are configured but likely expired or revoked",
        fix_nl="log into x.com in your browser, then re-run",
        fix_cli=SETUP_BROWSER_COOKIES_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "scrapecreators", "key_missing",
        cause="SCRAPECREATORS_API_KEY is not set",
        fix_nl=(
            "ask the agent to run setup with the GitHub device flow "
            "(free 10,000-call signup; the key is persisted automatically)"
        ),
        fix_cli=SETUP_GITHUB_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "bluesky", "app_password_missing",
        cause="BSKY_HANDLE and/or BSKY_APP_PASSWORD are not set",
        fix_nl=(
            "generate an app password at bsky.app/settings/app-passwords and "
            "add BSKY_HANDLE plus BSKY_APP_PASSWORD to ~/.config/last30days/.env"
        ),
        fix_cli="BSKY_HANDLE=<your-handle> BSKY_APP_PASSWORD=<xxxx-xxxx-xxxx-xxxx>",
        anchor="bluesky-app-password-format-and-search-host",
    ),
    _entry(
        "youtube", "transcription_key_missing",
        cause=(
            "no transcription provider key for the caption-free transcript "
            "backstop (GROQ_API_KEY or OPENAI_API_KEY)"
        ),
        fix_nl=(
            "add a free Groq key from console.groq.com to "
            "~/.config/last30days/.env so caption-free videos still get "
            "transcripts (OPENAI_API_KEY also works as the paid backstop)"
        ),
        fix_cli="GROQ_API_KEY=<your-groq-key>",
        anchor="api-keys-env",
    ),
    _entry(
        "digg", "pp_cli_missing",
        cause="digg-pp-cli is not installed",
        fix_nl=(
            "install the Digg CLI through the Printing Press library, then "
            "re-run setup so the source activates"
        ),
        fix_cli=_DIGG_PP_INSTALL_CLI,
        anchor="first-run-onboarding",
    ),
    _entry(
        "digg", "pp_cli_broken",
        cause=(
            "digg-pp-cli resolves on PATH but won't execute (broken or "
            "hanging binary left behind by a bad install)"
        ),
        fix_nl=(
            "reinstall the Digg CLI (re-run the Printing Press install) so "
            "the binary actually executes; it is installed but not serving"
        ),
        fix_cli=_DIGG_PP_INSTALL_CLI,
        anchor="first-run-onboarding",
    ),
    _entry(
        "digg", "pp_cli_off_path",
        cause=(
            "digg-pp-cli is installed but its directory is not on the "
            "agent-subprocess PATH"
        ),
        fix_nl=(
            "add the install directory (default ~/.local/bin) to the PATH the "
            "agent subprocess uses; the engine gate only activates the source "
            "when the binary resolves on PATH"
        ),
        fix_cli='export PATH="$HOME/.local/bin:$PATH"',
        anchor="first-run-onboarding",
    ),
    _entry(
        "youtube", "ytdlp_missing",
        cause="yt-dlp is not installed on the agent-subprocess PATH",
        fix_nl="install yt-dlp to enable the free local YouTube lane",
        fix_cli=_YTDLP_BREW_INSTALL,
        alt_cli=("scoop install yt-dlp", "pip install -U yt-dlp"),
    ),
    _entry(
        "youtube", "ytdlp_stale",
        cause=(
            "yt-dlp is installed but stale: YouTube's caption format changes "
            "frequently and old binaries silently fail every transcript"
        ),
        fix_nl="update yt-dlp via your package manager",
        fix_cli="brew upgrade yt-dlp",
        alt_cli=("scoop update yt-dlp", "pip install -U yt-dlp"),
    ),
    _entry(
        "youtube", "ytdlp_broken",
        cause=(
            "yt-dlp resolves on PATH but won't execute (the stale-shim class: "
            "a wrapper left behind by an interpreter upgrade)"
        ),
        fix_nl=(
            "reinstall yt-dlp so the binary actually executes; a plain "
            "install reads as a no-op because the broken shim is still present"
        ),
        fix_cli=_YTDLP_BREW_REINSTALL,
        alt_cli=(_YTDLP_PIPX_REINSTALL,),
    ),
    _entry(
        "truthsocial", "token_missing",
        cause="TRUTHSOCIAL_TOKEN is not set",
        fix_nl=(
            "log into truthsocial.com in your browser and let setup read the "
            "session cookie, or copy the bearer token from your browser's dev "
            "tools into ~/.config/last30days/.env"
        ),
        fix_cli=SETUP_BROWSER_COOKIES_CLI,
        anchor="api-keys-env",
    ),
    _entry(
        "xiaohongshu", "service_unreachable",
        cause=(
            "Xiaohongshu browser-session service is unreachable or not logged "
            "in; last30days auto-probes http://localhost:18060 and "
            "http://host.docker.internal:18060 unless XIAOHONGSHU_API_BASE is set"
        ),
        fix_nl=(
            "start a local x-mcp browser plugin or xpzouying/xiaohongshu-mcp "
            "service that can see your logged-in Xiaohongshu browser session; "
            "set XIAOHONGSHU_API_BASE only when it runs on a custom host/port"
        ),
        fix_cli="XIAOHONGSHU_API_BASE=http://your-host:18060  # only for a custom host; leave unset to auto-probe localhost and host.docker.internal",
        anchor="api-keys-env",
    ),
))


def lookup(source: str, failure: str) -> Optional[Prescription]:
    """Return the registered entry for (source, failure), or None."""
    return REGISTRY.get((source, failure))


def get(source: str, failure: str) -> Prescription:
    """Return the registered entry, or the generic CONFIGURATION.md fallback.

    Never raises: an unregistered failure mode still yields an actionable
    (if generic) prescription, so a report renderer cannot crash on a
    failure class the registry has not learned yet.
    """
    entry = lookup(source, failure)
    if entry is not None:
        return entry
    return Prescription(
        source=source,
        failure=failure,
        cause=f"{source}: {failure.replace('_', ' ')}",
        fix_nl=GENERIC_FIX_NL,
        fix_cli=f"{ENGINE_CLI} setup",
    )


# ---------------------------------------------------------------------------
# Composition with U1 dependency probes
# ---------------------------------------------------------------------------

def _dependency_failure(probe: health.DependencyProbe) -> Optional[Tuple[str, str]]:
    """Map a failed dependency probe onto a registered (source, failure)."""
    if probe.name == "yt-dlp":
        if probe.status == health.MISSING:
            return ("youtube", "ytdlp_missing")
        return ("youtube", "ytdlp_broken")  # BROKEN and TIMEOUT: reinstall class
    if probe.name == "digg-pp-cli":
        # health reports off-PATH binaries as MISSING with ``off_path=True``;
        # the distinction only picks cause/NL wording — the probe's own
        # prescription wins the CLI form either way.
        if probe.status == health.MISSING:
            if probe.off_path:
                return ("digg", "pp_cli_off_path")
            return ("digg", "pp_cli_missing")
        return ("digg", "pp_cli_broken")  # BROKEN and TIMEOUT: reinstall class
    return None


def for_dependency_probe(probe: health.DependencyProbe) -> Optional[Prescription]:
    """Prescription for a failed U1 dependency probe (None when OK).

    U1's machine-aware prescription (the manager that owns the binary on
    THIS machine, or a PATH edit for off-PATH installs) wins the CLI form;
    the registry entry supplies the shared cause/NL/anchor vocabulary.
    Unregistered dependencies wrap the probe so callers still get both
    fix forms without this module restating U1's strings.
    """
    if probe.ok:
        return None
    key = _dependency_failure(probe)
    entry = REGISTRY.get(key) if key else None
    if entry is None:
        return Prescription(
            source=probe.name,
            failure=probe.status,
            cause=probe.detail or f"{probe.name}: {probe.status}",
            fix_nl=f"repair the {probe.name} install; {GENERIC_FIX_NL}",
            fix_cli=probe.prescription or f"{ENGINE_CLI} setup",
        )
    updates = {}
    if probe.detail:
        updates["cause"] = probe.detail
    if probe.prescription and probe.prescription != entry.fix_cli:
        updates["fix_cli"] = probe.prescription
    return replace(entry, **updates) if updates else entry

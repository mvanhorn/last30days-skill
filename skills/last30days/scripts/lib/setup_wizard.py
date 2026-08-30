"""First-run setup wizard for last30days.

Detects first run, performs auto-setup (cookie extraction + yt-dlp check),
and writes configuration. The actual wizard UI is SKILL.md-driven (the LLM
presents it), but this module provides the detection and setup actions.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import brightdata

logger = logging.getLogger(__name__)


def is_first_run(config: Dict[str, Any]) -> bool:
    """Return True if the setup wizard has not been completed.

    Checks for SETUP_COMPLETE in the config dict. If it's not set
    (None or empty string), the user hasn't gone through setup yet.
    """
    return not config.get("SETUP_COMPLETE")


_WELCOME_TEXT = """Welcome to /last30days! I research any topic across Reddit, X, YouTube, TikTok, Digg, arXiv, Techmeme, HN, Polymarket & more - what people actually said in the last 30 days. Let's get you set up (~30s).

I synthesize what people are actually saying right now across social, news, and market sources.

Auto setup gives you the core sources free in about 30 seconds:
- Reddit with comments - free keyless discovery (RSS + shreddit), no API key needed.
- YouTube search + transcripts - installs yt-dlp (open source, 190K+ GitHub stars).
- Digg - trending news, GitHub stars, and pipeline feeds - installs the free, keyless Digg CLI.
- arXiv (papers) + Techmeme (tech-news) - install free, keyless Printing Press CLIs and run on any topic (arXiv is relevance + recency gated to research topics).
- StockTwits - retail trader sentiment - auto-on when your topic is a ticker or crypto (e.g. "$NVDA earnings", "bitcoin"), off for everything else.
- Trustpilot - brand/company review sentiment - opt-in (add trustpilot to INCLUDE_SOURCES), off by default.
- Hacker News + Polymarket + GitHub (auto-on if the gh CLI is installed) - always on, zero config.
- X/Twitter - optional. It stays available when you already configured it, or after you explicitly approve a browser-cookie read; skipping it never blocks research.

Want TikTok and Instagram too? ScrapeCreators adds those (10,000 free calls, scrapecreators.com). No kickbacks, no affiliation.

Power users can turn on more sources in the Manual Setup guide (LinkedIn, Bluesky, Perplexity, and others) - each needs its own credential, so they are off by default."""


def render_welcome() -> str:
    """Return the first-run welcome text.

    Owned by the engine (single source of truth) so the model relays it rather
    than re-authoring it -- authored prose gets skipped, relayed command output
    does not. Mirrors the SKILL.md welcome; keep in sync if the source set
    changes.
    """
    return _WELCOME_TEXT


def run_auto_setup(config: Dict[str, Any], *, allow_browser_cookies: bool = False) -> Dict[str, Any]:
    """Perform the auto-setup actions.

    - Optionally runs cookie extraction for all registered domains, trying the
      browsers from ``env.cookie_extraction_browsers()``. Browser reads are off
      unless ``allow_browser_cookies`` is true.
    - Checks if yt-dlp is installed
    - Best-effort install of digg-pp-cli (Printing Press library)

    Returns:
        Dict with keys:
          cookies_found: {source_name: browser_name} for each source where cookies were found
          browser_cookie_scan_attempted: bool (True only after explicit consent)
          ytdlp_installed: bool
          ytdlp_action: already_installed | installed | install_failed | no_homebrew
          digg_installed: bool (True when the engine can resolve digg-pp-cli on PATH)
          digg_action: already_installed | installed | installed_off_path | install_failed | no_npx
          env_written: bool (always False here — caller writes config separately)
          ytdlp_stderr: present when ytdlp_action is install_failed
          digg_stderr: present when digg_action is install_failed
          digg_path: present when digg_action is installed_off_path (binary on disk, not on PATH)
    """
    from .env import COOKIE_DOMAINS, cookie_extraction_browsers

    cookies_found: Dict[str, str] = {}

    if allow_browser_cookies:
        from . import cookie_extract

        cookie_config = dict(config)
        if not (cookie_config.get("FROM_BROWSER") or "").strip():
            # Chromium-first: Chrome/Brave/etc. read cookies via the Keychain
            # with no Full Disk Access, so try them before Safari, whose
            # binarycookies read requires FDA (the dead-end most users hit).
            # firefox/safari stay as the silent fallbacks. Note: an explicit
            # comma list preserves this order (cookie_extraction_browsers);
            # "auto" would put the silent browsers first, so do not use it here.
            cookie_config["FROM_BROWSER"] = "chrome,brave,edge,vivaldi,arc,chromium,firefox,safari"
        browsers = cookie_extraction_browsers(cookie_config)

        for source_name, spec in COOKIE_DOMAINS.items():
            domain = spec["domain"]
            cookie_names = spec["cookies"]

            for browser in browsers:
                try:
                    result = cookie_extract.extract_cookies_with_source(browser, domain, cookie_names)
                except Exception as exc:
                    logger.debug("Cookie extraction failed for %s via %s: %s", source_name, browser, exc)
                    continue
                if result is not None and result[0]:
                    cookies_found[source_name] = result[1]
                    break  # Found cookies for this service, stop trying browsers

    # Check yt-dlp availability and install via Homebrew if missing. Windows
    # has no Homebrew, and its working install path is `pip install yt-dlp`
    # (see #904), so it gets its own no-op-install guidance branch instead of
    # falling into the Homebrew-oriented no_homebrew outcome.
    ytdlp_action: str
    if shutil.which("yt-dlp") is not None:
        ytdlp_installed = True
        ytdlp_action = "already_installed"
    elif os.name == "nt":
        ytdlp_installed = False
        ytdlp_action = "no_pip_windows"
    elif shutil.which("brew") is not None:
        brew_stderr = ""
        try:
            proc = subprocess.run(
                ["brew", "install", "yt-dlp"],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode == 0:
                ytdlp_installed = True
                ytdlp_action = "installed"
            else:
                ytdlp_installed = False
                ytdlp_action = "install_failed"
                brew_stderr = proc.stderr
                logger.warning("brew install yt-dlp failed: %s", proc.stderr)
        except Exception as exc:
            ytdlp_installed = False
            ytdlp_action = "install_failed"
            brew_stderr = str(exc)
            logger.warning("brew install yt-dlp exception: %s", exc)
    else:
        ytdlp_installed = False
        ytdlp_action = "no_homebrew"

    digg_installed, digg_action, digg_stderr, digg_path = _install_digg_cli()
    pp_sources = install_default_pp_sources()

    results: Dict[str, Any] = {
        "cookies_found": cookies_found,
        "browser_cookie_scan_attempted": allow_browser_cookies,
        "ytdlp_installed": ytdlp_installed,
        "ytdlp_action": ytdlp_action,
        "digg_installed": digg_installed,
        "digg_action": digg_action,
        # Per-CLI status for the additional default-on Printing Press sources
        # (arxiv, techmeme, trustpilot): {source: {installed, action, ...}}.
        "pp_sources": pp_sources,
        # Reported, never installed: this CLI spends the user's own metered
        # credits, so acquiring it stays their decision (U5/R11). Passing
        # config matters: a user whose key lives in a .env file or the
        # keychain (rather than a `brightdata login` credentials file) is
        # active in the engine, and setup must not tell them otherwise.
        "brightdata": brightdata_status(config),
        "env_written": False,
    }
    if ytdlp_action == "install_failed":
        results["ytdlp_stderr"] = brew_stderr
    if digg_action == "install_failed":
        results["digg_stderr"] = digg_stderr
    if digg_path:
        results["digg_path"] = digg_path
    return results


# Generous timeout: the install shells out to `npx`, which may download the
# Printing Press package and build the Go binary over the network.
DIGG_INSTALL_TIMEOUT = 300
DIGG_CLI_BIN = "digg-pp-cli"
# Pin the catalog installer; matches printing-press-library npm 0.1.16 default
# ($HOME/.local/bin on macOS/Linux).
PRINTING_PRESS_NPM = "@mvanhorn/printing-press-library@0.1.16"
DIGG_INSTALL_CMD = f"npx -y {PRINTING_PRESS_NPM} install digg --cli-only"


def _digg_bin_candidate_paths() -> list[Path]:
    """Known install locations for digg-pp-cli (Printing Press library defaults).

    Order: current installer default (~/.local/bin), legacy Go bins, Windows
    managed dir. The directory list is ``health.installer_bin_dirs()`` — the
    shared single source — with the Digg filename variants appended (plain
    name for Unix-style dirs, ``.exe`` in the Windows managed dir).
    ``pipeline.available_sources()`` only activates Digg when
    ``shutil.which`` resolves on PATH — probing these dirs is for setup
    verification and honest off-PATH messaging, not engine activation.
    """
    from . import health

    win_dir = health.windows_printing_press_bin_dir()
    candidates: list[Path] = []
    for directory in health.installer_bin_dirs():
        if win_dir is not None and directory == win_dir:
            candidates.append(directory / f"{DIGG_CLI_BIN}.exe")
        else:
            candidates.append(directory / DIGG_CLI_BIN)
    return candidates


def _digg_on_path() -> Optional[str]:
    """Return digg-pp-cli when the engine would activate Digg (PATH-resolvable)."""
    return shutil.which(DIGG_CLI_BIN)


def _digg_off_path_binary() -> Optional[str]:
    """Return digg-pp-cli path from known install dirs when not on PATH."""
    for candidate in _digg_bin_candidate_paths():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _digg_bin_dir_hint(digg_path: str) -> str:
    """Return a copy-pasteable PATH directory for the given binary path."""
    parent = os.path.dirname(os.path.expanduser(digg_path))
    if os.name == "nt":
        # Windows PATH edits use absolute dirs; $HOME is a Unix shell convention.
        return parent
    home = str(Path.home())
    if parent == home:
        return "$HOME"
    prefix = home + os.sep
    if parent.startswith(prefix):
        rel = parent[len(prefix):].replace(os.sep, "/")
        return f"$HOME/{rel}" if rel else "$HOME"
    return parent


def _run_npx_install(slug: str) -> Tuple[str, str]:
    """Resolve ``npx`` and run the Printing Press catalog install for ``slug``.

    Shared by ``_install_digg_cli`` and ``_install_pp_cli`` -- this is only the
    "resolve npx, run the install, interpret no_npx/exception/nonzero-rc"
    slice; each caller keeps its own on-path/off-path re-verification
    (``_digg_bin_candidate_paths`` vs ``_pp_bin_candidate_paths`` already use
    different candidate-directory sources, so merging them here would change
    off-path detection behavior beyond this fix's scope).

    Fixes the Windows PATHEXT mismatch: ``shutil.which("npx")`` resolves
    ``npx.CMD`` via PATHEXT, but ``subprocess.run`` given the bare string
    ``"npx"`` as argv[0] does not do that resolution and fails with
    ``WinError 2``. Passing the resolved path is a no-op on macOS/Linux, where
    ``shutil.which`` already returns the exact path ``CreateProcess``/``execve``
    would resolve.

    Returns ``(action, stderr)``: ``action`` is ``"no_npx"``,
    ``"install_failed"``, or ``""`` when the subprocess ran and returned
    ``rc=0`` (in which case ``stderr`` carries any non-fatal stderr output for
    the caller's own off-path logging).
    """
    npx = shutil.which("npx")
    if npx is None:
        return "no_npx", ""
    try:
        proc = subprocess.run(
            [npx, "-y", PRINTING_PRESS_NPM, "install", slug, "--cli-only"],
            capture_output=True, text=True, timeout=DIGG_INSTALL_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("npx install %s exception: %s", slug, exc)
        return "install_failed", str(exc)
    if proc.returncode != 0:
        stderr = proc.stderr or f"npx install {slug} exited {proc.returncode}"
        logger.warning("npx install %s failed (rc=%s): %s", slug, proc.returncode, stderr)
        return "install_failed", stderr
    return "", (proc.stderr or "")


def _install_digg_cli() -> Tuple[bool, str, str, str]:
    """Best-effort install of the digg-pp-cli binary.

    Mirrors the yt-dlp/brew auto-install: it never raises, and degrades to a
    recommend-only outcome when the installer is unavailable. Uses
    ``@mvanhorn/printing-press-library`` (``--cli-only``) — the same catalog
    installer as pp-digg; Hermes/OpenClaw skill wiring is irrelevant here.

    Returns ``(engine_active, action, stderr, off_path_binary)`` where
    ``engine_active`` is True only when ``shutil.which`` resolves the binary
    (matching ``pipeline.available_sources()``). ``action`` is one of:
      already_installed | installed | installed_off_path | install_failed | no_npx
    ``stderr`` is populated on ``install_failed``. ``off_path_binary`` is set
    when the binary exists on disk but is not PATH-visible to this process.
    """
    on_path = _digg_on_path()
    if on_path:
        return True, "already_installed", "", ""
    off_path = _digg_off_path_binary()
    if off_path:
        return False, "installed_off_path", "", off_path
    action, stderr = _run_npx_install("digg")
    if action:
        return False, action, stderr, ""
    on_path = _digg_on_path()
    if on_path:
        return True, "installed", "", ""
    off_path = _digg_off_path_binary()
    if off_path:
        combined = stderr.strip()
        if combined:
            logger.warning("digg-pp-cli installed off PATH: %s", combined)
        return False, "installed_off_path", combined, off_path
    stderr_msg = stderr or "install completed but digg-pp-cli was not found"
    logger.warning("npx install digg failed verification: %s", stderr_msg)
    return False, "install_failed", stderr_msg, ""


# Additional default-on Printing Press sources installed the same way as Digg:
# (engine source key, slug for `install <slug>`, binary name). These activate in
# ``pipeline.available_sources()`` when ``shutil.which`` resolves the binary.
# Trustpilot is intentionally NOT here: it is opt-in (INCLUDE_SOURCES=trustpilot)
# because of its headless-Chrome cookie harvest, so auto-installing its binary
# for a source that stays off by default would be wasted work. Opting in installs
# it on demand via `npx ... install trustpilot --cli-only` (see CONFIGURATION.md).
PP_DEFAULT_SOURCES: list[tuple[str, str, str]] = [
    ("arxiv", "arxiv", "arxiv-pp-cli"),
    ("techmeme", "techmeme", "techmeme-pp-cli"),
]

# Bright Data is deliberately absent from PP_DEFAULT_SOURCES: it is not a
# Printing Press CLI, it is opt-in like Trustpilot, and it spends the user's
# own metered credits. Setup reports its state and never installs it.
BRIGHTDATA_BIN = "brightdata"


def _brightdata_off_path_binary() -> Optional[str]:
    """Locate a brightdata binary that exists on disk but not on PATH.

    Covers the common npm global prefixes. The distinction matters because
    Hermes and OpenClaw gateways routinely run the engine with a PATH that
    excludes the user's npm bin directory, so "installed" and "the engine
    can see it" are different questions.
    """
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / BRIGHTDATA_BIN,
        home / ".npm-global" / "bin" / BRIGHTDATA_BIN,
        Path("/opt/homebrew/bin") / BRIGHTDATA_BIN,
        Path("/usr/local/bin") / BRIGHTDATA_BIN,
    ]
    npm_prefix = os.environ.get("NPM_CONFIG_PREFIX")
    if npm_prefix:
        candidates.insert(0, Path(npm_prefix) / "bin" / BRIGHTDATA_BIN)
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def brightdata_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Report the Bright Data install and auth state honestly.

    Deliberately never claims the source is active unless the engine's own
    gate would pass -- ``brightdata.is_available`` is the single predicate,
    so setup and the engine cannot drift apart. Three states matter:

    * ``already_installed``  -- on PATH; ``authenticated`` says whether the
      amazon lane will actually run.
    * ``installed_off_path`` -- on disk but invisible to the engine, which
      is the Hermes/OpenClaw failure mode. Carries the path so the user can
      fix their PATH.
    * ``not_installed``      -- nothing found. No auto-install: this CLI
      spends the user's metered credits, so acquiring it is their call.
    """
    installed = brightdata.is_installed()
    authenticated = brightdata.has_credentials(config)
    if installed:
        action = "already_installed"
        off_path = ""
    else:
        off_path = _brightdata_off_path_binary() or ""
        action = "installed_off_path" if off_path else "not_installed"

    status: Dict[str, Any] = {
        "installed": installed,
        "action": action,
        "authenticated": installed and authenticated,
        # The engine gate, verbatim. Never report active on anything else.
        "engine_active": brightdata.is_available(config),
    }
    if off_path:
        status["path"] = off_path
        status["hint"] = (
            f"brightdata found at {off_path} but not on PATH; add its directory "
            "to PATH so the engine subprocess can see it"
        )
    elif installed and not authenticated:
        status["hint"] = "run `brightdata login` to activate the amazon source"
    elif not installed:
        status["hint"] = (
            "install with `npm i -g @brightdata/cli` then `brightdata login` "
            "to enable the amazon source"
        )
    return status


def _pp_bin_candidate_paths(bin_name: str) -> list[Path]:
    """Known install locations for a Printing Press CLI binary (slug-parameterized
    mirror of ``_digg_bin_candidate_paths``)."""
    home = Path.home()
    candidates: list[Path] = [home / ".local" / "bin" / bin_name]
    gopath = os.environ.get("GOPATH")
    if gopath:
        candidates.append(Path(gopath) / "bin" / bin_name)
    candidates.append(home / "go" / "bin" / bin_name)
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA") or os.environ.get("LocalAppData")
        if local_app:
            candidates.append(
                Path(local_app) / "Programs" / "PrintingPress" / "bin" / f"{bin_name}.exe"
            )
    return candidates


def _pp_off_path_binary(bin_name: str) -> Optional[str]:
    for candidate in _pp_bin_candidate_paths(bin_name):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _install_pp_cli(slug: str, bin_name: str) -> Tuple[bool, str, str, str]:
    """Best-effort install of a Printing Press CLI binary.

    Slug-parameterized mirror of ``_install_digg_cli``: never raises, degrades
    to recommend-only when the installer is unavailable. Returns
    ``(engine_active, action, stderr, off_path_binary)`` with the same action
    taxonomy: already_installed | installed | installed_off_path |
    install_failed | no_npx.
    """
    on_path = shutil.which(bin_name)
    if on_path:
        return True, "already_installed", "", ""
    off_path = _pp_off_path_binary(bin_name)
    if off_path:
        return False, "installed_off_path", "", off_path
    action, stderr = _run_npx_install(slug)
    if action:
        return False, action, stderr, ""
    on_path = shutil.which(bin_name)
    if on_path:
        return True, "installed", "", ""
    off_path = _pp_off_path_binary(bin_name)
    if off_path:
        combined = stderr.strip()
        if combined:
            logger.warning("%s installed off PATH: %s", bin_name, combined)
        return False, "installed_off_path", combined, off_path
    stderr_msg = stderr or f"install completed but {bin_name} was not found"
    logger.warning("npx install %s failed verification: %s", slug, stderr_msg)
    return False, "install_failed", stderr_msg, ""


def install_default_pp_sources() -> Dict[str, Dict[str, Any]]:
    """Best-effort install of every additional default-on Printing Press source.

    Returns ``{source_key: {installed, action, stderr?, path?}}`` so the wizard
    can report per-CLI status alongside Digg without raising on any single
    failure.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for source_key, slug, bin_name in PP_DEFAULT_SOURCES:
        installed, action, stderr, off_path = _install_pp_cli(slug, bin_name)
        entry: Dict[str, Any] = {"installed": installed, "action": action}
        if action == "install_failed" and stderr:
            entry["stderr"] = stderr
        if off_path:
            entry["path"] = off_path
        out[source_key] = entry
    return out


def _open_secret_append(path: Path):
    """Open ``path`` for appending as a 0o600 secret file with no readable window.

    ``os.open`` with ``O_CREAT|O_WRONLY|O_APPEND`` and mode ``0o600`` sets
    restrictive permissions at creation (umask can only further restrict, never
    widen, so the file is never world-readable even transiently). An explicit
    ``chmod`` afterwards also tightens a pre-existing loose file. This matters
    because the .env stores API keys, cookies, and tokens.
    """
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return os.fdopen(fd, "a", encoding="utf-8")


def _format_env_value(value: str) -> str:
    """Quote a value so it round-trips through env.load_env_file.

    env.load_env_file strips a single layer of matching surrounding quotes but
    does NOT process backslash escapes, so we wrap (never escape):
      - plain tokens (no whitespace, no leading quote): returned unchanged;
      - values with whitespace/leading quote and no double-quote: double-quoted;
      - values containing a double-quote but no single-quote: single-quoted;
      - values containing both quote types: returned as-is (best effort; no
        wrapper round-trips through the loader, and tokens never hit this).
    Newlines are not valid in a single-line env value and are stripped.
    """
    value = value.replace("\r", "").replace("\n", " ")
    needs_quoting = (not value) or value[0] in ("'", '"') or any(c.isspace() for c in value)
    if not needs_quoting:
        return value
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    return value


def write_setup_config(env_path: Path, from_browser: str | None = None) -> bool:
    """Write SETUP_COMPLETE and FROM_BROWSER to the .env file.

    Creates the file and parent directories if needed.
    Appends to existing file without overwriting existing keys.

    Args:
        env_path: Path to the .env file (e.g. ~/.config/last30days/.env)
        from_browser: Browser extraction mode to persist. Pass the browser that
            actually yielded cookies (e.g. "firefox") to fast-path future runs.
            Pass None (default) to NOT pin FROM_BROWSER — the steady-state
            default (Firefox/Safari, no Keychain prompt) then applies. We avoid
            persisting "auto" because it makes every later run probe Chrome and
            re-trigger the Keychain prompt.

    Returns:
        True if config was written successfully, False on error.
    """
    try:
        env_path = Path(env_path)
        env_path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing content to avoid overwriting keys
        existing_keys: set = set()
        existing_content = ""
        if env_path.exists():
            existing_content = env_path.read_text(encoding="utf-8")
            for line in existing_content.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    existing_keys.add(key)

        lines_to_add = []
        if "SETUP_COMPLETE" not in existing_keys:
            lines_to_add.append("SETUP_COMPLETE=true")
        if from_browser and "FROM_BROWSER" not in existing_keys:
            lines_to_add.append(f"FROM_BROWSER={_format_env_value(from_browser)}")

        if not lines_to_add:
            return True  # Nothing to write, already configured

        # Create/append as a 0o600 secret file: the .env holds tokens and keys,
        # so it must never be created world-readable.
        with _open_secret_append(env_path) as f:
            if existing_content and not existing_content.endswith("\n"):
                f.write("\n")
            f.write("\n".join(lines_to_add) + "\n")

        return True

    except OSError as exc:
        logger.error("Failed to write setup config to %s: %s", env_path, exc)
        return False


def write_api_key(env_path: Path, api_key: str, key_name: str = "SCRAPECREATORS_API_KEY") -> bool:
    """Append an API key to the .env file as a 0o600 secret.

    Reuses the same secret-safe write path as ``write_setup_config`` so the
    value lands with restrictive permissions and round-trips through
    ``env.load_env_file``. Idempotent: if ``key_name`` is already present in
    the file, nothing is written and the existing value is preserved (we never
    clobber a key the user may have set by hand).

    Args:
        env_path: Path to the .env file (e.g. ~/.config/last30days/.env).
        api_key: The raw key value to persist.
        key_name: The env var name to write (default SCRAPECREATORS_API_KEY).

    Returns:
        True if the key was written or already present, False on error or when
        ``api_key`` is empty.
    """
    if not api_key:
        return False
    try:
        env_path = Path(env_path)
        env_path.parent.mkdir(parents=True, exist_ok=True)

        existing_content = ""
        if env_path.exists():
            existing_content = env_path.read_text(encoding="utf-8")
            for line in existing_content.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    if stripped.split("=", 1)[0].strip() == key_name:
                        return True  # Already configured; do not duplicate

        line = f"{key_name}={_format_env_value(api_key)}\n"
        with _open_secret_append(env_path) as f:
            if existing_content and not existing_content.endswith("\n"):
                f.write("\n")
            f.write(line)

        return True

    except OSError as exc:
        logger.error("Failed to write API key to %s: %s", env_path, exc)
        return False


def mask_api_key(api_key: str) -> str:
    """Return a non-secret display form of an API key (prefix + last 4).

    Used so the key never appears verbatim in stdout the host model captures.
    Short or empty keys collapse to a fixed placeholder.
    """
    if not api_key or len(api_key) <= 8:
        return "sc_…"
    return f"{api_key[:3]}…{api_key[-4:]}"


def get_setup_status_text(results: Dict[str, Any]) -> str:
    """Return a human-readable summary of auto-setup results.

    Args:
        results: Dict from run_auto_setup()

    Returns:
        Multi-line status text.
    """
    lines = []
    lines.append("Setup complete! Here's what I found:")
    lines.append("")

    cookies_found = results.get("cookies_found", {})
    if results.get("browser_cookie_scan_attempted") and cookies_found:
        for source, browser in cookies_found.items():
            lines.append(f"  - {source.upper()} cookies found in {browser}")

    ytdlp_action = results.get("ytdlp_action", "")
    if ytdlp_action == "installed":
        lines.append("  - Installed yt-dlp via Homebrew")
    elif ytdlp_action == "install_failed":
        lines.append("  - yt-dlp install failed \u2014 run `brew install yt-dlp` manually")
    elif ytdlp_action == "no_homebrew":
        lines.append("  - yt-dlp not found. Install Homebrew first, then: brew install yt-dlp")
    elif ytdlp_action == "no_pip_windows":
        lines.append(
            "  - yt-dlp not found. Install with: pip install yt-dlp "
            "(it may install to a Scripts directory not on PATH -- add it to PATH if YouTube search stays inactive)"
        )
    elif ytdlp_action == "already_installed":
        lines.append("  - yt-dlp already installed")
    elif results.get("ytdlp_installed", False):
        lines.append("  - yt-dlp is installed (YouTube search ready)")
    else:
        lines.append("  - yt-dlp not found (install with: brew install yt-dlp)")

    digg_action = results.get("digg_action", "")
    if digg_action == "installed":
        lines.append("  - Installed Digg CLI (free AI-news clusters source now active)")
    elif digg_action == "already_installed":
        lines.append("  - Digg CLI already installed (AI-news clusters active)")
    elif digg_action == "installed_off_path":
        digg_path = results.get("digg_path", "")
        if digg_path:
            bin_dir = _digg_bin_dir_hint(digg_path)
            lines.append(
                f"  - Digg CLI found at {digg_path} but not on PATH — add "
                f"{bin_dir} to PATH and restart your agent session/gateway "
                "for Digg to activate"
            )
        else:
            lines.append(
                "  - Digg CLI is installed but not on PATH — add its install "
                "directory to PATH and restart your agent session/gateway for "
                "Digg to activate"
            )
    elif digg_action == "install_failed":
        lines.append(f"  - Digg CLI install failed — run `{DIGG_INSTALL_CMD}` manually")
    elif digg_action == "no_npx":
        lines.append(
            "  - Digg CLI not installed (free, optional). Install Node/npx, then: "
            f"{DIGG_INSTALL_CMD}"
        )

    pp_sources = results.get("pp_sources", {})
    pp_name: dict[str, str] = {"arxiv": "arXiv", "techmeme": "Techmeme"}
    for source_key, entry in sorted(pp_sources.items()):
        name = pp_name.get(source_key, source_key.title())
        action = entry.get("action", "")
        if action == "installed":
            lines.append(f"  - Installed {name} CLI ({name} source now active)")
        elif action == "already_installed":
            lines.append(f"  - {name} CLI already installed ({name} active)")
        elif action == "installed_off_path":
            path = entry.get("path", "")
            if path:
                lines.append(
                    f"  - {name} CLI at {path} but not on PATH — add "
                    f"{os.path.dirname(os.path.expanduser(path))} to PATH and "
                    f"restart your agent session/gateway for {name} to activate"
                )
            else:
                lines.append(
                    f"  - {name} CLI installed but not on PATH — add its install "
                    "directory to PATH and restart your agent session/gateway for "
                    f"{name} to activate"
                )
        elif action == "install_failed":
            lines.append(
                f"  - {name} CLI install failed — run "
                f"`npx -y {PRINTING_PRESS_NPM} install {source_key} --cli-only` manually"
            )
        elif action == "no_npx":
            lines.append(
                f"  - {name} CLI not installed (free, optional). Install Node/npx, "
                f"then: `npx -y {PRINTING_PRESS_NPM} install {source_key} --cli-only`"
            )

    # Bright Data / Amazon. Reported but never installed (it spends the user's
    # own metered credits), so the only useful thing setup can do is say
    # precisely why the lane is or is not active -- the three states below are
    # otherwise invisible, since SKILL.md tells the model not to raise the
    # subject mid-run.
    brightdata_status_entry = results.get("brightdata") or {}
    bd_action = brightdata_status_entry.get("action", "")
    if brightdata_status_entry.get("engine_active"):
        lines.append("  - Bright Data CLI ready (Amazon buyer signals available)")
    elif bd_action == "already_installed":
        lines.append(
            "  - Bright Data CLI installed but not logged in — run "
            "`brightdata login` to enable Amazon buyer signals (optional)"
        )
    elif bd_action == "installed_off_path":
        bd_path = brightdata_status_entry.get("path", "")
        lines.append(
            f"  - Bright Data CLI found at {bd_path} but not on PATH — add "
            f"{os.path.dirname(os.path.expanduser(bd_path))} to PATH and restart "
            "your agent session/gateway for Amazon buyer signals to activate"
        )
    elif bd_action == "not_installed":
        lines.append(
            "  - Amazon buyer signals not installed (optional; 5,000 free "
            "requests/month). Install with: npm i -g @brightdata/cli && brightdata login"
        )

    env_written = results.get("env_written", False)
    if env_written:
        lines.append("")
        lines.append("Configuration saved. Future runs will auto-detect your browsers.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenClaw server-side setup (no browser, JSON output)
# ---------------------------------------------------------------------------

_OPENCLAW_KEY_NAMES = [
    "SCRAPECREATORS_API_KEY",
    "XAI_API_KEY",
    "BRAVE_API_KEY",
    "EXA_API_KEY",
    "SERPER_API_KEY",
    "OPENAI_API_KEY",
    "AUTH_TOKEN",
]


def run_openclaw_setup(config: Dict[str, Any]) -> Dict[str, Any]:
    """Server-side setup probe: no cookies, tool + key availability, Digg CLI.

    Best-effort installs digg-pp-cli when npx is available (same as desktop
    ``run_auto_setup``). Returns a dict suitable for JSON output to stdout so
    that SKILL.md can present appropriate options to the user.
    """
    yt_dlp = shutil.which("yt-dlp") is not None
    node = shutil.which("node") is not None
    python3 = shutil.which("python3") is not None

    digg_installed, digg_action, digg_stderr, digg_path = _install_digg_cli()

    keys: Dict[str, bool] = {}
    for key_name in _OPENCLAW_KEY_NAMES:
        short = key_name.lower().replace("_api_key", "").replace("_key", "").replace("_token", "")
        # Normalize: AUTH_TOKEN -> auth, SCRAPECREATORS_API_KEY -> scrapecreators
        keys[short] = bool(config.get(key_name))

    # Determine x_method
    if config.get("XAI_API_KEY"):
        x_method: Optional[str] = "xai"
    elif config.get("AUTH_TOKEN") and config.get("CT0"):
        x_method = "cookies"
    else:
        x_method = None

    payload: Dict[str, Any] = {
        "yt_dlp": yt_dlp,
        "node": node,
        "python3": python3,
        "digg_cli": digg_installed,
        "digg_action": digg_action,
        "keys": keys,
        "x_method": x_method,
    }
    if digg_path:
        payload["digg_path"] = digg_path
    if digg_action == "install_failed" and digg_stderr:
        payload["digg_stderr"] = digg_stderr
    return payload


# ---------------------------------------------------------------------------
# Device auth flow (GitHub OAuth via ScrapeCreators)
# ---------------------------------------------------------------------------

_DEVICE_BASE = "https://api.scrapecreators.com/v1/github/device"

# A GitHub device code is always XXXX-XXXX (uppercase alphanumerics). We validate
# user_code against this before copying, labeling, or emitting it so a malformed
# or key-shaped value (e.g. a returning-account server response) is never
# mislabeled as a device code or leaked to stdout/clipboard.
_DEVICE_CODE_RE = re.compile(r"^[0-9A-Z]{4}-[0-9A-Z]{4}$")


def _existing_scrapecreators_key() -> Optional[str]:
    """Return the SCRAPECREATORS_API_KEY already saved in the .env, if any."""
    try:
        from . import env as _env

        if _env.CONFIG_FILE and _env.CONFIG_FILE.exists():
            return _env.load_env_file(_env.CONFIG_FILE).get("SCRAPECREATORS_API_KEY") or None
    except Exception as exc:  # never let a config-read failure block auth
        logger.debug("Could not read existing ScrapeCreators key: %s", exc)
    return None


def run_device_auth() -> Optional[Tuple[str, str, str, int]]:
    """Start the device authorization flow.

    POSTs to the ScrapeCreators device/code endpoint.

    Returns:
        (device_code, user_code, verification_uri, interval) on success,
        None on failure.
    """
    try:
        body = json.dumps({}).encode()
        req = Request(f"{_DEVICE_BASE}/code", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError, OSError) as exc:
        logger.warning("Device auth code request failed: %s", exc)
        return None

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri")
    interval = data.get("interval", 5)

    if not device_code or not user_code:
        # Log only the response's key names, never its values — a returning
        # account's response could carry a raw API key we must not write to logs.
        logger.warning(
            "Device auth returned incomplete response (keys: %s)", sorted(data.keys())
        )
        return None

    return (device_code, user_code, verification_uri or "", interval)


def poll_device_auth(
    device_code: str,
    interval: int,
    timeout: int = 300,
    user_code: str = "",
    clipboard_ok: bool = False,
) -> Optional[str]:
    """Poll for an access token after the user authorizes the device.

    Args:
        device_code: The device_code from run_device_auth().
        interval: Polling interval in seconds.
        timeout: Maximum time to poll in seconds.
        user_code: The user code to remind about during polling.
        clipboard_ok: Whether the code was copied to clipboard.

    Returns:
        access_token on success, None on timeout or failure.
    """
    import sys

    started_at = time.time()
    deadline = started_at + timeout
    last_reminder = started_at
    reminder_count = 0
    max_reminders = 4
    reminder_interval = 30  # seconds between reminders

    while time.time() < deadline:
        time.sleep(interval)

        # Periodic reminder of the code while waiting
        if (
            user_code
            and reminder_count < max_reminders
            and time.time() - last_reminder >= reminder_interval
        ):
            clipboard_hint = " (on your clipboard)" if clipboard_ok else ""
            print(
                f"  Still waiting... Your code: {user_code}{clipboard_hint}",
                file=sys.stderr,
                flush=True,
            )
            last_reminder = time.time()
            reminder_count += 1

        try:
            body = json.dumps({"device_code": device_code}).encode()
            req = Request(f"{_DEVICE_BASE}/token", data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except HTTPError as exc:
            if exc.code in (400, 403, 428):
                continue
            logger.warning("Device auth poll error: %s", exc)
            return None
        except (URLError, OSError):
            continue

        if data.get("access_token"):
            return data["access_token"]

        error = data.get("error")
        if error == "slow_down":
            interval = min(interval + 2, 30)
            continue
        if error == "authorization_pending":
            continue
        if error in ("expired_token", "access_denied"):
            logger.warning("Device auth failed: %s", error)
            return None

    return None


def fetch_api_key(access_token: str) -> Optional[str]:
    """Fetch the ScrapeCreators API key using the GitHub access token.

    GETs the device/profile endpoint with Bearer auth.

    Returns:
        api_key string on success, None on failure.
    """
    try:
        req = Request(f"{_DEVICE_BASE}/profile")
        req.add_header("Authorization", f"Bearer {access_token}")
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError, OSError) as exc:
        logger.warning("Failed to fetch API key: %s", exc)
        return None

    api_key = data.get("api_key")
    if not api_key:
        # The /profile response parsed but carried no api_key — the common case
        # for a GitHub account already linked to a ScrapeCreators account. Log
        # the response's FIELD NAMES only (never values — the body may contain a
        # key under a different field) so the already-registered response shape
        # can be handled in a follow-up (see plan OQ1).
        logger.warning(
            "Device auth /profile returned no api_key (fields: %s)", sorted(data.keys())
        )
        return None
    return api_key


def _device_handle_path() -> Path:
    """Where run_github_start persists the device_code/interval for run_github_poll.

    Kept next to the .env in the config dir; falls back to the OS temp dir when
    no config dir is resolvable (clean/no-config mode).
    """
    try:
        from . import env as _env

        if _env.CONFIG_FILE:
            return _env.CONFIG_FILE.parent / ".github-device-handle.json"
    except Exception:
        pass
    import tempfile

    return Path(tempfile.gettempdir()) / "last30days-github-device-handle.json"


def _start_device_flow() -> "Tuple[Dict[str, Any], Optional[Dict[str, Any]]]":
    """Submit the GitHub device flow and surface the code, without polling.

    Returns ``(public_result, handle)``. ``handle`` is None for the
    already-registered and error cases (nothing to poll); otherwise it carries
    the private poll state (``device_code``/``interval``/``user_code``/
    ``clipboard_ok``) that never belongs in the public, stdout-printed result.
    Callers either persist the handle to a file (``run_github_start``, for a
    separate poll process) or hand it straight to ``run_github_poll`` in-memory
    (``run_full_device_auth``, so a failed file write can't strand the one-shot).
    """
    import sys
    import webbrowser

    # Already-registered short-circuit: a saved key means no device dance. The
    # key is returned raw here and masked at the CLI boundary before print.
    existing = _existing_scrapecreators_key()
    if existing:
        return (
            {
                "status": "already_registered",
                "method": "existing",
                "api_key": existing,
                "persisted": True,
            },
            None,
        )

    result = run_device_auth()
    if result is None:
        return ({"status": "error", "message": "Failed to start device auth flow"}, None)

    device_code, user_code, verification_uri, interval = result

    # Validate the code shape BEFORE copying, labeling, or emitting it. A
    # non-conforming user_code (e.g. a key-shaped value) is never surfaced as a
    # GitHub device code; we stop rather than instruct the user to paste garbage.
    if not _DEVICE_CODE_RE.match(user_code):
        logger.warning("Device auth returned a non-device-shaped user_code; aborting.")
        return (
            {
                "status": "error",
                "message": "ScrapeCreators returned an unexpected device-code format.",
            },
            None,
        )

    # Structured stdout line for machine consumers.
    print(
        json.dumps(
            {
                "event": "device_code_ready",
                "user_code": user_code,
                "verification_uri": verification_uri,
            }
        ),
        flush=True,
    )

    # Copy the code to the clipboard BEFORE opening the browser.
    clipboard_ok = False
    if sys.platform == "darwin":
        try:
            subprocess.run(["pbcopy"], input=user_code.encode(), check=True, timeout=5)
            clipboard_ok = True
        except Exception:
            pass  # pbcopy unavailable or failed, fall through

    # Print the code as a plain HUMAN line on stdout too, so a foreground caller
    # sees it in the returned output even without reading the JSON. The clipboard
    # claim is only made when pbcopy actually succeeded (else: type it).
    if clipboard_ok:
        print(
            f"Your GitHub code: {user_code}  (already on your clipboard - just paste it, Cmd+V)",
            flush=True,
        )
    else:
        print(f"Your GitHub code: {user_code}  (type it on the GitHub page)", flush=True)

    # Human box on stderr for direct-terminal users.
    clipboard_hint = "  (copied to clipboard)" if clipboard_ok else ""
    code_line = f"  Your code: {user_code}{clipboard_hint}"
    action_line = "  Paste it on the GitHub page that just opened"
    width = max(len(code_line), len(action_line)) + 2
    border = "-" * width
    print(f"\n+{border}+", file=sys.stderr)
    print(f"|{code_line.ljust(width)}|", file=sys.stderr)
    print(f"|{action_line.ljust(width)}|", file=sys.stderr)
    print(f"+{border}+", file=sys.stderr)

    if verification_uri:
        try:
            webbrowser.open(verification_uri)
        except Exception:
            print(f"Open: {verification_uri}", file=sys.stderr)

    public = {
        "status": "awaiting_authorization",
        "user_code": user_code,
        "verification_uri": verification_uri,
        "clipboard_ok": clipboard_ok,
    }
    handle = {
        "device_code": device_code,
        "interval": interval,
        "user_code": user_code,
        "clipboard_ok": clipboard_ok,
    }
    return (public, handle)


def run_github_start() -> Dict[str, Any]:
    """Start the device flow and persist the poll handle for a later
    ``run_github_poll`` process. Returns the public result (never the private
    device_code). See ``_start_device_flow`` for the returned statuses."""
    public, handle = _start_device_flow()
    if handle is not None:
        # Persist the poll handle (0o600) so a separate --github-poll process can
        # resume it. Best-effort: the in-memory one-shot path does not depend on
        # this write succeeding.
        path = _device_handle_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(handle), encoding="utf-8")
            os.chmod(path, 0o600)
        except Exception as exc:
            logger.warning("Could not persist device handle: %s", exc)
    return public


def run_github_poll(timeout: int = 300, *, _handle: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Poll for authorization using the handle from start.

    ``_handle`` (in-memory, from the one-shot) takes precedence over the
    persisted handle file. Returns success (with the fetched key), timeout, or
    the honest "Authorized but failed to fetch API key" branch. Deletes the
    persisted handle when the flow terminates.
    """
    import sys

    if _handle is not None:
        data = _handle
    else:
        try:
            data = json.loads(_device_handle_path().read_text(encoding="utf-8"))
        except Exception:
            return {
                "status": "error",
                "message": "No pending GitHub device flow; run setup --github-start first.",
            }

    device_code = data["device_code"]
    interval = int(data.get("interval", 5))
    user_code = data.get("user_code", "")
    # Read the real clipboard state so the polling reminder never falsely claims
    # the code is on the clipboard (non-macOS, or a failed pbcopy). Missing key
    # (older handle) defaults to False -- don't overstate.
    clipboard_ok = bool(data.get("clipboard_ok", False))

    print("Waiting for authorization...", file=sys.stderr, flush=True)
    access_token = poll_device_auth(
        device_code, interval, timeout=timeout, user_code=user_code, clipboard_ok=clipboard_ok
    )

    def _cleanup() -> None:
        try:
            _device_handle_path().unlink()
        except Exception:
            pass

    if access_token is None:
        _cleanup()
        return {"status": "timeout", "user_code": user_code}

    api_key = fetch_api_key(access_token)
    _cleanup()
    if api_key is None:
        return {
            "status": "error",
            "message": "Authorized but failed to fetch API key",
        }

    return {"status": "success", "method": "device", "api_key": api_key, "user_code": user_code}


def run_full_device_auth(timeout: int = 300) -> Dict[str, Any]:
    """Back-compat one-shot: start the device flow, then poll to completion.

    Passes the poll handle to ``run_github_poll`` IN MEMORY, so a failed handle-
    file write can't strand the one-shot. Kept so callers of ``setup --github`` /
    ``--device-auth`` still work; the model-driven wizard uses the two-command
    split (start then poll) instead.
    """
    public, handle = _start_device_flow()
    if handle is None:
        return public  # already_registered or error
    return run_github_poll(timeout=timeout, _handle=handle)


# ---------------------------------------------------------------------------
# Unified GitHub auth
# ---------------------------------------------------------------------------


def run_github_auth(timeout: int = 300) -> Dict[str, Any]:
    """Run the --github setup path via device auth (one-shot, back-compat).

    The existing-key short-circuit now lives in run_github_start; this delegates
    to the start+poll chain. This path must not read or forward local GitHub CLI
    tokens.
    """
    return run_full_device_auth(timeout=timeout)

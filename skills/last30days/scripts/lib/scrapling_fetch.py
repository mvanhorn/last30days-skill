"""Optional Scrapling-CLI fetch helper for anti-bot / JS-rendered pages.

Several lanes (Reddit, Bluesky, Trustpilot) routinely hit HTTP 403s,
Cloudflare interstitials, or JS-only pages that the stdlib ``urllib`` path
in ``http.py`` cannot clear. When the Scrapling CLI is installed on PATH,
this helper fetches those pages through a real browser with Chrome TLS
impersonation and (optionally) Cloudflare Turnstile solving, returning the
page as clean markdown / text / HTML.

Activation gate: mirrors the other CLI-gated optional sources
(``arxiv-pp-cli``, ``yt-dlp``, ``digg-pp-cli``). ``is_available()`` checks
``shutil.which("scrapling")`` on the agent subprocess PATH; callers gate on
it before use. Install is out of band and deliberately isolated from this
stdlib-only skill -- Scrapling is invoked as a subprocess, never imported::

    uv tool install 'scrapling[all]' && scrapling install

Foundation, not yet wired in: this module is import-safe and fully tested,
but no lane calls it yet. Wiring a specific lane (e.g. Reddit's 403 path) to
fall through to Scrapling touches that lane's retry logic and needs an
integration test plus a SKILL.md note, so it belongs in a dedicated
beta-channel PR rather than riding in with the primitive.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from . import log, subproc


CLI_BIN = "scrapling"

# ``scrapling extract`` sub-command -> what each rung is for.
MODE_GET = "get"                  # fast HTTP + Chrome TLS impersonation
MODE_FETCH = "fetch"             # full browser render (JS-only pages)
MODE_STEALTHY = "stealthy-fetch"  # anti-bot wall (Cloudflare Turnstile)
_MODES = frozenset({MODE_GET, MODE_FETCH, MODE_STEALTHY})

# Output extension picks the format, matching the Scrapling CLI's convention.
_FORMAT_EXT = {"markdown": ".md", "text": ".txt", "html": ".html"}

# Generous: a cold ``stealthy-fetch`` may spend time solving a challenge.
DEFAULT_TIMEOUT = 75


def _log(msg: str) -> None:
    log.source_log("scrapling", msg, tty_only=False)


def is_available() -> bool:
    """True when the ``scrapling`` CLI is on the agent subprocess PATH."""
    return shutil.which(CLI_BIN) is not None


def _build_args(
    mode: str,
    url: str,
    out_path: Path,
    *,
    css_selector: Optional[str] = None,
    solve_cloudflare: bool = False,
) -> List[str]:
    """Build the ``scrapling extract`` argv. ``--solve-cloudflare`` is only
    meaningful for stealthy-fetch, so it is dropped for the other modes."""
    args = [CLI_BIN, "extract", mode, url, str(out_path)]
    if css_selector:
        args += ["--css-selector", css_selector]
    if solve_cloudflare and mode == MODE_STEALTHY:
        args.append("--solve-cloudflare")
    return args


def fetch(
    url: str,
    *,
    mode: str = MODE_GET,
    fmt: str = "markdown",
    css_selector: Optional[str] = None,
    solve_cloudflare: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """Fetch ``url`` through the Scrapling CLI. Returns the content, or None.

    Never raises: an absent CLI, an unknown mode/format, a timeout, a
    non-zero exit, an unreadable or empty result all return ``None`` with a
    one-line logged reason, so a caller can treat this strictly as an
    optional fallback below its primary fetch path.
    """
    if mode not in _MODES:
        _log(f"unknown mode {mode!r}")
        return None
    ext = _FORMAT_EXT.get(fmt)
    if ext is None:
        _log(f"unknown format {fmt!r}")
        return None
    if not is_available():
        _log(f"{CLI_BIN} not on PATH")
        return None

    # Scrapling writes to a file whose extension selects the format; use a
    # temp file and read it back so callers receive a string. Creation is
    # inside the never-raise contract too: a full or unwritable temp dir
    # must degrade to None like every other failure, not escape as OSError.
    tmp: Optional[Path] = None
    try:
        try:
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as fh:
                tmp = Path(fh.name)
        except OSError as exc:
            _log(f"temp file failed: {exc}")
            return None
        cmd = _build_args(
            mode, url, tmp,
            css_selector=css_selector,
            solve_cloudflare=solve_cloudflare,
        )
        try:
            result = subproc.run_with_timeout(cmd, timeout=timeout)
        except subproc.SubprocTimeout as exc:
            _log(f"timeout: {exc}")
            return None
        except FileNotFoundError as exc:
            _log(f"binary missing: {exc}")
            return None
        except OSError as exc:
            _log(f"spawn failed: {exc}")
            return None

        if result.returncode != 0:
            snippet = (result.stderr or "").strip().splitlines()[:1]
            first = snippet[0] if snippet else f"exit {result.returncode}"
            _log(f"CLI exit {result.returncode}: {first}")
            return None

        try:
            content = tmp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _log(f"could not read output: {exc}")
            return None
        if not content.strip():
            _log("empty output")
            return None
        return content
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass

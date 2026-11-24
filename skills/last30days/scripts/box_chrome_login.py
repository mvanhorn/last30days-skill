#!/usr/bin/env python3
"""Extras-host X login helper: print (or ``--exec``) the box-chrome launch.

On extra hosts (Linux, a Darwin Mac mini, a Darwin agentcookie sink, or
``AGENTCOOKIE=on``) the local Chrome cookie store cannot be decrypted, so the
only way to hand bird a live X session is to launch a throwaway Chrome with a
remote-debugging port, let the human log in, and read the pair over CDP. This
helper prints the exact, host-correct launch command — or launches it with
``--exec`` — and refuses to do anything on a MacBook (which keeps its Keychain /
Firefox / Safari extract path).

Contract, matching the rest of the feature:
* **Extras only.** On a MacBook (and any non-extras host) it prints "no launch
  needed" and never spawns a browser, even with ``--exec``. An official-only
  host (``LAST30DAYS_HOST=grok-bot``, see ``env.x_policy``) gets the same
  no-launch recipe with a neutral note, even on Linux or a Mac mini.
* Launch on the last30days extras NUX port ``18800``
  (``SAND_CHROME_REMOTE_DEBUG_PORT=18800``) so ``chrome_cdp`` finds it. This is
  NOT box-chrome's built-in default (``9222`` + the display number).
* Uses the host ``box-chrome`` wrapper (which sets ``--class=box-chrome``);
  never assembles a raw ``google-chrome-stable`` flag soup and never launches
  raw Chrome with a custom ``--class`` (a raw Chrome with ``--class=l30d-…``
  failed live where ``box-chrome`` succeeded). No special user-agent is
  required. If ``box-chrome`` is missing it tells the user to sign into a
  remote-debugging Chrome and pin ``BROWSER_CDP_URL``.
* Reads NO cookies and prints NO cookie values. It never writes to the ``.env``.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import chrome_cdp, env  # noqa: E402

# The last30days extras NUX port — single source of truth is chrome_cdp.
EXTRAS_CDP_PORT = chrome_cdp._BOX_CHROME_PORT
LOGIN_URL = "https://x.com/login"
BOX_CHROME_BIN = "box-chrome"
# Printed on an official-only host instead of the MacBook note. Names no
# cookie mechanism: this host searches X through official access only.
OFFICIAL_HOST_NOTE = (
    "No launch needed: this host searches X through official access and "
    "does not use a browser login window."
)


def _is_safe_owner_dir(path: Path) -> bool:
    """True when ``path`` is an existing non-symlink directory owned by the current user with no group/other access."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return False
    if os.name != "nt":
        if st.st_uid != os.getuid():
            return False
        if stat.S_IMODE(st.st_mode) & 0o077:
            return False
    return True


def _default_profile_dir() -> str:
    """Return an owner-only per-user runtime/cache profile path (never a fixed /tmp path)."""
    xdg_runtime = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if xdg_runtime and os.path.isabs(xdg_runtime):
        runtime_path = Path(xdg_runtime)
        if _is_safe_owner_dir(runtime_path):
            return str(runtime_path / "last30days-x-chrome")
    xdg_cache = (os.environ.get("XDG_CACHE_HOME") or "").strip()
    if xdg_cache and os.path.isabs(xdg_cache):
        cache_root = Path(xdg_cache)
    else:
        cache_root = Path.home() / ".cache"
    return str(cache_root / "last30days" / "x-chrome")


DEFAULT_PROFILE_DIR = _default_profile_dir()


def _ensure_private_profile_dir(profile_dir: str) -> None:
    """Create and verify a non-symlink, owner-only (0o700) Chrome profile directory."""
    if not profile_dir or not profile_dir.strip():
        raise OSError("Profile directory path must not be empty")

    try:
        st = os.lstat(profile_dir)
    except FileNotFoundError:
        os.makedirs(profile_dir, mode=0o700, exist_ok=True)
        st = os.lstat(profile_dir)

    if stat.S_ISLNK(st.st_mode):
        raise OSError(f"Refusing symlink profile directory: {profile_dir}")
    if not stat.S_ISDIR(st.st_mode):
        raise OSError(f"Profile path is not a directory: {profile_dir}")

    if os.name != "nt":
        uid = os.getuid()
        if st.st_uid != uid:
            raise PermissionError(f"Profile directory is not owned by uid {uid}: {profile_dir}")
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.chmod(profile_dir, 0o700)
            st = os.lstat(profile_dir)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise OSError(f"Profile directory changed unexpectedly: {profile_dir}")
            if st.st_uid != uid:
                raise PermissionError(f"Profile directory is not owned by uid {uid}: {profile_dir}")
            if stat.S_IMODE(st.st_mode) != 0o700:
                raise PermissionError(
                    f"Could not enforce 0700 permissions on profile directory: {profile_dir}"
                )


def build_recipe(
    config: Dict[str, Any],
    *,
    profile_dir: Optional[str] = None,
    url: str = LOGIN_URL,
) -> Dict[str, Any]:
    """Return the gated launch recipe. Never spawns, never reads cookies.

    Keys: ``applies`` (extras host?), ``box_chrome`` (path or None), ``port``,
    ``profile_dir``, ``url``, ``env`` (launch env overrides or None),
    ``command`` (argv or None), ``note`` (human guidance).
    """
    profile_dir = _default_profile_dir() if profile_dir is None else profile_dir
    # Official-only host (LAST30DAYS_HOST=grok-bot): the same no-launch
    # recipe as a MacBook, checked BEFORE the extras signals so a Linux or
    # Mac mini Grok Bot computer never gets a launch command. The note is
    # neutral on purpose. A LAST30DAYS_X_BACKEND=bird pin re-enables
    # discovery through env.x_policy, and with it this helper.
    if not env.x_policy(config).cookie_discovery:
        return {
            "applies": False,
            "box_chrome": None,
            "port": EXTRAS_CDP_PORT,
            "profile_dir": profile_dir,
            "url": url,
            "env": None,
            "command": None,
            "note": OFFICIAL_HOST_NOTE,
        }

    if not env.x_extras_enabled(config):
        return {
            "applies": False,
            "box_chrome": None,
            "port": EXTRAS_CDP_PORT,
            "profile_dir": profile_dir,
            "url": url,
            "env": None,
            "command": None,
            "note": (
                "This host uses the standard browser-cookie path (Keychain / "
                "Firefox / Safari extract). No box-chrome login is needed; "
                "run `setup --allow-browser-cookies` as usual."
            ),
        }

    box = shutil.which(BOX_CHROME_BIN)
    if box:
        return {
            "applies": True,
            "box_chrome": box,
            "port": EXTRAS_CDP_PORT,
            "profile_dir": profile_dir,
            "url": url,
            "env": {
                "CHROME_USER_DATA_DIR": profile_dir,
                "SAND_CHROME_REMOTE_DEBUG_PORT": str(EXTRAS_CDP_PORT),
            },
            "command": [box, "--new-window", url],
            "note": (
                "Launch this throwaway login Chrome, wait for the x.com login "
                "page, then hand the desktop to the human to type. Do NOT drive "
                "the page. After they sign in, pin "
                f"BROWSER_CDP_URL=http://127.0.0.1:{EXTRAS_CDP_PORT} in .env "
                "(never AUTH_TOKEN/CT0) and run `setup --allow-browser-cookies`."
            ),
        }

    return {
        "applies": True,
        "box_chrome": None,
        "port": EXTRAS_CDP_PORT,
        "profile_dir": profile_dir,
        "url": url,
        "env": None,
        "command": None,
        "note": (
            "box-chrome is not on PATH. Do not assemble a raw google-chrome "
            "command or launch raw Chrome with a custom --class (that is what "
            "failed live; box-chrome sets --class=box-chrome). Sign into x.com "
            "in a Chrome that already exposes a remote-debugging port, then pin "
            "BROWSER_CDP_URL to that endpoint in .env and run "
            "`setup --allow-browser-cookies`."
        ),
    }


def render_recipe(recipe: Dict[str, Any]) -> str:
    """Human-readable recipe. Contains no cookie values (none are read)."""
    lines: List[str] = []
    if not recipe["applies"]:
        if recipe["note"] == OFFICIAL_HOST_NOTE:
            lines.append("[login helper] No launch needed on this host.")
        else:
            lines.append("[box-chrome login] Not an extras host.")
        lines.append(recipe["note"])
        return "\n".join(lines)

    if recipe["command"] is None:
        lines.append("[box-chrome login] Extras host, but box-chrome is unavailable.")
        lines.append(recipe["note"])
        return "\n".join(lines)

    env_prefix = " ".join(f"{k}={shlex.quote(str(v))}" for k, v in recipe["env"].items())
    cmd = " ".join(shlex.quote(str(part)) for part in recipe["command"])
    lines.append("[box-chrome login] Extras host. Launch the throwaway login Chrome:")
    lines.append("")
    lines.append(f"  mkdir -m 700 -p {shlex.quote(recipe['profile_dir'])}")
    lines.append(f"  {env_prefix} {cmd}")
    lines.append("")
    lines.append(recipe["note"])
    lines.append(
        "For this first-run harvest, set AGENTCOOKIE=off so a sidecar can't mix "
        "a different pair; leave AGENTCOOKIE unset again after success."
    )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    do_exec = "--exec" in argv
    as_json = "--json" in argv

    config = env.get_config()  # default policy: no cookie reads, no discovery
    recipe = build_recipe(config)

    if as_json:
        printable = {k: v for k, v in recipe.items() if k != "box_chrome"}
        printable["box_chrome_present"] = recipe["box_chrome"] is not None
        print(json.dumps(printable))
    else:
        print(render_recipe(recipe))

    if do_exec:
        # Refuse to spawn on a non-extras host, or when box-chrome is missing.
        if not recipe["applies"] or not recipe["command"]:
            return 0
        try:
            _ensure_private_profile_dir(recipe["profile_dir"])
        except OSError as exc:
            print(
                f"[box-chrome login] unsafe or unusable profile directory: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        launch_env = os.environ.copy()
        launch_env.update(recipe["env"])
        try:
            subprocess.Popen(recipe["command"], env=launch_env)
        except OSError as exc:
            print(f"[box-chrome login] failed to launch: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

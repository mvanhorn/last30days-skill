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
  needed" and never spawns a browser, even with ``--exec``.
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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from lib import chrome_cdp, env  # noqa: E402

# The last30days extras NUX port — single source of truth is chrome_cdp.
EXTRAS_CDP_PORT = chrome_cdp._BOX_CHROME_PORT
DEFAULT_PROFILE_DIR = "/tmp/last30days-x-chrome"
LOGIN_URL = "https://x.com/login"
BOX_CHROME_BIN = "box-chrome"


def build_recipe(
    config: Dict[str, Any],
    *,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    url: str = LOGIN_URL,
) -> Dict[str, Any]:
    """Return the gated launch recipe. Never spawns, never reads cookies.

    Keys: ``applies`` (extras host?), ``box_chrome`` (path or None), ``port``,
    ``profile_dir``, ``url``, ``env`` (launch env overrides or None),
    ``command`` (argv or None), ``note`` (human guidance).
    """
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
        lines.append("[box-chrome login] Not an extras host.")
        lines.append(recipe["note"])
        return "\n".join(lines)

    if recipe["command"] is None:
        lines.append("[box-chrome login] Extras host, but box-chrome is unavailable.")
        lines.append(recipe["note"])
        return "\n".join(lines)

    env_prefix = " ".join(f"{k}={v}" for k, v in recipe["env"].items())
    cmd = " ".join(recipe["command"])
    lines.append("[box-chrome login] Extras host. Launch the throwaway login Chrome:")
    lines.append("")
    lines.append(f"  mkdir -p {recipe['profile_dir']}")
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
            os.makedirs(recipe["profile_dir"], exist_ok=True)
        except OSError:
            pass
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

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

import argparse
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


def _ancestor_chain(path: Path) -> List[Path]:
    """Return existing/planned ancestors of ``path`` from filesystem root down to ``path.parent``."""
    return list(reversed(path.parents))


def _check_trusted_ancestor(anc: Path, *, is_immediate_parent: bool, uid: int) -> None:
    """Verify an existing ancestor directory cannot be tampered with by another local user."""
    st = os.lstat(anc)
    if stat.S_ISLNK(st.st_mode):
        # Never allow a symlink at the immediate parent or owned by a non-root user.
        if is_immediate_parent or os.name == "nt" or st.st_uid != 0:
            raise OSError(f"Refusing symlink in profile path: {anc}")
        # Allow root-owned system symlinks strictly above the immediate parent
        # (e.g., macOS /var -> private/var) only when the target dir is also trusted.
        try:
            st = os.stat(anc)
        except FileNotFoundError as exc:
            raise OSError(f"Refusing broken symlink in profile path: {anc}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise OSError(f"Ancestor in profile path is not a directory: {anc}")
    if os.name == "nt":
        return

    mode = stat.S_IMODE(st.st_mode)
    other_writable = bool(mode & 0o022)
    is_sticky = bool(mode & stat.S_ISVTX)

    if is_immediate_parent:
        if st.st_uid != uid:
            raise PermissionError(
                f"Profile parent directory is not owned by uid {uid}: {anc}"
            )
        if other_writable:
            raise PermissionError(
                f"Profile parent directory is writable by group or others: {anc}"
            )
        return

    if st.st_uid not in (0, uid):
        raise PermissionError(
            f"Profile ancestor directory has untrusted owner: {anc}"
        )
    # A root-owned sticky directory (e.g., /tmp with mode 1777) prevents other
    # non-root users from replacing a uid-owned child directory beneath it; any
    # other group/world-writable ancestor allows path substitution.
    if other_writable and not (st.st_uid == 0 and is_sticky):
        raise PermissionError(
            f"Profile ancestor directory is writable by group or others: {anc}"
        )


def _is_safe_owner_dir(path: Path) -> bool:
    """True when ``path`` is an existing owner-only dir inside a trusted ancestor chain."""
    try:
        if ".." in path.parts:
            return False
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False
        uid = os.getuid() if os.name != "nt" else 0
        if os.name != "nt":
            if st.st_uid != uid:
                return False
            if stat.S_IMODE(st.st_mode) & 0o077:
                return False
        for anc in _ancestor_chain(path):
            _check_trusted_ancestor(anc, is_immediate_parent=False, uid=uid)
        canonical = path.resolve(strict=True)
        for anc in _ancestor_chain(canonical):
            _check_trusted_ancestor(anc, is_immediate_parent=False, uid=uid)
    except OSError:
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
        cache_candidate = Path(xdg_cache)
        if _is_safe_owner_dir(cache_candidate):
            return str(cache_candidate / "last30days" / "x-chrome")
    return str(Path.home() / ".cache" / "last30days" / "x-chrome")


DEFAULT_PROFILE_DIR = _default_profile_dir()


def _harden_owned_dir_0700(path: Path, uid: int) -> tuple[int, int]:
    """Verify non-symlink ownership and enforce mode 0700 on ``path``, returning ``(st_dev, st_ino)``."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise OSError(f"Refusing symlink directory: {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise OSError(f"Path is not a directory: {path}")
    if os.name == "nt":
        return (st.st_dev, st.st_ino)
    if st.st_uid != uid:
        raise PermissionError(f"Directory is not owned by uid {uid}: {path}")
    if hasattr(os, "O_DIRECTORY") and hasattr(os, "fchmod"):
        open_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(path), open_flags)
        try:
            fst = os.fstat(fd)
            if not stat.S_ISDIR(fst.st_mode):
                raise OSError(f"Path is not a directory: {path}")
            if fst.st_uid != uid:
                raise PermissionError(f"Directory is not owned by uid {uid}: {path}")
            if stat.S_IMODE(fst.st_mode) != 0o700:
                os.fchmod(fd, 0o700)
                fst = os.fstat(fd)
                if stat.S_IMODE(fst.st_mode) != 0o700:
                    raise PermissionError(
                        f"Could not enforce 0700 permissions on directory: {path}"
                    )
            return (fst.st_dev, fst.st_ino)
        finally:
            os.close(fd)
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(str(path), 0o700)
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"Directory changed unexpectedly: {path}")
        if st.st_uid != uid:
            raise PermissionError(f"Directory is not owned by uid {uid}: {path}")
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise PermissionError(f"Could not enforce 0700 permissions on directory: {path}")
    return (st.st_dev, st.st_ino)


def _ensure_private_profile_dir(profile_dir: str) -> str:
    """Create and verify a non-symlink, owner-only (0o700) profile dir; return canonical path."""
    if not profile_dir or not profile_dir.strip():
        raise OSError("Profile directory path must not be empty")

    raw_path = Path(profile_dir)
    if ".." in raw_path.parts:
        raise OSError(f"Refusing traversal segments in profile directory: {profile_dir}")
    target = raw_path if raw_path.is_absolute() else Path(os.path.abspath(profile_dir))

    uid = os.getuid() if os.name != "nt" else 0
    ancestors = _ancestor_chain(target)
    if not ancestors:
        raise OSError(f"Refusing root as profile directory: {profile_dir}")

    # Phase 1 (read-only): validate every existing unresolved ancestor AND the
    # full resolved ancestor chain of the deepest existing ancestor BEFORE
    # creating any directories or changing any permissions.
    deepest_existing: Optional[Path] = None
    deepest_idx = -1
    for idx, anc in enumerate(ancestors):
        is_immediate = idx == len(ancestors) - 1
        try:
            _check_trusted_ancestor(anc, is_immediate_parent=is_immediate, uid=uid)
        except FileNotFoundError:
            break
        deepest_existing = anc
        deepest_idx = idx

    if deepest_existing is None:
        raise FileNotFoundError(
            f"No existing ancestor directory for profile path: {profile_dir}"
        )

    resolved_existing = deepest_existing.resolve(strict=True)
    resolved_chain = _ancestor_chain(resolved_existing) + [resolved_existing]
    deepest_is_immediate = deepest_idx == len(ancestors) - 1
    for idx, anc in enumerate(resolved_chain):
        _check_trusted_ancestor(
            anc,
            is_immediate_parent=(deepest_is_immediate and idx == len(resolved_chain) - 1),
            uid=uid,
        )

    # Phase 2: create any missing per-user intermediate directories beneath the
    # verified existing prefix with explicit 0700 mode.
    for idx in range(deepest_idx + 1, len(ancestors)):
        anc = ancestors[idx]
        is_immediate = idx == len(ancestors) - 1
        os.makedirs(str(anc), mode=0o700, exist_ok=True)
        _harden_owned_dir_0700(anc, uid)
        _check_trusted_ancestor(anc, is_immediate_parent=is_immediate, uid=uid)

    try:
        os.lstat(target)
    except FileNotFoundError:
        os.makedirs(str(target), mode=0o700, exist_ok=True)

    verified_id = _harden_owned_dir_0700(target, uid)

    # Resolve the full canonical path (expanding any root-owned system symlinks
    # like macOS /var -> /private/var), validate every resolved ancestor from /
    # downward, and confirm canonical still refers to the exact verified inode.
    canonical = target.resolve(strict=True)
    canonical_ancestors = _ancestor_chain(canonical)
    if not canonical_ancestors:
        raise OSError(f"Refusing root as profile directory: {profile_dir}")
    for idx, anc in enumerate(canonical_ancestors):
        _check_trusted_ancestor(
            anc, is_immediate_parent=(idx == len(canonical_ancestors) - 1), uid=uid
        )

    cst = os.lstat(canonical)
    if stat.S_ISLNK(cst.st_mode) or not stat.S_ISDIR(cst.st_mode):
        raise OSError(f"Resolved profile path is not a directory: {canonical}")
    if os.name != "nt":
        if cst.st_uid != uid:
            raise PermissionError(
                f"Resolved profile directory is not owned by uid {uid}: {canonical}"
            )
        if stat.S_IMODE(cst.st_mode) != 0o700:
            raise PermissionError(
                f"Resolved profile directory does not have mode 0700: {canonical}"
            )
        if (cst.st_dev, cst.st_ino) != verified_id:
            raise OSError(f"Profile directory changed during verification: {canonical}")

    return str(canonical)


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

    helper_cmd = [sys.executable or "python3", str(Path(__file__).resolve()), "--exec"]
    if recipe["profile_dir"] != _default_profile_dir():
        helper_cmd.extend(["--profile-dir", str(recipe["profile_dir"])])
    if recipe["url"] != LOGIN_URL:
        helper_cmd.extend(["--url", str(recipe["url"])])
    launch_line = " ".join(shlex.quote(part) for part in helper_cmd)
    lines.append(
        "[box-chrome login] Extras host. Launch the throwaway login Chrome "
        "via --exec (verifies trusted 0700 profile dir):"
    )
    lines.append("")
    lines.append(f"  {launch_line}")
    lines.append("")
    lines.append(
        f"Launches {recipe['command'][0]} with "
        f"SAND_CHROME_REMOTE_DEBUG_PORT={recipe['port']} and "
        f"CHROME_USER_DATA_DIR={recipe['profile_dir']}."
    )
    lines.append(recipe["note"])
    lines.append(
        "For this first-run harvest, set AGENTCOOKIE=off so a sidecar can't mix "
        "a different pair; leave AGENTCOOKIE unset again after success."
    )
    return "\n".join(lines)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print or launch the host-gated box-chrome X login helper."
    )
    parser.add_argument("--exec", dest="do_exec", action="store_true")
    parser.add_argument("--json", dest="as_json", action="store_true")
    parser.add_argument("--profile-dir", dest="profile_dir", default=None)
    parser.add_argument("--url", dest="url", default=LOGIN_URL)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    config = env.get_config()  # default policy: no cookie reads, no discovery
    recipe = build_recipe(config, profile_dir=args.profile_dir, url=args.url)

    if args.as_json:
        printable = {k: v for k, v in recipe.items() if k != "box_chrome"}
        printable["box_chrome_present"] = recipe["box_chrome"] is not None
        print(json.dumps(printable))
    else:
        print(render_recipe(recipe))

    if args.do_exec:
        # Refuse to spawn on a non-extras host, or when box-chrome is missing.
        if not recipe["applies"] or not recipe["command"]:
            return 0
        try:
            verified_profile_dir = _ensure_private_profile_dir(recipe["profile_dir"])
        except OSError as exc:
            print(
                f"[box-chrome login] unsafe or unusable profile directory: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 1
        launch_env = os.environ.copy()
        launch_env.update(recipe["env"])
        launch_env["CHROME_USER_DATA_DIR"] = verified_profile_dir
        try:
            subprocess.Popen(recipe["command"], env=launch_env)
        except OSError as exc:
            print(f"[box-chrome login] failed to launch: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

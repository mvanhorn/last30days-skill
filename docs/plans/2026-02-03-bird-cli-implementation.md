# Bird CLI Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Bird CLI as a free, zero-config alternative to xAI for X/Twitter searches with interactive installation.

**Architecture:** New `bird_x.py` module handles Bird detection, installation prompts, and search. Modified `env.py` determines X source priority (Bird → xAI → WebSearch). Main script prompts for Bird install if not found.

**Tech Stack:** Python 3, subprocess for Bird CLI calls, existing lib modules for normalization/scoring.

---

### Task 1: Create bird_x.py - Detection Functions

**Files:**
- Create: `scripts/lib/bird_x.py`

**Step 1: Create the module with detection functions**

```python
"""Bird CLI client for X (Twitter) search."""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def _log(msg: str):
    """Log to stderr."""
    sys.stderr.write(f"[Bird] {msg}\n")
    sys.stderr.flush()


def is_bird_installed() -> bool:
    """Check if Bird CLI is installed."""
    return shutil.which("bird") is not None


def is_bird_authenticated() -> Optional[str]:
    """Check if Bird is authenticated by running 'bird whoami'.

    Returns:
        Username if authenticated, None otherwise.
    """
    if not is_bird_installed():
        return None

    try:
        result = subprocess.run(
            ["bird", "whoami"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Output is typically the username
            return result.stdout.strip().split('\n')[0]
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None


def check_npm_available() -> bool:
    """Check if npm is available for installation."""
    return shutil.which("npm") is not None
```

**Step 2: Verify the module loads**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import bird_x; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add scripts/lib/bird_x.py
git commit -m "feat(bird): add detection functions for Bird CLI"
```

---

### Task 2: Add Bird Installation Functions

**Files:**
- Modify: `scripts/lib/bird_x.py`

**Step 1: Add installation function**

Add after `check_npm_available()`:

```python
def install_bird() -> Tuple[bool, str]:
    """Install Bird CLI via npm.

    Returns:
        Tuple of (success, message).
    """
    if not check_npm_available():
        return False, "npm not found. Install Node.js first, or install Bird manually: https://github.com/steipete/bird"

    try:
        _log("Installing Bird CLI...")
        result = subprocess.run(
            ["npm", "install", "-g", "@steipete/bird"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, "Bird CLI installed successfully!"
        else:
            error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            return False, f"Installation failed: {error}"
    except subprocess.TimeoutExpired:
        return False, "Installation timed out"
    except Exception as e:
        return False, f"Installation error: {e}"


def get_bird_status() -> Dict[str, Any]:
    """Get comprehensive Bird status.

    Returns:
        Dict with keys: installed, authenticated, username, can_install
    """
    installed = is_bird_installed()
    username = is_bird_authenticated() if installed else None

    return {
        "installed": installed,
        "authenticated": username is not None,
        "username": username,
        "can_install": check_npm_available(),
    }
```

**Step 2: Verify functions work**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import bird_x; print(bird_x.get_bird_status())"`
Expected: Dict with installed/authenticated status

**Step 3: Commit**

```bash
git add scripts/lib/bird_x.py
git commit -m "feat(bird): add installation and status functions"
```

---

### Task 3: Add Bird Search Function

**Files:**
- Modify: `scripts/lib/bird_x.py`

**Step 1: Add depth config and search function**

Add after imports at top:

```python
# Depth configurations: number of results to request
DEPTH_CONFIG = {
    "quick": 12,
    "default": 30,
    "deep": 60,
}
```

Add after `get_bird_status()`:

```python
def search_x(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search X using Bird CLI.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"

    Returns:
        Raw Bird JSON response or error dict.
    """
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    # Build command
    cmd = [
        "bird", "search",
        topic,
        "--since", from_date,
        "-n", str(count),
        "--json",
    ]

    # Adjust timeout based on depth
    timeout = 30 if depth == "quick" else 45 if depth == "default" else 60

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            error = result.stderr.strip() or "Bird search failed"
            return {"error": error, "items": []}

        # Parse JSON output
        output = result.stdout.strip()
        if not output:
            return {"items": []}

        return json.loads(output)

    except subprocess.TimeoutExpired:
        return {"error": "Search timed out", "items": []}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON response: {e}", "items": []}
    except Exception as e:
        return {"error": str(e), "items": []}
```

**Step 2: Verify search function signature**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import bird_x; import inspect; print(inspect.signature(bird_x.search_x))"`
Expected: `(topic: str, from_date: str, to_date: str, depth: str = 'default') -> Dict[str, Any]`

**Step 3: Commit**

```bash
git add scripts/lib/bird_x.py
git commit -m "feat(bird): add search_x function"
```

---

### Task 4: Add Bird Response Parser

**Files:**
- Modify: `scripts/lib/bird_x.py`

**Step 1: Add parse function**

Add at end of file:

```python
def parse_bird_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Bird response to match xai_x output format.

    Args:
        response: Raw Bird JSON response

    Returns:
        List of normalized item dicts matching xai_x.parse_x_response() format.
    """
    items = []

    # Check for errors
    if "error" in response and response["error"]:
        _log(f"Bird error: {response['error']}")
        return items

    # Bird returns a list of tweets directly or under a key
    raw_items = response if isinstance(response, list) else response.get("items", response.get("tweets", []))

    if not isinstance(raw_items, list):
        return items

    for i, tweet in enumerate(raw_items):
        if not isinstance(tweet, dict):
            continue

        # Extract URL - Bird uses permanent_url or we construct from id
        url = tweet.get("permanent_url") or tweet.get("url", "")
        if not url and tweet.get("id"):
            screen_name = tweet.get("user", {}).get("screen_name", "")
            if screen_name:
                url = f"https://x.com/{screen_name}/status/{tweet['id']}"

        if not url:
            continue

        # Parse date from created_at (e.g., "Wed Jan 15 14:30:00 +0000 2026")
        date = None
        created_at = tweet.get("created_at", "")
        if created_at:
            try:
                # Try ISO format first
                if "T" in created_at:
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                else:
                    # Twitter format: "Wed Jan 15 14:30:00 +0000 2026"
                    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                date = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        # Extract user info
        user = tweet.get("user", {})
        author_handle = user.get("screen_name", "") or tweet.get("author_handle", "")

        # Build engagement dict
        engagement = {
            "likes": tweet.get("like_count") or tweet.get("favorite_count"),
            "reposts": tweet.get("retweet_count"),
            "replies": tweet.get("reply_count"),
            "quotes": tweet.get("quote_count"),
        }
        # Convert to int where possible
        for key in engagement:
            if engagement[key] is not None:
                try:
                    engagement[key] = int(engagement[key])
                except (ValueError, TypeError):
                    engagement[key] = None

        # Build normalized item
        item = {
            "id": f"X{i+1}",
            "text": str(tweet.get("text", tweet.get("full_text", ""))).strip()[:500],
            "url": url,
            "author_handle": author_handle.lstrip("@"),
            "date": date,
            "engagement": engagement if any(v is not None for v in engagement.values()) else None,
            "why_relevant": "",  # Bird doesn't provide relevance explanations
            "relevance": 0.7,  # Default relevance, let score.py re-rank
        }

        items.append(item)

    return items
```

**Step 2: Verify parser handles empty input**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import bird_x; print(bird_x.parse_bird_response({}))"`
Expected: `[]`

**Step 3: Commit**

```bash
git add scripts/lib/bird_x.py
git commit -m "feat(bird): add response parser matching xai_x format"
```

---

### Task 5: Add UI Functions for Bird Prompts

**Files:**
- Modify: `scripts/lib/ui.py`

**Step 1: Add Bird-related messages and prompts**

Add after `PROMO_SINGLE_KEY_PLAIN` dict (around line 128):

```python
# Bird CLI prompts
BIRD_INSTALL_PROMPT = f"""
{Colors.CYAN}{Colors.BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{Colors.RESET}
{Colors.CYAN}🐦 FREE X/TWITTER SEARCH AVAILABLE{Colors.RESET}

Bird CLI provides free X search using your browser session (no API key needed).

"""

BIRD_INSTALL_PROMPT_PLAIN = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🐦 FREE X/TWITTER SEARCH AVAILABLE

Bird CLI provides free X search using your browser session (no API key needed).

"""

BIRD_AUTH_HELP = f"""
{Colors.YELLOW}Bird authentication failed.{Colors.RESET}

To fix this:
1. Log into X (twitter.com) in Safari, Chrome, or Firefox
2. Run: {Colors.BOLD}bird check{Colors.RESET} to verify credentials
3. Try again

For manual setup, see: https://github.com/steipete/bird#authentication
"""

BIRD_AUTH_HELP_PLAIN = """
Bird authentication failed.

To fix this:
1. Log into X (twitter.com) in Safari, Chrome, or Firefox
2. Run: bird check to verify credentials
3. Try again

For manual setup, see: https://github.com/steipete/bird#authentication
"""
```

**Step 2: Add prompt functions to ProgressDisplay class**

Add these methods to the `ProgressDisplay` class (after `show_promo` method, around line 310):

```python
    def prompt_bird_install(self) -> bool:
        """Prompt user to install Bird CLI.

        Returns:
            True if user wants to install, False otherwise.
        """
        if IS_TTY:
            sys.stderr.write(BIRD_INSTALL_PROMPT)
        else:
            sys.stderr.write(BIRD_INSTALL_PROMPT_PLAIN)
        sys.stderr.flush()

        try:
            response = input("Install Bird CLI now? (y/n): ").strip().lower()
            return response in ('y', 'yes')
        except (EOFError, KeyboardInterrupt):
            return False

    def show_bird_install_success(self, username: str):
        """Show Bird installation success message."""
        msg = f"{Colors.GREEN}✓ Bird installed and authenticated as @{username}{Colors.RESET}\n" if IS_TTY else f"✓ Bird installed and authenticated as @{username}\n"
        sys.stderr.write(msg)
        sys.stderr.flush()

    def show_bird_install_failed(self, error: str):
        """Show Bird installation failure message."""
        msg = f"{Colors.RED}✗ Bird installation failed: {error}{Colors.RESET}\n" if IS_TTY else f"✗ Bird installation failed: {error}\n"
        sys.stderr.write(msg)
        sys.stderr.flush()

    def show_bird_auth_help(self):
        """Show Bird authentication help."""
        if IS_TTY:
            sys.stderr.write(BIRD_AUTH_HELP)
        else:
            sys.stderr.write(BIRD_AUTH_HELP_PLAIN)
        sys.stderr.flush()
```

**Step 3: Verify new methods exist**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib.ui import ProgressDisplay; p = ProgressDisplay('test', show_banner=False); print(hasattr(p, 'prompt_bird_install'))"`
Expected: `True`

**Step 4: Commit**

```bash
git add scripts/lib/ui.py
git commit -m "feat(ui): add Bird CLI install prompts and auth help"
```

---

### Task 6: Update env.py with X Source Detection

**Files:**
- Modify: `scripts/lib/env.py`

**Step 1: Add get_x_source function**

Add at end of file:

```python
def get_x_source(config: Dict[str, Any]) -> Optional[str]:
    """Determine the best available X/Twitter source.

    Priority: Bird (free) → xAI (paid API)

    Args:
        config: Configuration dict from get_config()

    Returns:
        'bird' if Bird is installed and authenticated,
        'xai' if XAI_API_KEY is configured,
        None if no X source available.
    """
    # Import here to avoid circular dependency
    from . import bird_x

    # Check Bird first (free option)
    if bird_x.is_bird_installed():
        username = bird_x.is_bird_authenticated()
        if username:
            return 'bird'

    # Fall back to xAI if key exists
    if config.get('XAI_API_KEY'):
        return 'xai'

    return None


def get_x_source_status(config: Dict[str, Any]) -> Dict[str, Any]:
    """Get detailed X source status for UI decisions.

    Returns:
        Dict with keys: source, bird_installed, bird_authenticated,
        bird_username, xai_available, can_install_bird
    """
    from . import bird_x

    bird_status = bird_x.get_bird_status()
    xai_available = bool(config.get('XAI_API_KEY'))

    # Determine active source
    if bird_status["authenticated"]:
        source = 'bird'
    elif xai_available:
        source = 'xai'
    else:
        source = None

    return {
        "source": source,
        "bird_installed": bird_status["installed"],
        "bird_authenticated": bird_status["authenticated"],
        "bird_username": bird_status["username"],
        "xai_available": xai_available,
        "can_install_bird": bird_status["can_install"],
    }
```

**Step 2: Verify function works**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import env; print(env.get_x_source_status(env.get_config()))"`
Expected: Dict with source status

**Step 3: Commit**

```bash
git add scripts/lib/env.py
git commit -m "feat(env): add X source detection with Bird priority"
```

---

### Task 7: Update __init__.py to Export bird_x

**Files:**
- Modify: `scripts/lib/__init__.py`

**Step 1: Add bird_x to imports**

Replace file contents with:

```python
# last30days library modules
from . import bird_x
```

**Step 2: Verify import works**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import bird_x; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add scripts/lib/__init__.py
git commit -m "feat(lib): export bird_x module"
```

---

### Task 8: Integrate Bird into Main Script - Part 1 (Setup Phase)

**Files:**
- Modify: `scripts/last30days.py`

**Step 1: Add bird_x import**

Add `bird_x` to the imports from lib (around line 36):

```python
from lib import (
    bird_x,
    dates,
    dedupe,
    env,
    http,
    models,
    normalize,
    openai_reddit,
    reddit_enrich,
    render,
    schema,
    score,
    ui,
    websearch,
    xai_x,
)
```

**Step 2: Add Bird setup function**

Add after the imports, before `load_fixture`:

```python
def setup_bird_if_needed(progress: ui.ProgressDisplay) -> Optional[str]:
    """Check Bird status and offer installation if needed.

    Returns:
        'bird' if Bird is ready to use,
        'declined' if user declined install,
        None if Bird not available and couldn't be installed.
    """
    status = bird_x.get_bird_status()

    # Already working
    if status["authenticated"]:
        return 'bird'

    # Installed but not authenticated
    if status["installed"]:
        progress.show_bird_auth_help()
        return None

    # Not installed - offer to install if npm available
    if status["can_install"]:
        if progress.prompt_bird_install():
            success, message = bird_x.install_bird()
            if success:
                # Check if auth works now
                username = bird_x.is_bird_authenticated()
                if username:
                    progress.show_bird_install_success(username)
                    return 'bird'
                else:
                    progress.show_bird_auth_help()
                    return None
            else:
                progress.show_bird_install_failed(message)
                return None
        else:
            return 'declined'

    return None
```

**Step 3: Verify script still loads**

Run: `cd $HOME/last30days-skill-private && python3 -c "import scripts.last30days; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add scripts/last30days.py
git commit -m "feat(main): add Bird setup function"
```

---

### Task 9: Integrate Bird into Main Script - Part 2 (Search Dispatch)

**Files:**
- Modify: `scripts/last30days.py`

**Step 1: Modify _search_x function to support Bird**

Replace the `_search_x` function (around line 119-159) with:

```python
def _search_x(
    topic: str,
    config: dict,
    selected_models: dict,
    from_date: str,
    to_date: str,
    depth: str,
    mock: bool,
    x_source: str = "xai",
) -> tuple:
    """Search X via Bird CLI or xAI (runs in thread).

    Args:
        x_source: 'bird' or 'xai' - which backend to use

    Returns:
        Tuple of (x_items, raw_response, error)
    """
    raw_response = None
    x_error = None

    if mock:
        raw_response = load_fixture("xai_sample.json")
        x_items = xai_x.parse_x_response(raw_response or {})
        return x_items, raw_response, x_error

    # Use Bird if specified
    if x_source == "bird":
        try:
            raw_response = bird_x.search_x(
                topic,
                from_date,
                to_date,
                depth=depth,
            )
        except Exception as e:
            raw_response = {"error": str(e)}
            x_error = f"{type(e).__name__}: {e}"

        x_items = bird_x.parse_bird_response(raw_response or {})

        # Check for error in response
        if raw_response and raw_response.get("error") and not x_error:
            x_error = raw_response["error"]

        return x_items, raw_response, x_error

    # Use xAI (original behavior)
    try:
        raw_response = xai_x.search_x(
            config["XAI_API_KEY"],
            selected_models["xai"],
            topic,
            from_date,
            to_date,
            depth=depth,
        )
    except http.HTTPError as e:
        raw_response = {"error": str(e)}
        x_error = f"API error: {e}"
    except Exception as e:
        raw_response = {"error": str(e)}
        x_error = f"{type(e).__name__}: {e}"

    x_items = xai_x.parse_x_response(raw_response or {})

    return x_items, raw_response, x_error
```

**Step 2: Update run_research to accept x_source parameter**

Find the `run_research` function signature (around line 161) and add `x_source` parameter:

```python
def run_research(
    topic: str,
    sources: str,
    config: dict,
    selected_models: dict,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock: bool = False,
    progress: ui.ProgressDisplay = None,
    x_source: str = "xai",
) -> tuple:
```

Then update the `_search_x` call inside (around line 218-222) to pass `x_source`:

```python
            x_future = executor.submit(
                _search_x, topic, config, selected_models,
                from_date, to_date, depth, mock, x_source
            )
```

**Step 3: Verify script syntax is valid**

Run: `cd $HOME/last30days-skill-private && python3 -m py_compile scripts/last30days.py && echo "OK"`
Expected: `OK`

**Step 4: Commit**

```bash
git add scripts/last30days.py
git commit -m "feat(main): dispatch X search to Bird or xAI"
```

---

### Task 10: Integrate Bird into Main Script - Part 3 (Main Function)

**Files:**
- Modify: `scripts/last30days.py`

**Step 1: Update main() to check Bird before research**

In the `main()` function, after loading config and before checking available sources (around line 345-355), add Bird setup:

Find this section:
```python
    # Load config
    config = env.get_config()

    # Check available sources
    available = env.get_available_sources(config)
```

Replace with:
```python
    # Load config
    config = env.get_config()

    # Initialize progress display early for Bird prompts
    progress = ui.ProgressDisplay(args.topic, show_banner=True)

    # Check Bird availability and offer install if needed
    x_source_status = env.get_x_source_status(config)
    x_source = x_source_status["source"]

    # If no X source and Bird can be installed, offer it
    if x_source is None and x_source_status["can_install_bird"]:
        bird_result = setup_bird_if_needed(progress)
        if bird_result == 'bird':
            x_source = 'bird'
            # Refresh status
            x_source_status = env.get_x_source_status(config)

    # Check available sources (now accounting for Bird)
    available = env.get_available_sources(config)

    # Override available if Bird is ready
    if x_source == 'bird':
        if available == 'reddit':
            available = 'both'  # Now have both Reddit + X (via Bird)
        elif available == 'web':
            available = 'x'  # Now have X via Bird
```

**Step 2: Remove duplicate progress initialization**

Find and remove the later `progress = ui.ProgressDisplay(...)` line (around line 371) since we now create it earlier.

**Step 3: Pass x_source to run_research**

Find the `run_research` call (around line 413) and add `x_source` parameter:

```python
    reddit_items, x_items, web_needed, raw_openai, raw_xai, raw_reddit_enriched, reddit_error, x_error = run_research(
        args.topic,
        sources,
        config,
        selected_models,
        from_date,
        to_date,
        depth,
        args.mock,
        progress,
        x_source=x_source or "xai",
    )
```

**Step 4: Verify script runs with --help**

Run: `cd $HOME/last30days-skill-private && python3 scripts/last30days.py --help`
Expected: Help text displays without errors

**Step 5: Commit**

```bash
git add scripts/last30days.py
git commit -m "feat(main): integrate Bird setup into main flow"
```

---

### Task 11: Test End-to-End with Mock Mode

**Files:**
- None (testing only)

**Step 1: Test mock mode still works**

Run: `cd $HOME/last30days-skill-private && python3 scripts/last30days.py "Claude Code" --mock --emit=compact 2>&1 | head -20`
Expected: Output showing research results without errors

**Step 2: Test Bird detection (informational)**

Run: `cd $HOME/last30days-skill-private && python3 -c "from scripts.lib import env; import json; print(json.dumps(env.get_x_source_status(env.get_config()), indent=2))"`
Expected: JSON showing current Bird/xAI status

**Step 3: Commit any fixes if needed, then final commit**

```bash
git add -A
git commit -m "feat(bird): complete Bird CLI integration

- Add bird_x.py module for Bird CLI detection, install, and search
- Add UI prompts for interactive Bird installation
- Update env.py with X source priority (Bird > xAI)
- Integrate Bird into main research flow
- Bird uses browser cookies (free, no API key needed)"
```

---

### Task 12: Push to Private Repo

**Files:**
- None (git only)

**Step 1: Push all changes**

Run: `cd $HOME/last30days-skill-private && git push origin main`
Expected: Changes pushed to private repo

**Step 2: Verify commit history**

Run: `cd $HOME/last30days-skill-private && git log --oneline -10`
Expected: Shows Bird integration commits

---

## Summary

After completing all tasks, the skill will:

1. Check if Bird CLI is installed on startup
2. If not installed but npm available, prompt user to install
3. If installed, verify authentication via `bird whoami`
4. If authenticated, use Bird for all X searches (free)
5. If not, fall back to xAI (if key exists) or WebSearch
6. Output format identical regardless of backend used

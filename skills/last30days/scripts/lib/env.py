"""Environment and API key management for last30days skill."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Allow override via environment variable for testing
# Set LAST30DAYS_CONFIG_DIR="" for clean/no-config mode
# Set LAST30DAYS_CONFIG_DIR="/path/to/dir" for custom config location
_config_override = os.environ.get('LAST30DAYS_CONFIG_DIR')
if _config_override == "":
    # Empty string = no config file (clean mode)
    CONFIG_DIR = None
    CONFIG_FILE = None
elif _config_override:
    CONFIG_DIR = Path(_config_override)
    CONFIG_FILE = CONFIG_DIR / ".env"
else:
    CONFIG_DIR = Path.home() / ".config" / "last30days"
    CONFIG_FILE = CONFIG_DIR / ".env"

# macOS Keychain integration: items stored with this service prefix are picked
# up automatically on Darwin as the lowest-priority credential source.
# Example: `security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."`.
KEYCHAIN_SERVICE_PREFIX = "last30days-"

# Optional non-secret aliases for users who already store API keys under a
# different Keychain naming convention. Configure as JSON in
# LAST30DAYS_KEYCHAIN_ALIASES, for example:
# {"XAI_API_KEY":{"account":"keychain-user","service":"existing-xai-api-key"}}
# A string value is shorthand for {"service": "..."} with the current user.
KEYCHAIN_ALIASES_ENV = "LAST30DAYS_KEYCHAIN_ALIASES"

# Single source of truth for which credentials the Keychain loader looks up.
# The setup-keychain.sh helper mirrors this list and is held in sync via
# tests/test_env_keychain.py::test_keychain_keys_match_setup_script.
KEYCHAIN_KEYS = (
    "OPENAI_API_KEY", "XAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_GENAI_API_KEY", "SCRAPECREATORS_API_KEY", "APIFY_API_TOKEN",
    "AUTH_TOKEN", "CT0", "BSKY_HANDLE", "BSKY_APP_PASSWORD",
    "TRUTHSOCIAL_TOKEN", "BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY",
    "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY", "PARALLEL_API_KEY", "XQUIK_API_KEY",
    "XIAOHONGSHU_API_BASE",
)

# pass(1) integration: Linux/Unix analog of the Keychain source. Each key in
# KEYCHAIN_KEYS is looked up at pass path f"{prefix}{KEY}", the direct analog of
# Keychain's "last30days-<KEY>" service-name convention, so any user stores keys
# under one namespace without editing code. The prefix is resolved at call time
# (in get_config) from LAST30DAYS_PASS_PREFIX in the process env or a config
# file, falling back to this default; included verbatim, so keep the trailing
# separator. Honors PASSWORD_STORE_DIR.
DEFAULT_PASS_PATH_PREFIX = "last30days/"

AuthSource = Literal["api_key", "none"]
AuthStatus = Literal["ok", "missing"]

AUTH_SOURCE_API_KEY: AuthSource = "api_key"
AUTH_SOURCE_NONE: AuthSource = "none"

AUTH_STATUS_OK: AuthStatus = "ok"
AUTH_STATUS_MISSING: AuthStatus = "missing"


@dataclass(frozen=True)
class OpenAIAuth:
    token: str | None
    source: AuthSource
    status: AuthStatus


BrowserCookieMode = Literal["off", "read", "plan_only"]


@dataclass(frozen=True)
class ConfigLoadPolicy:
    """Local-read gates for configuration loading.

    Bare library calls use the safe default: no browser-cookie extraction and no
    project-scoped config. CLI entry points can opt into narrower behavior after
    parsing command intent.
    """

    browser_cookies: BrowserCookieMode = "off"
    allow_project_config: bool = False
    inspect_ignored_project_config: bool = False


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _project_config_trusted(policy: ConfigLoadPolicy, file_env: dict[str, Any]) -> bool:
    if policy.allow_project_config:
        return True
    process_value = os.environ.get("LAST30DAYS_TRUST_PROJECT_CONFIG")
    if process_value is not None:
        return _truthy(process_value)
    return _truthy(file_env.get("LAST30DAYS_TRUST_PROJECT_CONFIG"))


def _check_file_permissions(path: Path) -> None:
    """Warn to stderr if a secrets file has overly permissive permissions."""
    if os.name == "nt":
        # Windows reports synthesized POSIX mode bits that do not reflect NTFS ACLs.
        return

    try:
        mode = path.stat().st_mode
        # Check if group or other can read (bits 0o044)
        if mode & 0o044:
            sys.stderr.write(
                f"[last30days] WARNING: {path} is readable by other users. "
                f"Run: chmod 600 {path}\n"
            )
            sys.stderr.flush()
    except OSError as exc:
        sys.stderr.write(f"[last30days] WARNING: could not stat {path}: {exc}\n")
        sys.stderr.flush()


def load_env_file(path: Path) -> dict[str, str]:
    """Load environment variables from a file."""
    env = {}
    if not path or not path.exists():
        return env
    _check_file_permissions(path)

    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if value and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                if key and value:
                    env[key] = value
    return env


def _parse_keychain_aliases(raw: str | None) -> dict[str, list[dict[str, str]]]:
    """Parse non-secret Keychain alias metadata from JSON.

    Supported forms:
      {"XAI_API_KEY": "existing-xai-api-key"}
      {"XAI_API_KEY": {"service": "existing-xai-api-key", "account": "keychain-user"}}
      {"XAI_API_KEY": [{"service": "primary"}, {"service": "fallback"}]}

    Invalid entries are ignored so a typo never blocks canonical
    `last30days-<KEY>` lookups; malformed JSON emits a warning.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(
            f"[last30days] WARNING: {KEYCHAIN_ALIASES_ENV} is not valid JSON; "
            f"ignoring Keychain aliases while keeping canonical lookups enabled: {exc}\n"
        )
        sys.stderr.flush()
        return {}
    if not isinstance(parsed, dict):
        return {}

    allowed = set(KEYCHAIN_KEYS)
    aliases: dict[str, list[dict[str, str]]] = {}
    for key, spec in parsed.items():
        if key not in allowed:
            continue
        specs = spec if isinstance(spec, list) else [spec]
        clean_specs: list[dict[str, str]] = []
        for item in specs:
            if isinstance(item, str):
                service = item.strip()
                account = ""
            elif isinstance(item, dict):
                service = str(item.get("service", "")).strip()
                account = str(item.get("account", "")).strip()
            else:
                continue
            if service:
                clean_specs.append({"service": service, "account": account})
        if clean_specs:
            aliases[key] = clean_specs
    return aliases


def _load_keychain(keys: list[str], aliases: dict[str, list[dict[str, str]]] | None = None) -> dict[str, str]:
    """Load credentials from macOS Keychain (no-op on other platforms).

    Each key is looked up as a generic password with service name
    ``f"{KEYCHAIN_SERVICE_PREFIX}{key}"`` for the current user. Missing items
    then fall back to optional alias metadata from
    ``LAST30DAYS_KEYCHAIN_ALIASES``. Lookup failures are silent — Keychain is
    the lowest-priority source and is meant to be additive over `.env` files
    and process environment.
    """
    import platform
    if platform.system() != "Darwin":
        return {}

    import shutil
    security = shutil.which("security")
    if not security:
        return {}

    import subprocess
    # USER can be unset under sudo, in Docker without --env USER, or in some CI
    # runners; fall back to the OS user record so lookups still match items
    # stored by setup-keychain.sh (which uses $USER).
    user = os.environ.get("USER")
    if not user:
        try:
            import pwd
        except ImportError:
            pwd = None

        if pwd is not None:
            try:
                user = pwd.getpwuid(os.getuid()).pw_name
            except AttributeError:
                user = "unknown"
        else:
            user = "unknown"
    env: dict[str, str] = {}

    def lookup(account: str, service: str) -> str:
        try:
            result = subprocess.run(
                [security, "find-generic-password",
                 "-a", account,
                 "-s", service,
                 "-w"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            return ""
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return ""

    for key in keys:
        value = lookup(user, f"{KEYCHAIN_SERVICE_PREFIX}{key}")
        if not value and aliases:
            for alias in aliases.get(key, []):
                alias_account = alias.get("account") or user
                value = lookup(alias_account, alias["service"])
                if value:
                    break
        if value:
            env[key] = value
    return env


def _load_pass(keys: list[str], prefix: str) -> dict[str, str]:
    """Load credentials from a pass(1) store (no-op if `pass` is absent).

    The Linux/Unix analog of the macOS Keychain source. Each env-var name is
    looked up at pass path ``f"{prefix}{key}"`` — mirroring Keychain's
    ``last30days-<key>`` service-name convention — so any user stores keys under
    that namespace without editing code (prefix overridable via
    ``LAST30DAYS_PASS_PREFIX``). The secret is decrypted in a subprocess and
    read from stdout's first line (pass keeps the secret there; any metadata
    follows) — never written to disk, never logged. Honors ``PASSWORD_STORE_DIR``.
    Missing entries and failures are silent: pass is a lowest-priority, additive
    source like Keychain, so an explicit .env or process-env value still wins.
    """
    import shutil
    pass_bin = shutil.which("pass")
    if not pass_bin:
        return {}

    import subprocess
    env: dict[str, str] = {}
    for key in keys:
        try:
            result = subprocess.run(
                [pass_bin, "show", f"{prefix}{key}"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError):
            # A timeout (GPG/pinentry hanging) or exec failure isn't a per-key
            # condition — it means the store is unusable right now. Stop instead
            # of paying the timeout once per key; otherwise a locked store would
            # stall every config load by 5s x len(keys). A genuinely missing key
            # returns fast with a non-zero exit and is handled below.
            break
        if result.returncode == 0 and result.stdout.strip():
            env[key] = result.stdout.strip().splitlines()[0]
    return env


def get_openai_auth(file_env: dict[str, str]) -> OpenAIAuth:
    """Resolve OpenAI API auth from explicit user-provided API keys."""
    api_key = os.environ.get('OPENAI_API_KEY') or file_env.get('OPENAI_API_KEY')
    if api_key:
        return OpenAIAuth(
            token=api_key,
            source=AUTH_SOURCE_API_KEY,
            status=AUTH_STATUS_OK,
        )

    return OpenAIAuth(
        token=None,
        source=AUTH_SOURCE_NONE,
        status=AUTH_STATUS_MISSING,
    )


def _find_project_env() -> Path | None:
    """Find per-project .env by walking up from cwd.

    Searches for .claude/last30days.env in each parent directory,
    stopping at the git root, user's home directory, or filesystem root.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / '.claude' / 'last30days.env'
        if candidate.exists():
            return candidate
        if (parent / ".git").exists():
            break
        # Stop at filesystem root or home
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def get_config(policy: ConfigLoadPolicy | None = None) -> dict[str, Any]:
    """Load configuration from multiple sources.

    Priority (highest wins):
      1. Environment variables (os.environ)
      2. Trusted .claude/last30days.env (per-project config)
      3. ~/.config/last30days/.env (global config)
      4. macOS Keychain items prefixed ``last30days-`` (Darwin only)
    """
    policy = policy or ConfigLoadPolicy()
    # Load from global config file
    file_env = load_env_file(CONFIG_FILE) if CONFIG_FILE else {}

    # Load per-project config only when trust comes from process env, global
    # user config, or an explicit policy. A project file cannot grant trust to
    # itself because it is not parsed until after this decision.
    project_config_trusted = _project_config_trusted(policy, file_env)
    project_env_path = _find_project_env() if project_config_trusted else None
    project_env = load_env_file(project_env_path) if project_env_path else {}
    ignored_project_env_path = None
    ignored_project_keys: list[str] = []
    if not project_config_trusted and policy.inspect_ignored_project_config:
        ignored_project_env_path = _find_project_env()
        if ignored_project_env_path:
            ignored_project_keys = sorted(load_env_file(ignored_project_env_path).keys())

    # Merge file sources: project > global
    merged_env = {**file_env, **project_env}

    # Keychain is the lowest-priority source (Darwin only; no-op elsewhere).
    # Loaded before openai_auth so OPENAI_API_KEY can come from Keychain too.
    keychain_aliases_raw = os.environ.get(KEYCHAIN_ALIASES_ENV) or merged_env.get(KEYCHAIN_ALIASES_ENV)
    keychain_aliases = _parse_keychain_aliases(keychain_aliases_raw)
    keychain_env = _load_keychain(list(KEYCHAIN_KEYS), keychain_aliases)
    merged_env = {**keychain_env, **merged_env}
    # pass(1) store: Linux/Unix analog of Keychain at convention path
    # {prefix}<KEY>. Decrypts transiently so secrets stay encrypted at rest (no
    # plaintext .env). Lowest priority: Keychain, the config files, and process
    # env all win over it. Two efficiency guards so a user who merely has `pass`
    # on PATH doesn't pay for it: resolve the prefix from the loaded config/env
    # (not import time, so a .env-set LAST30DAYS_PASS_PREFIX is honored), and
    # probe ONLY keys still unset after the higher-priority sources — an empty
    # list short-circuits with no gpg/pinentry calls at all.
    pass_prefix = (
        os.environ.get("LAST30DAYS_PASS_PREFIX")
        or merged_env.get("LAST30DAYS_PASS_PREFIX")
        or DEFAULT_PASS_PATH_PREFIX
    )
    pass_missing = [k for k in KEYCHAIN_KEYS if k not in os.environ and not merged_env.get(k)]
    pass_env = _load_pass(pass_missing, pass_prefix)
    merged_env = {**pass_env, **merged_env}

    openai_auth = get_openai_auth(merged_env)

    # Build config: Codex/OpenAI auth + process.env > project .env > global .env
    config = {
        'OPENAI_API_KEY': openai_auth.token,
        'OPENAI_AUTH_SOURCE': openai_auth.source,
        'OPENAI_AUTH_STATUS': openai_auth.status,
    }

    keys = [
        ('XAI_API_KEY', None),
        ('GOOGLE_API_KEY', None),
        ('GEMINI_API_KEY', None),
        ('GOOGLE_GENAI_API_KEY', None),
        ('XIAOHONGSHU_API_BASE', None),
        ('LAST30DAYS_REASONING_PROVIDER', 'auto'),
        ('LAST30DAYS_PLANNER_MODEL', None),
        ('LAST30DAYS_RERANK_MODEL', None),
        ('LAST30DAYS_X_MODEL', None),
        ('LAST30DAYS_X_BACKEND', None),
        ('LAST30DAYS_REDDIT_BACKEND', None),
        ('LAST30DAYS_REDDIT_SC_MIN_ITEMS', None),
        ('LAST30DAYS_STORE', None),
        ('LAST30DAYS_MEMORY_DIR', None),
        ('OPENAI_MODEL_PIN', None),
        ('XAI_MODEL_PIN', None),
        ('OPENAI_BASE_URL', None),
        ('XAI_BASE_URL', None),
        ('SCRAPECREATORS_API_KEY', None),
        ('APIFY_API_TOKEN', None),
        ('AUTH_TOKEN', None),
        ('CT0', None),
        ('BSKY_HANDLE', None),
        ('BSKY_APP_PASSWORD', None),
        ('BSKY_SEARCH_HOST', None),
        ('TRUTHSOCIAL_TOKEN', None),
        ('BRAVE_API_KEY', None),
        ('EXA_API_KEY', None),
        ('SERPER_API_KEY', None),
        ('OPENROUTER_API_KEY', None),
        ('PERPLEXITY_API_KEY', None),
        ('LAST30DAYS_PERPLEXITY_MODE', 'sonar'),
        ('LAST30DAYS_PERPLEXITY_MODEL', None),
        ('LAST30DAYS_PERPLEXITY_MAX_RESULTS', None),
        ('LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE', None),
        ('LAST30DAYS_PERPLEXITY_SEARCH_MODE', None),
        ('LAST30DAYS_PERPLEXITY_DOMAIN_FILTER', None),
        ('LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER', None),
        ('LAST30DAYS_PERPLEXITY_COUNTRY', None),
        ('LAST30DAYS_PERPLEXITY_RECENCY_FILTER', None),
        ('LAST30DAYS_PERPLEXITY_REASONING_EFFORT', None),
        ('LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS', '600'),
        ('PARALLEL_API_KEY', None),
        ('XQUIK_API_KEY', None),
        # Host-native search signal: set by the SKILL.md agent-host path when the
        # invoking runtime has its own (better) web-search tool, so the engine's
        # keyless search floor stays off there. Defaults unset -> floor allowed.
        ('LAST30DAYS_NATIVE_SEARCH', None),
        # Optional SearXNG instance for the keyless-search fallback rung.
        ('LAST30DAYS_SEARXNG_URL', None),
        # Truthy -> disable Trustpilot's headless-Chrome WAF-cookie harvest in
        # automated contexts (cron/CI/eval). Read by trustpilot._harvest_allowed.
        ('LAST30DAYS_TRUSTPILOT_NO_BROWSER', None),
        ('FROM_BROWSER', None),
        ('LAST30DAYS_TRUST_PROJECT_CONFIG', None),
        ('SETUP_COMPLETE', None),
        ('INCLUDE_SOURCES', ''),
        ('EXCLUDE_SOURCES', ''),
        ('LAST30DAYS_DEFAULT_SEARCH', ''),
        ('LAST30DAYS_YOUTUBE_SSH_HOST', None),
        ('LAST30DAYS_TRANSCRIPT_TIMEOUT', None),
        (KEYCHAIN_ALIASES_ENV, None),
        # Whisper transcription provider for caption-free audio/video. Groq's
        # free tier is preferred; OPENAI_API_KEY is the paid backstop (already
        # resolved above via openai_auth).
        ('GROQ_API_KEY', None),
        ('LAST30DAYS_YT_SUB_LANGS', 'en,es,pt'),
    ]

    for key, default in keys:
        config[key] = os.environ.get(key) or merged_env.get(key, default)

    # Backward-compat: ScrapeCreators' own examples and tutorials use the
    # SCRAPE_CREATORS_API_KEY spelling (with underscore between SCRAPE and
    # CREATORS). Accept that form too so users who follow the vendor's docs
    # don't silently end up with has_scrapecreators=False. Canonical name
    # wins when both are set.
    if not config.get('SCRAPECREATORS_API_KEY'):
        legacy = os.environ.get('SCRAPE_CREATORS_API_KEY') or merged_env.get('SCRAPE_CREATORS_API_KEY')
        if legacy:
            config['SCRAPECREATORS_API_KEY'] = legacy

    # Multi-key rotation: comma-separated SCRAPECREATORS_API_KEY round-robins
    # via random.choice per run. Originally added in #268, accidentally dropped
    # in v3.0.6, restored here.
    sc_key_raw = config.get('SCRAPECREATORS_API_KEY') or ''
    if ',' in sc_key_raw:
        import random
        sc_keys = [k.strip() for k in sc_key_raw.split(',') if k.strip()]
        config['SCRAPECREATORS_API_KEY'] = random.choice(sc_keys) if sc_keys else ''

    # Track which config source was used (highest-priority file source wins
    # the label; keychain is only reported when nothing else is configured).
    if project_env_path:
        config['_CONFIG_SOURCE'] = f'project:{project_env_path}'
    elif CONFIG_FILE and CONFIG_FILE.exists():
        config['_CONFIG_SOURCE'] = f'global:{CONFIG_FILE}'
    elif keychain_env:
        config['_CONFIG_SOURCE'] = 'keychain'
    elif pass_env:
        config['_CONFIG_SOURCE'] = 'pass'
    else:
        config['_CONFIG_SOURCE'] = 'env_only'
    if ignored_project_env_path:
        config['_IGNORED_PROJECT_CONFIG'] = str(ignored_project_env_path)
        config['_IGNORED_PROJECT_CONFIG_KEYS'] = ignored_project_keys
    config['_BROWSER_COOKIE_MODE'] = policy.browser_cookies
    config['_BROWSER_COOKIE_BROWSERS'] = cookie_extraction_browsers(config)

    if policy.browser_cookies == "read":
        browser_creds = extract_browser_credentials(config)
        for key, value in browser_creds.items():
            if not config.get(key):
                config[key] = value
                config[f"_{key}_SOURCE"] = "browser"

    return config


# ---------------------------------------------------------------------------
# Browser cookie extraction
# ---------------------------------------------------------------------------

COOKIE_DOMAINS: dict[str, dict[str, Any]] = {
    "x": {
        "domain": ".x.com",
        "cookies": ["auth_token", "ct0"],
        "mapping": {"auth_token": "AUTH_TOKEN", "ct0": "CT0"},
    },
    "truthsocial": {
        "domain": ".truthsocial.com",
        "cookies": ["_session_id"],
        "mapping": {"_session_id": "TRUTHSOCIAL_TOKEN"},
    },
}


def cookie_extraction_browsers(config: dict[str, Any]) -> list[str]:
    """Browsers to try for cookie extraction, honoring FROM_BROWSER.

    Default (FROM_BROWSER unset): no browser-cookie reads. The Chromium family
    (Chrome, Brave, Edge, Vivaldi, Opera, Arc, Chromium) is available only when
    explicitly selected because reading their cookies on macOS requires the
    browser's Safe Storage Keychain key, which triggers a system password prompt
    that cannot be reliably suppressed. On Windows only Firefox cookie
    extraction is supported; Chrome and Edge use DPAPI-encrypted cookie stores
    that are not yet supported.

    - ``FROM_BROWSER=<name>`` - a single browser (e.g. ``firefox``, ``brave``,
      ``edge``, ``arc``).
    - ``FROM_BROWSER=firefox,safari`` - a comma-separated explicit browser list.
    - ``FROM_BROWSER=auto`` - also try every Chromium browser (user accepts the
      Keychain dialog when needed).
    - ``FROM_BROWSER=off`` - returns [] (extraction disabled).

    Returning the browser list from one place keeps the setup wizard and the
    steady-state path on the same policy, so neither surprises the user with an
    unrequested Keychain prompt.
    """
    silent_browsers = ["firefox", "safari"]
    chromium_browsers = ["chrome", "brave", "edge", "vivaldi", "opera", "arc", "chromium"]
    known_browsers = silent_browsers + chromium_browsers
    from_browser = (config.get("FROM_BROWSER") or "").strip().lower()
    if not from_browser:
        return []
    if from_browser == "off":
        return []
    if from_browser == "auto":
        return silent_browsers + chromium_browsers
    if "," in from_browser:
        requested = [b.strip() for b in from_browser.split(",") if b.strip()]
        resolved = [b for b in requested if b in known_browsers]
        unknown = [b for b in requested if b not in known_browsers]
        if unknown:
            sys.stderr.write(
                "[last30days] WARNING: FROM_BROWSER ignored unrecognized browser(s): "
                f"{', '.join(unknown)} (known: {', '.join(known_browsers)})\n"
            )
            sys.stderr.flush()
        return resolved
    if from_browser in known_browsers:
        return [from_browser]
    # Non-empty, not off/auto, not a known browser, not a list: unrecognized.
    # Warn rather than fail silently so a typo (FROM_BROWSER=chrme) is visible
    # instead of looking like "no cookies found".
    sys.stderr.write(
        f"[last30days] WARNING: FROM_BROWSER='{from_browser}' is not a recognized "
        f"browser; no cookies will be read (known: {', '.join(known_browsers)}, "
        "or 'auto'/'off')\n"
    )
    sys.stderr.flush()
    return []



def extract_browser_credentials(config: dict[str, Any]) -> dict[str, str]:
    """Extract auth cookies from local browsers.

    Browser selection (and the Chrome-prompt caveat) is handled by
    ``cookie_extraction_browsers``; this function just runs the extraction for
    each configured cookie domain.
    """
    browsers = cookie_extraction_browsers(config)
    if not browsers:
        return {}
    try:
        from . import cookie_extract
    except ImportError:
        return {}
    extracted: dict[str, str] = {}
    for _service, spec in COOKIE_DOMAINS.items():
        if all(config.get(env_key) for env_key in spec["mapping"].values()):
            continue
        for browser in browsers:
            try:
                cookies = cookie_extract.extract_cookies(browser, spec["domain"], spec["cookies"])
            except Exception:
                continue
            if cookies:
                for cookie_name, env_key in spec["mapping"].items():
                    if cookie_name in cookies and not config.get(env_key):
                        extracted[env_key] = cookies[cookie_name]
                break  # Found cookies for this service, stop trying browsers
    return extracted


def get_x_source_with_method(config: dict[str, Any]) -> tuple[str | None, str]:
    """Return (source, method) for X search, where method describes the auth origin."""
    if config.get("XAI_API_KEY"):
        return "xai", "xai"
    if config.get("AUTH_TOKEN") and config.get("CT0"):
        method = config.get("_AUTH_TOKEN_SOURCE", "env")
        return "bird", method
    # Fall back to xurl CLI (official X API v2, OAuth2, free developer app)
    from . import xurl_x
    if xurl_x.is_available():
        return "xurl", "oauth2"
    return None, "none"


def config_exists(policy: ConfigLoadPolicy | None = None) -> bool:
    """Check if any configuration source exists."""
    policy = policy or ConfigLoadPolicy()
    file_env = load_env_file(CONFIG_FILE) if CONFIG_FILE and CONFIG_FILE.exists() else {}
    if _project_config_trusted(policy, file_env) and _find_project_env():
        return True
    if CONFIG_FILE:
        return CONFIG_FILE.exists()
    return False


def get_reddit_source(config: dict[str, Any]) -> str | None:
    """Determine which Reddit backend to use.

    Returns: 'scrapecreators' or None
    """
    if config.get('SCRAPECREATORS_API_KEY'):
        return 'scrapecreators'
    return None


# Default X backend priority. The first available backend is the primary X
# source; the rest are ordered failover backups, tried only if the one before
# returns nothing or errors. There is one X source ("x"); these are its
# interchangeable backends, never run in parallel.
#   xai   — xAI/Grok live search (XAI_API_KEY)
#   bird  — X GraphQL scrape via the user's browser cookies (AUTH_TOKEN/CT0)
#   xurl  — official X API v2 (xurl CLI, OAuth2)
#   xquik — key-based REST X search (XQUIK_API_KEY); keyless of browser cookies
_X_BACKEND_ORDER = ("xai", "bird", "xurl", "xquik")


def _x_backend_available(backend: str, config: dict[str, Any], has_bird_creds: bool) -> bool:
    if backend == 'xai':
        return bool(config.get('XAI_API_KEY'))
    if backend == 'bird':
        from . import bird_x
        return has_bird_creds and bird_x.is_bird_installed()
    if backend == 'xurl':
        from . import xurl_x
        return xurl_x.is_available()
    if backend == 'xquik':
        return is_xquik_available(config)
    return False


def x_backend_chain(config: dict[str, Any]) -> list[str]:
    """Ordered list of available X backends.

    ``chain[0]`` is the default X source; the remaining entries are failover
    backups, used only when the one before yields no items or errors. There is
    exactly one X source — these are its backends, never fetched in parallel.

    A ``LAST30DAYS_X_BACKEND`` pin forces a single backend (no failover): the
    user explicitly chose it. Browser-cookie probing is intentionally avoided
    (automatic Keychain access causes popups); bird counts as available only
    when AUTH_TOKEN and CT0 are present explicitly.
    """
    from . import bird_x
    has_bird_creds = bool(config.get('AUTH_TOKEN') and config.get('CT0'))
    if has_bird_creds:
        bird_x.set_credentials(config.get('AUTH_TOKEN'), config.get('CT0'))

    preferred = (config.get('LAST30DAYS_X_BACKEND') or '').lower()
    if preferred in _X_BACKEND_ORDER:
        return [preferred] if _x_backend_available(preferred, config, has_bird_creds) else []

    return [b for b in _X_BACKEND_ORDER if _x_backend_available(b, config, has_bird_creds)]


def get_x_source(config: dict[str, Any]) -> str | None:
    """The default (primary) X backend, or None if no X source is available.

    Thin wrapper over ``x_backend_chain`` returning the first/primary backend;
    callers that want failover should use ``x_backend_chain`` directly.
    """
    chain = x_backend_chain(config)
    return chain[0] if chain else None


def x_pending_browser_auth(config: dict[str, Any]) -> bool:
    """True when X is not available now but ``FROM_BROWSER`` will authenticate it at run time.

    ``--diagnose`` / ``--preflight`` load config in ``plan_only`` mode, which
    deliberately skips browser-cookie extraction (no Keychain popup,
    ``reads_values: false``). As a result ``get_x_source`` returns None and X is
    dropped from ``available_sources`` even though a normal run would extract the
    same cookies and authenticate X fine. This predicate reports that
    "available pending browser auth" state without reading a single cookie — it
    keys only on the already-resolved browser list (``cookie_extraction_browsers``
    derives it from ``FROM_BROWSER`` alone, no secrets), bird being installed, and
    X having a cookie-domain mapping. Side-effect free, so the safe-inspection
    contract of diagnose/preflight is preserved.

    Returns False whenever X is already available outright (static AUTH_TOKEN/CT0,
    or xAI/xurl/xquik backend), and in ``read`` mode (a real run has already
    extracted creds, so its status must be unchanged — never "pending").
    """
    # Already available via a static backend (bird creds, xAI, xurl, xquik).
    if get_x_source(config):
        return False
    # Only meaningful in inspection modes that skip extraction; a real ``read``
    # run has already attempted extraction and must report its true state.
    if config.get('_BROWSER_COOKIE_MODE') == 'read':
        return False
    if 'x' not in COOKIE_DOMAINS:
        return False
    if not cookie_extraction_browsers(config):
        return False
    from . import bird_x
    return bird_x.is_bird_installed()


def is_ytdlp_available() -> bool:
    """Check if yt-dlp is installed for YouTube search."""
    from . import youtube_yt
    return youtube_yt.is_ytdlp_installed()


def is_youtube_comments_available(config: dict[str, Any]) -> bool:
    """Check if YouTube comment enrichment is available.

    Default-on when SCRAPECREATORS_API_KEY is set — the same key-only backup
    tier as the YouTube transcript fallback (``is_youtube_sc_available``). Cost
    is bounded by ``enrich_with_comments(max_videos=3)`` (~3 credits per run).
    Suppress via ``EXCLUDE_SOURCES=youtube_comments``.

    Note: TikTok/Instagram comments remain explicit ``INCLUDE_SOURCES`` opt-ins
    (see ``is_tiktok_comments_available``); only YouTube comments are default-on.
    """
    if not config.get('SCRAPECREATORS_API_KEY'):
        return False
    return 'youtube_comments' not in _parse_exclude_sources(config)


def is_tiktok_comments_available(config: dict[str, Any]) -> bool:
    """Check if TikTok comment enrichment is available.

    Requires SCRAPECREATORS_API_KEY AND tiktok_comments in INCLUDE_SOURCES.
    Mirrors the youtube_comments opt-in pattern.
    """
    if not config.get('SCRAPECREATORS_API_KEY'):
        return False
    include = _parse_include_sources(config)
    return 'tiktok_comments' in include


def is_youtube_sc_available(config: dict[str, Any]) -> bool:
    """Check if ScrapeCreators YouTube search fallback is available.

    Used when yt-dlp is not installed or fails.
    """
    return bool(config.get('SCRAPECREATORS_API_KEY'))


def is_hackernews_available() -> bool:
    """Check if Hacker News source is available.

    Always returns True - HN uses free Algolia API, no key needed.
    """
    return True


def is_native_search(config: dict[str, Any]) -> bool:
    """Whether the invoking host has its own (better) native web search.

    Defined by capability, not host identity: the SKILL.md agent-host path sets
    ``LAST30DAYS_NATIVE_SEARCH`` when the runtime actually has a native web-search
    tool (e.g. Claude Code's WebSearch). When true, the engine's keyless search
    floor is suppressed so a worse free search never preempts the model's own.
    Defaults False (unset), so headless/cron and hosts without native search fall
    to the keyless floor.
    """
    raw = config.get('LAST30DAYS_NATIVE_SEARCH')
    if raw is None:
        return False
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def keyless_web_allowed(config: dict[str, Any]) -> bool:
    """Whether the engine may use its keyless web-search floor for this run.

    Allowed only when the host does NOT have native search. Independent of
    whether a paid key is set (the grounding dispatcher prefers paid first and
    falls to keyless on empty/error for non-native runs).
    """
    return not is_native_search(config)


def transcription_providers(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Ordered (name, api_key) Whisper providers for caption-free transcription.

    Groq (free tier) first, OpenAI (paid) as the backstop. Empty when neither
    key is set, in which case transcription degrades rather than runs.
    """
    providers: list[tuple[str, str]] = []
    if config.get('GROQ_API_KEY'):
        providers.append(('groq', config['GROQ_API_KEY']))
    if config.get('OPENAI_API_KEY'):
        providers.append(('openai', config['OPENAI_API_KEY']))
    return providers


def is_bluesky_available(config: dict[str, Any]) -> bool:
    """Check if Bluesky source is available.

    Requires BSKY_HANDLE and BSKY_APP_PASSWORD (app password from bsky.app/settings).
    """
    return bool(config.get('BSKY_HANDLE') and config.get('BSKY_APP_PASSWORD'))


def is_truthsocial_available(config: dict[str, Any]) -> bool:
    """Check if Truth Social source is available.

    Requires TRUTHSOCIAL_TOKEN (bearer token from browser dev tools).
    """
    return bool(config.get('TRUTHSOCIAL_TOKEN'))


def is_polymarket_available() -> bool:
    """Check if Polymarket source is available.

    Always returns True - Gamma API is free, no key needed.
    """
    return True


def is_tiktok_available(config: dict[str, Any]) -> bool:
    """Check if TikTok source is available (ScrapeCreators or legacy Apify).

    Returns True if SCRAPECREATORS_API_KEY or APIFY_API_TOKEN is set.
    """
    return bool(config.get('SCRAPECREATORS_API_KEY') or config.get('APIFY_API_TOKEN'))


def get_tiktok_token(config: dict[str, Any]) -> str:
    """Get TikTok API token, preferring ScrapeCreators over legacy Apify."""
    return config.get('SCRAPECREATORS_API_KEY') or config.get('APIFY_API_TOKEN') or ''


def _parse_include_sources(config: dict[str, Any]) -> set[str]:
    """Parse INCLUDE_SOURCES config value into a set of lowercase source names."""
    raw = config.get('INCLUDE_SOURCES') or ''
    return {s.strip().lower() for s in raw.split(',') if s.strip()}


def _parse_exclude_sources(config: dict[str, Any]) -> set[str]:
    """Parse EXCLUDE_SOURCES config value into a set of lowercase source names."""
    raw = config.get('EXCLUDE_SOURCES') or ''
    return {s.strip().lower() for s in raw.split(',') if s.strip()}


def is_threads_available(config: dict[str, Any]) -> bool:
    """Check if Threads source is available.

    Returns True when SCRAPECREATORS_API_KEY is set. Threads runs alongside
    TikTok and Instagram as part of the SC family — same key, same per-call
    cost shape, so the same default-on rule applies. Suppress via
    EXCLUDE_SOURCES=threads.
    """
    return bool(config.get('SCRAPECREATORS_API_KEY'))


def is_instagram_available(config: dict[str, Any]) -> bool:
    """Check if Instagram source is available (ScrapeCreators).

    Returns True if SCRAPECREATORS_API_KEY is set.
    Instagram uses the same key as TikTok.
    """
    return bool(config.get('SCRAPECREATORS_API_KEY'))


def get_instagram_token(config: dict[str, Any]) -> str:
    """Get Instagram API token (same ScrapeCreators key as TikTok)."""
    return config.get('SCRAPECREATORS_API_KEY') or ''


def get_xiaohongshu_api_base(config: dict[str, Any]) -> str:
    """Get Xiaohongshu HTTP API base URL.

    Defaults to host.docker.internal so OpenClaw Docker can reach host service.
    """
    return (config.get('XIAOHONGSHU_API_BASE') or "http://host.docker.internal:18060").rstrip("/")


def is_xiaohongshu_available(config: dict[str, Any]) -> bool:
    """Check whether Xiaohongshu HTTP API is reachable and logged in."""
    # Import here to avoid heavy imports at module load.
    from . import http

    base = get_xiaohongshu_api_base(config)
    try:
        # Keep health probe snappy, but allow one retry for transient hiccups.
        health = http.get(f"{base}/health", timeout=3, retries=2)
        if not isinstance(health, dict):
            return False
        if not health.get("success"):
            return False

        # Login probe can be slower on some deployments (browser/session checks),
        # so use a slightly longer timeout to avoid false negatives.
        login = http.get(f"{base}/api/v1/login/status", timeout=8, retries=2)
        is_logged_in = (
            login.get("data", {}).get("is_logged_in")
            if isinstance(login, dict) else False
        )
        return bool(is_logged_in)
    except (OSError, http.HTTPError):
        return False
    except Exception as exc:
        sys.stderr.write(
            f"[last30days] WARNING: unexpected error checking Xiaohongshu: "
            f"{type(exc).__name__}: {exc}\n"
        )
        sys.stderr.flush()
        return False


# Backward compat alias
is_apify_available = is_tiktok_available


def get_x_source_status(config: dict[str, Any], probe: bool = False) -> dict[str, Any]:
    """Get detailed X source status for UI decisions.

    Args:
        probe: when True, run a cheap 1-tweet bird probe and downgrade
            ``bird_authenticated`` to False when X clearly returns nothing,
            so ``--diagnose`` reflects runtime reality instead of static
            credential presence. A transient timeout leaves the status
            unchanged (fail open).

    Returns:
        Dict with keys: source, bird_installed, bird_authenticated,
        bird_username, xai_available, can_install_bird
    """
    from . import bird_x

    if config.get('AUTH_TOKEN') and config.get('CT0'):
        bird_x.set_credentials(config.get('AUTH_TOKEN'), config.get('CT0'))
    bird_status = bird_x.get_bird_status()
    xai_available = bool(config.get('XAI_API_KEY'))

    # Report the TRUE auth lane (browser / env / keychain) rather than the static
    # "env AUTH_TOKEN" label — tokens usually come from live browser cookies, and
    # mislabeling the lane sent past debugging down a 30-minute wrong path.
    if bird_status["authenticated"]:
        lane = config.get('_AUTH_TOKEN_SOURCE') or 'env'
        bird_status["username"] = f"{lane} AUTH_TOKEN"

    # Optional runtime probe: don't show X green when it's effectively dead.
    if probe and bird_status["authenticated"]:
        if bird_x.probe_works() is False:
            bird_status["authenticated"] = False
            bird_status["username"] = "probe failed (no working X auth)"

    # Xquik: the key-based X source used when bird's cookie auth isn't available.
    # Probe so --diagnose reports the true state — funded, or configured-but-
    # unpaid (402) — instead of false-green on mere key presence.
    xquik_available = is_xquik_available(config)
    xquik_working: bool | None = None
    xquik_status = ""
    if xquik_available:
        if probe:
            from . import xquik
            xquik_working = xquik.probe_works(get_xquik_token(config))
            xquik_status = xquik.probe_reason()
        else:
            xquik_status = "configured (not probed)"

    # Determine active source. bird (browser cookies) and xAI win when present;
    # when neither is available, xquik is the active X source. A probe that
    # clearly failed (False) means xquik is not actually usable.
    if bird_status["authenticated"]:
        source = 'bird'
    elif xai_available:
        source = 'xai'
    else:
        from . import xurl_x as _xurl_check
        if _xurl_check.is_available():
            source = 'xurl'
        elif xquik_available and xquik_working is not False:
            source = 'xquik'
        else:
            source = None

    from . import xurl_x as _xurl_x
    return {
        "source": source,
        "bird_installed": bird_status["installed"],
        "bird_authenticated": bird_status["authenticated"],
        "bird_username": bird_status["username"],
        "xai_available": xai_available,
        "xurl_available": _xurl_x.is_available(),
        "xquik_available": xquik_available,
        "xquik_working": xquik_working,
        "xquik_status": xquik_status,
        "can_install_bird": bird_status["can_install"],
    }


# Pinterest
def is_pinterest_available(config: dict[str, Any]) -> bool:
    """Check if Pinterest source is available.

    Returns True when SCRAPECREATORS_API_KEY is set AND 'pinterest' is in
    INCLUDE_SOURCES (or requested_sources at the pipeline level).  Pinterest
    is opt-in because not every topic benefits from visual pin results.
    """
    return bool(config.get('SCRAPECREATORS_API_KEY'))


def get_pinterest_token(config: dict[str, Any]) -> str:
    """Get Pinterest API token (same ScrapeCreators key as TikTok/Instagram)."""
    return config.get('SCRAPECREATORS_API_KEY') or ''


# Xquik
def is_xquik_available(config: dict[str, Any]) -> bool:
    """Check if Xquik X search source is available.

    Requires XQUIK_API_KEY (API key from xquik.com).
    """
    return bool(config.get('XQUIK_API_KEY'))


def get_xquik_token(config: dict[str, Any]) -> str:
    """Get Xquik API key."""
    return config.get('XQUIK_API_KEY') or ''

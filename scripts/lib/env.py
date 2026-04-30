"""Environment and auth management for last30days skill."""

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Literal

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

CODEX_AUTH_FILE = Path(os.environ.get("CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json")))

AuthSource = Literal["codex", "none"]
AuthStatus = Literal["ok", "missing", "expired", "missing_account_id"]

AUTH_SOURCE_CODEX: AuthSource = "codex"
AUTH_SOURCE_NONE: AuthSource = "none"

AUTH_STATUS_OK: AuthStatus = "ok"
AUTH_STATUS_MISSING: AuthStatus = "missing"
AUTH_STATUS_EXPIRED: AuthStatus = "expired"
AUTH_STATUS_MISSING_ACCOUNT_ID: AuthStatus = "missing_account_id"


@dataclass(frozen=True)
class OpenAIAuth:
    token: Optional[str]
    source: AuthSource
    status: AuthStatus
    account_id: Optional[str]
    codex_auth_file: str


def _check_file_permissions(path: Path) -> None:
    """Warn to stderr if a secrets file has overly permissive permissions."""
    try:
        mode = path.stat().st_mode
        # Check if group or other can read (bits 0o044)
        if mode & 0o044:
            import sys
            sys.stderr.write(
                f"[last30days] WARNING: {path} is readable by other users. "
                f"Run: chmod 600 {path}\n"
            )
            sys.stderr.flush()
    except OSError:
        pass


def load_env_file(path: Path) -> Dict[str, str]:
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


def _decode_jwt_payload(token: str) -> Optional[Dict[str, Any]]:
    """Decode JWT payload without verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        pad = "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(payload_b64 + pad)
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return None


def _token_expired(token: str, leeway_seconds: int = 60) -> bool:
    """Check if JWT token is expired."""
    payload = _decode_jwt_payload(token)
    if not payload:
        return False
    exp = payload.get("exp")
    if not exp:
        return False
    return exp <= (time.time() + leeway_seconds)


def extract_chatgpt_account_id(access_token: str) -> Optional[str]:
    """Extract chatgpt_account_id from JWT token."""
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return None
    auth_claim = payload.get("https://api.openai.com/auth", {})
    if isinstance(auth_claim, dict):
        return auth_claim.get("chatgpt_account_id")
    return None


def load_codex_auth(path: Path = CODEX_AUTH_FILE) -> Dict[str, Any]:
    """Load Codex auth JSON."""
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def get_codex_access_token() -> tuple[Optional[str], str]:
    """Get Codex access token from auth.json.

    Returns:
        (token, status) where status is 'ok', 'missing', or 'expired'
    """
    auth = load_codex_auth()
    token = None
    if isinstance(auth, dict):
        tokens = auth.get("tokens") or {}
        if isinstance(tokens, dict):
            token = tokens.get("access_token")
        if not token:
            token = auth.get("access_token")
    if not token:
        return None, AUTH_STATUS_MISSING
    if _token_expired(token):
        return None, AUTH_STATUS_EXPIRED
    return token, AUTH_STATUS_OK


def get_openai_auth() -> OpenAIAuth:
    """Resolve OpenAI auth from Codex device login only."""
    codex_token, codex_status = get_codex_access_token()
    if codex_token:
        account_id = extract_chatgpt_account_id(codex_token)
        if account_id:
            return OpenAIAuth(
                token=codex_token,
                source=AUTH_SOURCE_CODEX,
                status=AUTH_STATUS_OK,
                account_id=account_id,
                codex_auth_file=str(CODEX_AUTH_FILE),
            )
        return OpenAIAuth(
            token=None,
            source=AUTH_SOURCE_CODEX,
            status=AUTH_STATUS_MISSING_ACCOUNT_ID,
            account_id=None,
            codex_auth_file=str(CODEX_AUTH_FILE),
        )

    return OpenAIAuth(
        token=None,
        source=AUTH_SOURCE_NONE,
        status=codex_status,
        account_id=None,
        codex_auth_file=str(CODEX_AUTH_FILE),
    )


def _find_project_env() -> Optional[Path]:
    """Find per-project .env by walking up from cwd.

    Searches for .claude/last30days.env in each parent directory,
    stopping at the user's home directory or filesystem root.
    """
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / '.claude' / 'last30days.env'
        if candidate.exists():
            return candidate
        # Stop at filesystem root or home
        if parent == Path.home() or parent == parent.parent:
            break
    return None


def get_config() -> Dict[str, Any]:
    """Load configuration from multiple sources.

    Priority (highest wins):
      1. Environment variables (os.environ)
      2. .claude/last30days.env (per-project config)
      3. ~/.config/last30days/.env (global config)
    """
    # Load from global config file
    file_env = load_env_file(CONFIG_FILE) if CONFIG_FILE else {}

    # Load from per-project config (overrides global)
    project_env_path = _find_project_env()
    project_env = load_env_file(project_env_path) if project_env_path else {}

    # Merge: project overrides global
    merged_env = {**file_env, **project_env}

    openai_auth = get_openai_auth()

    # Build config: Codex/OpenAI auth + process.env > project .env > global .env
    config = {
        'OPENAI_ACCESS_TOKEN': openai_auth.token,
        'OPENAI_AUTH_SOURCE': openai_auth.source,
        'OPENAI_AUTH_STATUS': openai_auth.status,
        'OPENAI_CHATGPT_ACCOUNT_ID': openai_auth.account_id,
        'CODEX_AUTH_FILE': openai_auth.codex_auth_file,
    }

    keys = [
        ('XAI_API_KEY', None),
        ('GOOGLE_API_KEY', None),
        ('GEMINI_API_KEY', None),
        ('GOOGLE_GENAI_API_KEY', None),
        ('OPENROUTER_API_KEY', None),
        ('PARALLEL_API_KEY', None),
        ('BRAVE_API_KEY', None),
        ('XIAOHONGSHU_API_BASE', None),
        ('GEMINI_MODEL', None),
        ('OPENAI_MODEL_POLICY', 'auto'),
        ('OPENAI_MODEL_PIN', None),
        ('XAI_MODEL_POLICY', 'latest'),
        ('XAI_MODEL_PIN', None),
        ('SCRAPECREATORS_API_KEY', None),
        ('APIFY_API_TOKEN', None),
        ('AUTH_TOKEN', None),
        ('CT0', None),
        ('BSKY_HANDLE', None),
        ('BSKY_APP_PASSWORD', None),
        ('TRUTHSOCIAL_TOKEN', None),
    ]

    for key, default in keys:
        config[key] = os.environ.get(key) or merged_env.get(key, default)

    # Track which config source was used
    if project_env_path:
        config['_CONFIG_SOURCE'] = f'project:{project_env_path}'
    elif CONFIG_FILE and CONFIG_FILE.exists():
        config['_CONFIG_SOURCE'] = f'global:{CONFIG_FILE}'
    else:
        config['_CONFIG_SOURCE'] = 'env_only'

    return config


def get_x_source(config: Dict[str, Any]) -> Optional[str]:
    """Determine which X source is available based on config."""
    if config.get('XAI_API_KEY'):
        return 'xai'
    if config.get('AUTH_TOKEN') and config.get('CT0'):
        return 'bird'
    return None


def get_x_source_status(config: Dict[str, Any]) -> Dict[str, bool]:
    """Return availability status for X backends."""
    return {
        'xai': bool(config.get('XAI_API_KEY')),
        'bird': bool(config.get('AUTH_TOKEN') and config.get('CT0')),
    }


def config_exists() -> bool:
    """Check if any configuration source exists."""
    if _find_project_env():
        return True
    if CONFIG_FILE:
        return CONFIG_FILE.exists()
    return False


def is_reddit_available(config: Dict[str, Any]) -> bool:
    """Check if Reddit search is available."""
    return bool(config.get('SCRAPECREATORS_API_KEY') or config.get('OPENAI_ACCESS_TOKEN'))


def get_reddit_source(config: Dict[str, Any]) -> Optional[str]:
    """Determine which Reddit backend to use.

    Returns:
        "scrapecreators" if ScrapeCreators is configured (preferred)
        "openai" if only Codex-backed OpenAI access is available
        None if no Reddit backend is configured
    """
    if config.get('SCRAPECREATORS_API_KEY'):
        return 'scrapecreators'
    if config.get('OPENAI_ACCESS_TOKEN'):
        return 'openai'
    return None


def is_bluesky_available(config: Dict[str, Any]) -> bool:
    """Stub: Bluesky availability."""
    return False


def is_truthsocial_available(config: Dict[str, Any]) -> bool:
    """Stub: TruthSocial availability."""
    return False


def is_xquik_available(config: Dict[str, Any]) -> bool:
    """Stub: XQuik availability."""
    return False


def is_youtube_sc_available(config: Dict[str, Any]) -> bool:
    """Stub: YouTube SC availability."""
    return False


def is_xiaohongshu_available(config: Dict[str, Any]) -> bool:
    """Stub: Xiaohongshu availability."""
    return False


def is_threads_available(config: Dict[str, Any]) -> bool:
    """Stub: Threads availability."""
    return False


def is_pinterest_available(config: Dict[str, Any]) -> bool:
    """Stub: Pinterest availability."""
    return False

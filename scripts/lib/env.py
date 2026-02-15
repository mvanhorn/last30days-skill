"""Environment and API key management for last30days skill."""

import os
from pathlib import Path
from typing import Optional, Dict, Any

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


def load_env_file(path: Path) -> Dict[str, str]:
    """Load environment variables from a file."""
    env = {}
    if not path.exists():
        return env

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


def get_config() -> Dict[str, Any]:
    """Load configuration from ~/.config/last30days/.env and environment."""
    # Load from config file first (if configured)
    file_env = load_env_file(CONFIG_FILE) if CONFIG_FILE else {}

    # Environment variables override file
    config = {
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY') or file_env.get('OPENAI_API_KEY'),
        'XAI_API_KEY': os.environ.get('XAI_API_KEY') or file_env.get('XAI_API_KEY'),
        'YOUTUBE_API_KEY': os.environ.get('YOUTUBE_API_KEY') or file_env.get('YOUTUBE_API_KEY'),
        'PH_ACCESS_TOKEN': os.environ.get('PH_ACCESS_TOKEN') or file_env.get('PH_ACCESS_TOKEN'),
        'OPENAI_MODEL_POLICY': os.environ.get('OPENAI_MODEL_POLICY') or file_env.get('OPENAI_MODEL_POLICY', 'auto'),
        'OPENAI_MODEL_PIN': os.environ.get('OPENAI_MODEL_PIN') or file_env.get('OPENAI_MODEL_PIN'),
        'XAI_MODEL_POLICY': os.environ.get('XAI_MODEL_POLICY') or file_env.get('XAI_MODEL_POLICY', 'latest'),
        'XAI_MODEL_PIN': os.environ.get('XAI_MODEL_PIN') or file_env.get('XAI_MODEL_PIN'),
    }

    return config


def config_exists() -> bool:
    """Check if configuration file exists."""
    return CONFIG_FILE.exists()


def get_available_sources(config: Dict[str, Any]) -> str:
    """Determine which sources are available based on API keys.

    Returns: 'both', 'reddit', 'x', or 'web' (fallback when no keys)
    """
    has_openai = bool(config.get('OPENAI_API_KEY'))
    has_xai = bool(config.get('XAI_API_KEY'))

    if has_openai and has_xai:
        return 'both'
    elif has_openai:
        return 'reddit'
    elif has_xai:
        return 'x'
    else:
        return 'web'  # Fallback: WebSearch only (no API keys needed)


def get_missing_keys(config: Dict[str, Any]) -> str:
    """Determine which sources are missing (accounting for Bird).

    Returns: 'both', 'reddit', 'x', or 'none'
    """
    has_openai = bool(config.get('OPENAI_API_KEY'))
    has_xai = bool(config.get('XAI_API_KEY'))

    # Check if Bird provides X access (import here to avoid circular dependency)
    from . import bird_x
    has_bird = bird_x.is_bird_installed() and bird_x.is_bird_authenticated()

    has_x = has_xai or has_bird

    if has_openai and has_x:
        return 'none'
    elif has_openai:
        return 'x'  # Missing X source
    elif has_x:
        return 'reddit'  # Missing OpenAI key
    else:
        return 'both'  # Missing both


def validate_sources(requested: str, available: str, include_web: bool = False) -> tuple[str, Optional[str]]:
    """Validate requested sources against available keys.

    Args:
        requested: 'auto', 'reddit', 'x', 'both', or 'web'
        available: Result from get_available_sources()
        include_web: If True, add WebSearch to available sources

    Returns:
        Tuple of (effective_sources, error_message)
    """
    # WebSearch-only mode (no API keys)
    if available == 'web':
        if requested == 'auto':
            return 'web', None
        elif requested == 'web':
            return 'web', None
        else:
            return 'web', f"No API keys configured. Using WebSearch fallback. Add keys to ~/.config/last30days/.env for Reddit/X."

    if requested == 'auto':
        # Add web to sources if include_web is set
        if include_web:
            if available == 'both':
                return 'all', None  # reddit + x + web
            elif available == 'reddit':
                return 'reddit-web', None
            elif available == 'x':
                return 'x-web', None
        return available, None

    if requested == 'web':
        return 'web', None

    if requested == 'both':
        if available not in ('both',):
            missing = 'xAI' if available == 'reddit' else 'OpenAI'
            return 'none', f"Requested both sources but {missing} key is missing. Use --sources=auto to use available keys."
        if include_web:
            return 'all', None
        return 'both', None

    if requested == 'reddit':
        if available == 'x':
            return 'none', "Requested Reddit but only xAI key is available."
        if include_web:
            return 'reddit-web', None
        return 'reddit', None

    if requested == 'x':
        if available == 'reddit':
            return 'none', "Requested X but only OpenAI key is available."
        if include_web:
            return 'x-web', None
        return 'x', None

    return requested, None


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

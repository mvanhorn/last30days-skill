"""Environment and API key management for last30days skill."""

import os
from pathlib import Path
from typing import Optional, Dict, Any

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
    # Load from config file first
    file_env = load_env_file(CONFIG_FILE)

    # Environment variables override file
    config = {
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY') or file_env.get('OPENAI_API_KEY'),
        'XAI_API_KEY': os.environ.get('XAI_API_KEY') or file_env.get('XAI_API_KEY'),
        'OPENROUTER_API_KEY': os.environ.get('OPENROUTER_API_KEY') or file_env.get('OPENROUTER_API_KEY'),
        'USE_OPENROUTER': (os.environ.get('USE_OPENROUTER') or file_env.get('USE_OPENROUTER', '')).lower() in ('1', 'true', 'yes'),
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
    has_openrouter = bool(config.get('OPENROUTER_API_KEY'))
    use_openrouter = config.get('USE_OPENROUTER', False)

    # If OpenRouter is configured and enabled, it provides both sources
    if has_openrouter and use_openrouter:
        return 'both'

    if has_openai and has_xai:
        return 'both'
    elif has_openai:
        return 'reddit'
    elif has_xai:
        return 'x'
    elif has_openrouter:
        # OpenRouter available but not explicitly enabled - still use it as fallback
        return 'both'
    else:
        return 'web'  # Fallback: WebSearch only (no API keys needed)


def should_use_openrouter(config: Dict[str, Any]) -> bool:
    """Determine if OpenRouter should be used for API calls.

    Returns True if:
    - OPENROUTER_API_KEY is set AND USE_OPENROUTER=true, OR
    - OPENROUTER_API_KEY is set AND neither OPENAI nor XAI keys are available
    """
    has_openrouter = bool(config.get('OPENROUTER_API_KEY'))
    use_openrouter = config.get('USE_OPENROUTER', False)
    has_openai = bool(config.get('OPENAI_API_KEY'))
    has_xai = bool(config.get('XAI_API_KEY'))

    if not has_openrouter:
        return False

    # Explicit opt-in
    if use_openrouter:
        return True

    # Auto-fallback: use OpenRouter if native keys aren't available
    if not has_openai and not has_xai:
        return True

    return False


def get_missing_keys(config: Dict[str, Any]) -> str:
    """Determine which API keys are missing.

    Returns: 'both', 'reddit', 'x', or 'none'
    """
    has_openai = bool(config.get('OPENAI_API_KEY'))
    has_xai = bool(config.get('XAI_API_KEY'))
    has_openrouter = bool(config.get('OPENROUTER_API_KEY'))

    # OpenRouter provides both sources, so if it's configured, no keys are missing
    if has_openrouter and should_use_openrouter(config):
        return 'none'

    if has_openai and has_xai:
        return 'none'
    elif has_openai:
        return 'x'  # Missing xAI key
    elif has_xai:
        return 'reddit'  # Missing OpenAI key
    else:
        return 'both'  # Missing both keys


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

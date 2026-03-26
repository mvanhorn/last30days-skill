"""Caching utilities for last30days skill."""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Import path configuration from env module
from . import env

DEFAULT_TTL_HOURS = 24
MODEL_CACHE_TTL_DAYS = 7


def _get_cache_dir() -> Path:
    """Get the cache directory, using centralized config with fallbacks.
    
    Priority:
    1. LAST30DAYS_CACHE_DIR environment variable
    2. ~/.cache/last30days (XDG default)
    3. System temp directory as fallback
    """
    return env.get_cache_dir()


def _get_model_cache_file() -> Path:
    """Get the model cache file path."""
    return _get_cache_dir() / "model_selection.json"


# Legacy module-level variables for backward compatibility
CACHE_DIR = Path.home() / ".cache" / "last30days"
MODEL_CACHE_FILE = CACHE_DIR / "model_selection.json"


def ensure_cache_dir():
    """Ensure cache directory exists. Uses centralized config with fallbacks.
    
    The actual path is determined by _get_cache_dir() which supports:
    - LAST30DAYS_CACHE_DIR environment variable
    - XDG default: ~/.cache/last30days
    - Temp directory fallback
    """
    _get_cache_dir()  # This creates the directory via get_cache_dir()


def get_cache_key(topic: str, from_date: str, to_date: str, sources: str) -> str:
    """Generate a cache key from query parameters."""
    key_data = f"{topic}|{from_date}|{to_date}|{sources}"
    return hashlib.sha256(key_data.encode()).hexdigest()[:16]


def get_cache_path(cache_key: str) -> Path:
    """Get path to cache file. Uses centralized cache directory config."""
    return _get_cache_dir() / f"{cache_key}.json"


def is_cache_valid(cache_path: Path, ttl_hours: int = DEFAULT_TTL_HOURS) -> bool:
    """Check if cache file exists and is within TTL."""
    if not cache_path.exists():
        return False

    try:
        stat = cache_path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        age_hours = (now - mtime).total_seconds() / 3600
        return age_hours < ttl_hours
    except OSError:
        return False


def load_cache(cache_key: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> Optional[dict]:
    """Load data from cache if valid."""
    cache_path = get_cache_path(cache_key)

    if not is_cache_valid(cache_path, ttl_hours):
        return None

    try:
        with open(cache_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def get_cache_age_hours(cache_path: Path) -> Optional[float]:
    """Get age of cache file in hours."""
    if not cache_path.exists():
        return None
    try:
        stat = cache_path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - mtime).total_seconds() / 3600
    except OSError:
        return None


def load_cache_with_age(cache_key: str, ttl_hours: int = DEFAULT_TTL_HOURS) -> tuple:
    """Load data from cache with age info.

    Returns:
        Tuple of (data, age_hours) or (None, None) if invalid
    """
    cache_path = get_cache_path(cache_key)

    if not is_cache_valid(cache_path, ttl_hours):
        return None, None

    age = get_cache_age_hours(cache_path)

    try:
        with open(cache_path, 'r') as f:
            return json.load(f), age
    except (json.JSONDecodeError, OSError):
        return None, None


def save_cache(cache_key: str, data: dict):
    """Save data to cache."""
    ensure_cache_dir()
    cache_path = get_cache_path(cache_key)

    try:
        with open(cache_path, 'w') as f:
            json.dump(data, f)
    except OSError:
        pass  # Silently fail on cache write errors


def clear_cache():
    """Clear all cache files. Uses centralized cache directory config."""
    cache_dir = _get_cache_dir()
    if cache_dir.exists():
        for f in cache_dir.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass


# Model selection cache (longer TTL)


def load_model_cache() -> dict:
    """Load model selection cache. Uses centralized cache directory config."""
    model_cache_file = _get_model_cache_file()
    if not is_cache_valid(model_cache_file, MODEL_CACHE_TTL_DAYS * 24):
        return {}

    try:
        with open(model_cache_file, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_model_cache(data: dict):
    """Save model selection cache. Uses centralized cache directory config."""
    ensure_cache_dir()
    model_cache_file = _get_model_cache_file()
    try:
        with open(model_cache_file, 'w') as f:
            json.dump(data, f)
    except OSError:
        pass


def get_cached_model(provider: str) -> Optional[str]:
    """Get cached model selection for a provider."""
    cache = load_model_cache()
    return cache.get(provider)


def set_cached_model(provider: str, model: str):
    """Cache model selection for a provider."""
    cache = load_model_cache()
    cache[provider] = model
    cache['updated_at'] = datetime.now(timezone.utc).isoformat()
    save_model_cache(cache)

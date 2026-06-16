"""Model auto-selection for last30days skill.

Model selection philosophy: this tool uses LLM APIs exclusively for
search tool invocation + structured JSON extraction. This is not
reasoning-heavy or creative work — mini models handle it equally well
at ~3-5x lower cost. We prefer the newest-generation mini model, falling
back to mainline only when mini isn't available.

xAI non-reasoning variant preferred: same pricing as reasoning, but
faster (skips thinking phase, saves reasoning token output costs).
"""

import re
import sys
from typing import Dict, List, Optional, Tuple

from . import cache, http, env

# OpenAI API
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
# Ordered by cost-efficiency for web_search + JSON extraction tasks.
# Mini models first: same structured extraction quality at ~3x lower cost.
OPENAI_FALLBACK_MODELS = ["gpt-5-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4o"]
# The ChatGPT Codex backend (chatgpt.com/backend-api/codex) only accepts a
# subset of models — it rejects the *-codex and -mini variants with HTTP 400.
# gpt-5.5 is the working general model for web_search + JSON extraction there.
CODEX_FALLBACK_MODELS = ["gpt-5.5"]

# xAI API - Agent Tools API requires grok-4 family
# Non-reasoning: same price, faster, no unnecessary thinking tokens.
# Both variants support function calling and structured outputs.
XAI_MODELS_URL = "https://api.x.ai/v1/models"
XAI_ALIASES = {
    "latest": "grok-4-1-fast-non-reasoning",
    "stable": "grok-4-1-fast-non-reasoning",
}


def parse_version(model_id: str) -> Optional[Tuple[int, ...]]:
    """Parse semantic version from model ID.

    Examples:
        gpt-5 -> (5,)
        gpt-5.2 -> (5, 2)
        gpt-5.2.1 -> (5, 2, 1)
    """
    match = re.search(r'(\d+(?:\.\d+)*)', model_id)
    if match:
        return tuple(int(x) for x in match.group(1).split('.'))
    return None


def is_search_capable_model(model_id: str) -> bool:
    """Check if model supports Responses API web_search with domain filtering.

    Includes mini variants (same structured extraction quality, lower cost).
    Excludes: nano (no web_search), gpt-4o-mini (no domain filtering),
    chat/codex/pro/preview/turbo/search (specialized variants).

    Note: gpt-5 with reasoning effort="minimal" does NOT support web_search
    (per OpenAI docs). We never set reasoning params — our usage is pure
    tool invocation + JSON extraction — so gpt-5 is safe to include here.
    """
    model_lower = model_id.lower()

    # gpt-4o-mini does NOT support web_search with filters — exclude it
    if model_lower.startswith("gpt-4o-mini"):
        return False

    # Must be gpt-4o, gpt-4.1[-mini], or gpt-5[-mini] series
    if not re.match(r'^gpt-(?:4o|4\.1|5)(\.\d+)*(-mini)?$', model_lower):
        return False

    # Exclude unsupported variants
    for exc in ['nano', 'chat', 'codex', 'pro', 'preview', 'turbo', 'search']:
        if exc in model_lower:
            return False

    return True


# Backward compat alias
is_mainline_openai_model = is_search_capable_model


def select_openai_model(
    api_key: str,
    policy: str = "auto",
    pin: Optional[str] = None,
    mock_models: Optional[List[Dict]] = None,
) -> str:
    """Select the most cost-efficient OpenAI model for web_search + JSON extraction.

    Prefers mini models within the newest generation available, since the task
    is structured extraction (not reasoning or creative work).

    Args:
        api_key: OpenAI API key
        policy: 'auto' or 'pinned'
        pin: Model to use if policy is 'pinned'
        mock_models: Mock model list for testing

    Returns:
        Selected model ID
    """
    if policy == "pinned" and pin:
        return pin

    # Check cache first
    cached = cache.get_cached_model("openai")
    if cached:
        return cached

    # Fetch model list
    if mock_models is not None:
        models = mock_models
    else:
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            response = http.get(OPENAI_MODELS_URL, headers=headers)
            models = response.get("data", [])
        except http.HTTPError as e:
            sys.stderr.write(f"[Models] Failed to fetch OpenAI models: {e}")
            if hasattr(e, 'status_code') and e.status_code in (401, 403):
                sys.stderr.write(" — API key may be invalid or lack permissions")
            sys.stderr.write(f", using fallback {OPENAI_FALLBACK_MODELS[0]}\n")
            return OPENAI_FALLBACK_MODELS[0]

    candidates = [m for m in models if is_search_capable_model(m.get("id", ""))]

    if not candidates:
        return OPENAI_FALLBACK_MODELS[0]

    # Sort: newest generation first, prefer mini within same generation
    def sort_key(m):
        model_id = m.get("id", "")
        version = parse_version(model_id) or (0,)
        major = version[0] if version else 0
        is_mini = 1 if "mini" in model_id.lower() else 0
        return (major, is_mini, version)

    candidates.sort(key=sort_key, reverse=True)
    selected = candidates[0]["id"]

    cache.set_cached_model("openai", selected)

    return selected


def select_xai_model(
    api_key: str,
    policy: str = "latest",
    pin: Optional[str] = None,
    mock_models: Optional[List[Dict]] = None,
) -> str:
    """Select the best xAI model based on policy.

    Args:
        api_key: xAI API key
        policy: 'latest', 'stable', or 'pinned'
        pin: Model to use if policy is 'pinned'
        mock_models: Mock model list for testing

    Returns:
        Selected model ID
    """
    if policy == "pinned" and pin:
        return pin

    # Use alias system
    if policy in XAI_ALIASES:
        alias = XAI_ALIASES[policy]

        # Check cache first
        cached = cache.get_cached_model("xai")
        if cached:
            return cached

        # Cache the alias
        cache.set_cached_model("xai", alias)
        return alias

    # Default to latest
    return XAI_ALIASES["latest"]


def get_models(
    config: Dict,
    mock_openai_models: Optional[List[Dict]] = None,
    mock_xai_models: Optional[List[Dict]] = None,
) -> Dict[str, Optional[str]]:
    """Get selected models for both providers.

    Returns:
        Dict with 'openai' and 'xai' keys
    """
    result = {"openai": None, "xai": None}

    if config.get("OPENAI_API_KEY"):
        if config.get("OPENAI_AUTH_SOURCE") == env.AUTH_SOURCE_CODEX:
            # Codex auth doesn't use the OpenAI models list endpoint
            policy = config.get("OPENAI_MODEL_POLICY", "auto")
            pin = config.get("OPENAI_MODEL_PIN")
            if policy == "pinned" and pin:
                result["openai"] = pin
            else:
                result["openai"] = CODEX_FALLBACK_MODELS[0]
        else:
            result["openai"] = select_openai_model(
                config["OPENAI_API_KEY"],
                config.get("OPENAI_MODEL_POLICY", "auto"),
                config.get("OPENAI_MODEL_PIN"),
                mock_openai_models,
            )

    if config.get("XAI_API_KEY"):
        result["xai"] = select_xai_model(
            config["XAI_API_KEY"],
            config.get("XAI_MODEL_POLICY", "latest"),
            config.get("XAI_MODEL_PIN"),
            mock_xai_models,
        )

    return result

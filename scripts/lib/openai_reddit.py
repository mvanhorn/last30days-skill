"""OpenAI Responses API client for Reddit discovery."""

import json
import re
import sys
from typing import Any, Dict, List, Optional

from . import http

# Fallback models when the selected model isn't accessible (e.g., org not verified for GPT-5)
MODEL_FALLBACK_ORDER = ["gpt-4.1", "gpt-4o", "gpt-4o-mini"]


def _log_error(msg: str):
    """Log error to stderr."""
    sys.stderr.write(f"[REDDIT ERROR] {msg}\n")
    sys.stderr.flush()


def _log_info(msg: str):
    """Log info to stderr."""
    sys.stderr.write(f"[REDDIT] {msg}\n")
    sys.stderr.flush()


def _is_model_access_error(error: http.HTTPError) -> bool:
    """Check if error is due to model access/verification issues."""
    if error.status_code not in (400, 403):
        return False
    if not error.body:
        return False
    body_lower = error.body.lower()
    # Check for common access/verification error messages
    return any(phrase in body_lower for phrase in [
        "verified",
        "organization must be",
        "does not have access",
        "not available",
        "not found",
    ])


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# Depth configurations: (min, max) threads to request
# Request MORE than needed since many get filtered by date
DEPTH_CONFIG = {
    "quick": (15, 25),
    "default": (30, 50),
    "deep": (70, 100),
}

REDDIT_SEARCH_PROMPT = """Find Reddit discussion threads about: {topic}

STEP 1: EXTRACT THE CORE SUBJECT
Get the MAIN NOUN/PRODUCT/TOPIC:
- "best nano banana prompting practices" → "nano banana"
- "killer features of clawdbot" → "clawdbot"
- "top Claude Code skills" → "Claude Code"
DO NOT include "best", "top", "tips", "practices", "features" in your search.

STEP 2: SEARCH BROADLY
Search for the core subject:
1. "[core subject] site:reddit.com"
2. "reddit [core subject]"
3. "[core subject] reddit"

Return as many relevant threads as you find. We filter by date server-side.

STEP 3: INCLUDE ALL MATCHES
- Include ALL threads about the core subject
- Set date to "YYYY-MM-DD" if you can determine it, otherwise null
- We verify dates and filter old content server-side
- DO NOT pre-filter aggressively - include anything relevant

REQUIRED: URLs must contain "/r/" AND "/comments/"
REJECT: developers.reddit.com, business.reddit.com

Find {min_items}-{max_items} threads. Return MORE rather than fewer.

Return JSON:
{{
  "items": [
    {{
      "title": "Thread title",
      "url": "https://www.reddit.com/r/sub/comments/xyz/title/",
      "subreddit": "subreddit_name",
      "date": "YYYY-MM-DD or null",
      "why_relevant": "Why relevant",
      "relevance": 0.85
    }}
  ]
}}"""


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for retry."""
    noise = ['best', 'top', 'how to', 'tips for', 'practices', 'features',
             'killer', 'guide', 'tutorial', 'recommendations', 'advice',
             'prompting', 'using', 'for', 'with', 'the', 'of', 'in', 'on']
    words = topic.lower().split()
    result = [w for w in words if w not in noise]
    return ' '.join(result[:3]) or topic  # Keep max 3 words


def search_reddit(
    api_key: str,
    model: str,
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
    _retry: bool = False,
) -> Dict[str, Any]:
    """Search Reddit for relevant threads using OpenAI Responses API.

    Args:
        api_key: OpenAI API key
        model: Model to use
        topic: Search topic
        from_date: Start date (YYYY-MM-DD) - only include threads after this
        to_date: End date (YYYY-MM-DD) - only include threads before this
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Raw API response
    """
    if mock_response is not None:
        return mock_response

    min_items, max_items = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Adjust timeout based on depth (generous for OpenAI web_search which can be slow)
    timeout = 90 if depth == "quick" else 120 if depth == "default" else 180

    # Build list of models to try: requested model first, then fallbacks
    models_to_try = [model] + [m for m in MODEL_FALLBACK_ORDER if m != model]

    # Note: allowed_domains accepts base domain, not subdomains
    # We rely on prompt to filter out developers.reddit.com, etc.
    input_text = REDDIT_SEARCH_PROMPT.format(
        topic=topic,
        from_date=from_date,
        to_date=to_date,
        min_items=min_items,
        max_items=max_items,
    )

    last_error = None
    for current_model in models_to_try:
        payload = {
            "model": current_model,
            "tools": [
                {
                    "type": "web_search",
                    "filters": {
                        "allowed_domains": ["reddit.com"]
                    }
                }
            ],
            "include": ["web_search_call.action.sources"],
            "input": input_text,
        }

        try:
            return http.post(OPENAI_RESPONSES_URL, payload, headers=headers, timeout=timeout)
        except http.HTTPError as e:
            last_error = e
            if _is_model_access_error(e):
                _log_info(f"Model {current_model} not accessible, trying fallback...")
                continue
            # Non-access error, don't retry with different model
            raise

    # All models failed with access errors
    if last_error:
        _log_error(f"All models failed. Last error: {last_error}")
        raise last_error
    raise http.HTTPError("No models available")


def parse_reddit_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse OpenAI response to extract Reddit items.

    Args:
        response: Raw API response

    Returns:
        List of item dicts
    """
    items = []

    # Check for API errors first
    if "error" in response and response["error"]:
        error = response["error"]
        err_msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        _log_error(f"OpenAI API error: {err_msg}")
        if http.DEBUG:
            _log_error(f"Full error response: {json.dumps(response, indent=2)[:1000]}")
        return items

    # Try to find the output text
    output_text = ""
    if "output" in response:
        output = response["output"]
        if isinstance(output, str):
            output_text = output
        elif isinstance(output, list):
            for item in output:
                if isinstance(item, dict):
                    if item.get("type") == "message":
                        content = item.get("content", [])
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "output_text":
                                output_text = c.get("text", "")
                                break
                    elif "text" in item:
                        output_text = item["text"]
                elif isinstance(item, str):
                    output_text = item
                if output_text:
                    break

    # Also check for choices (older format)
    if not output_text and "choices" in response:
        for choice in response["choices"]:
            if "message" in choice:
                output_text = choice["message"].get("content", "")
                break

    if not output_text:
        print(f"[REDDIT WARNING] No output text found in OpenAI response. Keys present: {list(response.keys())}", flush=True)
        return items

    # Extract JSON from the response
    json_match = re.search(r'\{[\s\S]*"items"[\s\S]*\}', output_text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            items = data.get("items", [])
        except json.JSONDecodeError:
            pass

    # Validate and clean items
    clean_items = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        url = item.get("url", "")
        if not url or "reddit.com" not in url:
            continue

        clean_item = {
            "id": f"R{i+1}",
            "title": str(item.get("title", "")).strip(),
            "url": url,
            "subreddit": str(item.get("subreddit", "")).strip().lstrip("r/"),
            "date": item.get("date"),
            "why_relevant": str(item.get("why_relevant", "")).strip(),
            "relevance": min(1.0, max(0.0, float(item.get("relevance", 0.5)))),
        }

        # Validate date format
        if clean_item["date"]:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(clean_item["date"])):
                clean_item["date"] = None

        clean_items.append(clean_item)

    return clean_items

"""OpenRouter API client for Reddit and X discovery.

OpenRouter provides a unified API to access multiple LLM providers with
native web search capabilities, including:
- OpenAI models with web_search tool (for Reddit)
- xAI models with x_search tool (for X/Twitter)

This module wraps both providers through OpenRouter's Responses API.
"""

import json
import re
import sys
from typing import Any, Dict, List, Optional

from . import http


def _log_error(msg: str, source: str = "OPENROUTER"):
    """Log error to stderr."""
    sys.stderr.write(f"[{source} ERROR] {msg}\n")
    sys.stderr.flush()


OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"

# Depth configurations for Reddit: (min, max) threads to request
REDDIT_DEPTH_CONFIG = {
    "quick": (15, 25),
    "default": (30, 50),
    "deep": (70, 100),
}

# Depth configurations for X: (min, max) posts to request
X_DEPTH_CONFIG = {
    "quick": (8, 12),
    "default": (20, 30),
    "deep": (40, 60),
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

X_SEARCH_PROMPT = """You have access to real-time X (Twitter) data. Search for posts about: {topic}

Focus on posts from {from_date} to {to_date}. Find {min_items}-{max_items} high-quality, relevant posts.

IMPORTANT: Return ONLY valid JSON in this exact format, no other text:
{{
  "items": [
    {{
      "text": "Post text content (truncated if long)",
      "url": "https://x.com/user/status/...",
      "author_handle": "username",
      "date": "YYYY-MM-DD or null if unknown",
      "engagement": {{
        "likes": 100,
        "reposts": 25,
        "replies": 15,
        "quotes": 5
      }},
      "why_relevant": "Brief explanation of relevance",
      "relevance": 0.85
    }}
  ]
}}

Rules:
- relevance is 0.0 to 1.0 (1.0 = highly relevant)
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
- Include diverse voices/accounts if applicable
- Prefer posts with substantive content, not just links"""


def search_reddit(
    api_key: str,
    model: str,
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Search Reddit using OpenRouter with OpenAI model and native web search.

    Args:
        api_key: OpenRouter API key
        model: OpenAI model to use (e.g., "gpt-5.2" - will be prefixed with openai/)
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Raw API response
    """
    if mock_response is not None:
        return mock_response

    min_items, max_items = REDDIT_DEPTH_CONFIG.get(depth, REDDIT_DEPTH_CONFIG["default"])

    # Prefix model with openai/ for OpenRouter
    openrouter_model = f"openai/{model}" if not model.startswith("openai/") else model

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/anthropics/claude-code",
        "X-Title": "last30days-skill",
    }

    # Adjust timeout based on depth
    timeout = 90 if depth == "quick" else 120 if depth == "default" else 180

    # Use OpenRouter's Responses API with native web search plugin
    # Note: allowed_domains filter is passed through to OpenAI's native search
    payload = {
        "model": openrouter_model,
        "plugins": [
            {
                "id": "web",
                "engine": "native",
            }
        ],
        "tools": [
            {
                "type": "web_search",
                "filters": {
                    "allowed_domains": ["reddit.com"]
                }
            }
        ],
        "include": ["web_search_call.action.sources"],
        "input": REDDIT_SEARCH_PROMPT.format(
            topic=topic,
            from_date=from_date,
            to_date=to_date,
            min_items=min_items,
            max_items=max_items,
        ),
    }

    return http.post(OPENROUTER_RESPONSES_URL, payload, headers=headers, timeout=timeout)


def search_x(
    api_key: str,
    model: str,
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Search X using OpenRouter with xAI model and native x_search.

    Args:
        api_key: OpenRouter API key
        model: xAI model to use (e.g., "grok-4-1-fast" - will be prefixed with xai/)
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Raw API response
    """
    if mock_response is not None:
        return mock_response

    min_items, max_items = X_DEPTH_CONFIG.get(depth, X_DEPTH_CONFIG["default"])

    # Prefix model with xai/ for OpenRouter
    openrouter_model = f"xai/{model}" if not model.startswith("xai/") else model

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/anthropics/claude-code",
        "X-Title": "last30days-skill",
    }

    # Adjust timeout based on depth
    timeout = 90 if depth == "quick" else 120 if depth == "default" else 180

    # Use OpenRouter's Responses API with native x_search via web plugin
    # For xAI models, the web plugin enables both Web Search and X Search
    payload = {
        "model": openrouter_model,
        "plugins": [
            {
                "id": "web",
                "engine": "native",
            }
        ],
        "tools": [
            {"type": "x_search"}
        ],
        "input": [
            {
                "role": "user",
                "content": X_SEARCH_PROMPT.format(
                    topic=topic,
                    from_date=from_date,
                    to_date=to_date,
                    min_items=min_items,
                    max_items=max_items,
                ),
            }
        ],
    }

    return http.post(OPENROUTER_RESPONSES_URL, payload, headers=headers, timeout=timeout)


def parse_reddit_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse OpenRouter response to extract Reddit items.

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
        _log_error(f"API error: {err_msg}", "REDDIT")
        if http.DEBUG:
            _log_error(f"Full error response: {json.dumps(response, indent=2)[:1000]}", "REDDIT")
        return items

    # Try to find the output text - OpenRouter may use different formats
    output_text = _extract_output_text(response)

    if not output_text:
        print(f"[REDDIT WARNING] No output text found in OpenRouter response. Keys present: {list(response.keys())}", flush=True)
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


def parse_x_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse OpenRouter response to extract X items.

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
        _log_error(f"API error: {err_msg}", "X")
        if http.DEBUG:
            _log_error(f"Full error response: {json.dumps(response, indent=2)[:1000]}", "X")
        return items

    # Try to find the output text
    output_text = _extract_output_text(response)

    if not output_text:
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
        if not url:
            continue

        # Parse engagement
        engagement = None
        eng_raw = item.get("engagement")
        if isinstance(eng_raw, dict):
            engagement = {
                "likes": int(eng_raw.get("likes", 0)) if eng_raw.get("likes") else None,
                "reposts": int(eng_raw.get("reposts", 0)) if eng_raw.get("reposts") else None,
                "replies": int(eng_raw.get("replies", 0)) if eng_raw.get("replies") else None,
                "quotes": int(eng_raw.get("quotes", 0)) if eng_raw.get("quotes") else None,
            }

        clean_item = {
            "id": f"X{i+1}",
            "text": str(item.get("text", "")).strip()[:500],  # Truncate long text
            "url": url,
            "author_handle": str(item.get("author_handle", "")).strip().lstrip("@"),
            "date": item.get("date"),
            "engagement": engagement,
            "why_relevant": str(item.get("why_relevant", "")).strip(),
            "relevance": min(1.0, max(0.0, float(item.get("relevance", 0.5)))),
        }

        # Validate date format
        if clean_item["date"]:
            if not re.match(r'^\d{4}-\d{2}-\d{2}$', str(clean_item["date"])):
                clean_item["date"] = None

        clean_items.append(clean_item)

    return clean_items


def _extract_output_text(response: Dict[str, Any]) -> str:
    """Extract output text from OpenRouter response.

    OpenRouter may return responses in different formats depending on the
    underlying provider. This function handles multiple formats.
    """
    output_text = ""

    # Format 1: Responses API format with output array
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

    # Format 2: Chat completions format with choices
    if not output_text and "choices" in response:
        for choice in response["choices"]:
            if "message" in choice:
                output_text = choice["message"].get("content", "")
                break

    # Format 3: Direct text response
    if not output_text and "text" in response:
        output_text = response["text"]

    # Format 4: Content array at top level
    if not output_text and "content" in response:
        content = response["content"]
        if isinstance(content, str):
            output_text = content
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    output_text = c.get("text", "")
                    break

    return output_text

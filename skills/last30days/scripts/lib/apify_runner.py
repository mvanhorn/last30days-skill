"""Shared Apify actor runner helpers for social source fallbacks."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from . import http


def build_headers(token: str) -> Dict[str, str]:
    """Build Authorization headers for Apify API calls."""
    return {"Authorization": f"Bearer {token}"}


def dedupe_tokens(tokens: Iterable[str] | None) -> List[str]:
    """Return unique, non-empty Apify tokens preserving order."""
    seen: set[str] = set()
    out: List[str] = []
    for token in tokens or []:
        token = (token or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def run_actor_dataset_items(
    actor_id: str,
    payload: Dict[str, Any],
    tokens: Iterable[str] | None,
    *,
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """Run an Apify actor and return dataset items.

    If multiple tokens are provided, tries the next token when the current one
    fails with auth / billing style errors.
    """
    token_list = dedupe_tokens(tokens)
    if not token_list:
        raise http.HTTPError("No APIFY_API_TOKEN configured")

    last_error: Exception | None = None
    retryable_statuses = {401, 402, 403, 429}

    for index, token in enumerate(token_list):
        try:
            raw_items = http.request(
                "POST",
                f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items",
                headers=build_headers(token),
                json_data=payload,
                params={"clean": "true", "format": "json"},
                timeout=timeout,
                retries=1,
                raw=False,
            )
            if isinstance(raw_items, dict):
                raw_items = raw_items.get("items") or raw_items.get("data") or []
            return raw_items if isinstance(raw_items, list) else []
        except http.HTTPError as exc:
            last_error = exc
            if exc.status_code in retryable_statuses and index < len(token_list) - 1:
                continue
            raise
        except Exception as exc:
            last_error = exc
            raise

    if last_error:
        raise last_error
    return []

"""Optional hosted publishing for rendered HTML artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://api.ht-ml.app/v1/sites"


class HtmlPublishError(RuntimeError):
    """Raised when the hosted HTML publish endpoint rejects the artifact."""


class HtmlPublishBatchResult(dict[str, dict[str, Any]]):
    """Successful document publishes plus an optional later failure."""

    def __init__(self) -> None:
        super().__init__()
        self.error: HtmlPublishError | None = None


def publish_html(
    html_content: str,
    *,
    password: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    opener: Callable[..., Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Publish a single HTML document and return the provider response."""
    if not html_content.strip():
        raise HtmlPublishError("HTML content is empty")

    payload: dict[str, str] = {"html_content": html_content}
    if password is not None:
        payload["password"] = password

    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    open_fn = opener or urlopen
    try:
        with open_fn(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise HtmlPublishError(_error_message(exc.code, detail)) from exc
    except URLError as exc:
        raise HtmlPublishError(str(exc.reason)) from exc
    except OSError as exc:
        raise HtmlPublishError(str(exc)) from exc

    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HtmlPublishError("publish endpoint returned non-JSON response") from exc
    if not isinstance(result, dict):
        raise HtmlPublishError("publish endpoint returned unexpected JSON response")

    url = result.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise HtmlPublishError("publish endpoint response did not include a valid url")
    return result


def publish_html_documents(
    documents: Mapping[str, str],
    *,
    password: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    opener: Callable[..., Any] | None = None,
    timeout: int = 30,
) -> HtmlPublishBatchResult:
    """Publish a named set of documents, preserving caller order in results."""
    results = HtmlPublishBatchResult()
    for name, content in documents.items():
        try:
            results[name] = publish_html(
                content,
                password=password,
                endpoint=endpoint,
                opener=opener,
                timeout=timeout,
            )
        except HtmlPublishError as exc:
            results.error = exc
            break
    return results


def _error_message(status: int, detail: str) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        payload = {}
    message = payload.get("message") if isinstance(payload, dict) else None
    if message:
        return f"{status}: {message}"
    return f"{status}: {detail.strip() or 'publish failed'}"

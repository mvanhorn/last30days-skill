"""Optional hosted publishing for rendered HTML artifacts."""

from __future__ import annotations

import http.client
import json
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request


DEFAULT_ENDPOINT = "https://api.ht-ml.app/v1/sites"
_DEFAULT_URLOPEN = urllib.request.urlopen
urlopen = _DEFAULT_URLOPEN


class HtmlPublishError(RuntimeError):
    """Raised when the hosted HTML publish endpoint rejects the artifact."""


class HtmlPublishBatchResult(dict[str, dict[str, Any]]):
    """Successful document publishes plus an optional later failure."""

    def __init__(self) -> None:
        super().__init__()
        self.error: HtmlPublishError | None = None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse all 3xx redirects so POST payloads and passwords cannot be forwarded or downgraded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(
            req.full_url,
            code,
            "publish endpoint redirects are not allowed",
            headers,
            fp,
        )


def _build_publish_opener() -> urllib.request.OpenerDirector:
    """Build an HTTPS-only OpenerDirector without file/ftp/data/http or redirect following."""
    opener = urllib.request.OpenerDirector()
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _NoRedirectHandler(),
        urllib.request.HTTPErrorProcessor(),
    ]
    if hasattr(http.client, "HTTPSConnection"):
        handlers.append(urllib.request.HTTPSHandler())
    for handler in handlers:
        opener.add_handler(handler)
    return opener


_PUBLISH_OPENER = _build_publish_opener()


def _validate_https_url(url: str, *, label: str) -> str:
    if not isinstance(url, str) or not url or url != url.strip():
        raise HtmlPublishError(f"{label} must be a valid https:// URL")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in url):
        raise HtmlPublishError(f"{label} contains invalid characters")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        _ = parts.port
    except ValueError as exc:
        raise HtmlPublishError(f"{label} is not a valid URL") from exc
    if parts.scheme.lower() != "https":
        raise HtmlPublishError(f"{label} must use https://")
    if not hostname:
        raise HtmlPublishError(f"{label} must include a hostname")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise HtmlPublishError(f"{label} must not contain credentials")
    return url


def _resolve_open_fn(opener: Callable[..., Any] | None) -> Callable[..., Any]:
    if opener is not None:
        return opener
    if urlopen is not _DEFAULT_URLOPEN:
        return urlopen
    if urllib.request.urlopen is not _DEFAULT_URLOPEN:
        return urllib.request.urlopen
    return _PUBLISH_OPENER.open


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
    validated_endpoint = _validate_https_url(endpoint, label="publish endpoint")

    payload: dict[str, str] = {"html_content": html_content}
    if password is not None:
        payload["password"] = password

    request = Request(
        validated_endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    open_fn = _resolve_open_fn(opener)
    try:
        with open_fn(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp is not None else ""
        if 300 <= exc.code < 400 and exc.reason:
            raise HtmlPublishError(f"{exc.code}: {exc.reason}") from exc
        raise HtmlPublishError(_error_message(exc.code, detail or str(exc.reason or ""))) from exc
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
    if not isinstance(url, str):
        raise HtmlPublishError("publish endpoint response did not include a valid url")
    try:
        _validate_https_url(url, label="publish endpoint response url")
    except HtmlPublishError as exc:
        raise HtmlPublishError("publish endpoint response did not include a valid url") from exc
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

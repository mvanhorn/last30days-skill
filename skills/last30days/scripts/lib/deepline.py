"""Deepline client for person runs: LinkedIn profile, LinkedIn posts, and lookup.

Calls Deepline's integration execute endpoint directly over the shared HTTP
layer with the user's own ``DEEPLINE_API_KEY`` (never through proxy mode):

    POST https://code.deepline.com/api/v2/integrations/{tool}/execute
    Authorization: Bearer <key>
    Idempotency-Key: <hash of run id, tool, and payload>
    {"payload": {...}}

Every call is paid, so a ``DeeplineClient`` is created once per person run and
holds that run's budget: at most one lookup, two profile reads, and two post
pages. A 402 stops every later call in the run. Failures come back as named
status strings, never as exceptions.

Privacy: payloads can carry an email. The client never logs a payload, the
idempotency key is a hash, and email-bearing calls are marked sensitive so the
shared HTTP layer keeps them out of its diagnostics and fixture capture.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from . import health, http, log

API_BASE = "https://code.deepline.com/api/v2/integrations"
KEY_VAR = "DEEPLINE_API_KEY"

TOOL_PROFILE = "harvestapi_get_profile"
TOOL_POSTS = "harvestapi_get_profile_posts"
TOOL_LOOKUP = "limadata_find_person_profiles"

# Per-run paid-call caps.
DEFAULT_CAPS: Dict[str, int] = {TOOL_LOOKUP: 1, TOOL_PROFILE: 2, TOOL_POSTS: 2}

# Values the posts tool accepts for scrapePostedLimit. The upstream does not
# guarantee enforcement, so callers still filter posts by date themselves.
POSTED_LIMITS = ("1h", "24h", "week", "month", "3months", "6months", "year")

# One attempt's ceiling, and the least time worth starting an attempt with.
ATTEMPT_TIMEOUT_SECONDS = 30.0
MIN_ATTEMPT_SECONDS = 10.0

# Status strings returned in every result's "status".
OK = "ok"
AUTH_FAILED = "auth failed"
OUT_OF_CREDITS = "out of credits"
RATE_LIMITED = "rate limited"
TIMEOUT = "timeout"
ERROR = "error"
OVER_BUDGET = "over budget"

_RETRYABLE = object()


def _log(msg: str) -> None:
    log.source_log("Deepline", msg, tty_only=False)


def is_available(config: Dict[str, Any]) -> bool:
    """The single availability check shared by the run and doctor."""
    return bool((config.get(KEY_VAR) or "").strip())


def from_config(config: Dict[str, Any], run_id: str) -> Optional["DeeplineClient"]:
    """A client for one person run, or None when no key is configured."""
    if not is_available(config):
        return None
    return DeeplineClient(config[KEY_VAR].strip(), run_id)


def idempotency_key(run_id: str, tool: str, payload: Dict[str, Any]) -> str:
    """Stable per (run, tool, payload); a hash, so no input value leaks."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{run_id}\x00{tool}\x00{canonical}".encode("utf-8"))
    return f"l30d-{digest.hexdigest()[:40]}"


def _result(status: str, data: Any = None, detail: str = "") -> Dict[str, Any]:
    return {"status": status, "data": data, "detail": detail}


class DeeplineClient:
    """One person run's Deepline access: budget, stop rule, and call log.

    ``events`` lists ``{"tool", "status"}`` per call outcome (no inputs), so the
    report can say what was checked. ``stopped`` holds the status that ended
    Deepline use for the run (``"out of credits"``), else None.
    """

    def __init__(self, api_key: str, run_id: str, caps: Optional[Dict[str, int]] = None):
        self.api_key = api_key
        self.run_id = run_id
        self.caps = dict(DEFAULT_CAPS if caps is None else caps)
        self._calls: Dict[str, int] = {}
        self.stopped: Optional[str] = None
        self.events: List[Dict[str, str]] = []

    def calls_made(self, tool: str) -> int:
        return self._calls.get(tool, 0)

    # -- public calls ---------------------------------------------------

    def get_profile(self, url: str, *, deadline_monotonic: Optional[float]) -> Dict[str, Any]:
        """Read a LinkedIn profile. ``data`` is ``parse_profile`` output."""
        return self._call(TOOL_PROFILE, {"url": url}, deadline_monotonic, parse_profile)

    def get_posts(
        self,
        profile_url: str,
        *,
        deadline_monotonic: Optional[float],
        posted_limit: str = "year",
        pagination_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read one page of a profile's posts. ``data`` is ``parse_posts`` output."""
        if posted_limit not in POSTED_LIMITS:
            raise ValueError(f"posted_limit must be one of {POSTED_LIMITS}")
        payload: Dict[str, Any] = {"profile": profile_url, "scrapePostedLimit": posted_limit}
        if pagination_token:
            payload["paginationToken"] = pagination_token
        return self._call(TOOL_POSTS, payload, deadline_monotonic, parse_posts)

    def find_profiles(
        self,
        *,
        deadline_monotonic: Optional[float],
        full_name: Optional[str] = None,
        company_name: Optional[str] = None,
        company_domain: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Look up profile URLs by name plus company (or email).

        ``data`` is ``parse_lookup`` output.
        """
        payload = {
            key: value.strip()
            for key, value in (
                ("full_name", full_name),
                ("company_name", company_name),
                ("company_domain", company_domain),
                ("email", email),
            )
            if isinstance(value, str) and value.strip()
        }
        return self._call(TOOL_LOOKUP, payload, deadline_monotonic, parse_lookup)

    # -- transport ------------------------------------------------------

    def _record(self, tool: str, status: str) -> None:
        self.events.append({"tool": tool, "status": status})

    def _finish(self, tool: str, result: Dict[str, Any], started: float) -> Dict[str, Any]:
        self._record(tool, result["status"])
        _log(f"{tool}: {result['status']} ({time.monotonic() - started:.1f}s)")
        return result

    def _call(self, tool, payload, deadline_monotonic, parse) -> Dict[str, Any]:
        started = time.monotonic()
        if self.stopped:
            return self._finish(tool, _result(self.stopped, detail="Deepline stopped for this run"), started)
        if self.calls_made(tool) >= self.caps.get(tool, 0):
            return self._finish(tool, _result(OVER_BUDGET, detail="per-run call cap reached"), started)
        if not self._time_for_attempt(deadline_monotonic):
            return self._finish(tool, _result(TIMEOUT, detail="not enough time left to start"), started)

        self._calls[tool] = self.calls_made(tool) + 1
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key(self.run_id, tool, payload),
        }
        url = f"{API_BASE}/{tool}/execute"
        sensitive = "email" in payload

        outcome: Any = None
        for attempt in range(2):
            if attempt and not self._time_for_attempt(deadline_monotonic):
                break
            outcome = self._attempt(url, payload, headers, deadline_monotonic, sensitive)
            if not (isinstance(outcome, tuple) and outcome[0] is _RETRYABLE):
                break
        if isinstance(outcome, tuple) and outcome[0] is _RETRYABLE:
            return self._finish(tool, outcome[1], started)
        if outcome["status"] != OK:
            return self._finish(tool, outcome, started)

        response = outcome["data"]
        if not isinstance(response, dict) or _payload_data(response) is None:
            return self._finish(tool, _result(ERROR, detail="unexpected response shape"), started)
        return self._finish(tool, _result(OK, parse(response)), started)

    @staticmethod
    def _time_for_attempt(deadline_monotonic: Optional[float]) -> bool:
        if deadline_monotonic is None:
            return True
        return deadline_monotonic - time.monotonic() >= MIN_ATTEMPT_SECONDS

    def _attempt(self, url, payload, headers, deadline_monotonic, sensitive):
        attempt_deadline = time.monotonic() + ATTEMPT_TIMEOUT_SECONDS
        if deadline_monotonic is not None:
            attempt_deadline = min(attempt_deadline, deadline_monotonic)
        try:
            response = http.request(
                "POST",
                url,
                headers=dict(headers),
                json_data={"payload": payload},
                timeout=ATTEMPT_TIMEOUT_SECONDS,
                retries=1,
                max_429_retries=1,
                deadline_monotonic=attempt_deadline,
                sensitive_url=sensitive,
            )
        except http.HTTPError as exc:
            return self._failure(exc)
        return _result(OK, response)

    def _failure(self, exc: "http.HTTPError"):
        code = exc.status_code
        state = exc.outcome_state
        detail = f"HTTP {code}" if code else state
        if state == health.PAYMENT_REQUIRED or code == 402:
            self.stopped = OUT_OF_CREDITS
            return _result(OUT_OF_CREDITS, detail=detail)
        if state == health.AUTH_FAILED:
            return _result(AUTH_FAILED, detail=detail)
        if state == health.RATE_LIMITED:
            return _result(RATE_LIMITED, detail=detail)
        if state == health.TIMEOUT:
            return (_RETRYABLE, _result(TIMEOUT, detail=detail))
        if code is not None and 500 <= code < 600:
            return (_RETRYABLE, _result(ERROR, detail=detail))
        return _result(ERROR, detail=detail)


# -- parsing ------------------------------------------------------------------


def _payload_data(response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Tool data from an execute response.

    The HTTP API puts it under ``result.data``; older envelopes used
    ``toolResponse.rawV2`` (under ``result`` or at the top level).
    """
    if response.get("status") not in (None, "completed"):
        return None
    result = response.get("result")
    candidates = []
    if isinstance(result, dict):
        candidates.append(result.get("data"))
        tool_response = result.get("toolResponse")
        if isinstance(tool_response, dict):
            candidates.append(tool_response.get("rawV2"))
    tool_response = response.get("toolResponse")
    if isinstance(tool_response, dict):
        candidates.append(tool_response.get("rawV2"))
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _date_text(value: Any) -> str:
    if isinstance(value, dict):
        text = _text(value.get("text"))
        if text:
            return text
        parts = [str(value[k]) for k in ("month", "year") if value.get(k) not in (None, "")]
        return " ".join(parts)
    return _text(value)


def _role(entry: Dict[str, Any]) -> Dict[str, Any]:
    role = {
        "title": _text(entry.get("position") or entry.get("title")),
        "company": _text(entry.get("companyName")),
        "company_linkedin_url": _text(entry.get("companyLinkedinUrl")) or None,
        "company_universal_name": _text(entry.get("companyUniversalName")) or None,
        "start_date": _date_text(entry.get("startDate")) or None,
        "end_date": _date_text(entry.get("endDate")) or None,
    }
    website = _text(entry.get("companyWebsite") or entry.get("companyWebsiteUrl"))
    if website:
        role["company_website"] = website
    return role


def _roles(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [_role(entry) for entry in raw if isinstance(entry, dict)]


def _url_list(raw: Any) -> List[str]:
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    urls: List[str] = []
    for item in raw:
        url = _text(item.get("url") if isinstance(item, dict) else item)
        if url and url not in urls:
            urls.append(url)
    return urls


def _location(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if not isinstance(raw, dict):
        return ""
    text = _text(raw.get("linkedinText"))
    if text:
        return text
    parsed = raw.get("parsed")
    if isinstance(parsed, dict):
        return ", ".join(_text(parsed.get(k)) for k in ("city", "state", "country") if _text(parsed.get(k)))
    return ""


def parse_profile(response: Dict[str, Any]) -> Dict[str, Any]:
    """Profile facts from a ``harvestapi_get_profile`` response."""
    data = _payload_data(response) or {}
    element = data.get("element") if isinstance(data.get("element"), dict) else data
    current = _roles(element.get("currentPosition"))
    history = _roles(element.get("experience"))
    company_urls: List[str] = []
    websites = _url_list(element.get("websites"))
    for role in current + history:
        url = role.get("company_linkedin_url")
        if url and url not in company_urls:
            company_urls.append(url)
        site = role.get("company_website")
        if site and site not in websites:
            websites.append(site)
    name = " ".join(p for p in (_text(element.get("firstName")), _text(element.get("lastName"))) if p)
    return {
        "name": name,
        "headline": _text(element.get("headline")),
        "linkedin_url": _text(element.get("linkedinUrl")) or None,
        "public_identifier": _text(element.get("publicIdentifier")) or None,
        "about": _text(element.get("about")),
        "location": _location(element.get("location")),
        "current_roles": current,
        "history": history,
        "websites": websites,
        "company_linkedin_urls": company_urls,
    }


def _post_date(posted_at: Any) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """(ISO timestamp, YYYY-MM-DD, epoch ms) from a ``postedAt`` object."""
    if not isinstance(posted_at, dict):
        return None, None, None
    stamp = posted_at.get("timestamp")
    stamp = stamp if isinstance(stamp, int) and not isinstance(stamp, bool) else None
    iso = _text(posted_at.get("date")) or None
    if iso is None and stamp is not None:
        iso = datetime.datetime.fromtimestamp(stamp / 1000, tz=datetime.timezone.utc).isoformat()
    day = None
    if iso:
        try:
            day = datetime.date.fromisoformat(iso[:10]).isoformat()
        except ValueError:
            day = None
    return iso, day, stamp


def parse_posts(response: Dict[str, Any]) -> Dict[str, Any]:
    """Posts and the next-page token from a ``harvestapi_get_profile_posts`` response."""
    data = _payload_data(response) or {}
    elements = data.get("elements")
    posts: List[Dict[str, Any]] = []
    for element in elements if isinstance(elements, list) else []:
        if not isinstance(element, dict):
            continue
        repost = element.get("repost")
        is_reshare = bool(repost) or bool(element.get("repostedBy"))
        text = _text(element.get("content"))
        if not text and isinstance(repost, dict):
            text = _text(repost.get("content"))
        posted_at, day, stamp = _post_date(element.get("postedAt"))
        author = element.get("author") if isinstance(element.get("author"), dict) else {}
        engagement = element.get("engagement") if isinstance(element.get("engagement"), dict) else {}
        posts.append({
            "id": str(element.get("id") or "") or None,
            "url": _text(element.get("linkedinUrl")) or None,
            "text": text,
            "posted_at": posted_at,
            "date": day,
            "timestamp": stamp,
            "is_reshare": is_reshare,
            "author": _text(author.get("name")),
            "author_url": _text(author.get("linkedinUrl")) or None,
            "author_id": _text(author.get("publicIdentifier")) or None,
            "engagement": {
                key: engagement[key]
                for key in ("likes", "comments", "shares")
                if isinstance(engagement.get(key), int)
            },
        })
    pagination = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
    return {"posts": posts, "next_token": _text(pagination.get("paginationToken")) or None}


def parse_lookup(response: Dict[str, Any]) -> Dict[str, Any]:
    """Candidate profile URLs from a ``limadata_find_person_profiles`` response."""
    data = _payload_data(response) or {}
    return {key: _text(data.get(key)) or None for key in ("linkedin_url", "github_url", "x_url")}

"""Deepline client: request shape, parsing, retries, budgets, and key plumbing.

HTTP is mocked at ``urllib.request.urlopen``; assertions read what the client
sends across that boundary and what it returns.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request

import pytest

from lib import deepline, doctor, env

PROFILE_URL = "https://www.linkedin.com/in/dana-okafor-nwf"
EMAIL = "dana.okafor@northwind.example"


def _envelope(data: dict) -> dict:
    """The live execute response: data under ``result.data``."""
    return {
        "job_id": "job_fixture_1",
        "status": "completed",
        "result": {"data": data},
        "billing": {"credits_charged": 0.2, "cost_usd": 0.02},
        "executionRecovery": {"idempotencyKey": "k", "state": "completed", "replayed": False},
    }


PROFILE_DATA = {
    "element": {
        "firstName": "Dana",
        "lastName": "Okafor",
        "headline": "VP Operations at Northwind Freight",
        "linkedinUrl": PROFILE_URL,
        "publicIdentifier": "dana-okafor-nwf",
        "about": "Freight operations, cold chain, and late-night dispatch stories.",
        "location": {
            "linkedinText": "Columbus, Ohio, United States",
            "parsed": {"city": "Columbus", "state": "Ohio", "country": "United States"},
        },
        "currentPosition": [
            {
                "position": "VP Operations",
                "companyName": "Northwind Freight",
                "companyLinkedinUrl": "https://www.linkedin.com/company/northwind-freight/",
                "companyUniversalName": "northwind-freight",
                "startDate": {"month": "Mar", "year": 2024, "text": "Mar 2024"},
                "endDate": {"text": "Present"},
            },
            {
                "position": "Board Member",
                "companyName": "Lakeside Food Bank",
                "startDate": {"text": "2022"},
            },
        ],
        "experience": [
            {
                "position": "VP Operations",
                "companyName": "Northwind Freight",
                "companyLinkedinUrl": "https://www.linkedin.com/company/northwind-freight/",
                "startDate": {"text": "Mar 2024"},
                "endDate": {"text": "Present"},
            },
            {
                "position": "Director of Dispatch",
                "companyName": "Contoso Logistics",
                "companyLinkedinUrl": "https://www.linkedin.com/company/contoso-logistics/",
                "startDate": {"text": "Jan 2019"},
                "endDate": {"text": "Feb 2024"},
            },
        ],
        "websites": [{"url": "https://danaokafor.example"}, "https://northwind.example/team/dana"],
    }
}

POSTS_DATA = {
    "elements": [
        {
            "id": "7300000000000000001",
            "linkedinUrl": "https://www.linkedin.com/posts/dana-okafor-nwf_activity-1",
            "content": "We cut dock wait times by 30% this quarter.",
            "postedAt": {
                "timestamp": 1762000000000,
                "date": "2025-11-01T12:26:40.000Z",
                "postedAgoShort": "2w",
            },
            "author": {
                "publicIdentifier": "dana-okafor-nwf",
                "name": "Dana Okafor",
                "linkedinUrl": PROFILE_URL,
            },
            "repost": None,
            "engagement": {"likes": 120, "comments": 14, "shares": 3},
        },
        {
            "id": "7300000000000000002",
            "linkedinUrl": "https://www.linkedin.com/posts/dana-okafor-nwf_activity-2",
            "content": "",
            "postedAt": {"timestamp": 1761000000000},
            "author": {"publicIdentifier": "dana-okafor-nwf", "name": "Dana Okafor"},
            "repost": {"id": "999", "content": "Northwind Freight opens a new hub."},
            "engagement": {"likes": 8},
        },
    ],
    "pagination": {"paginationToken": "page-2-token"},
}


class _Response:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def read(self):
        return self._body

    def geturl(self):
        return ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://code.deepline.com", code, "fixture", hdrs={}, fp=io.BytesIO(body.encode())
    )


class _Transport:
    """Scripted urlopen: each step is a payload dict or an exception."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[dict] = []

    def __call__(self, req, timeout=None):
        self.requests.append(
            {
                "url": req.full_url,
                "method": req.get_method(),
                "headers": {k.lower(): v for k, v in req.header_items()},
                "body": json.loads(req.data.decode("utf-8")) if req.data else None,
            }
        )
        step = self.steps.pop(0)
        if callable(step):
            step = step()
        if isinstance(step, BaseException):
            raise step
        return _Response(step)


@pytest.fixture
def transport(monkeypatch):
    def install(*steps):
        fake = _Transport(*steps)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return fake

    return install


def _deadline(seconds: float = 60.0) -> float:
    return time.monotonic() + seconds


def _client(run_id: str = "run-fixture") -> deepline.DeeplineClient:
    return deepline.DeeplineClient("dl-test-key", run_id)


# -- request shape and parsing ------------------------------------------------


def test_profile_read_sends_execute_request_and_parses_roles(transport):
    fake = transport(_envelope(PROFILE_DATA))

    result = _client().get_profile(PROFILE_URL, deadline_monotonic=_deadline())

    sent = fake.requests[0]
    assert sent["method"] == "POST"
    assert sent["url"] == (
        "https://code.deepline.com/api/v2/integrations/harvestapi_get_profile/execute"
    )
    assert sent["headers"]["authorization"] == "Bearer dl-test-key"
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["headers"]["idempotency-key"]
    assert sent["body"] == {"payload": {"url": PROFILE_URL}}

    assert result["status"] == "ok"
    profile = result["data"]
    assert profile["name"] == "Dana Okafor"
    assert profile["headline"] == "VP Operations at Northwind Freight"
    assert profile["location"] == "Columbus, Ohio, United States"
    assert profile["linkedin_url"] == PROFILE_URL
    assert profile["current_roles"][0] == {
        "title": "VP Operations",
        "company": "Northwind Freight",
        "company_linkedin_url": "https://www.linkedin.com/company/northwind-freight/",
        "company_universal_name": "northwind-freight",
        "start_date": "Mar 2024",
        "end_date": "Present",
    }
    assert profile["current_roles"][1]["company"] == "Lakeside Food Bank"
    assert profile["current_roles"][1]["start_date"] == "2022"
    assert [r["company"] for r in profile["history"]] == [
        "Northwind Freight",
        "Contoso Logistics",
    ]
    assert profile["history"][1]["end_date"] == "Feb 2024"
    assert profile["websites"] == [
        "https://danaokafor.example",
        "https://northwind.example/team/dana",
    ]
    assert profile["company_linkedin_urls"] == [
        "https://www.linkedin.com/company/northwind-freight/",
        "https://www.linkedin.com/company/contoso-logistics/",
    ]


def test_profile_parse_tolerates_missing_fields_and_legacy_envelope():
    legacy = {"result": {"toolResponse": {"rawV2": {"element": {"firstName": "Dana"}}}}}
    profile = deepline.parse_profile(legacy)
    assert profile["name"] == "Dana"
    assert profile["current_roles"] == []
    assert profile["history"] == []
    assert profile["websites"] == []
    assert profile["location"] == ""

    parsed_only = {"result": {"data": {"element": {"location": {"parsed": {"city": "Columbus", "country": "United States"}}, "websites": None}}}}
    assert deepline.parse_profile(parsed_only)["location"] == "Columbus, United States"


def test_posts_read_parses_date_url_text_and_reshare_flag(transport):
    fake = transport(_envelope(POSTS_DATA))

    result = _client().get_posts(PROFILE_URL, deadline_monotonic=_deadline())

    sent = fake.requests[0]
    assert sent["url"].endswith("/integrations/harvestapi_get_profile_posts/execute")
    assert sent["body"] == {"payload": {"profile": PROFILE_URL, "scrapePostedLimit": "year"}}

    assert result["status"] == "ok"
    posts = result["data"]["posts"]
    assert result["data"]["next_token"] == "page-2-token"
    first, second = posts
    assert first["id"] == "7300000000000000001"
    assert first["url"] == "https://www.linkedin.com/posts/dana-okafor-nwf_activity-1"
    assert first["text"] == "We cut dock wait times by 30% this quarter."
    assert first["date"] == "2025-11-01"
    assert first["timestamp"] == 1762000000000
    assert first["is_reshare"] is False
    assert first["engagement"]["likes"] == 120
    # Date derived from the millisecond timestamp when no ISO date is given.
    assert second["date"] == "2025-10-20"
    assert second["is_reshare"] is True
    assert second["text"] == "Northwind Freight opens a new hub."


def test_posts_second_page_sends_pagination_token(transport):
    fake = transport(_envelope({"elements": []}))
    result = _client().get_posts(
        PROFILE_URL, deadline_monotonic=_deadline(), pagination_token="page-2-token"
    )
    assert fake.requests[0]["body"]["payload"]["paginationToken"] == "page-2-token"
    assert result["data"] == {"posts": [], "next_token": None}


def test_lookup_parses_candidate_urls(transport):
    fake = transport(_envelope({"linkedin_url": PROFILE_URL, "github_url": "", "x_url": None}))

    result = _client().find_profiles(
        full_name="Dana Okafor",
        company_name="Northwind Freight",
        deadline_monotonic=_deadline(),
    )

    assert fake.requests[0]["url"].endswith("/integrations/limadata_find_person_profiles/execute")
    assert fake.requests[0]["body"] == {
        "payload": {"full_name": "Dana Okafor", "company_name": "Northwind Freight"}
    }
    assert result["status"] == "ok"
    assert result["data"] == {"linkedin_url": PROFILE_URL, "github_url": None, "x_url": None}


# -- idempotency, retries, deadlines -------------------------------------------


def test_timeout_then_success_reuses_the_same_idempotency_key(transport):
    fake = transport(TimeoutError("timed out"), _envelope(PROFILE_DATA))

    result = _client().get_profile(PROFILE_URL, deadline_monotonic=_deadline())

    assert result["status"] == "ok"
    assert len(fake.requests) == 2
    keys = {r["headers"]["idempotency-key"] for r in fake.requests}
    assert len(keys) == 1


def test_5xx_is_retried_once_then_reported(transport):
    fake = transport(_http_error(502), _http_error(503))
    result = _client().get_profile(PROFILE_URL, deadline_monotonic=_deadline())
    assert len(fake.requests) == 2
    assert result["status"] == "error"
    assert result["data"] is None


def test_no_retry_when_time_left_cannot_cover_a_fresh_attempt(transport, monkeypatch):
    monkeypatch.setattr(deepline, "MIN_ATTEMPT_SECONDS", 0.5)

    def slow_timeout():
        time.sleep(0.3)
        raise TimeoutError("timed out")

    fake = transport(slow_timeout, _envelope(PROFILE_DATA))
    result = _client().get_profile(PROFILE_URL, deadline_monotonic=time.monotonic() + 0.7)
    assert len(fake.requests) == 1
    assert result["status"] == "timeout"


def test_call_is_skipped_when_deadline_is_too_close(transport):
    fake = transport(_envelope(PROFILE_DATA))
    client = _client()
    result = client.get_profile(PROFILE_URL, deadline_monotonic=time.monotonic() + 1.0)
    assert fake.requests == []
    assert result["status"] == "timeout"
    # A skipped call spends no budget.
    assert client.calls_made(deepline.TOOL_PROFILE) == 0


def test_idempotency_key_depends_on_run_tool_and_input_and_hides_email(transport):
    fake = transport(*[_envelope({"linkedin_url": ""})] * 3)
    deepline.DeeplineClient("k", "run-a").find_profiles(email=EMAIL, deadline_monotonic=_deadline())
    deepline.DeeplineClient("k", "run-b").find_profiles(email=EMAIL, deadline_monotonic=_deadline())
    deepline.DeeplineClient("k", "run-a").find_profiles(
        email="other@northwind.example", deadline_monotonic=_deadline()
    )
    keys = [r["headers"]["idempotency-key"] for r in fake.requests]
    assert len(set(keys)) == 3
    for key in keys:
        assert "@" not in key and "dana" not in key.lower()


# -- named failure states, stop rules, and caps ---------------------------------


@pytest.mark.parametrize(
    ("code", "status"),
    [(401, "auth failed"), (429, "rate limited"), (402, "out of credits")],
)
def test_http_failures_map_to_named_states(transport, code, status):
    transport(_http_error(code, '{"error": "fixture"}'))
    result = _client().get_profile(PROFILE_URL, deadline_monotonic=_deadline())
    assert result == {"status": status, "data": None, "detail": result["detail"]}


def test_402_stops_further_deepline_calls_for_the_run(transport):
    fake = transport(_http_error(402, '{"error": "insufficient credits"}'), _envelope(POSTS_DATA))
    client = _client()

    first = client.get_profile(PROFILE_URL, deadline_monotonic=_deadline())
    second = client.get_posts(PROFILE_URL, deadline_monotonic=_deadline())

    assert first["status"] == "out of credits"
    assert second["status"] == "out of credits"
    assert len(fake.requests) == 1
    assert client.stopped == "out of credits"
    assert {"tool": deepline.TOOL_PROFILE, "status": "out of credits"} in client.events


def test_budget_caps_refuse_second_lookup_and_third_profile_read(transport):
    fake = transport(
        _envelope({"linkedin_url": PROFILE_URL}),
        _envelope(PROFILE_DATA),
        _envelope(PROFILE_DATA),
    )
    client = _client()

    assert client.find_profiles(
        full_name="Dana Okafor", company_name="Northwind Freight", deadline_monotonic=_deadline()
    )["status"] == "ok"
    second_lookup = client.find_profiles(
        full_name="Dana Okafor", company_domain="northwind.example", deadline_monotonic=_deadline()
    )
    assert client.get_profile(PROFILE_URL, deadline_monotonic=_deadline())["status"] == "ok"
    assert client.get_profile(PROFILE_URL + "-2", deadline_monotonic=_deadline())["status"] == "ok"
    third_read = client.get_profile(PROFILE_URL + "-3", deadline_monotonic=_deadline())

    assert second_lookup["status"] == "over budget"
    assert third_read["status"] == "over budget"
    assert len(fake.requests) == 3
    assert {"tool": deepline.TOOL_LOOKUP, "status": "over budget"} in client.events


def test_post_pages_are_capped_at_two(transport):
    fake = transport(_envelope(POSTS_DATA), _envelope({"elements": []}))
    client = _client()
    client.get_posts(PROFILE_URL, deadline_monotonic=_deadline())
    client.get_posts(PROFILE_URL, deadline_monotonic=_deadline(), pagination_token="page-2-token")
    third = client.get_posts(PROFILE_URL, deadline_monotonic=_deadline(), pagination_token="p3")
    assert third["status"] == "over budget"
    assert len(fake.requests) == 2


def test_unexpected_response_shape_is_an_error_not_an_exception(transport):
    transport({"status": "failed", "error": "upstream"})
    result = _client().get_profile(PROFILE_URL, deadline_monotonic=_deadline())
    assert result["status"] == "error"
    assert result["data"] is None


# -- privacy -------------------------------------------------------------------


def test_email_never_appears_in_log_output(transport, monkeypatch, capsys):
    monkeypatch.setenv("LAST30DAYS_DEBUG", "1")
    transport(
        _http_error(500, json.dumps({"error": "bad input", "email": EMAIL})),
        _http_error(500, json.dumps({"error": "bad input", "email": EMAIL})),
    )
    result = _client().find_profiles(email=EMAIL, deadline_monotonic=_deadline())
    assert result["status"] == "error"
    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert "Deepline" in output  # the client does log the call
    assert EMAIL not in output
    assert "dana.okafor" not in output


# -- key plumbing: run and doctor read the same availability -------------------


def test_env_file_key_is_seen_by_run_and_doctor():
    env.CONFIG_FILE.write_text("DEEPLINE_API_KEY=dl-from-dotenv\n", encoding="utf-8")
    env.CONFIG_FILE.chmod(0o600)

    config = env.get_config()

    assert config["DEEPLINE_API_KEY"] == "dl-from-dotenv"
    assert deepline.is_available(config) is True
    client = deepline.from_config(config, "run-fixture")
    assert client is not None and client.api_key == "dl-from-dotenv"
    record = doctor._SOURCE_BUILDERS["deepline"](config)
    assert record["status"] == "ok"
    assert "deepline" in doctor.SOURCE_ORDER


def test_doctor_reports_deepline_unconfigured_without_key():
    config = env.get_config()
    assert deepline.is_available(config) is False
    assert deepline.from_config(config, "run-fixture") is None
    record = doctor._SOURCE_BUILDERS["deepline"](config)
    assert record["status"] == "unconfigured"
    assert "DEEPLINE_API_KEY" in record["fix"]

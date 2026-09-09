"""xapi: the direct X API v2 backend and the shared v2 parser (U4).

Every test mocks ``lib.http.get`` (or urlopen for the fixture seam); no test
makes a live X call. The dummy token below is obvious fake data and must
never appear in any error string, source_log line, or recorded fixture.
"""

from __future__ import annotations

import io
import json
import threading
from contextlib import redirect_stderr
from datetime import datetime, timezone
from unittest import mock
from unittest.mock import MagicMock

import pytest

from lib import env, health, http, pipeline, schema, x_api, xurl_x

DUMMY_TOKEN = "dummy-x-bearer-secret-000"
ACCOUNT_ID = "acct-1234567890"
FIXED_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
FROM = "2026-08-10"
TO = "2026-09-08"


def _tweet(id_, text, author_id="u1", created_at="2026-08-20T12:00:00Z", metrics=None, **extra):
    t = {"id": id_, "text": text, "author_id": author_id}
    if created_at:
        t["created_at"] = created_at
    if metrics is not None:
        t["public_metrics"] = metrics
    t.update(extra)
    return t


def _v2(tweets, users=None, next_token=None):
    resp = {"data": tweets, "meta": {"result_count": len(tweets)}}
    if users:
        resp["includes"] = {"users": users}
    if next_token:
        resp["meta"]["next_token"] = next_token
    return resp


def _users(*pairs):
    return [{"id": uid, "username": name} for uid, name in pairs]


def _http_error(status, body="", message=None):
    return http.HTTPError(message or f"HTTP {status}: Error", status_code=status, body=body)


@pytest.fixture
def fixed_now(monkeypatch):
    monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
    return FIXED_NOW


@pytest.fixture
def get_mock(monkeypatch):
    m = MagicMock(name="http.get")
    monkeypatch.setattr(x_api.http, "get", m)
    return m


def _params(call):
    return call.kwargs.get("params") or {}


def _url(call):
    return call.args[0] if call.args else call.kwargs["url"]


# ---------------------------------------------------------------------------
# parse_v2_response
# ---------------------------------------------------------------------------


class TestParseV2Response:
    def test_fixture_with_users_parses_all_fields(self):
        metrics = {
            "like_count": 42, "retweet_count": 10, "reply_count": 5,
            "quote_count": 2, "bookmark_count": 7, "impression_count": 9000,
        }
        resp = _v2(
            [_tweet("1956158892141441450", "Claude Code agents are great", metrics=metrics)],
            users=_users(("u1", "alice")),
        )
        items = x_api.parse_v2_response(resp, "Claude Code", (FROM, TO))
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "XAPI1"
        assert item["post_id"] == "1956158892141441450"
        assert item["author_handle"] == "alice"
        assert item["url"] == "https://x.com/alice/status/1956158892141441450"
        assert item["date"] == "2026-08-20"
        assert item["engagement"] == {
            "likes": 42, "reposts": 10, "replies": 5, "quotes": 2,
            "bookmarks": 7, "views": 9000,
        }
        assert item["why_relevant"] == ""
        assert item["relevance"] > 0.5
        assert item["mentioned_handles"] == []

    def test_without_users_keeps_item_with_i_status_url(self):
        resp = _v2([_tweet("999", "text here", author_id="unknown")])
        items = x_api.parse_v2_response(resp, "text", (FROM, TO))
        assert len(items) == 1
        assert items[0]["url"] == "https://x.com/i/status/999"
        assert items[0]["author_handle"] == ""

    def test_note_tweet_replaces_truncated_text(self):
        resp = _v2([_tweet("5", "truncated...", note_tweet={"text": "the full long-form post text"})])
        items = x_api.parse_v2_response(resp, "", None)
        assert items[0]["text"] == "the full long-form post text"

    def test_url_is_built_from_id_never_from_response_url_fields(self):
        resp = _v2(
            [_tweet("7", "hi", url="https://evil.example/phish",
                    entities={"urls": [{"expanded_url": "https://evil.example/x"}]})],
            users=_users(("u1", "bob")),
        )
        items = x_api.parse_v2_response(resp, "", None)
        assert items[0]["url"] == "https://x.com/bob/status/7"
        assert "evil.example" not in json.dumps(items)

    def test_username_outside_handle_grammar_falls_back_to_i_status(self):
        resp = _v2([_tweet("8", "hi")], users=[{"id": "u1", "username": "bad name/../x"}])
        items = x_api.parse_v2_response(resp, "", None)
        assert items[0]["author_handle"] == ""
        assert items[0]["url"] == "https://x.com/i/status/8"

    def test_leading_mentions_become_mentioned_handles(self):
        resp = _v2([_tweet("9", "@steipete @alice thanks for this")])
        items = x_api.parse_v2_response(resp, "", None)
        assert items[0]["mentioned_handles"] == ["steipete", "alice"]

    def test_engagement_none_without_metrics_and_bookmarks_views_only_when_present(self):
        resp = _v2([
            _tweet("1", "a"),
            _tweet("2", "b", metrics={"like_count": 1, "retweet_count": 0, "reply_count": 0, "quote_count": 0}),
        ])
        items = x_api.parse_v2_response(resp, "", None)
        assert items[0]["engagement"] is None
        assert items[1]["engagement"] == {"likes": 1, "reposts": 0, "replies": 0, "quotes": 0}

    def test_non_numeric_ids_are_dropped(self):
        resp = _v2([_tweet("abc", "x"), _tweet("12", "y")])
        items = x_api.parse_v2_response(resp, "", None)
        assert [i["post_id"] for i in items] == ["12"]

    def test_window_drops_dated_items_outside_it(self):
        resp = _v2([
            _tweet("1", "old", created_at="2026-07-01T00:00:00Z"),
            _tweet("2", "in", created_at="2026-08-20T00:00:00Z"),
        ])
        items = x_api.parse_v2_response(resp, "", (FROM, TO))
        assert [i["post_id"] for i in items] == ["2"]

    def test_error_response_yields_nothing(self):
        assert x_api.parse_v2_response({"error": "x"}, "", None) == []
        assert x_api.parse_v2_response({}, "", None) == []

    def test_xurl_delegates_to_shared_parser_and_depth_config(self):
        resp = _v2([_tweet("3", "hello")], users=_users(("u1", "alice")))
        items = xurl_x.parse_x_response(resp, topic="hello")
        assert items[0]["id"] == "XURL1"
        assert items[0]["url"] == "https://x.com/alice/status/3"
        assert items[0]["post_id"] == "3"
        assert xurl_x.DEPTH_CONFIG is x_api.DEPTH_CONFIG
        assert x_api.DEPTH_CONFIG == {"quick": 10, "default": 30, "deep": 60}


# ---------------------------------------------------------------------------
# Query compilation (R8a)
# ---------------------------------------------------------------------------


class TestBuildQuery:
    def test_wraps_core_in_one_quote_pair_and_appends_no_retweets(self):
        q = x_api.build_query('Claude Code "agents"')
        assert q == '"Claude Code agents" -is:retweet'

    def test_operator_injection_is_stripped(self):
        q = x_api.build_query('foo" OR from:attacker since:2015-01-01 "')
        assert q == '"foo" -is:retweet'
        assert "from:" not in q and "since:" not in q and " OR " not in q
        assert q.count('"') == 2

    def test_negation_and_grouping_characters_are_stripped(self):
        q = x_api.build_query("(Peter Steinberger) -steipete “quoted” [x] {y}")
        assert q == '"Peter Steinberger quoted x y" -is:retweet'

    def test_600_char_topic_compiles_under_512_with_balanced_quotes(self):
        topic = " ".join(f"word{i}" for i in range(100))
        assert len(topic) >= 600
        q = x_api.build_query(topic)
        assert len(q) <= x_api.MAX_QUERY_CHARS
        assert q.count('"') == 2
        assert q.endswith('" -is:retweet')
        assert not q.startswith('"word')  or q.startswith('"word0 ')
        # Cut at a token boundary: the last kept token is intact.
        core = q[1:q.index('" -is:retweet')]
        assert all(tok.startswith("word") and tok[4:].isdigit() for tok in core.split())

    def test_single_giant_token_is_truncated_not_dropped(self):
        q = x_api.build_query("a" * 700)
        assert 0 < len(q) <= x_api.MAX_QUERY_CHARS
        assert q.count('"') == 2

    def test_only_operators_yields_empty_query(self):
        assert x_api.build_query("from:attacker OR -is:reply lang:en") == ""


# ---------------------------------------------------------------------------
# Request shape (KTD4)
# ---------------------------------------------------------------------------


class TestRequestShape:
    def test_full_archive_request_shape(self, get_mock, fixed_now):
        get_mock.return_value = _v2([_tweet("1", "Claude Code rocks")], users=_users(("u1", "a")))
        result = x_api.search_x(DUMMY_TOKEN, "Claude Code", FROM, TO, depth="quick")
        assert "error" not in result
        assert [i["id"] for i in result["items"]] == ["XAPI1"]
        call = get_mock.call_args
        assert _url(call) == "https://api.x.com/2/tweets/search/all"
        p = _params(call)
        assert p["query"] == '"Claude Code" -is:retweet'
        assert p["start_time"] == "2026-08-10T00:00:00Z"
        # to_date is "today" relative to the fixed clock: end_time keeps a
        # 30 second safety margin before now.
        assert p["end_time"] == "2026-09-08T11:59:30Z"
        assert p["sort_order"] == "recency"
        assert p["max_results"] == 10
        assert p["expansions"] == "author_id"
        assert p["tweet.fields"] == "created_at,public_metrics,note_tweet,entities"
        assert p["user.fields"] == "username"
        assert "lang:" not in p["query"]
        assert call.kwargs["headers"]["Authorization"] == f"Bearer {DUMMY_TOKEN}"
        assert call.kwargs["timeout"] == 30
        assert call.kwargs["retries"] == 2

    def test_past_end_date_uses_end_of_day(self, get_mock, fixed_now):
        get_mock.return_value = _v2([])
        x_api.search_x(DUMMY_TOKEN, "topic", "2026-08-01", "2026-08-31", depth="quick")
        assert _params(get_mock.call_args)["end_time"] == "2026-08-31T23:59:59Z"

    def test_max_results_clamped_to_10_and_100(self, get_mock, fixed_now, monkeypatch):
        get_mock.return_value = _v2([])
        x_api.search_handles(["steipete"], "t", FROM, TO, count_per=3, token=DUMMY_TOKEN)
        assert _params(get_mock.call_args)["max_results"] == 10
        monkeypatch.setitem(x_api.DEPTH_CONFIG, "deep", 150)
        x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="deep")
        assert _params(get_mock.call_args)["max_results"] == 100

    def test_pagination_stops_at_depth_count(self, get_mock, fixed_now):
        page = lambda start, n, nxt: _v2(
            [_tweet(str(1000 + start + i), f"post {i}") for i in range(n)], next_token=nxt,
        )
        get_mock.side_effect = [page(0, 20, "p2"), page(20, 20, "p3"), page(40, 20, "p4")]
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="default")
        assert get_mock.call_count == 2, "30 posts reached after two pages; the third is never fetched"
        assert len(result["items"]) == 30
        assert "next_token" not in _params(get_mock.call_args_list[0])
        assert _params(get_mock.call_args_list[1])["next_token"] == "p2"
        assert len({i["id"] for i in result["items"]}) == 30

    def test_pagination_stops_at_the_wall_clock_deadline(self, get_mock, fixed_now, monkeypatch):
        page = lambda start, n, nxt: _v2(
            [_tweet(str(1000 + start + i), f"post {i}") for i in range(n)], next_token=nxt,
        )
        get_mock.side_effect = [page(0, 20, "p2"), page(20, 20, "p3"), page(40, 20, "p4")]
        clock = iter([0.0, x_api.DEADLINE_SECONDS + 1.0, x_api.DEADLINE_SECONDS + 2.0, x_api.DEADLINE_SECONDS + 3.0])
        monkeypatch.setattr(x_api.time, "monotonic", lambda: next(clock))
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="deep")
        assert get_mock.call_count == 1, "the first page always runs; the second is skipped past the deadline"
        assert len(result["items"]) == 20
        assert "error" not in result

    def test_pagination_stops_without_next_token(self, get_mock, fixed_now):
        get_mock.return_value = _v2([_tweet("1", "only one")])
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="deep")
        assert get_mock.call_count == 1
        assert len(result["items"]) == 1

    def test_every_request_targets_api_x_com_and_carries_no_token_outside_the_header(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _http_error(403, body='{"reason":"client-not-enrolled"}'),
            _v2([_tweet("1", "a")], next_token="n"),
            _v2([_tweet("2", "b")]),
        ]
        x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        x_api.search_mentions(["steipete"], FROM, TO, topic="t", token=DUMMY_TOKEN)
        assert get_mock.call_count >= 3
        for call in get_mock.call_args_list:
            url = _url(call)
            assert url.startswith("https://api.x.com/2/tweets/search/")
            assert DUMMY_TOKEN not in url
            assert DUMMY_TOKEN not in json.dumps(_params(call))
            headers = dict(call.kwargs["headers"])
            headers.pop("Authorization")
            assert DUMMY_TOKEN not in json.dumps(headers)

    def test_operator_injection_still_carries_engine_window(self, get_mock, fixed_now):
        get_mock.return_value = _v2([])
        x_api.search_x(DUMMY_TOKEN, 'foo" OR from:attacker since:2015-01-01 "', FROM, TO)
        p = _params(get_mock.call_args)
        assert p["query"] == '"foo" -is:retweet'
        assert p["start_time"] == "2026-08-10T00:00:00Z"
        assert p["end_time"] == "2026-09-08T11:59:30Z"

    def test_empty_query_after_sanitizing_makes_no_request(self, get_mock, fixed_now):
        result = x_api.search_x(DUMMY_TOKEN, "from:attacker OR", FROM, TO)
        assert result["items"] == []
        assert result["error"] == x_api.ERR_EMPTY_QUERY
        get_mock.assert_not_called()

    def test_missing_token_makes_no_request(self, get_mock):
        result = x_api.search_x("", "topic", FROM, TO)
        assert result["items"] == [] and result["error"]
        get_mock.assert_not_called()


# ---------------------------------------------------------------------------
# 403 enrollment fallback (KTD4)
# ---------------------------------------------------------------------------


class TestEnrollmentFallback:
    def test_enrollment_403_retries_recent_once_with_clamped_window(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _http_error(403, body='{"reason":"client-not-enrolled","detail":"...","client_id":"' + ACCOUNT_ID + '"}'),
            _v2([_tweet("1", "recent post", created_at="2026-09-05T00:00:00Z")], users=_users(("u1", "a"))),
        ]
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        assert get_mock.call_count == 2
        assert _url(get_mock.call_args_list[0]) == "https://api.x.com/2/tweets/search/all"
        assert _url(get_mock.call_args_list[1]) == "https://api.x.com/2/tweets/search/recent"
        p2 = _params(get_mock.call_args_list[1])
        assert p2["start_time"] == "2026-09-01T12:00:30Z", "start clamped to now minus 7 days"
        assert p2["end_time"] == "2026-09-08T11:59:30Z"
        assert "error" not in result
        assert result["warning"] == x_api.TRUNCATION_DETAIL == "window truncated to 7 days"
        assert len(result["items"]) == 1

    def test_enrollment_fallback_keeps_start_when_window_already_recent(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _http_error(403, body="not enrolled"),
            _v2([]),
        ]
        x_api.search_x(DUMMY_TOKEN, "topic", "2026-09-06", TO, depth="quick")
        assert _params(get_mock.call_args_list[1])["start_time"] == "2026-09-06T00:00:00Z"

    def test_plain_403_does_not_retry(self, get_mock, fixed_now):
        get_mock.side_effect = [_http_error(403, body="Forbidden")]
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        assert get_mock.call_count == 1
        assert result["items"] == []
        assert result["error"] == x_api.ERR_FORBIDDEN
        assert http.classify_failure(message=result["error"]) == health.AUTH_FAILED

    def test_enrollment_fallback_with_window_before_the_floor_sends_no_request(self, get_mock, fixed_now):
        """A window that ends before now-7d cannot be served by recent search:
        no second request with start_time after end_time."""
        get_mock.side_effect = [_http_error(403, body="client-not-enrolled")]
        result = x_api.search_x(DUMMY_TOKEN, "topic", "2026-08-01", "2026-08-20", depth="quick")
        assert get_mock.call_count == 1
        assert result == {"items": [], "warning": x_api.TRUNCATION_DETAIL}

    def test_second_403_on_recent_is_a_fixed_forbidden_error(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _http_error(403, body="client-not-enrolled"),
            _http_error(403, body="client-not-enrolled " + DUMMY_TOKEN),
        ]
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        assert get_mock.call_count == 2
        assert result["error"] == x_api.ERR_FORBIDDEN


# ---------------------------------------------------------------------------
# Fixed-string errors (R8, KTD6)
# ---------------------------------------------------------------------------

# A body that echoes the token and an account id but carries no status
# marker of its own, so the status code alone decides the fixed string.
_LEAKY_BODY = json.dumps({
    "title": "Client Error",
    "detail": f"request for account {ACCOUNT_ID} rejected; token {DUMMY_TOKEN}",
    "client_id": ACCOUNT_ID,
})


def _run_capturing_stderr(fn):
    buf = io.StringIO()
    with redirect_stderr(buf):
        with mock.patch("sys.stderr.isatty", return_value=False, create=True):
            out = fn()
    return out, buf.getvalue()


class TestFixedErrors:
    @pytest.mark.parametrize("status,expected,state", [
        (402, x_api.ERR_PAYMENT_REQUIRED, health.PAYMENT_REQUIRED),
        (403, x_api.ERR_FORBIDDEN, health.AUTH_FAILED),
        (401, x_api.ERR_UNAUTHORIZED, health.AUTH_FAILED),
        (429, x_api.ERR_RATE_LIMITED, health.RATE_LIMITED),
    ])
    def test_status_maps_to_fixed_string_without_body(self, get_mock, fixed_now, status, expected, state):
        get_mock.side_effect = _http_error(status, body=_LEAKY_BODY, message=f"HTTP {status}: {ACCOUNT_ID}")
        result, stderr = _run_capturing_stderr(
            lambda: x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        )
        assert result["items"] == []
        assert result["error"] == expected
        assert http.classify_failure(message=result["error"]) == state
        for leak in (DUMMY_TOKEN, ACCOUNT_ID, "Client Error", "request for account"):
            assert leak not in result["error"]
            assert leak not in stderr
        assert "[xapi]" in stderr or "xapi" in stderr

    def test_fixed_strings_are_the_planned_literals(self):
        assert x_api.ERR_PAYMENT_REQUIRED == "xapi: payment required (X API credits exhausted)"
        assert x_api.ERR_UNAUTHORIZED == "xapi: unauthorized (bearer token rejected)"
        assert x_api.ERR_FORBIDDEN == "xapi: forbidden (bearer token lacks access)"
        assert x_api.ERR_RATE_LIMITED == "xapi: rate limit exceeded (X API)"
        assert x_api.ERR_TIMED_OUT == "xapi: timed out"

    def test_credit_marker_in_body_maps_to_payment_required_on_any_status(self, get_mock, fixed_now):
        get_mock.side_effect = _http_error(403, body='{"detail":"insufficient credits"}')
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        assert result["error"] == x_api.ERR_PAYMENT_REQUIRED

    def test_other_http_status_is_fixed_http_n(self, get_mock, fixed_now):
        get_mock.side_effect = _http_error(503, body=_LEAKY_BODY)
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        assert result["error"] == "xapi: http 503"

    def test_timeout_is_fixed_string(self, get_mock, fixed_now):
        get_mock.side_effect = http.HTTPError(
            f"URL Error: timed out reading {ACCOUNT_ID}", outcome_state=health.TIMEOUT,
        )
        result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        assert result["error"] == x_api.ERR_TIMED_OUT
        assert http.classify_failure(message=result["error"]) == health.TIMEOUT

    def test_unexpected_exception_never_carries_its_message(self, get_mock, fixed_now):
        get_mock.side_effect = ValueError(f"boom {DUMMY_TOKEN} {ACCOUNT_ID}")
        result, stderr = _run_capturing_stderr(
            lambda: x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")
        )
        assert result["error"] == "xapi: request failed (ValueError)"
        assert DUMMY_TOKEN not in stderr and ACCOUNT_ID not in stderr

    @pytest.mark.parametrize("status,state", [(402, health.PAYMENT_REQUIRED), (403, health.AUTH_FAILED)])
    def test_pipeline_outcome_carries_only_the_fixed_string(self, get_mock, fixed_now, status, state):
        get_mock.side_effect = _http_error(status, body=_LEAKY_BODY, message=f"HTTP {status}: {ACCOUNT_ID}")
        sq = schema.SubQuery(label="primary", search_query="q", ranking_query="q?", sources=["x"])
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock",
            x_search_backend=None,
        )

        def run():
            with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]):
                with pytest.raises(pipeline.SourceRunError) as ctx:
                    pipeline._retrieve_stream(
                        topic="q", subquery=sq, source="x",
                        config={"X_BEARER_TOKEN": DUMMY_TOKEN},
                        depth="quick", date_range=(FROM, TO),
                        runtime=runtime, mock=False,
                    )
            return ctx.value

        exc, stderr = _run_capturing_stderr(run)
        assert exc.outcome_state == state
        rendered = str(exc)
        assert DUMMY_TOKEN not in rendered and ACCOUNT_ID not in rendered
        assert DUMMY_TOKEN not in stderr and ACCOUNT_ID not in stderr
        detail_state, attempted = pipeline._classify_source_failure(exc)
        assert (detail_state, attempted) == (state, True)


# ---------------------------------------------------------------------------
# Handle lanes (R7)
# ---------------------------------------------------------------------------


class TestHandleLanes:
    def test_from_lane_query_excludes_topic(self, get_mock, fixed_now):
        get_mock.return_value = _v2(
            [_tweet("1", "anything", created_at="2026-08-20T00:00:00Z")], users=_users(("u1", "steipete")),
        )
        items = x_api.search_handles(["@steipete"], "Grok 4", FROM, TO, count_per=8, token=DUMMY_TOKEN)
        p = _params(get_mock.call_args)
        assert p["query"] == "from:steipete -is:retweet"
        assert "Grok" not in p["query"]
        assert p["start_time"] == "2026-08-10T00:00:00Z"
        assert [i["id"] for i in items] == ["XF1"]
        assert items[0]["relevance"] >= 0

    def test_mention_lane_excludes_subjects_own_posts(self, get_mock, fixed_now):
        get_mock.return_value = _v2(
            [
                _tweet("1", "@steipete nice", author_id="fan"),
                _tweet("2", "my own post", author_id="me"),
            ],
            users=_users(("fan", "fanuser"), ("me", "steipete")),
        )
        items = x_api.search_mentions(["steipete"], FROM, TO, topic="Grok 4", count_per=5, token=DUMMY_TOKEN)
        p = _params(get_mock.call_args)
        assert p["query"] == "@steipete -from:steipete -is:retweet"
        assert {i["author_handle"] for i in items} == {"fanuser"}
        assert items[0]["id"].startswith("XA")

    def test_invalid_handle_skips_lane_with_receipt(self, get_mock, fixed_now):
        items, stderr = _run_capturing_stderr(
            lambda: x_api.search_handles(["x OR from:elonmusk"], "t", FROM, TO, token=DUMMY_TOKEN)
        )
        assert items == []
        get_mock.assert_not_called()
        assert "skip" in stderr.lower()
        mentions, _ = _run_capturing_stderr(
            lambda: x_api.search_mentions(["bad'; drop", "a" * 16], FROM, TO, token=DUMMY_TOKEN)
        )
        assert mentions == []
        get_mock.assert_not_called()

    def test_item_ids_unique_across_handles(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _v2([_tweet("1", "a")], users=_users(("u1", "h1"))),
            _v2([_tweet("2", "b")], users=_users(("u1", "h2"))),
        ]
        items = x_api.search_handles(["h1", "h2"], "topic", FROM, TO, token=DUMMY_TOKEN)
        ids = [i["id"] for i in items]
        assert len(ids) == len(set(ids)) == 2

    def test_fatal_auth_failure_stops_remaining_handles(self, get_mock, fixed_now):
        # Handles fan out on a bounded pool (five in flight), so the handles
        # already scheduled beside the failing one may still be called; the
        # ones beyond the pool window are not, and nothing leaks.
        handles = [f"h{i}" for i in range(1, 8)]
        get_mock.side_effect = _http_error(401, body=_LEAKY_BODY)
        items, stderr = _run_capturing_stderr(
            lambda: x_api.search_handles(handles, "topic", FROM, TO, token=DUMMY_TOKEN)
        )
        assert items == []
        assert 1 <= get_mock.call_count <= x_api._MAX_LANE_WORKERS
        called = {_params(c)["query"] for c in get_mock.call_args_list}
        assert called <= {f"from:{h} -is:retweet" for h in handles}
        assert DUMMY_TOKEN not in stderr and ACCOUNT_ID not in stderr

    def test_handle_results_merge_in_handle_order(self, get_mock, fixed_now):
        def _by_query(url, headers=None, params=None, **kwargs):
            handle = params["query"].split(":")[1].split(" ")[0]
            return _v2([_tweet(str(ord(handle[-1])), handle)], users=_users(("u1", handle)))

        get_mock.side_effect = _by_query
        handles = ["h3", "h1", "h2"]
        items = x_api.search_handles(handles, "topic", FROM, TO, token=DUMMY_TOKEN)
        assert [i["author_handle"] for i in items] == handles
        assert [i["id"] for i in items] == ["XF1", "XF2", "XF3"]

    def test_transient_failure_continues_to_next_handle(self, get_mock, fixed_now):
        get_mock.side_effect = [_http_error(500), _v2([_tweet("2", "b")], users=_users(("u1", "h2")))]
        items = x_api.search_handles(["h1", "h2"], "topic", FROM, TO, token=DUMMY_TOKEN)
        assert [i["author_handle"] for i in items] == ["h2"]

    def test_no_token_or_handles_returns_empty(self, get_mock):
        assert x_api.search_handles(["a"], "t", FROM, TO, token="") == []
        assert x_api.search_handles([], "t", FROM, TO, token=DUMMY_TOKEN) == []
        assert x_api.search_mentions([], FROM, TO, token=DUMMY_TOKEN) == []
        get_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Moved helpers (KTD3)
# ---------------------------------------------------------------------------


class TestSharedHelpers:
    def test_grok_x_imports_helpers_from_x_api(self):
        from lib import grok_x
        assert grok_x._decode_snowflake is x_api._decode_snowflake
        assert grok_x._clean_handle is x_api._clean_handle
        assert grok_x._looks_generated is x_api._looks_generated
        assert grok_x._HANDLE_RE is x_api._HANDLE_RE

    def test_snowflake_decodes_to_utc_datetime(self):
        when = x_api._decode_snowflake("1956158892141441450")
        assert when is not None and when.tzinfo is not None
        assert when.year == 2025
        assert x_api._decode_snowflake("nope") is None
        assert x_api._decode_snowflake("0") is None

    def test_clean_handle_grammar(self):
        assert x_api._clean_handle("@steipete") == "steipete"
        assert x_api._clean_handle("Peter Steinberger") == ""
        assert x_api._clean_handle("a" * 16) == ""
        assert x_api._clean_handle("x OR from:elonmusk") == ""

    def test_looks_generated_flags_uniform_runs(self):
        assert x_api._looks_generated(["100", "200", "300", "400"]) is True
        assert x_api._looks_generated(["100", "250", "300", "900"]) is False
        assert x_api._looks_generated(["1", "2"]) is False


# ---------------------------------------------------------------------------
# Pipeline wiring (R7): lane selection and failover line
# ---------------------------------------------------------------------------


def _make_source_item(source, item_id, url, author=None, body=""):
    return schema.SourceItem(
        item_id=item_id, source=source, title=f"Item {item_id}", body=body, url=url, author=author,
    )


def _make_plan(topic):
    return schema.QueryPlan(
        intent="exploration", freshness_mode="balanced_recent", cluster_mode="topic",
        raw_topic=topic,
        subqueries=[schema.SubQuery(
            label="primary", search_query=topic,
            ranking_query=f"What recent evidence matters for {topic}?", sources=["x"],
        )],
        source_weights={"x": 1.0},
    )


class TestPipelineWiring:
    def test_chain_xapi_yields_primary_xapi_and_runs_both_lanes(self):
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/analyst1/status/1", author="analyst1", body="AI safety analysis"),
            _make_source_item("x", "X2", "https://x.com/analyst1/status/2", author="analyst1", body="AI safety research"),
        ]
        from_items = [{
            "id": "XF1", "text": "from analyst1", "url": "https://x.com/analyst1/status/777",
            "author_handle": "analyst1", "date": "2026-03-15",
            "engagement": {"likes": 30}, "relevance": 0.8, "why_relevant": "",
        }]
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]), \
             mock.patch("lib.entity_extract.extract_entities",
                        return_value={"x_handles": ["analyst1"], "x_hashtags": [], "reddit_subreddits": []}), \
             mock.patch("lib.x_api.search_handles", return_value=from_items) as from_lane, \
             mock.patch("lib.x_api.search_mentions", return_value=[]) as about_lane, \
             mock.patch("lib.bird_x.search_handles") as bird_lane, \
             mock.patch("lib.xquik.search_handles") as xquik_lane:
            pipeline._run_supplemental_searches(
                topic="AI safety", bundle=bundle, plan=_make_plan("AI safety"),
                config={"X_BEARER_TOKEN": DUMMY_TOKEN}, depth="default",
                date_range=("2026-02-15", "2026-03-17"), runtime=runtime, mock=False,
                rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
            )
        from_lane.assert_called_once()
        about_lane.assert_called_once()
        assert from_lane.call_args.kwargs["token"] == DUMMY_TOKEN
        assert about_lane.call_args.kwargs["token"] == DUMMY_TOKEN
        bird_lane.assert_not_called()
        xquik_lane.assert_not_called()
        x_urls = {item.url for item in bundle.items_by_source.get("x", [])}
        assert "https://x.com/analyst1/status/777" in x_urls

    def test_xapi_lanes_share_one_deadline(self):
        """Explicit-from, extracted-from, about, and related lanes must draw
        on one wall-clock budget, never a fresh 90s each (review finding)."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/analyst1/status/1", author="analyst1", body="AI safety analysis"),
        ]
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]), \
             mock.patch("lib.entity_extract.extract_entities",
                        return_value={"x_handles": ["analyst1"], "x_hashtags": [], "reddit_subreddits": []}), \
             mock.patch("lib.x_api.search_handles", return_value=[]) as from_lane, \
             mock.patch("lib.x_api.search_mentions", return_value=[]) as about_lane:
            pipeline._run_supplemental_searches(
                topic="AI safety", bundle=bundle, plan=_make_plan("AI safety"),
                config={"X_BEARER_TOKEN": DUMMY_TOKEN}, depth="default",
                date_range=("2026-02-15", "2026-03-17"), runtime=runtime, mock=False,
                rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
                x_handle="steipete", x_related=["peer1"],
            )
        calls = from_lane.call_args_list + about_lane.call_args_list
        assert len(calls) >= 3, "explicit from, extracted from, about, related"
        deadlines = {c.kwargs.get("deadline") for c in calls}
        assert len(deadlines) == 1 and None not in deadlines, deadlines

    def test_lane_search_past_a_shared_deadline_sends_no_request(self, get_mock, fixed_now, monkeypatch):
        monkeypatch.setattr(x_api.time, "monotonic", lambda: 1000.0)
        assert x_api.search_handles(["steipete"], "t", FROM, TO, token=DUMMY_TOKEN, deadline=999.0) == []
        assert x_api.search_mentions(["steipete"], FROM, TO, token=DUMMY_TOKEN, deadline=999.0) == []
        get_mock.assert_not_called()
        get_mock.return_value = _v2([_tweet("1", "hi")], users=_users(("u1", "steipete")))
        assert len(x_api.search_handles(["steipete"], "t", FROM, TO, token=DUMMY_TOKEN, deadline=1001.0)) == 1

    def test_all_backends_failed_keeps_the_payment_required_state(self):
        """xapi's 402 must not be masked by a later backend's generic failure:
        the outcome that reaches doctor says top up, not re-authenticate."""
        plan = {
            "intent": "general", "freshness_mode": "balanced_recent", "cluster_mode": "story",
            "subqueries": [{
                "label": "primary", "search_query": "topic",
                "ranking_query": "What are people saying about topic?", "sources": ["x"],
            }],
            "source_weights": {"x": 1.0},
        }
        answers = iter([
            ([], x_api.ERR_PAYMENT_REQUIRED),
            ([], "request failed (HTTPError)"),
        ])
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi", "xai"]), \
             mock.patch("lib.pipeline._fetch_x_backend", side_effect=lambda *a, **k: next(answers)):
            report = pipeline.run(
                topic="topic", config={"X_BEARER_TOKEN": DUMMY_TOKEN, "XAI_API_KEY": "dummy-xai"},
                depth="quick", requested_sources=["x"], mock=False,
                external_plan=plan, web_backend="none", save_dir="",
            )
        assert report.source_status["x"].state == health.PAYMENT_REQUIRED
        assert "payment required" in report.source_status["x"].detail

    def test_fetch_x_backend_registers_xapi(self, get_mock, fixed_now):
        get_mock.return_value = _v2([_tweet("1", "hello topic")], users=_users(("u1", "a")))
        items, err = pipeline._fetch_x_backend(
            "xapi", "topic", FROM, TO, "quick", {"X_BEARER_TOKEN": DUMMY_TOKEN},
        )
        assert err == ""
        assert [i["id"] for i in items] == ["XAPI1"]
        assert get_mock.call_args.kwargs["headers"]["Authorization"] == f"Bearer {DUMMY_TOKEN}"

    def test_fetch_x_backend_surfaces_truncation_warning(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _http_error(403, body="client-not-enrolled"),
            _v2([_tweet("1", "hello topic", created_at="2026-09-05T00:00:00Z")]),
        ]
        warnings: list[str] = []
        items, err = pipeline._fetch_x_backend(
            "xapi", "topic", FROM, TO, "quick", {"X_BEARER_TOKEN": DUMMY_TOKEN}, warnings=warnings,
        )
        assert err == "" and len(items) == 1
        assert warnings and "window truncated to 7 days" in warnings[0]

    def test_truncation_receipt_reaches_the_stream_artifact(self, get_mock, fixed_now):
        get_mock.side_effect = [
            _http_error(403, body="client-not-enrolled"),
            _v2([_tweet("1", "hello topic", created_at="2026-09-05T00:00:00Z")]),
        ]
        sq = schema.SubQuery(label="primary", search_query="q", ranking_query="q?", sources=["x"])
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )

        def run():
            with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]):
                return pipeline._retrieve_stream(
                    topic="q", subquery=sq, source="x", config={"X_BEARER_TOKEN": DUMMY_TOKEN},
                    depth="quick", date_range=(FROM, TO), runtime=runtime, mock=False,
                )

        (items, artifact), stderr = _run_capturing_stderr(run)
        assert len(items) == 1
        assert artifact["x_receipts"] == ["X: xapi window truncated to 7 days"]
        assert "window truncated to 7 days" in stderr

    def test_failover_stderr_line_names_xapi_and_credits(self):
        sq = schema.SubQuery(label="primary", search_query="q", ranking_query="q?", sources=["x"])
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        good = [{"id": "XAPI1", "text": "t", "url": "https://x.com/a/status/1", "author_handle": "a",
                 "date": "2026-08-20", "engagement": None, "relevance": 0.5, "why_relevant": ""}]

        def fetch(backend, *_args, **_kwargs):
            return (good, "") if backend == "xapi" else ([], "")

        def run():
            with mock.patch("lib.env.x_backend_chain", return_value=["xai", "xapi"]), \
                 mock.patch("lib.pipeline._fetch_x_backend", side_effect=fetch):
                return pipeline._retrieve_stream(
                    topic="q", subquery=sq, source="x", config={"X_BEARER_TOKEN": DUMMY_TOKEN},
                    depth="quick", date_range=(FROM, TO), runtime=runtime, mock=False,
                )

        (items, _artifact), stderr = _run_capturing_stderr(run)
        assert len(items) == 1
        assert "used fallback 'xapi'" in stderr
        assert "X API credits" in stderr


# ---------------------------------------------------------------------------
# Fixture redaction at both seams (R8)
# ---------------------------------------------------------------------------


def _urlopen_response(body: str):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.read.return_value = body.encode("utf-8")
    response.status = 200
    return response


def test_fixture_redacts_bearer_loaded_only_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"X_BEARER_TOKEN={DUMMY_TOKEN}\n", encoding="utf-8")
    env_file.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", env_file)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")
    monkeypatch.setattr(env, "_load_pass", lambda *a, **k: {})
    monkeypatch.setattr(env, "_find_project_env", lambda: None)
    monkeypatch.setattr(
        http.urllib.request, "urlopen",
        lambda *_a, **_k: _urlopen_response(json.dumps({"detail": f"token {DUMMY_TOKEN} rejected"})),
    )
    fixture_dir = tmp_path / "fixture"

    with http.recording_requests(fixture_dir):
        config = env.get_config()
        assert config["X_BEARER_TOKEN"] == DUMMY_TOKEN
        http.get(
            "https://api.x.com/2/tweets/search/all",
            headers={"Authorization": f"Bearer {DUMMY_TOKEN}"},
            params={"query": "topic"},
        )
        http.fixture_source_record(
            {"source": "x", "topic": "t", "search_query": "t", "date_range": [FROM, TO], "depth": "quick"},
            [[{"text": f"echo {DUMMY_TOKEN}"}], {}],
        )

    payload = json.loads((fixture_dir / "http.json").read_text(encoding="utf-8"))
    text = json.dumps(payload)
    assert DUMMY_TOKEN not in text
    assert "<redacted>" in json.dumps(payload["exchanges"])
    assert "<redacted>" in json.dumps(payload["source_exchanges"])


def test_http_seam_redacts_bare_bearer_without_any_config(tmp_path, monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(
        http.urllib.request, "urlopen",
        lambda *_a, **_k: _urlopen_response(json.dumps({"echo": DUMMY_TOKEN})),
    )
    fixture_dir = tmp_path / "fixture"
    with http.recording_requests(fixture_dir):
        http.get("https://api.x.com/2/tweets/search/all", headers={"Authorization": f"Bearer {DUMMY_TOKEN}"})
    text = (fixture_dir / "http.json").read_text(encoding="utf-8")
    assert DUMMY_TOKEN not in text
    assert '"echo": "<redacted>"' in text


# ---------------------------------------------------------------------------
# xurl fixed strings (same rule as xapi)
# ---------------------------------------------------------------------------


class TestXurlFixedStrings:
    def _run(self, returncode, stderr="", stdout=""):
        completed = mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)
        with mock.patch("subprocess.run", return_value=completed):
            return xurl_x.search_x("test")

    def test_rate_limited_stderr_with_token_maps_to_fixed_string(self):
        result = self._run(1, stderr=f"429 rate limit exceeded for {DUMMY_TOKEN} ({ACCOUNT_ID})")
        assert result["error"] == xurl_x.ERR_RATE_LIMITED
        assert DUMMY_TOKEN not in result["error"] and ACCOUNT_ID not in result["error"]
        assert http.classify_failure(message=result["error"]) == health.RATE_LIMITED

    @pytest.mark.parametrize("stderr,expected,state", [
        ("401 Unauthorized", "ERR_UNAUTHORIZED", health.AUTH_FAILED),
        ("403 Forbidden: client-not-enrolled", "ERR_FORBIDDEN", health.AUTH_FAILED),
        ("402 Payment Required", "ERR_PAYMENT_REQUIRED", health.PAYMENT_REQUIRED),
        ("something else entirely", "ERR_FAILED", health.ERROR),
    ])
    def test_status_words_map_to_fixed_strings(self, stderr, expected, state):
        result = self._run(1, stderr=f"{stderr} {DUMMY_TOKEN}")
        assert result["error"] == getattr(xurl_x, expected)
        assert DUMMY_TOKEN not in result["error"]
        assert http.classify_failure(message=result["error"]) == state

    def test_invalid_json_is_fixed_string(self):
        result = self._run(0, stdout=f"NOT JSON {DUMMY_TOKEN}")
        assert result["error"] == xurl_x.ERR_INVALID_JSON
        assert DUMMY_TOKEN not in result["error"]

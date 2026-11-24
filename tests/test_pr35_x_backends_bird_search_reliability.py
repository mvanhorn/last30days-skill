"""Regression tests for PR 35: X backends & vendored bird-search reliability.

Covers:
- HIGH-139: grok_x JSON wrapper unwrapping with numeric id and multi-key prose fallback.
- HIGH-145: xai_x envelope null-safety (content/choices/message/text) & K/M/B engagement parsing.
- HIGH-146: x_judge LOW_SIGNAL_QUERY_TOKENS exclusion on planner ranking queries.
- HIGH-147: x_api full-archive 403 expected_misses isolation & conditional 7-day truncation warning.
- HIGH-148: x_api handle-lane error propagation into pipeline & pre-start deadline/cancel error.
- HIGH-149: xquik HTTP 429 early stop & non-auth failure reporting in search_xquik.
- HIGH-151: xurl_x explicit UTF-8 subprocess decoding under ASCII/C locale.
- HIGH-162: bird-search TweetWithVisibilityResults, TimelineReplaceEntry cursor, and null-safe parsing.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import threading
import urllib.error
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

from lib import grok_x, health, http, pipeline, schema, x_api, x_judge, xai_x, xquik, xurl_x

REPO_ROOT = Path(__file__).resolve().parents[1]
BIRD_UTILS_TEST = REPO_ROOT / "tests" / "test_bird_search_utils.test.mjs"
HAS_NODE = shutil.which("node") is not None

WINDOW = ("2026-07-14", "2026-08-13")
IN_WINDOW_ID = "2087568620465607078"  # 2026-08-12T15:55:18Z
DUMMY_TOKEN = "dummy-x-bearer-secret-000"
FIXED_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
FROM = "2026-08-10"
TO = "2026-09-08"


def _grok_block(
    post_id: str = IN_WINDOW_ID,
    handle: str = "steipete",
    created: str = "Wed, 12 Aug 2026 15:55:18 GMT",
    likes: int = 1462,
    text: str = "cli was a year ago.",
) -> str:
    return (
        f"id: {post_id}\n"
        f"handle: {handle}\n"
        f"created_at: {created}\n"
        f"likes: {likes}\n"
        f"reposts: 48\nreplies: 95\nquotes: 20\n"
        f"text: {text}\n"
    )


def _xai_wrap(items: list[dict]) -> dict:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps({"items": items})}],
            }
        ]
    }


def _make_source_item(source: str, item_id: str, url: str, author: str | None = None, body: str = ""):
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=f"Item {item_id}",
        body=body,
        url=url,
        author=author,
    )


def _make_plan(topic: str) -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="exploration",
        freshness_mode="balanced_recent",
        cluster_mode="topic",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=f"What recent evidence from the last 30 days is most relevant to {topic}?",
                sources=["x"],
            )
        ],
        source_weights={"x": 1.0},
    )


# ---------------------------------------------------------------------------
# HIGH-139: grok_x JSON CLI wrapper unwrapping & multi-key prose fallback
# ---------------------------------------------------------------------------


class TestHigh139GrokWrapperParsing:
    @pytest.mark.parametrize("collection_key", ["posts", "items", "results", "data", "tweets"])
    def test_numeric_wrapper_id_unwraps_collection_keys_before_direct_post(self, collection_key):
        envelope = {
            "id": 42,
            collection_key: [
                {
                    "url": f"https://x.com/steipete/status/{IN_WINDOW_ID}",
                    "text": "real post inside collection",
                    "likes": 120,
                }
            ],
        }
        items = grok_x.parse_x_response({"text": json.dumps(envelope)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "steipete"
        assert items[0]["url"] == f"https://x.com/steipete/status/{IN_WINDOW_ID}"
        assert items[0]["engagement"]["likes"] == 120

    def test_numeric_wrapper_id_unwraps_json_in_content_string(self):
        inner_posts = json.dumps([
            {
                "id": IN_WINDOW_ID,
                "handle": "steipete",
                "text": "wrapped JSON string payload",
                "likes": 300,
            }
        ])
        envelope = {"id": 99, "content": inner_posts}
        items = grok_x.parse_x_response({"text": json.dumps(envelope)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "steipete"
        assert items[0]["text"] == "wrapped JSON string payload"

    def test_numeric_wrapper_id_with_prose_blocks_in_content_not_misparsed_as_single_post(self):
        envelope = {"id": 42, "content": _grok_block(text="prose inside numeric-id wrapper")}
        items = grok_x.parse_x_response({"text": json.dumps(envelope)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "steipete"
        assert items[0]["url"].endswith(f"/{IN_WINDOW_ID}")
        assert items[0]["text"] == "prose inside numeric-id wrapper"

    def test_wrapper_fallback_does_not_break_on_status_message_before_result(self):
        envelope = {
            "id": 7,
            "message": "ok",
            "result": _grok_block(text="found in result after ok message"),
        }
        items = grok_x.parse_x_response({"text": json.dumps(envelope)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "steipete"
        assert items[0]["text"] == "found in result after ok message"

    def test_wrapper_fallback_checks_output_after_non_post_message_and_nested_dict(self):
        envelope = {
            "message": "completed",
            "text": "summary only",
            "output": _grok_block(text="found in output key"),
        }
        items = grok_x.parse_x_response({"text": json.dumps(envelope)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["text"] == "found in output key"

        nested = {
            "id": 12,
            "message": {"content": _grok_block(text="nested wrapper dict prose")},
        }
        nested_items = grok_x.parse_x_response({"text": json.dumps(nested)}, "steipete", *WINDOW)
        assert len(nested_items) == 1
        assert nested_items[0]["text"] == "nested wrapper dict prose"

    def test_direct_single_post_json_still_parses_and_placeholder_still_rejected(self):
        single_post = {
            "id": IN_WINDOW_ID,
            "handle": "steipete",
            "text": "direct single post object",
            "likes": 55,
        }
        items = grok_x.parse_x_response({"text": json.dumps(single_post)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "steipete"
        assert items[0]["engagement"]["likes"] == 55

        placeholder = {
            "id": IN_WINDOW_ID,
            "handle": "unknown",
            "text": "fabricated",
        }
        assert grok_x.parse_x_response({"text": json.dumps(placeholder)}, "steipete", *WINDOW) == []

    def test_real_tweet_embedding_json_in_text_is_not_hijacked_by_inner_snippet(self):
        tweet_with_embedded_json = {
            "id": IN_WINDOW_ID,
            "author_handle": "alice",
            "text": 'Debugging API payload: {"id": "123", "user": "bob", "text": "err"}',
            "likes": 17,
        }
        items = grok_x.parse_x_response({"text": json.dumps(tweet_with_embedded_json)}, "alice", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "alice"
        assert items[0]["url"] == f"https://x.com/alice/status/{IN_WINDOW_ID}"
        assert "Debugging API payload:" in items[0]["text"]

        # Same protection when author uses `username` (no `author_handle`/`handle`/`url`),
        # including when the embedded JSON lives in `content`.
        username_tweet = {
            "id": IN_WINDOW_ID,
            "username": "alice",
            "text": 'Debugging API payload: {"id": "123", "user": "bob", "text": "err"}',
        }
        items_u = grok_x.parse_x_response({"text": json.dumps(username_tweet)}, "alice", *WINDOW)
        assert len(items_u) == 1
        assert items_u[0]["author_handle"] == "alice"
        assert items_u[0]["url"] == f"https://x.com/alice/status/{IN_WINDOW_ID}"

        username_content_tweet = {
            "id": IN_WINDOW_ID,
            "username": "alice",
            "content": 'Example payload: {"id": "123", "username": "bob", "text": "err"}',
            "likes": 25,
        }
        items_uc = grok_x.parse_x_response({"text": json.dumps(username_content_tweet)}, "alice", *WINDOW)
        assert len(items_uc) == 1
        assert items_uc[0]["author_handle"] == "alice"
        assert items_uc[0]["url"] == f"https://x.com/alice/status/{IN_WINDOW_ID}"
        assert "Example payload:" in items_uc[0]["text"]

    def test_top_level_json_array_of_cli_wrappers_unwraps_prose_and_collections(self):
        prose_stream = [
            {"type": "system", "message": "init"},
            {"type": "result", "id": 11, "result": _grok_block(text="from top-level array prose wrapper")},
        ]
        items = grok_x.parse_x_response({"text": json.dumps(prose_stream)}, "steipete", *WINDOW)
        assert len(items) == 1
        assert items[0]["author_handle"] == "steipete"
        assert items[0]["text"] == "from top-level array prose wrapper"

        json_stream = [
            {"type": "system", "message": "init"},
            {
                "type": "result",
                "id": 12,
                "posts": [
                    {
                        "url": f"https://x.com/steipete/status/{IN_WINDOW_ID}",
                        "text": "from top-level array collection wrapper",
                        "likes": 42,
                    }
                ],
            },
        ]
        items_json = grok_x.parse_x_response({"text": json.dumps(json_stream)}, "steipete", *WINDOW)
        assert len(items_json) == 1
        assert items_json[0]["text"] == "from top-level array collection wrapper"


# ---------------------------------------------------------------------------
# HIGH-145: xai_x envelope null-safety & engagement/relevance robustness
# ---------------------------------------------------------------------------


class TestHigh145XaiXRobustParsing:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.2K", 1200),
            ("2.3K", 2300),
            ("39k", 39000),
            ("1.5M", 1_500_000),
            ("2B", 2_000_000_000),
            ("1,462", 1462),
            ("1,234,567", 1_234_567),
            ("100.0", 100),
            (0, 0),
            ("", None),
            ("N/A", None),
            ("-5", None),
            ("12abc", None),
            ("inf", None),
            ("NaN", None),
            ("١٢٣", None),
            ("9999B", None),
            ("9" * 400, None),
            (float("inf"), None),
            (float("nan"), None),
            (True, None),
            (False, None),
        ],
    )
    def test_safe_int_abbreviated_commas_and_malformed_inputs(self, raw, expected):
        assert xai_x._safe_int(raw) == expected

    def test_parse_x_response_handles_abbreviated_engagement_and_bad_relevance(self):
        resp = _xai_wrap([
            {
                "text": "viral post",
                "url": f"https://x.com/steipete/status/{IN_WINDOW_ID}",
                "author_handle": "steipete",
                "engagement": {
                    "likes": "1.2K",
                    "reposts": "1,462",
                    "replies": "",
                    "quotes": "N/A",
                },
                "relevance": "high",
            }
        ])
        items = xai_x.parse_x_response(resp, *WINDOW)
        assert len(items) == 1
        assert items[0]["engagement"] == {
            "likes": 1200,
            "reposts": 1462,
            "replies": None,
            "quotes": None,
        }
        assert items[0]["relevance"] == 0.5

    def test_non_dict_response_returns_empty(self):
        for bad in (None, [], "string", 123):
            assert xai_x.parse_x_response(bad, *WINDOW) == []  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "malformed_envelope",
        [
            {"output": [{"type": "message", "content": None}]},
            {"output": [{"type": "message", "content": 123}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": None}]}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": 42}]}]},
            {"output": [{"text": 123}]},
            {"choices": None},
            {"choices": [None]},
            {"choices": [{"message": None}]},
            {"choices": [{"message": "not-a-dict"}]},
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {"content": 123}}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"items": null}'}]}]},
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"items": "not-a-list"}'}]}]},
        ],
    )
    def test_malformed_envelopes_raise_schema_drift_not_type_or_attribute_error(self, malformed_envelope):
        with pytest.raises(http.HTTPError) as exc_info:
            xai_x.parse_x_response(malformed_envelope, *WINDOW)
        assert exc_info.value.outcome_state == health.SCHEMA_DRIFT

    def test_skips_null_content_and_null_choice_to_parse_valid_sibling(self):
        valid_payload = json.dumps({
            "items": [
                {
                    "text": "valid item",
                    "url": f"https://x.com/steipete/status/{IN_WINDOW_ID}",
                    "author_handle": "steipete",
                    "relevance": None,
                }
            ]
        })
        resp_output = {
            "output": [
                {"type": "message", "content": None},
                {"type": "message", "content": [{"type": "output_text", "text": None}, {"type": "output_text", "text": valid_payload}]},
            ]
        }
        items = xai_x.parse_x_response(resp_output, *WINDOW)
        assert len(items) == 1
        assert items[0]["relevance"] == 0.5

        resp_choices = {
            "choices": [
                None,
                {"message": None},
                {"message": {"content": None}},
                {"message": {"content": valid_payload}},
            ]
        }
        items_choices = xai_x.parse_x_response(resp_choices, *WINDOW)
        assert len(items_choices) == 1
        assert items_choices[0]["author_handle"] == "steipete"

        # Whitespace-only message content / output string must not short-circuit valid siblings.
        resp_ws_content = {
            "output": [
                {"type": "message", "content": "   \n\t  "},
                {"type": "message", "content": [{"type": "output_text", "text": valid_payload}]},
            ]
        }
        items_ws = xai_x.parse_x_response(resp_ws_content, *WINDOW)
        assert len(items_ws) == 1
        assert items_ws[0]["author_handle"] == "steipete"

        resp_ws_output = {
            "output": "   ",
            "choices": [{"message": {"content": valid_payload}}],
        }
        items_ws_out = xai_x.parse_x_response(resp_ws_output, *WINDOW)
        assert len(items_ws_out) == 1
        assert items_ws_out[0]["author_handle"] == "steipete"

    def test_malformed_xai_response_fails_over_cleanly_in_attempt_x_backend(self, monkeypatch):
        monkeypatch.setattr(
            pipeline.xai_x,
            "search_x",
            lambda *a, **k: {"choices": [{"message": None}]},
        )
        items, detail, state = pipeline._attempt_x_backend(
            "xai", "topic", WINDOW[0], WINDOW[1], "quick", {"XAI_API_KEY": "xai-dummy"},
        )
        assert items == []
        assert detail
        assert state == health.SCHEMA_DRIFT


# ---------------------------------------------------------------------------
# HIGH-146: x_judge excludes LOW_SIGNAL_QUERY_TOKENS on ranking_query
# ---------------------------------------------------------------------------


class TestHigh146XJudgeLowSignalExclusion:
    def test_planner_scaffolding_words_do_not_make_off_topic_posts_on_topic(self):
        ranking_query = "What recent evidence from the last 30 days is most relevant to Rome?"
        off_topic_posts = [
            {"author_handle": "spam1", "text": "Zelensky addressed world leaders in the last 30 days"},
            {"author_handle": "spam1", "text": "Most people agree this new evidence is relevant"},
            {"author_handle": "spam2", "text": "Recent update from the last few days"},
            {"author_handle": "spam2", "text": "30 new tips for people around the world"},
            {"author_handle": "spam3", "text": "The most recent story in the world today"},
            {"author_handle": "spam3", "text": "Evidence shows last week was busy"},
        ]
        on_topic_posts = [
            {"author_handle": "romefan", "text": "Trevi Fountain in Rome today"},
            {"author_handle": "romefan", "text": "Best pasta spots in Rome"},
        ]

        for post in off_topic_posts:
            assert x_judge._compute_relevance(ranking_query, post["text"]) == 0.0

        corpus = on_topic_posts + off_topic_posts
        judgment = x_judge.judge_x_corpus(corpus, "Rome", ranking_query=ranking_query)
        assert judgment["on_topic_items"] == on_topic_posts
        assert len(judgment["off_topic_items"]) == len(off_topic_posts)
        assert judgment["on_topic_ratio"] == pytest.approx(2 / 8)
        assert judgment["is_off_topic_flood"] is True
        assert x_judge.should_retry_x_search(corpus, "Rome", ranking_query=ranking_query, depth="default") is True
        assert x_judge.prune_off_topic_items(corpus, "Rome", ranking_query=ranking_query) == on_topic_posts

        _, extracted = x_judge.promotable_handles(
            corpus, "Rome", ["spam1", "spam2", "romefan"], ranking_query=ranking_query,
        )
        assert extracted == ["romefan"]

    def test_case_sensitive_us_acronym_inside_planner_ranking_query(self):
        ranking_query = "What recent evidence from the last 30 days is most relevant to US?"
        # Lowercase pronoun "us" plus scaffolding words must score 0.0, not fall back to scaffolding.
        assert x_judge._compute_relevance(ranking_query, "Tell us what happened in the last 30 days") == 0.0
        # Uppercase country acronym "US" passes.
        assert x_judge._compute_relevance(ranking_query, "US tariff news over the last 30 days") == 1.0

    def test_all_low_signal_query_falls_back_like_prepared_query(self):
        assert x_judge._compute_relevance("latest news", "Breaking latest news update") > 0.0


# ---------------------------------------------------------------------------
# HIGH-147: x_api full-archive 403 expected_misses & conditional truncation
# ---------------------------------------------------------------------------


class TestHigh147XApiExpectedMissAndTruncation:
    def _urlopen_for(self, responses: list[tuple[int, str]]):
        seq = iter(responses)

        def fake_urlopen(req, timeout=None):
            status, body = next(seq)
            if status >= 400:
                raise urllib.error.HTTPError(
                    req.full_url,
                    status,
                    "Error",
                    hdrs={},
                    fp=BytesIO(body.encode("utf-8")),
                )
            resp = mock.MagicMock()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = False
            resp.status = status
            resp.read.return_value = body.encode("utf-8")
            return resp

        return fake_urlopen

    def test_enrollment_403_probe_does_not_pollute_failure_sink_or_misreport_auth_failed(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        monkeypatch.setattr(
            http.urllib.request,
            "urlopen",
            self._urlopen_for([
                (403, '{"reason":"client-not-enrolled","title":"Client Forbidden"}'),
                (200, json.dumps({"data": [], "meta": {"result_count": 0}})),
            ]),
        )
        with http.capture_failures() as failures:
            result = x_api.search_x(DUMMY_TOKEN, "quiet topic", FROM, TO, depth="quick")

        assert result == {"items": [], "warning": x_api.TRUNCATION_DETAIL}
        assert failures == [], f"enrollment probe 403 polluted failure sink: {failures}"
        assert pipeline._resolve_stream_outcome("x", {}, failures) is None

    def test_enrollment_403_with_old_as_of_window_does_not_pollute_failure_sink(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        monkeypatch.setattr(
            http.urllib.request,
            "urlopen",
            self._urlopen_for([
                (403, '{"reason":"client-not-enrolled"}'),
            ]),
        )
        with http.capture_failures() as failures:
            result = x_api.search_x(DUMMY_TOKEN, "old window", "2026-08-01", "2026-08-20", depth="quick")

        assert result == {"items": [], "warning": x_api.TRUNCATION_DETAIL}
        assert failures == []
        assert pipeline._resolve_stream_outcome("x", {}, failures) is None

    def test_plain_non_enrollment_403_still_returns_err_forbidden(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        monkeypatch.setattr(
            http.urllib.request,
            "urlopen",
            self._urlopen_for([(403, '{"title":"Forbidden","detail":"revoked"}')]),
        )
        with http.capture_failures() as failures:
            result = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick")

        assert result == {"items": [], "error": x_api.ERR_FORBIDDEN}
        assert http.classify_failure(message=result["error"]) == health.AUTH_FAILED
        assert failures == []

    def test_recent_window_within_7_days_omits_truncation_warning(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        with mock.patch("lib.x_api.http.get") as mock_get:
            mock_get.side_effect = [
                http.HTTPError("HTTP 403: Forbidden", status_code=403, body="client-not-enrolled"),
                {"data": [{"id": "1", "text": "fresh post", "created_at": "2026-09-07T10:00:00Z"}], "meta": {"result_count": 1}},
            ]
            # 2-day window (2026-09-06..2026-09-08) is strictly inside recent floor (2026-09-01T12:00:30Z).
            result = x_api.search_x(DUMMY_TOKEN, "topic", "2026-09-06", TO, depth="quick")

        assert "error" not in result
        assert "warning" not in result
        assert len(result["items"]) == 1


# ---------------------------------------------------------------------------
# HIGH-148: x_api handle-lane errors & pre-start deadline/cancel error
# ---------------------------------------------------------------------------


class TestHigh148XApiHandleLaneErrorsAndPreStartBudget:
    @pytest.mark.parametrize(
        "exc,expected_error",
        [
            (http.HTTPError("HTTP 401", status_code=401), x_api.ERR_UNAUTHORIZED),
            (http.HTTPError("HTTP 402", status_code=402), x_api.ERR_PAYMENT_REQUIRED),
            (http.HTTPError("HTTP 403", status_code=403, body="Forbidden"), x_api.ERR_FORBIDDEN),
            (http.HTTPError("HTTP 429", status_code=429), x_api.ERR_RATE_LIMITED),
            (http.DeadlineExceeded(), x_api.ERR_TIMED_OUT),
        ],
    )
    def test_search_handles_and_mentions_populate_errors_sink(self, monkeypatch, exc, expected_error):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        with mock.patch("lib.x_api.http.get", side_effect=exc):
            from_errors: list[str] = []
            from_items = x_api.search_handles(
                ["alice", "bob"], "topic", FROM, TO, token=DUMMY_TOKEN, errors=from_errors,
            )
            about_errors: list[str] = []
            about_items = x_api.search_mentions(
                ["alice"], FROM, TO, topic="topic", token=DUMMY_TOKEN, errors=about_errors,
            )

        assert from_items == []
        assert from_errors == [expected_error]
        assert about_items == []
        assert about_errors == [expected_error]

    def test_search_x_past_deadline_or_cancelled_returns_err_timed_out(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        monkeypatch.setattr(x_api.time, "monotonic", lambda: 1000.0)
        with mock.patch("lib.x_api.http.get") as mock_get:
            expired = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick", deadline=999.0)
            cancel = threading.Event()
            cancel.set()
            cancelled = x_api.search_x(DUMMY_TOKEN, "topic", FROM, TO, depth="quick", cancel=cancel)

        mock_get.assert_not_called()
        assert expired == {"items": [], "error": x_api.ERR_TIMED_OUT}
        assert cancelled == {"items": [], "error": x_api.ERR_TIMED_OUT}

    def test_cancelled_handle_lane_returns_warning_not_error(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        cancel = threading.Event()
        cancel.set()
        warnings: list[str] = []
        errors: list[str] = []
        with mock.patch("lib.x_api.http.get") as mock_get:
            from_items = x_api.search_handles(
                ["steipete"], "topic", FROM, TO, token=DUMMY_TOKEN,
                warnings=warnings, errors=errors, cancel=cancel,
            )
            about_items = x_api.search_mentions(
                ["steipete"], FROM, TO, topic="topic", token=DUMMY_TOKEN,
                warnings=warnings, errors=errors, cancel=cancel,
            )

        mock_get.assert_not_called()
        assert from_items == []
        assert about_items == []
        assert warnings == [x_api.DEADLINE_DETAIL]
        assert errors == []

    def test_pipeline_xapi_handle_lane_401_records_auth_failed_and_skips_about_lane(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/steipete/status/1", author="steipete", body="AI safety analysis"),
        ]
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]), \
             mock.patch("lib.entity_extract.extract_entities", return_value={"x_handles": [], "x_hashtags": [], "reddit_subreddits": []}), \
             mock.patch("lib.x_api.http.get", side_effect=http.HTTPError("Unauthorized", status_code=401)), \
             mock.patch("lib.x_api.search_mentions", wraps=x_api.search_mentions) as about_spy:
            pipeline._run_supplemental_searches(
                topic="AI safety",
                bundle=bundle,
                plan=_make_plan("AI safety"),
                config={"X_BEARER_TOKEN": DUMMY_TOKEN},
                depth="default",
                date_range=(FROM, TO),
                runtime=runtime,
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                x_handle="steipete",
            )

        about_spy.assert_not_called()
        assert bundle.source_status["x"].state == health.AUTH_FAILED
        assert "authentication failed" in (bundle.source_status["x"].detail or "")

    def test_pipeline_xapi_handle_lane_402_records_payment_required_and_skips_subsequent_lanes(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        bundle = schema.RetrievalBundle()
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]), \
             mock.patch("lib.entity_extract.extract_entities", return_value={"x_handles": [], "x_hashtags": [], "reddit_subreddits": []}), \
             mock.patch("lib.x_api.http.get", side_effect=http.HTTPError("Payment Required", status_code=402)) as mock_get:
            pipeline._run_supplemental_searches(
                topic="AI safety",
                bundle=bundle,
                plan=_make_plan("AI safety"),
                config={"X_BEARER_TOKEN": DUMMY_TOKEN},
                depth="default",
                date_range=(FROM, TO),
                runtime=runtime,
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                x_handle="steipete",
                x_related=["peer1"],
            )

        assert mock_get.call_count == 1
        assert bundle.source_status["x"].state == health.PAYMENT_REQUIRED
        assert bundle.failure_state_by_source["x"] == health.PAYMENT_REQUIRED
        assert "payment required" in (bundle.source_status["x"].detail or "")

    def test_pipeline_xapi_handle_lane_429_records_rate_limited(self, monkeypatch):
        monkeypatch.setattr(x_api, "_utcnow", lambda: FIXED_NOW)
        bundle = schema.RetrievalBundle()
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]), \
             mock.patch("lib.entity_extract.extract_entities", return_value={"x_handles": [], "x_hashtags": [], "reddit_subreddits": []}), \
             mock.patch("lib.x_api.http.get", side_effect=http.HTTPError("Too Many Requests", status_code=429)):
            pipeline._run_supplemental_searches(
                topic="AI safety",
                bundle=bundle,
                plan=_make_plan("AI safety"),
                config={"X_BEARER_TOKEN": DUMMY_TOKEN},
                depth="default",
                date_range=(FROM, TO),
                runtime=runtime,
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                x_handle="steipete",
            )

        assert bundle.source_status["x"].state == health.RATE_LIMITED
        assert "rate limit" in (bundle.source_status["x"].detail or "")


# ---------------------------------------------------------------------------
# HIGH-149: xquik HTTP 429 early stop & non-auth error surfacing
# ---------------------------------------------------------------------------


class TestHigh149XquikRateLimitAndErrorSurfacing:
    def test_429_breaks_query_loop_immediately_and_returns_rate_limited_error(self):
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["q1", "q2", "q3"]), \
             mock.patch(
                 "lib.xquik.http.get",
                 side_effect=http.HTTPError("blocked", status_code=429),
             ) as mock_get:
            result = xquik.search_xquik("topic", FROM, TO, depth="deep", token="dummy-key")

        assert mock_get.call_count == 1
        assert result["items"] == []
        assert "error" in result
        assert http.classify_failure(message=result["error"]) == health.RATE_LIMITED

    def test_429_bare_code_with_forbidden_topic_still_classifies_rate_limited(self):
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["forbidden city", "q2"]), \
             mock.patch(
                 "lib.xquik.http.get",
                 side_effect=http.HTTPError("throttled (429)", status_code=429),
             ) as mock_get:
            result = xquik.search_xquik("forbidden city", FROM, TO, depth="default", token="dummy-key")

        assert mock_get.call_count == 1
        assert result["items"] == []
        assert "HTTP 429 rate limit" in result["error"]
        assert http.classify_failure(message=result["error"]) == health.RATE_LIMITED

    def test_429_after_503_stops_and_preserves_rate_limited_specificity(self):
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["q1", "q2", "q3"]), \
             mock.patch(
                 "lib.xquik.http.get",
                 side_effect=[
                     http.HTTPError("HTTP 503: Service Unavailable", status_code=503),
                     http.HTTPError("Too Many Requests", status_code=429),
                 ],
             ) as mock_get:
            result = xquik.search_xquik("topic", FROM, TO, depth="deep", token="dummy-key")

        assert mock_get.call_count == 2
        assert result["items"] == []
        assert http.classify_failure(message=result["error"]) == health.RATE_LIMITED

    def test_query_text_with_403_forbidden_does_not_outrank_real_429(self):
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["403 Forbidden", "followup query"]), \
             mock.patch(
                 "lib.xquik.http.get",
                 side_effect=[
                     http.HTTPError("HTTP 503: Service Unavailable", status_code=503),
                     http.HTTPError("Too Many Requests", status_code=429),
                 ],
             ) as mock_get:
            result = xquik.search_xquik("403 Forbidden", FROM, TO, depth="default", token="dummy-key")

        assert mock_get.call_count == 2
        assert result["items"] == []
        assert "followup query" in result["error"]
        assert http.classify_failure(message=result["error"]) == health.RATE_LIMITED

    def test_429_through_real_http_get_makes_single_attempt_without_retry(self, monkeypatch):
        attempts: list[str] = []

        def fake_urlopen(req, timeout=None):
            attempts.append(req.full_url)
            raise urllib.error.HTTPError(
                req.full_url,
                429,
                "Too Many Requests",
                hdrs={"Retry-After": "30"},
                fp=BytesIO(b'{"error":"rate limited"}'),
            )

        monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(http.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("429 must not sleep/retry")))
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["q1", "q2", "q3"]):
            result = xquik.search_xquik("topic", FROM, TO, depth="deep", token="dummy-key")

        assert len(attempts) == 1
        assert result["items"] == []
        assert http.classify_failure(message=result["error"]) == health.RATE_LIMITED

    def test_429_after_first_query_items_stops_remaining_queries_and_keeps_items(self):
        tweet = {
            "id": "1001",
            "text": "valid item",
            "createdAt": "2026-08-20T12:00:00Z",
            "author": {"username": "alice"},
        }
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["q1", "q2", "q3"]), \
             mock.patch(
                 "lib.xquik.http.get",
                 side_effect=[{"tweets": [tweet]}, http.HTTPError("Too Many Requests", status_code=429)],
             ) as mock_get:
            result = xquik.search_xquik("topic", FROM, TO, depth="deep", token="dummy-key")

        assert mock_get.call_count == 2
        assert len(result["items"]) == 1
        assert "error" not in result

    @pytest.mark.parametrize(
        "side_effect,expected_state",
        [
            (http.HTTPError("HTTP 503: Service Unavailable", status_code=503), health.ERROR),
            (http.DeadlineExceeded(), health.TIMEOUT),
            (TimeoutError(), health.TIMEOUT),
            (OSError("connection reset by peer"), health.UNREACHABLE),
        ],
    )
    def test_non_auth_failures_surface_error_when_no_items_returned(self, side_effect, expected_state):
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=["q1", "q2"]), \
             mock.patch("lib.xquik.http.get", side_effect=side_effect) as mock_get:
            result = xquik.search_xquik("topic", FROM, TO, depth="default", token="dummy-key")

        assert result["items"] == []
        assert "error" in result and result["error"]
        assert http.classify_failure(message=result["error"]) == expected_state
        if isinstance(side_effect, http.DeadlineExceeded):
            assert mock_get.call_count == 1

    def test_non_list_tweets_payload_surfaces_error_and_reaches_pipeline_chain(self):
        with mock.patch("lib.xquik.http.get", return_value={"tweets": "not-a-list"}):
            result = xquik.search_xquik("topic", FROM, TO, depth="quick", token="dummy-key")

        assert result["items"] == []
        assert "no tweets array" in result.get("error", "")

        sq = schema.SubQuery(label="primary", search_query="topic", ranking_query="topic?", sources=["x"])
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock", x_search_backend=None,
        )
        with mock.patch("lib.env.x_backend_chain", return_value=["xquik"]), \
             mock.patch("lib.xquik.http.get", return_value={"tweets": "not-a-list"}):
            with pytest.raises(pipeline.SourceRunError) as exc_info:
                pipeline._retrieve_stream(
                    topic="topic",
                    subquery=sq,
                    source="x",
                    config={"XQUIK_API_KEY": "dummy-key"},
                    depth="quick",
                    date_range=(FROM, TO),
                    runtime=runtime,
                    mock=False,
                )
        assert "no tweets array" in str(exc_info.value)


# ---------------------------------------------------------------------------
# HIGH-151: xurl_x explicit UTF-8 subprocess decoding under ASCII/C locale
# ---------------------------------------------------------------------------


class TestHigh151XurlUtf8SubprocessDecoding:
    def test_subprocess_calls_pass_utf8_encoding_and_replace_errors(self):
        xurl_x.clear_availability_cache()
        try:
            with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="bearer: ✓\n")) as run_mock:
                assert xurl_x.is_available() is True
            assert run_mock.call_args.kwargs.get("encoding") == "utf-8"
            assert run_mock.call_args.kwargs.get("errors") == "replace"
        finally:
            xurl_x.clear_availability_cache()

        payload = {"data": [{"id": "1", "text": "hello 🚀 “quotes”", "author_id": "u1"}]}
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=json.dumps(payload))) as run_mock:
            res = xurl_x.search_x("hello")
        assert res["data"][0]["text"] == "hello 🚀 “quotes”"
        assert run_mock.call_args.kwargs.get("encoding") == "utf-8"
        assert run_mock.call_args.kwargs.get("errors") == "replace"
        assert run_mock.call_args.args[0][:3] == ["xurl", "search", "hello"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim for LC_ALL=C locale test")
    def test_real_subprocess_under_c_locale_handles_utf8_checkmark_and_emoji(self, tmp_path):
        fake_xurl = tmp_path / "xurl"
        fake_xurl.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "if sys.argv[1:3] == ['auth', 'status']:\n"
            "    sys.stdout.buffer.write('oauth1: ✗\\nbearer: ✓\\n'.encode('utf-8'))\n"
            "    sys.exit(0)\n"
            "if sys.argv[1] == 'search':\n"
            "    payload = {'data': [{'id': '123', 'text': 'Emoji 🚀 smart “quotes” 日本語', 'author_id': 'u1'}],\n"
            "               'includes': {'users': [{'id': 'u1', 'username': 'alice'}]}}\n"
            "    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))\n"
            "    sys.exit(0)\n"
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        fake_xurl.chmod(fake_xurl.stat().st_mode | stat.S_IXUSR)

        env = os.environ.copy()
        env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["PYTHONUTF8"] = "0"
        env["PYTHONPATH"] = str(REPO_ROOT / "skills" / "last30days" / "scripts")

        script = (
            "import json\n"
            "from lib import xurl_x\n"
            "xurl_x.clear_availability_cache()\n"
            "avail = xurl_x.is_available()\n"
            "res = xurl_x.search_x('emoji test')\n"
            "items = xurl_x.parse_x_response(res, topic='emoji')\n"
            "print(json.dumps({'avail': avail, 'text': items[0]['text']}, ensure_ascii=True))\n"
        )
        proc = subprocess.run(
            ["python3", "-X", "utf8=0", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        parsed = json.loads(proc.stdout.strip())
        assert parsed["avail"] is True
        assert parsed["text"] == "Emoji 🚀 smart “quotes” 日本語"


# ---------------------------------------------------------------------------
# HIGH-162: Node test suite for vendored bird-search twitter-client-utils.js
# ---------------------------------------------------------------------------


def _node_major_version() -> int | None:
    if not HAS_NODE:
        return None
    proc = subprocess.run(
        ["node", "-v"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip().lstrip("v").split(".", 1)[0]
    return int(head) if head.isdigit() else None


def test_high162_bird_search_utils_node_suite():
    major = _node_major_version()
    if major is None or major < 18:
        pytest.skip(f"node >= 18 is required for `node --test` (found: {major})")
    proc = subprocess.run(
        ["node", "--test", str(BIRD_UTILS_TEST)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, f"node --test failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

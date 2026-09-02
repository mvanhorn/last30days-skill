"""Qualifier-only GitHub topics must not embed unbounded raw topics.

Stream fanout calls ``search_github`` once per subquery. Before #954 the
error envelope used ``{topic!r}`` and the strip log used the full core, so
log volume, error-report volume, and doctor-hint size scaled with both
fanout and topic length.
"""

from unittest.mock import patch

from lib import github

_FROM = "2026-07-01"
_TO = "2026-07-31"
# Wrapper text plus a ~120-char topic slice. The pre-fix error embedded
# the entire raw topic (thousands of chars on a pathological planner query).
_ERROR_BOUND = 300
_STRIP_LOG_BOUND = 400


def _long_qualifier_only_topic() -> str:
    return "created:>2025-03-20 " + ("stars:>1 " * 400)


def test_qualifier_only_error_detail_is_bounded():
    topic = _long_qualifier_only_topic()
    with patch.object(github, "_resolve_token", return_value="t"), patch.object(
        github, "_fetch_json"
    ) as fetch:
        result = github.search_github(topic, _FROM, _TO)
    fetch.assert_not_called()
    error = result["error"]
    assert "qualifier" in error.lower()
    assert len(error) < _ERROR_BOUND
    assert len(error) < len(topic)
    assert "created:>2025-03-20" in error
    assert "..." in error


def test_short_qualifier_only_topic_is_fully_shown():
    topic = "created:>2025-03-20"
    with patch.object(github, "_resolve_token", return_value="t"), patch.object(
        github, "_fetch_json"
    ) as fetch:
        result = github.search_github(topic, _FROM, _TO)
    fetch.assert_not_called()
    assert topic in result["error"]
    assert "..." not in result["error"]


def test_qualifier_only_logs_are_bounded_across_fanout():
    topic = _long_qualifier_only_topic()
    logs: list[str] = []
    with patch.object(github, "_resolve_token", return_value="t"), patch.object(
        github, "_fetch_json"
    ) as fetch, patch.object(github, "_log", side_effect=lambda m: logs.append(m)):
        for _ in range(5):
            github.search_github(topic, _FROM, _TO)
    fetch.assert_not_called()
    assert logs
    for msg in logs:
        assert len(msg) < _ERROR_BOUND
        assert topic not in msg


def test_mixed_topic_strip_log_is_bounded():
    subject = "open source ai " * 200
    topic = subject + "stars:>1000 created:>2025-03-20"
    logs: list[str] = []
    with patch.object(github, "_resolve_token", return_value="t"), patch.object(
        github, "_fetch_json", return_value={"items": []}
    ), patch.object(github, "_log", side_effect=lambda m: logs.append(m)):
        github.search_github(topic, _FROM, _TO)
    strip_logs = [m for m in logs if m.startswith("Stripped search qualifiers:")]
    assert strip_logs
    for msg in strip_logs:
        assert len(msg) < _STRIP_LOG_BOUND
        assert subject not in msg

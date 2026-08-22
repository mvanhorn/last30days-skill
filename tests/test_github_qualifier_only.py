"""Empty and qualifier-only GitHub topics must not be classified as ERROR (#953).

Pre-#949, search_github('') built a site-wide created:> window query.
The qualifier strip then returned an error envelope with no network call,
and pipeline._result_outcome_artifact mapped that error to a failed attempt.
"""

from unittest.mock import patch

from lib import github, pipeline, schema


def _search(topic):
    with patch.object(github, "_resolve_token", return_value="test-token"):
        with patch.object(github, "_fetch_json") as mock_fetch:
            result = github.search_github(topic, "2026-07-01", "2026-07-31")
    return result, mock_fetch


def _bundle_from_envelope(envelope):
    artifact = pipeline._result_outcome_artifact("github", envelope)
    bundle = schema.RetrievalBundle()
    bundle.mark_attempted("github")
    outcome = artifact.get("_source_outcome") if isinstance(artifact, dict) else None
    if isinstance(outcome, dict):
        bundle.record_failure(
            "github",
            outcome["state"],
            outcome["detail"],
            attempted=outcome.get("attempted", True),
        )
    items = github.parse_github_response(envelope)
    bundle.add_items("primary", "github", items)
    return artifact, bundle


def test_empty_topic_is_clean_no_results_not_error():
    result, mock_fetch = _search("")
    mock_fetch.assert_not_called()
    assert result["items"] == []
    assert "error" not in result
    artifact, bundle = _bundle_from_envelope(result)
    assert artifact == {}
    assert "github" not in bundle.errors_by_source
    outcome = bundle.source_status["github"]
    assert outcome.state == schema.NO_RESULTS
    assert outcome.attempted is True


def test_qualifier_only_topic_is_clean_no_results_not_error():
    result, mock_fetch = _search("created:>2025-03-20")
    mock_fetch.assert_not_called()
    assert result["items"] == []
    assert "error" not in result
    artifact, bundle = _bundle_from_envelope(result)
    assert artifact == {}
    assert "github" not in bundle.errors_by_source
    assert bundle.source_status["github"].state == schema.NO_RESULTS


def test_noise_plus_qualifier_topic_is_clean_no_results_not_error():
    # extract_core_subject('the best stars:>1000') -> 'stars:>1000' -> plain ''
    result, mock_fetch = _search("the best stars:>1000")
    mock_fetch.assert_not_called()
    assert result["items"] == []
    assert "error" not in result
    artifact, bundle = _bundle_from_envelope(result)
    assert artifact == {}
    assert "github" not in bundle.errors_by_source
    assert bundle.source_status["github"].state == schema.NO_RESULTS

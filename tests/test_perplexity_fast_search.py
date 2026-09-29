"""Fast Search transport contract; no provider calls or credentials required."""
import sys
from unittest.mock import patch

import pytest

import last30days as cli
from lib import env, perplexity

DATE_RANGE = ("2026-09-01", "2026-09-27")
KEY = "LAST30DAYS_PERPLEXITY_SEARCH_TYPE"
SEARCH_RESPONSE = {
    "id": "search-fixture",
    "results": [{"title": "Rust release", "url": "https://example.com/rust",
                 "snippet": "Release source", "date": "2026-09-25"}],
}
AGENT_RESPONSE = {"id": "agent-fixture", "status": "completed", "output_text": "Summary", "output": []}


@pytest.mark.parametrize("search_type", [None, "web", "fast"])
@pytest.mark.parametrize("mode", ["search", "agent", "both"])
def test_transport_type_and_safe_receipt_preserve_filters(search_type, mode):
    config = {"PERPLEXITY_API_KEY": "dummy-test-key",
              "LAST30DAYS_PERPLEXITY_MODE": mode,
              "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER": "example.com",
              "LAST30DAYS_PERPLEXITY_MAX_RESULTS": "25"}
    if search_type:
        config[KEY] = search_type
    responses = {perplexity.PERPLEXITY_SEARCH_URL: SEARCH_RESPONSE,
                 perplexity.PERPLEXITY_AGENT_URL: AGENT_RESPONSE}
    with patch.object(perplexity.http, "post", side_effect=lambda url, *a, **kw: responses[url]) as post:
        items, artifact = perplexity.search("Rust releases", DATE_RANGE, config)
    assert post.call_count == (2 if mode == "both" else 1)
    for call in post.call_args_list:
        is_search = call.args[0] == perplexity.PERPLEXITY_SEARCH_URL
        payload = call.args[1]
        request = payload if is_search else payload["tools"][0]
        receipt = artifact["search" if is_search else "agent"] if mode == "both" else artifact
        recorded = receipt["request"] if is_search else receipt["request"]["tools"][0]
        if search_type:
            assert request["search_type"] == recorded["search_type"] == search_type
        else:
            assert "search_type" not in request
            assert "search_type" not in recorded
        filters = request if is_search else request["filters"]
        assert filters["search_domain_filter"] == ["example.com"]
        assert filters["search_after_date_filter"] == "09/01/2026"
        assert filters["search_before_date_filter"] == "09/27/2026"
        assert request["max_results"] == 20
        assert "dummy-test-key" not in str(receipt)
        if not is_search:
            assert "preset" not in payload
            assert payload["tool_choice"] == {"type": "web_search"}
    if mode == "search":
        assert items[0]["snippet"] == "Release source"
        assert items[0]["date"] == "2026-09-25"


@pytest.mark.parametrize("deep,preset", [(False, "fast"), (False, "high"), (True, None)])
def test_explicit_type_is_independent_of_mutable_agent_preset(deep, preset):
    config = {KEY: "web", "LAST30DAYS_PERPLEXITY_AGENT_PRESET": preset}
    payload, receipt = perplexity._build_agent_payload("test prompt", DATE_RANGE, config, deep)
    assert payload["preset"] == ("high" if deep else preset)
    assert payload["tools"][0]["search_type"] == "web"
    assert receipt["request"]["tools"][0]["search_type"] == "web"


@pytest.mark.parametrize("mode", ["search", "agent", "both"])
def test_invalid_type_makes_no_paid_request(mode):
    with patch.object(perplexity.http, "post") as post:
        items, artifact = perplexity.search("topic", DATE_RANGE, {
            "PERPLEXITY_API_KEY": "dummy-test-key", KEY: "pro",
            "LAST30DAYS_PERPLEXITY_MODE": mode})
    post.assert_not_called()
    assert items == []
    assert "must be web or fast" in str(artifact)


def test_openrouter_keeps_sonar_and_discloses_ignored_search_type():
    response = {"choices": [{"message": {"content": "Summary"}}]}
    with patch.object(perplexity.http, "post", return_value=response) as post, patch.object(perplexity, "_log") as log:
        _, artifact = perplexity.search("topic", DATE_RANGE, {
            "OPENROUTER_API_KEY": "dummy-test-key", KEY: "fast"})
    assert post.call_count == 1
    assert post.call_args.args[0] == perplexity.OPENROUTER_URL
    assert "search_type" not in str(post.call_args.args[1])
    assert artifact["provider"] == "openrouter"
    assert any("does not change OpenRouter" in str(call) for call in log.call_args_list)


@pytest.mark.parametrize("cli_value,expected", [(None, "fast"), ("web", "web")])
def test_cli_override_reaches_config_without_enabling_paid_source(cli_value, expected):
    args = ["last30days.py", "Rust releases"]
    if cli_value:
        args += ["--perplexity-search-type", cli_value]
    class ConfigCaptured(Exception):
        pass
    with patch.object(sys, "argv", args), patch.object(cli.env, "get_config", return_value={KEY: "fast"}), patch.object(cli, "_propagate_config_to_environ", side_effect=ConfigCaptured) as capture:
        with pytest.raises(ConfigCaptured):
            cli.main()
    config = capture.call_args.args[0]
    assert config[KEY] == expected
    assert "INCLUDE_SOURCES" not in config
    assert "LAST30DAYS_PERPLEXITY_MODE" not in config
    assert "LAST30DAYS_PERPLEXITY_AGENT_PRESET" not in config


def test_cli_rejects_sonar_only_search_type():
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["topic", "--perplexity-search-type", "pro"])
    assert exc.value.code == 2


def test_search_type_loads_from_config_file_and_environment(tmp_path, monkeypatch):
    config_file = tmp_path / ".env"
    config_file.write_text(f"{KEY}=fast\n")
    config_file.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", config_file)
    monkeypatch.delenv(KEY, raising=False)
    with patch.object(env, "_find_project_env", return_value=None), patch.object(env, "_load_keychain", return_value={}), patch.object(env, "_load_pass", return_value={}):
        assert env.get_config()[KEY] == "fast"
        monkeypatch.setenv(KEY, "web")
        assert env.get_config()[KEY] == "web"


@pytest.mark.parametrize("mode", ["search", "agent", "both"])
@pytest.mark.parametrize("failure", [
    perplexity.http.HTTPError("unavailable", status_code=503),
    TimeoutError("timed out"),
])
def test_failed_legs_keep_requested_type_without_extra_requests(mode, failure):
    with patch.object(perplexity.http, "post", side_effect=failure) as post:
        items, artifact = perplexity.search("topic", DATE_RANGE, {
            "PERPLEXITY_API_KEY": "dummy-test-key", KEY: "fast",
            "LAST30DAYS_PERPLEXITY_MODE": mode})
    assert post.call_count == (2 if mode == "both" else 1)
    assert items == []
    legs = [artifact["search"], artifact["agent"]] if mode == "both" else [artifact]
    for leg in legs:
        assert leg["error"]
        assert leg["requested_search_type"] == "fast"
        assert "effective_search_type" not in leg
        assert "dummy-test-key" not in str(leg)

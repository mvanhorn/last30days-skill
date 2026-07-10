from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import last30days as cli
from lib import http


def _response(body: str):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.read.return_value = body.encode("utf-8")
    response.status = 200
    return response


def test_http_recording_scrubs_credentials_and_replays_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *_args, **_kwargs: _response('{"items": [{"url": "https://example.test/item"}]}'))
    fixture_dir = tmp_path / "fixture"

    with http.recording_requests(fixture_dir):
        live = http.get("https://api.example.test/search?api_key=live-secret&q=agents")

    fixture_text = (fixture_dir / "http.json").read_text(encoding="utf-8")
    assert "live-secret" not in fixture_text
    assert "%3Credacted%3E" in fixture_text

    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("fixture replay attempted the network"),
    )
    with http.replaying_requests(fixture_dir):
        replayed = http.get("https://api.example.test/search?api_key=another-secret&q=agents")

    assert replayed == live


def test_http_recording_redacts_credentials_echoed_in_response_values(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _response('{"echo": "Bearer live-secret"}'),
    )
    fixture_dir = tmp_path / "fixture"

    with http.recording_requests(fixture_dir):
        http.get(
            "https://api.example.test/profile",
            headers={"Authorization": "Bearer live-secret"},
        )

    fixture_text = (fixture_dir / "http.json").read_text(encoding="utf-8")
    assert "live-secret" not in fixture_text
    assert '"echo": "<redacted>"' in fixture_text


def test_aborted_recording_does_not_overwrite_existing_fixture(tmp_path):
    fixture = tmp_path / "http.json"
    fixture.write_text("existing fixture\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="capture failed"):
        with http.recording_requests(fixture):
            raise RuntimeError("capture failed")

    assert fixture.read_text(encoding="utf-8") == "existing fixture\n"


def test_http_replay_rejects_unrecorded_requests(tmp_path):
    fixture = tmp_path / "http.json"
    fixture.write_text(
        json.dumps({"format": "last30days-http-fixture/v1", "exchanges": []}),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="Unrecorded HTTP request"), \
         http.replaying_requests(fixture):
        http.get("https://example.test/not-recorded")


def test_cli_backed_source_results_record_at_the_module_seam(tmp_path):
    fixture_dir = tmp_path / "fixture"
    request = {
        "source": "digg",
        "topic": "agents",
        "search_query": "agents",
        "date_range": ["2026-06-10", "2026-07-10"],
        "depth": "quick",
    }
    value = [[{"url": "https://di.gg/ai/example"}], {"provider": "fixture"}]

    with http.recording_requests(fixture_dir):
        http.fixture_source_record(request, value)

    with http.replaying_requests(fixture_dir):
        matched, replayed = http.fixture_source_replay(request)

    assert matched is True
    assert replayed == value


def test_module_seam_capture_omits_nested_http_exchanges(tmp_path, monkeypatch):
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *_args, **_kwargs: _response('{"ok": true}'))
    fixture_dir = tmp_path / "fixture"
    request = {
        "source": "youtube",
        "topic": "agents",
        "search_query": "agents",
        "date_range": ["2026-06-10", "2026-07-10"],
        "depth": "quick",
    }

    with http.recording_requests(fixture_dir):
        with http.fixture_module_capture(True):
            http.get("https://api.example.test/nested-enrichment")
        http.fixture_source_record(request, [[], {}])

    payload = json.loads((fixture_dir / "http.json").read_text(encoding="utf-8"))
    assert payload["exchanges"] == []
    assert len(payload["source_exchanges"]) == 1


def test_record_fixtures_flag_is_dev_only_and_hidden_from_help():
    parser = cli.build_parser()
    args = parser.parse_args(["topic", "--record-fixtures", "tmp/eval-topic"])

    assert args.record_fixtures == "tmp/eval-topic"
    assert "--record-fixtures" not in parser.format_help()

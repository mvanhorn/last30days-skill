"""Only authoritative provider response amounts become budget dollars."""

import io
import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from lib import brightdata, grok_x, hosted, http, parallel_mcp, transcribe, usage, xurl_x


@pytest.fixture
def journal(tmp_path, monkeypatch):
    path = tmp_path / "usage.db"
    usage.create_journal(path)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(path))
    return path


def request(monkeypatch, url, payload):
    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(http, "_open_request", lambda req, timeout: Response(json.dumps(payload).encode()))
    return http.post(url, {"model": "example"}, retries=1)


def test_perplexity_agent_records_usd_and_tokens(journal, monkeypatch):
    request(monkeypatch, "https://api.perplexity.ai/v1/agent", {
        "status": "completed",
        "usage": {"input_tokens": 80, "output_tokens": 20, "cost": {"currency": "USD", "total_cost": 0.75}},
    })
    assert usage.read_journal(journal) == {
        "token_cost": 0.75, "cost_unknown": 0, "prompt_tokens": 80, "completion_tokens": 20,
    }


@pytest.mark.parametrize("cost", [None, True, -0.1, float("nan"), float("inf"), "0.50", {}])
def test_invalid_cost_never_becomes_known_spend(journal, monkeypatch, cost):
    request(monkeypatch, "https://openrouter.ai/api/v1/chat/completions", {"usage": {"cost": cost}})
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_usage_counts_and_non_usd_currency_are_unknown(journal, monkeypatch):
    request(monkeypatch, "https://api.perplexity.ai/v1/agent", {
        "usage": {"input_tokens": 1000, "cost": {"currency": "credits", "total_cost": 20}},
    })
    assert usage.read_journal(journal)["cost_unknown"] == 1
    assert usage.read_journal(journal)["token_cost"] == 0


def test_in_progress_remote_charge_is_unknown(journal, monkeypatch):
    request(monkeypatch, "https://api.perplexity.ai/v1/agent", {
        "status": "in_progress", "usage": {"cost": {"currency": "USD", "total_cost": 0.3}},
    })
    assert usage.read_journal(journal)["cost_unknown"] == 1
    assert usage.read_journal(journal)["token_cost"] == 0.3


def test_untrusted_search_result_cannot_add_cost(journal, monkeypatch):
    request(monkeypatch, "https://example.org/article", {"usage": {"cost": 99.9}})
    assert usage.read_journal(journal)["token_cost"] == 0
    assert usage.read_journal(journal)["cost_unknown"] == 0


def test_parallel_requests_keep_every_charge(journal, monkeypatch):
    request(monkeypatch, "https://openrouter.ai/api/v1/chat/completions", {"usage": {"cost": 0.1}})
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(lambda _: http.post("https://openrouter.ai/api/v1/chat/completions", {}), range(20)))
    assert usage.read_journal(journal)["token_cost"] == pytest.approx(2.1)
    assert usage.read_journal(journal)["cost_unknown"] == 0


def test_pending_transport_is_unknown_and_journal_excludes_secrets(journal, monkeypatch):
    def interrupted(req, timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr(http, "_open_request", interrupted)
    with pytest.raises(KeyboardInterrupt):
        http.post("https://openrouter.ai/api/v1/chat/completions?secret=forbidden-query", {"input": "forbidden-prompt"}, headers={"Authorization": "Bearer forbidden-token"})
    assert usage.read_journal(journal)["cost_unknown"] == 1
    assert b"forbidden" not in journal.read_bytes()
    assert journal.stat().st_mode & 0o777 == 0o600


def test_unscoped_calls_have_no_accounting_side_effects(tmp_path, monkeypatch):
    monkeypatch.delenv(usage.JOURNAL_ENV, raising=False)
    request(monkeypatch, "https://openrouter.ai/api/v1/chat/completions", {"usage": {"cost": 0.1}})
    assert list(tmp_path.iterdir()) == []


def test_broken_journal_remains_unknown(journal):
    with sqlite3.connect(journal) as conn:
        conn.execute("DROP TABLE attempts")
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_transcription_charge_without_usd_remains_unknown(journal, tmp_path, monkeypatch):
    audio = tmp_path / "sample.mp3"
    audio.write_bytes(b"dummy-audio")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(b'{"text":"words"}'))
    assert transcribe._post_audio("openai", str(audio), "dummy-key", 1) == "words"
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_brightdata_charge_without_usd_remains_unknown(journal, monkeypatch):
    monkeypatch.setattr(brightdata, "is_installed", lambda: True)
    monkeypatch.setattr(brightdata.subproc, "run_with_timeout", lambda *a, **kw: subprocess.CompletedProcess([], 0, "[]", ""))
    brightdata.run_pipeline("amazon_product_search", [], timeout=1, config={"BRIGHTDATA_API_KEY": "dummy-key"})
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_grok_charge_without_usd_remains_unknown(journal, tmp_path, monkeypatch):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/dummy/grok")
    monkeypatch.setattr(grok_x, "_stage_child_home", lambda _: str(tmp_path))
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["/dummy/grok", "--version"]:
            assert usage.read_journal(journal)["cost_unknown"] == 0
            return subprocess.CompletedProcess(cmd, 0, "grok 1.0.46 (2765805b9442)", "")
        assert cmd[:3] == ["/dummy/grok", "-p", "topic"]
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(grok_x.subprocess, "run", run)
    assert grok_x._invoke("topic", 1) == {"text": "{}"}
    assert len(calls) == 2
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_hosted_charge_without_usd_remains_unknown(journal, monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
    monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-key")
    request(monkeypatch, "https://example.org", {})
    hosted.submit("topic", "quick")
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_authenticated_parallel_tool_charge_without_usd_remains_unknown(journal, monkeypatch):
    class Response(io.BytesIO):
        headers = {"Content-Type": "application/json"}

    class Opener:
        def open(self, *args, **kwargs):
            return Response(b'{"jsonrpc":"2.0","id":1,"result":{}}')

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    parallel_mcp._request({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}, "dummy-key")
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_xurl_search_charge_without_usd_remains_unknown(journal, monkeypatch):
    monkeypatch.setattr(xurl_x.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, '{"data":[]}', ""))
    assert xurl_x.search_x("topic") == {"data": []}
    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_xurl_local_auth_probe_does_not_consume_budget(journal, monkeypatch):
    monkeypatch.setattr(xurl_x.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, "bearer: ✓", ""))
    assert xurl_x._is_available_uncached()
    assert usage.read_journal(journal)["cost_unknown"] == 0


def test_missing_xurl_binary_has_no_charge(journal, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(xurl_x.subprocess, "run", missing)
    assert xurl_x.search_x("topic") == {"error": xurl_x.ERR_NOT_FOUND}
    assert usage.read_journal(journal)["cost_unknown"] == 0

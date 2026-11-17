import json
import re
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import http, pipeline, rerank, schema, subproc, youtube_yt


@pytest.mark.parametrize("slow", [False, True])
def test_transcript_backfill_keeps_videos_within_the_remaining_budget(monkeypatch, slow):
    now = [1000.0]
    calls = []
    cancel = threading.Event()
    transcript = "SQLite explains how write-ahead logging keeps concurrent readers working."
    existing = schema.SourceItem(
        item_id="recorded", source="youtube", title="SQLite introduction",
        body="SQLite introduction", url="https://www.youtube.com/watch?v=recorded",
        metadata={"transcript_snippet": "Existing transcript"},
    )
    survivor = schema.SourceItem(
        item_id="survivor", source="youtube", title="SQLite write-ahead logging",
        body="SQLite write-ahead logging", url="https://www.youtube.com/watch?v=survivor",
    )

    def run_ytdlp(command, *, timeout, deadline_monotonic=None, cancel=None):
        calls.append((timeout, deadline_monotonic, cancel))
        if slow:
            now[0] += timeout
            raise subproc.SubprocTimeout("timed out")
        target = Path(command[command.index("-o") + 1]).parent / "survivor.en.vtt"
        target.write_text(f"WEBVTT\n\n00:00:00.000 --> 00:00:04.000\n{transcript}\n")
        return subproc.SubprocResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt, "_ytdlp_ssh_host", lambda: None)
    monkeypatch.setattr(subproc, "run_with_timeout", run_ytdlp)

    finalized = pipeline._finalize_items_by_source(
        {"youtube": [existing, survivor]}, topic="SQLite", elapsed=275.0,
        config={"_enrich_cancel": cancel},
    )

    assert finalized["youtube"] == [existing, survivor]
    assert existing.metadata["transcript_snippet"] == "Existing transcript"
    assert calls == [(5.0, 1005.0, cancel)]
    if slow:
        assert "transcript_snippet" not in survivor.metadata
    else:
        assert survivor.metadata["transcript_snippet"] == transcript


@pytest.mark.parametrize("scenario", ["fast", "slow", "inherited", "command"])
def test_report_keeps_evidence_when_reranking_runs_out_of_time(monkeypatch, capsys, scenario):
    now = [1000.0]
    timeouts = []
    topic = "SQLite write-ahead logging"
    story = {
        "id": "HN1", "title": topic, "url": "https://example.com/sqlite-wal",
        "author": "sqlite-author", "date": "2026-10-25",
        "engagement": {"points": 84, "comments": 12},
    }
    plan = schema.QueryPlan(
        raw_topic=topic, intent="technical", freshness_mode="balanced_recent",
        cluster_mode="coverage", source_weights={"hackernews": 1.0},
        subqueries=[schema.SubQuery(
            label="primary", search_query=topic, ranking_query=topic,
            sources=["hackernews"],
        )],
    )
    finalize = pipeline._finalize_items_by_source

    def finalize_after_retrieval(*args, **kwargs):
        now[0] += 273.0
        return finalize(*args, **kwargs)

    def open_request(request, timeout):
        timeouts.append(timeout)
        if scenario != "fast":
            now[0] += timeout
            raise TimeoutError("simulated socket timeout")
        prompt = json.loads(request.data)["input"][0]["content"]
        candidate_id = re.search(r"candidate_id: (\S+)", prompt).group(1)
        body = {"scores": [{"candidate_id": candidate_id, "relevance": 94, "fun": 37}]}
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({"output_text": json.dumps(body)}).encode()
        return response

    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(http.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(pipeline, "available_sources", lambda *args, **kwargs: ["hackernews"])
    monkeypatch.setattr(pipeline, "_retrieve_stream", lambda **kwargs: ([story], {}))
    monkeypatch.setattr(http, "_open_request", open_request)

    config = {"XAI_API_KEY": "dummy-key"}
    if scenario == "command":
        def diagnose(*args, **kwargs):
            now[0] += 273.0
            return {"available_sources": ["hackernews"]}

        monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: config)
        monkeypatch.setattr(cli.pipeline, "diagnose", diagnose)
        monkeypatch.setattr(sys, "argv", [
            "last30days.py", topic, "--quick", "--search=hackernews",
            "--as-of=2026-10-26", "--web-backend=none", "--emit=json",
            "--json-profile=raw", "--save-dir=", "--no-browser-cookies",
            "--plan", json.dumps(schema.to_dict(plan)),
        ])
        assert cli.main() == 0
        report = schema.report_from_dict(json.loads(capsys.readouterr().out))
    else:
        if scenario == "inherited":
            config["_research_deadline"] = 1280.0
        monkeypatch.setattr(pipeline, "_finalize_items_by_source", finalize_after_retrieval)
        report = pipeline.run(
            topic=topic, config=config, depth="quick",
            requested_sources=["hackernews"], external_plan=schema.to_dict(plan),
            as_of_date="2026-10-26", web_backend="none",
        )

    assert [item.url for item in report.items_by_source["hackernews"]] == [story["url"]]
    candidate, = report.ranked_candidates
    if scenario != "fast":
        assert timeouts == ([] if scenario == "inherited" else [7.0])
        assert candidate.metadata[rerank.RERANK_PROVENANCE_KEY] == rerank.RERANK_PROVENANCE_FALLBACK
        assert candidate.fun_explanation == "heuristic-fallback"
    else:
        assert timeouts == [7.0, 7.0]
        assert candidate.rerank_score == 94
        assert candidate.fun_score == 37

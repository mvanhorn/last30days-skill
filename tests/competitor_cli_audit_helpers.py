"""Run real CLI/fanout wiring while collecting the research boundary inputs."""

import sys
from types import SimpleNamespace
from unittest import mock

import last30days as cli
from lib import resolve, schema


def run_competitor_cli(
    monkeypatch, tmp_path, *, args=(), config=None, has_backend=False, resolutions=None
):
    config = {} if config is None else config
    calls = {}
    reports = {}

    def research(**kwargs):
        topic = kwargs["topic"]
        calls[topic] = kwargs
        report = schema.Report(
            topic=topic, range_from="2026-06-01", range_to="2026-06-30",
            generated_at="2026-06-30T12:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "none", "none"),
            query_plan=schema.QueryPlan("general", "recent", "none", topic, [], {}),
            clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
        )
        reports[topic] = report
        return report

    if isinstance(resolutions, Exception):
        resolver = mock.Mock(side_effect=resolutions)
    else:
        resolver = mock.Mock(side_effect=lambda topic, _config: (resolutions or {}).get(topic, {}))
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: config)
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cli.env, "CONFIG_FILE", tmp_path / "config" / ".env")
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: {"available_sources": ["reddit"]})
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(resolve, "_has_backend", lambda _config: has_backend)
    monkeypatch.setattr(resolve, "auto_resolve", resolver)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", "")
    monkeypatch.setenv("LAST30DAYS_STORE", "")
    monkeypatch.delenv("LAST30DAYS_API_BASE", raising=False)
    monkeypatch.delenv("LAST30DAYS_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "MainBrand", "--competitors-list", "PeerOne,PeerTwo",
        "--emit=json", "--json-profile=raw", "--save-dir=", *args,
    ])

    assert cli.main() == 0
    assert set(calls) == {"MainBrand", "PeerOne", "PeerTwo"}
    return SimpleNamespace(calls=calls, reports=reports, resolver=resolver)

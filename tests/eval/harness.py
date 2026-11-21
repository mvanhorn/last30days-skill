"""Offline research-quality eval harness built on recorded source responses."""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "last30days" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import entity_extract, fetch_memo, http, pipeline, public_host, schema  # noqa: E402

FIXTURES_DIR = Path(__file__).with_name("fixtures")
BASELINE_PATH = Path(__file__).with_name("baseline.json")
METRIC_NAMES = (
    "citation_grounding",
    "recency_compliance",
    "cluster_coherence",
    "coverage",
    "determinism",
)
ENTITY_OVERLAP_FLOOR = 0.45
# The address a fixture's listed public host resolves to during replay.
FIXTURE_PUBLIC_ADDRESS = "93.184.216.34"


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        fixed = cls(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        return fixed if tz is not None else fixed.replace(tzinfo=None)


@dataclass(frozen=True)
class EvalFixture:
    name: str
    path: Path
    manifest: dict[str, Any]
    input_urls: frozenset[str]


@dataclass
class EvalResult:
    fixture: EvalFixture
    report: schema.Report
    scores: dict[str, float]


def _http_urls(value: Any) -> set[str]:
    urls: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            urls.update(_http_urls(child))
    elif isinstance(value, list):
        for child in value:
            urls.update(_http_urls(child))
    elif isinstance(value, str) and value.startswith(("https://", "http://")):
        urls.add(value)
    return urls


def _polymarket_event_urls(request: dict[str, Any], value: Any) -> set[str]:
    """Polymarket event URLs a Gamma response grounds. Gamma returns each
    event's slug, not its page URL; the parser builds the URL from the slug."""
    if not str(request.get("url") or "").startswith("https://gamma-api.polymarket.com/"):
        return set()
    events = value.get("events") if isinstance(value, dict) else value
    return {
        f"https://polymarket.com/event/{event['slug']}"
        for event in events or []
        if isinstance(event, dict) and event.get("slug")
    }


def load_fixtures(root: Path = FIXTURES_DIR) -> list[EvalFixture]:
    fixtures: list[EvalFixture] = []
    for manifest_path in sorted(root.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        http_payload = json.loads((manifest_path.parent / "http.json").read_text(encoding="utf-8"))
        input_urls: set[str] = set()
        for exchange in http_payload.get("exchanges") or []:
            value = (exchange.get("response") or {}).get("value")
            input_urls.update(_http_urls(value))
            input_urls.update(_polymarket_event_urls(exchange.get("request") or {}, value))
        for exchange in http_payload.get("source_exchanges") or []:
            input_urls.update(_http_urls(exchange.get("value")))
        fixtures.append(
            EvalFixture(
                name=manifest.get("name") or manifest_path.parent.name,
                path=manifest_path.parent,
                manifest=manifest,
                input_urls=frozenset(input_urls),
            )
        )
    return fixtures


@contextlib.contextmanager
def _fixture_dns(public_hosts: list[str]):
    """Resolve only the fixture's listed hosts, each to one public address.

    Lanes that fetch from a site named in remote content check that the name
    resolves to public addresses before fetching. Replay never reaches the
    network, so the fixture names which hosts are public; every other name
    fails to resolve and the check still refuses it.
    """
    if not public_hosts:
        yield
        return
    known = {host.lower() for host in public_hosts}

    def getaddrinfo(name, *_args, **_kwargs):
        if str(name).lower() not in known:
            raise socket.gaierror(f"{name} is not a fixture host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (FIXTURE_PUBLIC_ADDRESS, 0))]

    public_host.resolves_public.cache_clear()
    try:
        with mock.patch.object(public_host.socket, "getaddrinfo", getaddrinfo):
            yield
    finally:
        public_host.resolves_public.cache_clear()


def _resolve_person(
    fixture: EvalFixture, config: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Run the CLI's person resolve step for a person-run fixture.

    The manifest's ``person`` object carries the person flags (``employer``,
    ``linkedin``, ``email``). The resolved context joins ``config`` as the
    CLI's does; the returned (x_handle, github_user) are the handles the step
    derived from the person's own links. A fixture without ``person`` is not
    a person run: nothing changes and both handles are None.
    """
    flags = fixture.manifest.get("person")
    if not flags:
        return None, None
    import last30days as cli

    args = argparse.Namespace(
        employer=flags.get("employer"),
        linkedin=flags.get("linkedin"),
        email=flags.get("email"),
        mock=False,
        x_handle=None,
        github_user=None,
        no_x_handle=None,
    )
    ctx = cli._person_resolve_step(
        args, fixture.manifest["topic"], config, list(fixture.manifest["fixture_sources"]),
    )
    if ctx is not None:
        config["_person"] = ctx
    return args.x_handle, args.github_user


def _run_once(fixture: EvalFixture) -> schema.Report:
    manifest = fixture.manifest
    config = dict(manifest.get("config") or {})
    network_error = AssertionError(f"Network attempted while replaying {fixture.name}")
    # Each replay is one command: a fetch memoized by an earlier run in this
    # process would leave its recorded exchanges unused.
    fetch_memo.reset()
    with mock.patch.object(pipeline, "datetime", _FrozenDateTime), \
         mock.patch.object(schema, "datetime", _FrozenDateTime), \
         mock.patch.object(
             pipeline,
             "available_sources",
             return_value=list(manifest["fixture_sources"]),
         ), \
         mock.patch.object(http.urllib.request, "urlopen", side_effect=network_error), \
         _fixture_dns(list(manifest.get("public_hosts") or [])), \
         http.replaying_requests(fixture.path):
        x_handle, github_user = _resolve_person(fixture, config)
        return pipeline.run(
            topic=manifest["topic"],
            config=config,
            depth=manifest.get("depth", "quick"),
            requested_sources=list(manifest["fixture_sources"]),
            mock=False,
            x_handle=x_handle,
            github_user=github_user,
            web_backend=manifest.get("web_backend", "none"),
            external_plan=manifest["plan"],
            lookback_days=int(manifest.get("lookback_days", 30)),
            as_of_date=manifest["as_of_date"],
        )


def _citation_grounding(report: schema.Report, fixture: EvalFixture) -> float:
    results = schema.to_agent_export(report)["results"]
    if not results:
        return 0.0
    grounded = sum(bool(result.get("url")) and result["url"] in fixture.input_urls for result in results)
    return grounded / len(results)


def _recency_compliance(report: schema.Report) -> float:
    ranked_items = [
        item
        for candidate in report.ranked_candidates
        for item in candidate.source_items
    ]
    if not ranked_items:
        return 0.0
    compliant = sum(
        not item.published_at
        or report.range_from <= item.published_at[:10] <= report.range_to
        for item in ranked_items
    )
    return compliant / len(ranked_items)


def _cluster_coherence(report: schema.Report, fixture: EvalFixture) -> float:
    candidates = {candidate.candidate_id: candidate for candidate in report.ranked_candidates}
    pair_scores: list[float] = []
    for report_cluster in report.clusters:
        members = [candidates[candidate_id] for candidate_id in report_cluster.candidate_ids if candidate_id in candidates]
        for left, right in itertools.combinations(members, 2):
            left_entities = entity_extract.extract_text_entities(f"{left.title} {left.snippet}")
            right_entities = entity_extract.extract_text_entities(f"{right.title} {right.snippet}")
            overlap = entity_extract.entity_overlap(left_entities, right_entities)
            pair_scores.append(1.0 if overlap >= ENTITY_OVERLAP_FLOOR else 0.0)
    if pair_scores:
        return sum(pair_scores) / len(pair_scores)
    # No multi-member clusters formed. For fixtures that historically cluster
    # (expects_clusters: true in the manifest), that means cluster formation
    # regressed and must fail rather than score a vacuous 1.0. Sparse topics
    # that legitimately produce singletons declare expects_clusters: false.
    return 0.0 if fixture.manifest.get("expects_clusters") else 1.0


def _coverage(report: schema.Report, fixture: EvalFixture) -> float:
    sources = list(fixture.manifest["fixture_sources"])
    if not sources:
        return 0.0
    covered = sum(
        bool(report.items_by_source.get(source)) or source in report.source_status
        for source in sources
    )
    return covered / len(sources)


def score_report(
    report: schema.Report,
    fixture: EvalFixture,
    *,
    deterministic: bool,
) -> dict[str, float]:
    return {
        "citation_grounding": _citation_grounding(report, fixture),
        "recency_compliance": _recency_compliance(report),
        "cluster_coherence": _cluster_coherence(report, fixture),
        "coverage": _coverage(report, fixture),
        "determinism": 1.0 if deterministic else 0.0,
    }


def evaluate_fixture(fixture: EvalFixture) -> EvalResult:
    first = _run_once(fixture)
    second = _run_once(fixture)
    deterministic = schema.to_dict(first) == schema.to_dict(second)
    return EvalResult(
        fixture=fixture,
        report=first,
        scores=score_report(first, fixture, deterministic=deterministic),
    )


def evaluate_all(fixtures: list[EvalFixture] | None = None) -> list[EvalResult]:
    selected = fixtures if fixtures is not None else load_fixtures()
    if not selected:
        raise AssertionError(f"No eval fixtures found under {FIXTURES_DIR}")
    return [evaluate_fixture(fixture) for fixture in selected]


def aggregate_scores(results: list[EvalResult]) -> dict[str, float]:
    return {
        metric: sum(result.scores[metric] for result in results) / len(results)
        for metric in METRIC_NAMES
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {metric: float(payload["metrics"][metric]) for metric in METRIC_NAMES}


def baseline_failures(
    scores: dict[str, float],
    baseline: dict[str, float] | None = None,
) -> list[str]:
    floors = baseline if baseline is not None else load_baseline()
    return [
        f"{metric}: {scores[metric]:.3f} < {floors[metric]:.3f}"
        for metric in METRIC_NAMES
        if scores[metric] + 1e-12 < floors[metric]
    ]


def per_fixture_failures(results: list["EvalResult"]) -> list[str]:
    """Enforce per-fixture floors so one broken archetype cannot hide in the
    cross-fixture average (a total clustering failure on breaking-event scores
    0.0 but averages to 0.857 across seven fixtures)."""
    raw = json.loads(BASELINE_PATH.read_text())
    floors = raw.get("per_fixture_floors") or {}
    failures: list[str] = []
    for result in results:
        for metric, floor in floors.items():
            value = result.scores.get(metric)
            if value is not None and value + 1e-12 < float(floor):
                failures.append(
                    f"{result.fixture.name}/{metric}: {value:.3f} < {float(floor):.3f}"
                )
    return failures


def format_score_table(results: list[EvalResult]) -> str:
    aggregate = aggregate_scores(results)
    headers = ["fixture", *METRIC_NAMES]
    rows = [
        [result.fixture.name, *(f"{result.scores[name]:.3f}" for name in METRIC_NAMES)]
        for result in results
    ]
    rows.append(["AVERAGE", *(f"{aggregate[name]:.3f}" for name in METRIC_NAMES)])
    widths = [max(len(str(row[index])) for row in [headers, *rows]) for index in range(len(headers))]
    rendered = [" | ".join(str(value).ljust(widths[index]) for index, value in enumerate(headers))]
    rendered.append("-+-".join("-" * width for width in widths))
    rendered.extend(
        " | ".join(str(value).ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )
    return "\n".join(rendered)


def main() -> int:
    results = evaluate_all()
    print(format_score_table(results))
    failures = baseline_failures(aggregate_scores(results))
    if failures:
        print("\nBaseline failures:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

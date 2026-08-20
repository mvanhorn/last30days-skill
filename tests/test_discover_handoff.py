"""U1 - discovery handoff file contracts for the three-leg host-judged protocol.

Leg 1 (``--discover --nominate-only``) writes a nominations bundle carrying
the FULL judge pool losslessly; leg 2 (``--discover --judgments <file>``)
binds host judgments to that bundle by bundle_id; leg 3 (``--discover
--finalize [--angles <file>]``) applies host-written angles. This file pins
the bundle writer/reader round-trip, strict-top-level / lenient-per-row
reader semantics, TTL and version gating, sanitation, collision handling,
and the host-facing digest.
"""

import inspect
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from lib import discovery_handoff as handoff
from lib import pipeline, rerank, schema


def _item(
    item_id: str,
    source: str,
    title: str,
    *,
    published_at: str = "2026-07-18",
    engagement: dict[str, int | float] | None = None,
    snippet: str = "",
    metadata: dict | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=f"https://{source}.example/{item_id}",
        published_at=published_at,
        engagement=engagement or {},
        snippet=snippet or f"Evidence about {title}",
        metadata=metadata or {},
    )


def _nomination(
    name: str,
    items: list[schema.SourceItem],
    *,
    seed_score: float = 42.5,
    summary: str = "",
    junk_shape: bool = False,
    worthiness: float | None = None,
) -> pipeline.Nomination:
    return pipeline.Nomination(
        name=name,
        seed_score=seed_score,
        items=items,
        summary=summary or f"Summary of {name}",
        junk_shape=junk_shape,
        worthiness=worthiness,
    )


def _entry(
    nomination: pipeline.Nomination,
    *,
    cluster_id: str = "c1",
    heuristic_name: str | None = None,
    heuristic_junk: bool = False,
) -> "handoff.PoolEntry":
    return handoff.PoolEntry(
        nomination=nomination,
        cluster_id=cluster_id,
        heuristic_name=heuristic_name if heuristic_name is not None else nomination.name,
        heuristic_junk=heuristic_junk,
    )


def _pool() -> list["handoff.PoolEntry"]:
    agent = _nomination(
        "Agent SDK Wars",
        [
            _item(
                "hn1", "hackernews",
                "Agent SDK Wars heat up as Anthropic ships a Claude agent runtime",
                engagement={"points": 900, "comments": 400},
            ),
            _item(
                "rd1", "reddit",
                "Agent SDK wars: which runtime are you betting on?",
                engagement={"score": 300, "num_comments": 80},
                metadata={"top_comments": [{
                    "excerpt": "The SDK churn is unsustainable for small teams",
                    "score": 1635,
                    "author": "dev_a",
                }]},
            ),
        ],
        seed_score=61.2,
    )
    quantum = _nomination(
        "Quantum Error Correction",
        [
            _item(
                "hn2", "hackernews",
                "Quantum error correction milestone announced",
                engagement={"points": 250, "comments": 60},
            ),
        ],
        seed_score=18.4,
    )
    return [
        _entry(agent, cluster_id="c-agent", heuristic_junk=False),
        _entry(quantum, cluster_id="c-quantum", heuristic_junk=True),
    ]


def _write(config_dir, entries=None, **overrides) -> "handoff.NominationsBundle":
    kwargs = dict(
        domain="AI",
        tier="deep",
        from_date="2026-06-21",
        to_date="2026-07-21",
        lookback_days=30,
        enrichment_source_boundary=None,
        requested_sources=["hackernews", "reddit"],
        save_dir=None,
        config_dir=config_dir,
    )
    kwargs.update(overrides)
    return handoff.write_nominations_bundle(
        entries if entries is not None else _pool(), **kwargs
    )


def _judgments_file(tmp_path, payload) -> "Path":
    path = tmp_path / "judgments.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- Scenario 1: bundle round-trip ------------------------------------------


def test_bundle_round_trip_is_lossless(tmp_path):
    written = _write(tmp_path)
    read = handoff.read_nominations_bundle(save_dir=None, config_dir=tmp_path)
    assert read.bundle_id == written.bundle_id
    assert read.schema_version == schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION
    assert (read.from_date, read.to_date) == ("2026-06-21", "2026-07-21")
    assert read.domain == "AI"
    assert read.tier == "deep"
    assert read.lookback_days == 30
    assert read.enrichment_source_boundary is None
    assert read.requested_sources == ["hackernews", "reddit"]
    assert [row.nomination_id for row in read.nominations] == ["n1", "n2"]
    for row, entry in zip(read.nominations, _pool()):
        # Full dataclass equality: name, seed_score, every seed item field,
        # summary, junk_shape, worthiness.
        assert row.nomination == entry.nomination
        assert row.cluster_id == entry.cluster_id
        assert row.heuristic_name == entry.heuristic_name
        assert row.heuristic_junk == entry.heuristic_junk
        assert row.sources == sorted({i.source for i in entry.nomination.items})
    assert read.path == tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME


def test_save_dir_takes_precedence_over_config_dir(tmp_path):
    save_dir = tmp_path / "saves"
    config_dir = tmp_path / "config"
    written = _write(config_dir, save_dir=save_dir)
    assert written.path == save_dir / handoff.NOMINATIONS_BUNDLE_FILENAME
    read = handoff.read_nominations_bundle(save_dir=save_dir, config_dir=config_dir)
    assert read.bundle_id == written.bundle_id


def test_source_boundary_and_shallow_tier_survive_round_trip(tmp_path):
    _write(
        tmp_path,
        tier="shallow",
        enrichment_source_boundary=["reddit", "hackernews"],
        requested_sources=None,
        lookback_days=7,
    )
    read = handoff.read_nominations_bundle(config_dir=tmp_path)
    assert read.tier == "shallow"
    assert read.enrichment_source_boundary == ["reddit", "hackernews"]
    assert read.requested_sources is None
    assert read.lookback_days == 7


def test_bundle_round_trips_sweep_source_status_and_mock_flag(tmp_path):
    """F1a/F19: the leg-1 sweep's per-source outcomes (including degraded
    states) and the mock provenance flag ride in the bundle so legs 2-3 can
    restore them - reusing the schema round-trip, never a parallel shape."""
    status = {
        "hackernews": schema.SourceOutcome(
            source="hackernews", state="ok", items_returned=2,
            at="2026-07-21T00:00:00Z",
        ),
        "reddit": schema.SourceOutcome(
            source="reddit", state=schema.UNREACHABLE, detail="dns failure",
            at="2026-07-21T00:00:01Z", fix_hint="doctor",
        ),
    }
    written = _write(tmp_path, source_status=status, mock=True)
    assert written.source_status == status
    assert written.mock is True
    read = handoff.read_nominations_bundle(config_dir=tmp_path)
    assert read.source_status == status
    assert read.mock is True


def test_bundle_reader_defaults_source_status_and_mock_for_older_files(tmp_path):
    """Older bundles carry neither key: restore an empty status map and a
    real (mock=False) provenance."""
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("source_status", None)
    payload.pop("mock", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    read = handoff.read_nominations_bundle(config_dir=tmp_path)
    assert read.source_status == {}
    assert read.mock is False


# --- Scenario 2: parity pin --------------------------------------------------


def test_parity_floor_and_velocity_inputs_survive_round_trip(tmp_path):
    entries = _pool()
    _write(tmp_path, entries)
    read = handoff.read_nominations_bundle(config_dir=tmp_path)
    for row, entry in zip(read.nominations, entries):
        before, after = entry.nomination.items, row.nomination.items
        assert len(after) == len(before)
        assert [i.engagement for i in after] == [i.engagement for i in before]
        assert [i.source for i in after] == [i.source for i in before]
        assert [i.published_at for i in after] == [i.published_at for i in before]
        # Entity-token disambiguation inputs (title + snippet) are lossless.
        assert [(i.title, i.snippet) for i in after] == [
            (i.title, i.snippet) for i in before
        ]
        # Velocity and floor inputs recompute identically to an in-memory run.
        assert rerank.discovery_velocity_score(
            after, as_of_date="2026-07-21"
        ) == rerank.discovery_velocity_score(before, as_of_date="2026-07-21")
        assert sum(rerank.discovery_engagement_total(i) for i in after) == sum(
            rerank.discovery_engagement_total(i) for i in before
        )
        assert {i.source for i in after} == {i.source for i in before}


# --- Scenario 3: judgments reader --------------------------------------------


def test_judgments_apply_by_id_with_per_row_leniency(tmp_path, capsys):
    bundle = _write(tmp_path)
    path = _judgments_file(tmp_path, {
        "bundle_id": bundle.bundle_id,
        "judgments": [
            {"id": "n1", "name": "Claude Agent Runtime Launch", "junk": False,
             "worthiness": 78},
            {"id": "n9", "name": "Ghost Topic", "worthiness": 50},
        ],
    })
    judgments = handoff.read_judgments(
        path, bundle, save_dir=None, config_dir=tmp_path
    )
    assert judgments["n1"] == handoff.HostJudgment(
        name="Claude Agent Runtime Launch", junk=False, worthiness=78,
    )
    # Unknown id: warned (always visible, tty_only=False) and ignored.
    assert "n9" not in judgments
    assert "n9" in capsys.readouterr().err
    # n2 omitted entirely -> per-row-absent marker; the caller falls back to
    # the bundle's heuristic name/junk.
    assert handoff.judgment_for(judgments, "n2") is handoff.ROW_ABSENT
    assert handoff.ROW_ABSENT.name is None
    assert handoff.ROW_ABSENT.junk is None
    assert handoff.ROW_ABSENT.worthiness is None


def test_judgments_worthiness_clamped_to_0_100_integers(tmp_path):
    bundle = _write(tmp_path)
    path = _judgments_file(tmp_path, {
        "bundle_id": bundle.bundle_id,
        "judgments": [
            {"id": "n1", "worthiness": 150},
            {"id": "n2", "worthiness": -3.7},
        ],
    })
    judgments = handoff.read_judgments(path, bundle)
    assert judgments["n1"].worthiness == 100
    assert judgments["n2"].worthiness == 0
    assert isinstance(judgments["n1"].worthiness, int)
    # No name on either row -> per-row-absent name and junk.
    assert judgments["n1"].name is None
    assert judgments["n1"].junk is None


def test_junk_accepted_even_without_usable_name(tmp_path):
    bundle = _write(tmp_path)
    path = _judgments_file(tmp_path, {
        "bundle_id": bundle.bundle_id,
        "judgments": [{"id": "n2", "name": "\U0001f525\U0001f525\U0001f525",
                       "junk": True}],
    })
    judgments = handoff.read_judgments(path, bundle)
    assert judgments["n2"].junk is True
    # Emoji-only sanitizes to empty = per-row-absent name.
    assert judgments["n2"].name is None


def test_junk_null_and_string_false_are_per_row_absent(tmp_path):
    """Only real JSON booleans count as a junk verdict. ``"junk": null`` and
    ``"junk": "false"`` are per-row-absent (fall back to the bundle
    heuristic) - a truthy non-empty string must never read as junk=True."""
    bundle = _write(tmp_path)
    path = _judgments_file(tmp_path, {
        "bundle_id": bundle.bundle_id,
        "judgments": [
            {"id": "n1", "junk": None, "worthiness": 60},
            {"id": "n2", "junk": "false", "worthiness": 40},
        ],
    })
    judgments = handoff.read_judgments(path, bundle)
    assert judgments["n1"].junk is None
    assert judgments["n2"].junk is None
    # The rest of each row still applies.
    assert judgments["n1"].worthiness == 60
    assert judgments["n2"].worthiness == 40


def test_bundle_reader_warns_and_keeps_valid_rows(tmp_path, capsys):
    """Lenient per row: a non-object row and a row whose nested nomination
    fails to construct are each warned (always visible) and skipped, while
    every valid row still parses."""
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["nominations"] = [
        "not an object",
        {"id": "nbad", "nomination": {"worthiness": "not-a-number"}},
        *payload["nominations"],
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    read = handoff.read_nominations_bundle(config_dir=tmp_path)
    assert [row.nomination_id for row in read.nominations] == ["n1", "n2"]
    err = capsys.readouterr().err
    assert "skipping malformed nomination row 1" in err
    assert "skipping unparseable nomination row 2" in err


def test_judgments_reader_warns_on_non_object_and_blank_id_rows(tmp_path, capsys):
    bundle = _write(tmp_path)
    path = _judgments_file(tmp_path, {
        "bundle_id": bundle.bundle_id,
        "judgments": [
            "not an object",
            {"name": "No Id Here", "worthiness": 90},
            {"id": "   ", "worthiness": 90},
            {"id": "n1", "worthiness": 70},
        ],
    })
    judgments = handoff.read_judgments(path, bundle)
    assert set(judgments) == {"n1"}
    assert judgments["n1"].worthiness == 70
    err = capsys.readouterr().err
    assert "skipping malformed judgments row (not an object)" in err
    assert err.count("skipping judgments row with no nomination id") == 2


def test_angles_reader_warns_on_non_object_and_blank_id_rows(tmp_path, capsys):
    bundle = _write(tmp_path)
    path = tmp_path / "angles.json"
    path.write_text(json.dumps({
        "bundle_id": bundle.bundle_id,
        "angles": [
            "not an object",
            {"podcast": "No id on this row"},
            {"id": "", "podcast": "Blank id"},
            {"id": "n1", "podcast": "A real hook about agent SDK churn"},
        ],
    }), encoding="utf-8")
    angles = handoff.read_angles(path, bundle)
    assert set(angles) == {"n1"}
    assert angles["n1"].podcast == "A real hook about agent SDK churn"
    err = capsys.readouterr().err
    assert "skipping malformed angles row (not an object)" in err
    assert err.count("skipping angles row with no nomination id") == 2


# --- Scenario 4: error matrix -------------------------------------------------


def test_error_unreadable_bundle_file(tmp_path):
    # A directory at the bundle path exists but cannot be read as a file.
    (tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME).mkdir()
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    assert excinfo.value.message


def test_error_invalid_json(tmp_path):
    (tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME).write_text(
        "{not json", encoding="utf-8"
    )
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    assert "JSON" in excinfo.value.message


def test_error_top_level_non_dict(tmp_path):
    (tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME).write_text(
        "[]", encoding="utf-8"
    )
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_nominations_bundle(config_dir=tmp_path)


def test_error_bundle_nominations_must_be_a_list(tmp_path):
    """A dict (or anything non-list) under "nominations" is corrupt state:
    fail closed with the re-sweep remedy, never an empty pool."""
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["nominations"] = {"n1": {"id": "n1"}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    message = excinfo.value.message
    assert "nominations" in message
    assert "--discover --nominate-only" in message


def test_error_bundle_all_rows_malformed_fails_closed(tmp_path):
    """Leg 1 never writes an empty bundle (a zero-nomination sweep
    short-circuits with no bundle file), so a non-empty nominations array
    that parses to ZERO valid rows is corrupt state: HandoffContractError
    with the re-sweep remedy, not a silent empty pool."""
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["nominations"] = [
        "not an object",
        {"id": "n1", "nomination": {"worthiness": "not-a-number"}},
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    assert "--discover --nominate-only" in excinfo.value.message


def test_error_bundle_empty_nominations_list_fails_closed(tmp_path):
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["nominations"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    assert "--discover --nominate-only" in excinfo.value.message


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permission bits",
)
def test_write_bundle_unwritable_dir_is_contract_error_not_traceback(tmp_path):
    """A locked/read-only/full state dir must be the protocol's clean exit-2
    path (HandoffContractError naming the path), never a raw OSError."""
    state_dir = tmp_path / "readonly"
    state_dir.mkdir()
    state_dir.chmod(0o500)
    try:
        with pytest.raises(handoff.HandoffContractError) as excinfo:
            _write(state_dir)
        message = excinfo.value.message
        assert str(state_dir / handoff.NOMINATIONS_BUNDLE_FILENAME) in message
        assert "Permission denied" in message
    finally:
        state_dir.chmod(0o700)


def test_error_wrong_schema_version(tmp_path):
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "99.0"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    assert "99.0" in excinfo.value.message


def test_error_stale_ttl(tmp_path):
    _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=handoff.DISCOVERY_HANDOFF_TTL_SECONDS + 60
    )
    payload["generated_at"] = stale.isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(config_dir=tmp_path)
    assert "--discover --nominate-only" in excinfo.value.message


def test_ttl_is_not_the_report_cache_env_knob(tmp_path, monkeypatch):
    """A user who lowered LAST30DAYS_REPORT_CACHE_TTL_SECONDS for drill
    freshness must not shrink the judgment-authoring window."""
    monkeypatch.setenv("LAST30DAYS_REPORT_CACHE_TTL_SECONDS", "1")
    written = _write(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    two_minutes_old = datetime.now(timezone.utc) - timedelta(seconds=120)
    payload["generated_at"] = two_minutes_old.isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    read = handoff.read_nominations_bundle(config_dir=tmp_path)
    assert read.bundle_id == written.bundle_id
    assert handoff.DISCOVERY_HANDOFF_TTL_SECONDS == 3600.0


def test_error_bundle_not_found_with_save_dir_names_only_save_dir(tmp_path):
    """An explicit save dir is the protocol's single handoff store: the
    not-found error names ONLY the save-dir location, never the config dir."""
    save_dir = tmp_path / "saves"
    config_dir = tmp_path / "config"
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(save_dir=save_dir, config_dir=config_dir)
    message = excinfo.value.message
    assert str(save_dir / handoff.NOMINATIONS_BUNDLE_FILENAME) in message
    assert str(config_dir) not in message
    assert "--discover --nominate-only" in message
    assert message.rstrip().endswith("re-sweep.")


def test_error_bundle_not_found_without_save_dir_names_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(save_dir=None, config_dir=config_dir)
    message = excinfo.value.message
    assert str(config_dir / handoff.NOMINATIONS_BUNDLE_FILENAME) in message
    assert "--discover --nominate-only" in message


def test_explicit_save_dir_never_falls_back_to_config_bundle(tmp_path):
    """SKILL.md contract: a different or missing save dir on a later leg
    means the leg cannot find the handoff files. A fresh bundle in the
    config dir must never silently satisfy a save-dir run (the same
    scoping _scoped_store_db applies to research.db)."""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write(config_dir)  # fresh, valid bundle in the config store
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_nominations_bundle(save_dir=save_dir, config_dir=config_dir)
    message = excinfo.value.message
    assert str(save_dir / handoff.NOMINATIONS_BUNDLE_FILENAME) in message
    assert str(config_dir) not in message


def test_error_bundle_id_mismatch_names_locations_and_fix_id_remedy(tmp_path):
    """A bundle_id MISMATCH means the host echoed the wrong id: the remedy is
    to correct the bundle_id field and re-run this same leg - never the
    expensive re-sweep/resume remedies (those belong to missing/stale
    state)."""
    save_dir = tmp_path / "saves"
    config_dir = tmp_path / "config"
    bundle = _write(config_dir)
    path = _judgments_file(tmp_path, {
        "bundle_id": "deadbeefdeadbeef",
        "judgments": [],
    })
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_judgments(path, bundle, save_dir=save_dir, config_dir=config_dir)
    message = excinfo.value.message
    # Both ids and the searched location (save dir only: explicit save dir
    # is the single handoff store) stay named.
    assert "deadbeefdeadbeef" in message
    assert bundle.bundle_id in message
    assert str(save_dir / handoff.NOMINATIONS_BUNDLE_FILENAME) in message
    assert str(config_dir) not in message
    # The remedy is the cheap one: fix the id, re-run this leg.
    assert "Correct the bundle_id field in your judgments file" in message
    assert "re-run this same leg" in message
    # The expensive-leg remedies must NOT appear on a mismatch.
    assert "--discover --nominate-only" not in message
    assert "--discover --judgments" not in message
    assert "re-sweep" not in message


def test_error_unreadable_judgments_path(tmp_path):
    bundle = _write(tmp_path)
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_judgments(tmp_path / "missing.json", bundle)


def test_error_judgments_top_level_strict(tmp_path):
    bundle = _write(tmp_path)
    # Missing the "judgments" list entirely: strict at top level.
    path = _judgments_file(tmp_path, {"bundle_id": bundle.bundle_id})
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_judgments(path, bundle)
    # Top-level non-dict.
    non_dict = tmp_path / "non-dict.json"
    non_dict.write_text('["not", "a", "dict"]', encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_judgments(non_dict, bundle)


# --- Scenario 5: sanitation and collisions ------------------------------------


def test_long_host_name_truncates_at_word_boundary(tmp_path):
    bundle = _write(tmp_path)
    long_name = " ".join(["momentum"] * 40)  # well over 96 chars
    path = _judgments_file(tmp_path, {
        "bundle_id": bundle.bundle_id,
        "judgments": [{"id": "n1", "name": long_name}],
    })
    judgments = handoff.read_judgments(path, bundle)
    name = judgments["n1"].name
    assert name is not None
    assert len(name) <= 96
    # Cut at a word boundary: no partial trailing token.
    assert set(name.split()) == {"momentum"}


def test_case_only_name_collisions_disambiguate_not_collapse():
    first = _nomination(
        "Agent Wars",
        [_item("hn1", "hackernews",
               "Agent Wars heat up as Anthropic ships Claude runtime",
               engagement={"points": 900, "comments": 100})],
    )
    second = _nomination(
        "Agent Runtime Rivalry",
        [_item("rd1", "reddit",
               "Agent wars escalate as OpenAI counters with Codex swarm",
               engagement={"score": 250, "num_comments": 30})],
    )
    resolved = handoff.resolve_name_collisions([
        (first, "Agent Wars"),
        (second, "agent wars"),
    ])
    assert len(resolved) == 2  # never collapses distinct nominations
    assert resolved[0] == "Agent Wars"
    assert resolved[1].casefold() != "agent wars"
    assert resolved[1].casefold().startswith("agent wars")
    assert len({name.casefold() for name in resolved}) == 2


def test_indistinguishable_collision_still_never_drops():
    shared = [_item("hn1", "hackernews", "Agent Wars heat up",
                    engagement={"points": 100})]
    first = _nomination("Agent Wars", shared)
    second = _nomination("Agent Wars redux", shared)
    resolved = handoff.resolve_name_collisions([
        (first, "Agent Wars"),
        (second, "agent wars"),
    ])
    assert len(resolved) == 2
    assert len({name.casefold() for name in resolved}) == 2


# --- Scenario 6: angles reader -------------------------------------------------


def test_angles_apply_truncate_and_none_path_returns_empty(tmp_path):
    bundle = _write(tmp_path)
    # Missing angles file is legal.
    assert handoff.read_angles(None, bundle) == {}
    long_angle = " ".join(["angle"] * 60)  # well over 200 chars
    path = tmp_path / "angles.json"
    path.write_text(json.dumps({
        "bundle_id": bundle.bundle_id,
        "angles": [
            {"id": "n1",
             "podcast": "Why the agent SDK churn is a tax on small teams",
             "x_article": long_angle},
            {"id": "n9", "podcast": "Ghost angle"},
        ],
    }), encoding="utf-8")
    angles = handoff.read_angles(path, bundle)
    assert angles["n1"].podcast == (
        "Why the agent SDK churn is a tax on small teams"
    )
    x_article = angles["n1"].x_article
    assert x_article is not None
    assert len(x_article) <= 200
    assert set(x_article.split()) == {"angle"}  # word-boundary truncation
    assert "n9" not in angles  # unknown ids ignored


# --- Scenario 7: digest ----------------------------------------------------------


LONG_TITLE = (
    "Anthropic ships a Claude agent runtime and the fallout reshapes agents " * 4
).strip()  # > 220 chars


def test_digest_names_bundle_path_instruction_and_capped_evidence(tmp_path):
    long_snippet = (
        "The community reaction spans pricing, lock-in, and migration pain. " * 10
    ).strip()  # > 420 chars
    nomination = _nomination(
        "Agent Runtime Fallout",
        [_item(
            "hn1", "hackernews", LONG_TITLE,
            engagement={"points": 1200, "comments": 300},
            snippet=long_snippet,
            metadata={"top_comments": [{
                "excerpt": "This will consolidate the whole agent ecosystem "
                           "within a year",
                "score": 1635,
                "author": "dev_a",
            }]},
        )],
        seed_score=77.7,
    )
    entries = [_entry(nomination, cluster_id="c-fallout"), _pool()[1]]
    bundle = _write(tmp_path, entries)
    digest = handoff.build_host_digest(bundle)
    # (b) names the bundle file path and instructs reading it before judging.
    assert str(bundle.path) in digest
    assert "before judging" in digest
    # (a) one structural line per nomination, keyed by nomination id.
    lines = digest.splitlines()
    n1_lines = [line for line in lines if line.startswith("n1 | ")]
    n2_lines = [line for line in lines if line.startswith("n2 | ")]
    assert len(n1_lines) == 1
    assert len(n2_lines) == 1
    # Structural line carries id/sources/signal only - the third-party title
    # lives inside the untrusted-content fence, never on the structural line.
    assert "hackernews" in n1_lines[0]  # seed source names
    assert "1,500 native interactions" in n1_lines[0]  # engagement signal
    assert LONG_TITLE[:40] not in n1_lines[0]
    # Evidence caps: the old judge surface (title ~220, snippet ~420).
    assert LONG_TITLE[:220] in digest
    assert LONG_TITLE[:230] not in digest
    assert long_snippet[:420] in digest
    assert long_snippet[:430] not in digest
    assert "consolidate the whole agent ecosystem" in digest  # top comment
    # Plain text: no markdown tables.
    assert not any(line.lstrip().startswith("|") for line in lines)


def test_digest_fences_untrusted_evidence_like_the_engine_judge(tmp_path):
    """F12: titles/snippets/comments are scraped third-party data. The digest
    wraps them in the same fence the rerank judge uses (security-notice
    header + <untrusted_content> tags); the structural surfaces (nomination
    id/sources/signal lines, bundle path, judging instructions) stay outside
    the fence."""
    bundle = _write(tmp_path)
    digest = handoff.build_host_digest(bundle)
    # The exact rerank fence: notice header and tags, reused not re-invented.
    assert rerank.UNTRUSTED_CONTENT_NOTICE in digest
    fence_open = digest.index("<untrusted_content>")
    fence_close = digest.index("</untrusted_content>")
    assert fence_open < fence_close
    # Every evidence surface (leader title, leader snippet, top community
    # comment) sits inside the fence.
    leader_title = (
        "Agent SDK Wars heat up as Anthropic ships a Claude agent runtime"
    )
    assert fence_open < digest.index(leader_title) < fence_close
    assert fence_open < digest.index(f"Evidence about {leader_title}") < fence_close
    assert fence_open < digest.index(
        "The SDK churn is unsustainable for small teams"
    ) < fence_close
    # Structural surfaces stay outside (before) the fence.
    assert digest.index(str(bundle.path)) < fence_open
    assert digest.index("before judging") < fence_open
    assert digest.index("n1 | ") < fence_open
    assert digest.index("n2 | ") < fence_open


# --- U5: pending-report reader (leg 3) ----------------------------------------


def _pending_payload(**overrides) -> dict:
    payload = {
        "kind": schema.DISCOVERY_PENDING_KIND,
        "schema_version": schema.DISCOVERY_PENDING_SCHEMA_VERSION,
        "bundle_id": "cafe1234cafe1234",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_ref": "discover:AI agents:2026-07-21T00:00:00+00:00",
        "report": {"domain": "AI agents", "topics": []},
        "angle_inputs": {
            "n1": {
                "name": "Agent SDK Wars",
                "titles": "t1; t2",
                "top_comment": "",
                "engagement": "1,500 native interactions across hackernews",
            },
        },
    }
    payload.update(overrides)
    return payload


def _write_pending(state_dir, **overrides) -> dict:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = _pending_payload(**overrides)
    (state_dir / handoff.PENDING_REPORT_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


def test_pending_report_round_trip(tmp_path):
    payload = _write_pending(tmp_path)
    pending = handoff.read_pending_report(save_dir=None, config_dir=tmp_path)
    assert pending.bundle_id == payload["bundle_id"]
    assert pending.schema_version == schema.DISCOVERY_PENDING_SCHEMA_VERSION
    assert pending.generated_at == payload["generated_at"]
    assert pending.run_ref == payload["run_ref"]
    assert pending.report == payload["report"]
    assert pending.angle_inputs == payload["angle_inputs"]
    assert pending.path == tmp_path / handoff.PENDING_REPORT_FILENAME


def test_pending_report_parses_mock_flag_defaulting_false(tmp_path):
    """F19: the pending reader restores the leg-2 mock provenance; files
    written before the flag existed read as real (mock=False)."""
    _write_pending(tmp_path, mock=True)
    assert handoff.read_pending_report(config_dir=tmp_path).mock is True
    _write_pending(tmp_path)  # no "mock" key at all
    assert handoff.read_pending_report(config_dir=tmp_path).mock is False


def test_pending_report_save_dir_takes_precedence(tmp_path):
    save_dir = tmp_path / "saves"
    config_dir = tmp_path / "config"
    _write_pending(save_dir, bundle_id="fromsavedir00001")
    _write_pending(config_dir, bundle_id="fromconfigdir001")
    pending = handoff.read_pending_report(save_dir=save_dir, config_dir=config_dir)
    assert pending.bundle_id == "fromsavedir00001"


def test_pending_report_not_found_with_save_dir_names_only_save_dir(tmp_path):
    save_dir = tmp_path / "saves"
    config_dir = tmp_path / "config"
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(save_dir=save_dir, config_dir=config_dir)
    message = excinfo.value.message
    assert str(save_dir / handoff.PENDING_REPORT_FILENAME) in message
    assert str(config_dir) not in message
    # Remedy: re-run the resume leg, or the full protocol when the bundle is
    # stale too.
    assert "--discover --judgments" in message
    assert "--discover --nominate-only" in message


def test_pending_report_not_found_without_save_dir_names_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(save_dir=None, config_dir=config_dir)
    message = excinfo.value.message
    assert str(config_dir / handoff.PENDING_REPORT_FILENAME) in message
    assert "--discover --judgments" in message


def test_explicit_save_dir_never_falls_back_to_config_pending(tmp_path):
    """The mandated F2 pin: explicit save dir + missing pending file there +
    a FRESH pending report in the config dir = HandoffContractError naming
    only the save-dir location. No silent cross-store load."""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    config_dir = tmp_path / "config"
    _write_pending(config_dir)  # fresh, valid pending report in config store
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(save_dir=save_dir, config_dir=config_dir)
    message = excinfo.value.message
    assert str(save_dir / handoff.PENDING_REPORT_FILENAME) in message
    assert str(config_dir) not in message


def test_pending_report_invalid_json(tmp_path):
    (tmp_path / handoff.PENDING_REPORT_FILENAME).write_text(
        "{not json", encoding="utf-8"
    )
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(config_dir=tmp_path)
    assert "JSON" in excinfo.value.message


def test_pending_report_top_level_non_dict(tmp_path):
    (tmp_path / handoff.PENDING_REPORT_FILENAME).write_text("[]", encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_pending_report(config_dir=tmp_path)


def test_pending_report_wrong_kind(tmp_path):
    _write_pending(tmp_path, kind="discovery-nominations")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(config_dir=tmp_path)
    assert "discovery-nominations" in excinfo.value.message


def test_pending_report_wrong_schema_version(tmp_path):
    _write_pending(tmp_path, schema_version="99.0")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(config_dir=tmp_path)
    assert "99.0" in excinfo.value.message


def test_pending_report_missing_bundle_id(tmp_path):
    _write_pending(tmp_path, bundle_id="")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(config_dir=tmp_path)
    assert "bundle_id" in excinfo.value.message


def test_pending_report_stale_ttl_measured_from_resume_write(tmp_path):
    """The leg-2 write started a FRESH TTL window: staleness is measured from
    the pending report's own generated_at, never the leg-1 sweep's."""
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=handoff.DISCOVERY_HANDOFF_TTL_SECONDS + 60
    )
    _write_pending(tmp_path, generated_at=stale.isoformat())
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(config_dir=tmp_path)
    message = excinfo.value.message
    assert "stale" in message
    assert "--discover --judgments" in message


def test_pending_report_non_dict_report_rejected(tmp_path):
    _write_pending(tmp_path, report=["not", "a", "dict"])
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_pending_report(config_dir=tmp_path)
    assert "report" in excinfo.value.message


def test_angles_bind_against_pending_report(tmp_path):
    """Leg 3 reads angles against the PENDING report: the bundle_id echo
    validates against it, and known ids are the surviving angle_inputs ids."""
    _write_pending(tmp_path)
    pending = handoff.read_pending_report(config_dir=tmp_path)
    path = tmp_path / "angles.json"
    path.write_text(json.dumps({
        "bundle_id": pending.bundle_id,
        "angles": [
            {"id": "n1", "podcast": "Why the SDK churn taxes small teams"},
            {"id": "n2", "podcast": "Ghost angle for a floored nomination"},
        ],
    }), encoding="utf-8")
    angles = handoff.read_angles(path, pending)
    assert angles["n1"].podcast == "Why the SDK churn taxes small teams"
    # n2 did not survive the floor (absent from angle_inputs): ignored.
    assert "n2" not in angles


def test_angles_bundle_id_mismatch_against_pending_report(tmp_path):
    _write_pending(tmp_path)
    pending = handoff.read_pending_report(config_dir=tmp_path)
    path = tmp_path / "angles.json"
    path.write_text(json.dumps({
        "bundle_id": "deadbeefdeadbeef",
        "angles": [{"id": "n1", "podcast": "Bound to the wrong bundle"}],
    }), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError) as excinfo:
        handoff.read_angles(path, pending, save_dir=None, config_dir=tmp_path)
    message = excinfo.value.message
    assert "deadbeefdeadbeef" in message
    assert pending.bundle_id in message
    # The finalize leg validates against the PENDING report - the mismatch
    # message must point the host's retry at discover-pending.json, never at
    # the nominations bundle (regression: the binding error used to name the
    # wrong file on this leg).
    assert "pending discovery report" in message
    assert "Pending-report locations searched" in message
    assert handoff.PENDING_REPORT_FILENAME in message
    assert handoff.NOMINATIONS_BUNDLE_FILENAME not in message
    # A MISMATCH is a wrong echoed id: the remedy is to fix the id and re-run
    # this same leg - the expensive re-sweep/resume remedies must not appear.
    assert "Correct the bundle_id field in your angles file" in message
    assert "re-run this same leg" in message
    assert "--discover --judgments" not in message
    assert "--discover --nominate-only" not in message


# --- Hygiene ---------------------------------------------------------------------


def test_handoff_module_does_not_reference_the_engine_judge():
    """The legacy engine-judge module is deleted (U6); the handoff module
    ports its sanitizers and must never reference the module by name."""
    source = inspect.getsource(handoff)
    needle = "discovery" + "_judge"  # split so this pin never matches itself
    assert needle not in source

"""The beat-writer lane inside the X supplemental search (real function, stubbed backend)."""

import threading
from unittest.mock import patch

import pytest

from lib import nfl, pipeline, schema

DATE_RANGE = ("2026-09-13", "2026-09-20")


def _runtime(backend="bird"):
    return schema.ProviderRuntime(reasoning_provider="mock", planner_model="mock", rerank_model="mock",
                                  x_search_backend=backend)


def _plan(topic="Chiefs"):
    return schema.QueryPlan(
        intent="exploration", freshness_mode="balanced_recent", cluster_mode="topic", raw_topic=topic,
        subqueries=[schema.SubQuery(label="primary", search_query=topic,
                                    ranking_query=f"What matters for {topic}?", sources=["x", "reddit"])],
        source_weights={"x": 1.0, "reddit": 1.0},
    )


def _post(author, n, text):
    return {"id": f"{author}{n}", "text": text, "url": f"https://x.com/{author}/status/{n}",
            "author_handle": author, "date": "2026-09-19", "engagement": {"likes": 40, "reposts": 5},
            "relevance": 0.6, "why_relevant": "beat writer post"}


def _config(depth="default"):
    ent = nfl.resolve("Chiefs")
    handles = nfl.beat_handles(ent, depth=depth)
    return {"_nfl": ent.as_dict(), "_beat_writer_handles": handles,
            "_beat_writer_meta": nfl.beat_writer_meta(handles)}, handles


def _drive(config, depth="default", backend="bird", x_dicts=True):
    bundle = schema.RetrievalBundle()
    if x_dicts:
        bundle.items_by_source["x"] = [schema.SourceItem(
            item_id="X0", source="x", title="t", body="Chiefs news", url="https://x.com/fan/status/0",
            author="fan", container=None, metadata={})]
    pipeline._run_supplemental_searches(
        topic="Chiefs", bundle=bundle, plan=_plan(), config=config, depth=depth, date_range=DATE_RANGE,
        runtime=_runtime(backend), mock=False, rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
    )
    return bundle


@pytest.mark.parametrize("depth", ["quick", "default"])
def test_beat_handles_are_searched_from_lane_without_topic_and_tagged(depth):
    config, handles = _config(depth)
    seen = {}

    def fake_from(hs, topic, from_date, count_per=8):
        seen["handles"], seen["count_per"] = list(hs), count_per
        return [
            _post("adamteicher", 1, "Practice report: Patrick Mahomes was a full participant Friday, Andy Reid said."),
            _post("RapSheet", 2, "Sources: the Chiefs are signing a veteran cornerback to the practice squad."),
            _post("Chiefs", 3, "Week 2 injury report is out. Rashee Rice is listed as questionable for Sunday."),
        ]

    with patch("lib.env.x_backend_chain", return_value=["bird"]), \
         patch("lib.bird_x.search_handles", side_effect=fake_from), \
         patch("lib.bird_x.search_mentions", return_value=[]):
        bundle = _drive(config, depth=depth, x_dicts=(depth != "quick"))

    assert seen["handles"] == handles  # official account, beat writers, insiders, in order
    assert seen["count_per"] == pipeline.FROM_LANE_COUNT_PER
    x_items = bundle.items_by_source["x"]
    by_author = {i.author: i for i in x_items}
    assert {"adamteicher", "RapSheet", "Chiefs"} <= set(by_author)
    assert by_author["adamteicher"].metadata["beat_writer"] == {
        "name": "Adam Teicher", "outlet": "ESPN", "role": "beat", "team": "KC"}
    assert by_author["RapSheet"].metadata["beat_writer"]["role"] == "insider"
    assert by_author["Chiefs"].metadata["beat_writer"]["role"] == "official"
    assert "beat_writer" not in (by_author["fan"].metadata if "fan" in by_author else {})


def test_beat_lane_runs_even_when_no_handles_were_extracted():
    """Regression: an early return used to skip the lane when nothing else named a handle."""
    config, handles = _config("quick")
    calls = []
    with patch("lib.env.x_backend_chain", return_value=["bird"]), \
         patch("lib.bird_x.search_handles", side_effect=lambda hs, *a, **k: calls.append(list(hs)) or []), \
         patch("lib.bird_x.search_mentions", return_value=[]):
        _drive(config, depth="quick", x_dicts=False)
    assert calls == [handles]


def test_beat_lane_is_off_without_a_roster():
    config = {"_nfl": None, "_beat_writer_handles": [], "_beat_writer_meta": {}}
    with patch("lib.env.x_backend_chain", return_value=["bird"]), \
         patch("lib.bird_x.search_handles", return_value=[]) as fn:
        _drive(config, depth="default", x_dicts=True)
    fn.assert_not_called()


def test_beat_lane_skips_handles_already_searched_as_explicit():
    config, handles = _config("default")
    seen = []
    bundle = schema.RetrievalBundle()
    bundle.items_by_source["x"] = []
    with patch("lib.env.x_backend_chain", return_value=["bird"]), \
         patch("lib.bird_x.search_handles", side_effect=lambda hs, *a, **k: seen.append(list(hs)) or []), \
         patch("lib.bird_x.search_mentions", return_value=[]):
        pipeline._run_supplemental_searches(
            topic="Chiefs", bundle=bundle, plan=_plan(), config=config, depth="default", date_range=DATE_RANGE,
            runtime=_runtime(), mock=False, rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
            x_handle="adamteicher",
        )
    flat = [h for call in seen for h in call]
    assert flat.count("adamteicher") == 1  # searched once (explicit), not again in the beat lane


def test_beat_lane_failure_is_recorded_not_raised():
    config, _ = _config("default")
    with patch("lib.env.x_backend_chain", return_value=["bird"]), \
         patch("lib.bird_x.search_handles", side_effect=RuntimeError("cookies expired")), \
         patch("lib.bird_x.search_mentions", return_value=[]):
        bundle = _drive(config, depth="default")
    assert "beat-writer lane" in bundle.errors_by_source["x"]
    assert "cookies expired" in bundle.errors_by_source["x"]
    assert "x" in bundle.source_status  # recorded as a source outcome, not raised


def test_backend_without_handle_lane_logs_and_skips(capsys):
    config, handles = _config("default")
    with patch("lib.env.x_backend_chain", return_value=["xai"]):
        _drive(config, depth="default", backend="xai")
    err = capsys.readouterr().err
    assert "beat-writer lane skipped" in err and str(len(handles)) in err


# === host-connector path (--x-posts) ===


def test_envelope_accepts_beat_handles_serves_them_as_primary_and_tags(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_x_envelope import FROM, TO, _call, _envelope, _row, _write  # noqa: E402
    from lib import x_envelope

    config, handles = _config("default")
    posts = [_row(0, "adamteicher", "Chiefs injury report: Mahomes full participant"),
             _row(1, "Chiefs", "Chiefs injury report: Rice questionable")]
    path = _write(tmp_path, _envelope([_call("from", handles=["adamteicher", "Chiefs"], posts=posts)],
                                      topic="Chiefs"))
    # Without the roster the claimed handles are outside the run's set, so the
    # host's from-lane is demoted to plain topic posts (no handle lane served).
    without = x_envelope.read(path, (FROM, TO), "Chiefs", handles=[], related=[])
    assert not without.take_lanes()
    envelope = x_envelope.read(path, (FROM, TO), "Chiefs", handles=handles, related=[])
    lanes = envelope.take_lanes()
    assert [c.lane for c in lanes] == ["from"]
    envelope = x_envelope.read(path, (FROM, TO), "Chiefs", handles=handles, related=[])  # single-serve: re-read

    bundle = schema.RetrievalBundle()
    plan = _plan()
    config["_x_envelope"] = envelope
    pipeline._run_supplemental_searches(
        topic="Chiefs", bundle=bundle, plan=plan, config=config, depth="default", date_range=(FROM, TO),
        runtime=_runtime(None), mock=False, rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
    )
    items = {i.author: i for i in bundle.items_by_source["x"]}
    assert set(items) == {"adamteicher", "Chiefs"}
    assert items["adamteicher"].metadata["beat_writer"]["outlet"] == "ESPN"
    assert items["Chiefs"].metadata["beat_writer"]["role"] == "official"
    # Primary label, not the 0.3-weight supplemental-related lane.
    assert not any(sq.label == "supplemental-related" for sq in plan.subqueries)


def test_cli_beat_handles_helper_honors_flag_depth_and_env():
    import argparse
    import importlib.util
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "skills" / "nfl30" / "scripts" / "nfl30.py"
    spec = importlib.util.spec_from_file_location("nfl30_cli_beat", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def ns(**kw):
        base = dict(team=None, player=None, beat_writers=None, deep=False, quick=False)
        base.update(kw)
        return argparse.Namespace(**base)

    default = module._nfl_beat_handles_for_run(ns(), "Chiefs")
    assert default[0] == "Chiefs" and len(default) == 11
    assert len(module._nfl_beat_handles_for_run(ns(quick=True), "Chiefs")) == 5
    assert module._nfl_beat_handles_for_run(ns(beat_writers="off"), "Chiefs") == []
    assert module._nfl_beat_handles_for_run(ns(), "Chiefs", {"NFL30_BEAT_WRITERS": "off"}) == []
    assert module._nfl_beat_handles_for_run(ns(), "best pizza in town") == []
    assert module._nfl_beat_handles_for_run(ns(team="GB"), "anything")[0] == "packers"

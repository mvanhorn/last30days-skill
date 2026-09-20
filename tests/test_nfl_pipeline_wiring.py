"""nfl30 wiring: entity resolution in run(), beat-writer handles, and attribution."""

from lib import nfl, pipeline, render, schema


def _x_item(author: str, text: str = "Practice report") -> schema.SourceItem:
    return schema.SourceItem(
        item_id=f"x-{author}",
        source="x",
        title=text,
        body=text,
        url=f"https://x.com/{author}/status/1",
        author=author,
        container="",
        published_at="2026-09-19",
        date_confidence="high",
        engagement={"likes": 10},
        relevance_hint=0.5,
        why_relevant="",
        snippet=text,
        metadata={},
    )


def test_beat_handles_helper_reads_config():
    assert pipeline._beat_handles({"_beat_writer_handles": ["a", " b ", ""]}) == ["a", " b "]
    assert pipeline._beat_handles({}) == []
    assert pipeline._beat_handles(None) == []


def test_tag_beat_writers_stamps_metadata_only_on_roster_x_items():
    config = {"_beat_writer_meta": nfl.beat_writer_meta(["adamteicher"])}
    items = [_x_item("AdamTeicher"), _x_item("someone_else")]
    reddit = _x_item("adamteicher")
    reddit.source = "reddit"
    pipeline._tag_beat_writers(items + [reddit], config)
    assert items[0].metadata["beat_writer"]["outlet"] == "ESPN"
    assert items[0].metadata["beat_writer"]["team"] == "KC"
    assert "beat_writer" not in items[1].metadata
    assert "beat_writer" not in reddit.metadata
    # No roster on the run: nothing is touched.
    plain = _x_item("adamteicher")
    pipeline._tag_beat_writers([plain], {})
    assert plain.metadata == {}


def test_render_attribution_appends_outlet():
    item = _x_item("adamteicher")
    assert render._format_actor(item) == "@adamteicher"
    item.metadata["beat_writer"] = {"outlet": "ESPN"}
    assert render._format_actor(item) == "@adamteicher (ESPN)"
    assert render._stats_actor(item) == "@adamteicher (ESPN)"


def test_run_resolves_entity_and_seeds_roster_and_subreddits():
    config: dict = {}
    pipeline.run(topic="Chiefs", config=config, depth="quick", mock=True)
    assert config["_nfl"]["kind"] == "team" and config["_nfl"]["team"]["abbr"] == "KC"
    handles = config["_beat_writer_handles"]
    assert len(handles) == 4 and "AdamSchefter" in handles
    assert config["_beat_writer_meta"]["adamteicher"]["outlet"]
    assert config["_dedicated_subreddits"] == ["KansasCityChiefs"]


def test_run_respects_beat_writers_off_and_explicit_team():
    config: dict = {"_beat_writers": "off", "_team": "GB"}
    pipeline.run(topic="something unrelated", config=config, depth="quick", mock=True)
    assert config["_nfl"]["team"]["abbr"] == "GB"
    assert config["_beat_writer_handles"] == []
    assert config["_beat_writer_meta"] == {}


def test_run_without_nfl_entity_leaves_roster_empty():
    config: dict = {}
    pipeline.run(topic="best pizza in town", config=config, depth="quick", mock=True)
    assert config["_nfl"] is None
    assert config["_beat_writer_handles"] == []
    assert "_dedicated_subreddits" not in config

"""nfl30 team_official lane: RSS parsing, presser selection, fallback, wiring."""

from unittest import mock

from lib import nfl, pipeline, subproc, team_official as to

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Week 2 Injury Report | Colts vs. Chiefs</title><link>https://www.chiefs.com/news/injury</link>
<pubDate>Fri, 18 Sep 2026 20:00:00 GMT</pubDate><description>&lt;p&gt;Mahomes &lt;b&gt;full&lt;/b&gt; participant.&lt;/p&gt;</description></item>
<item><title>Old story</title><link>https://www.chiefs.com/news/old</link>
<pubDate>Mon, 01 Jun 2026 20:00:00 GMT</pubDate><description>x</description></item>
<item><title>No link</title></item>
</channel></rss>"""

KC = nfl.resolve("Chiefs").as_dict()


def test_parse_feed_rss_strips_html_and_skips_bad_rows():
    rows = to.parse_feed(RSS)
    assert [r["title"] for r in rows] == ["Week 2 Injury Report | Colts vs. Chiefs", "Old story"]
    assert rows[0]["date"] == "2026-09-18" and rows[0]["text"] == "Mahomes full participant."
    assert to.parse_feed("") == [] and to.parse_feed("<not xml") == []


def test_parse_feed_atom():
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>'
            '<link href="https://x.com/a"/><published>2026-09-19T10:00:00Z</published>'
            '<summary>S</summary></entry></feed>')
    rows = to.parse_feed(atom)
    assert rows == [{"title": "T", "url": "https://x.com/a", "date": "2026-09-19", "text": "S"}]


def test_fetch_rss_none_on_failure_or_non_feed():
    with mock.patch.object(to.http, "get_text", return_value=None):
        assert to.fetch_rss("u") is None
    with mock.patch.object(to.http, "get_text", return_value="<html>nope</html>"):
        assert to.fetch_rss("u") is None
    with mock.patch.object(to.http, "get_text", return_value=RSS):
        assert len(to.fetch_rss("u")) == 2


def test_select_pressers_filters_titles_and_window():
    rows = [
        {"id": "a", "title": "Andy Reid Postgame Press Conference | Week 1", "date": "2026-09-14"},
        {"id": "b", "title": "Highlights: Chiefs vs Bills", "date": "2026-09-14"},
        {"id": "c", "title": "Patrick Mahomes Media Availability", "date": "2026-09-16"},
        {"id": "d", "title": "Old Press Conference", "date": "2026-05-01"},
        {"id": "e", "title": "Undated Presser", "date": None},
        {"id": "f", "title": "Future Press Conference", "date": "2026-12-01"},
    ]
    kept = to.select_pressers(rows, "2026-09-13", "2026-09-20", cap=10)
    assert [r["id"] for r in kept] == ["a", "c", "e"]
    assert [r["id"] for r in to.select_pressers(rows, "2026-09-13", "2026-09-20", cap=1)] == ["a"]


def test_speaker_from_title():
    assert to.speaker_from_title("Andy Reid Postgame Press Conference | Week 3") == "Andy Reid"
    assert to.speaker_from_title("HC Andy Reid: Media Availability") == "Andy Reid"


def test_list_channel_videos_parses_flat_playlist_json():
    out = ('{"id": "v1", "title": "Reid Postgame Press Conference", "upload_date": "20260914", "view_count": 500}\n'
           'garbage line\n{"id": "v2", "title": "Other", "timestamp": 1789000000}\n')
    proc = subproc.SubprocResult(returncode=0, stdout=out, stderr="")
    with mock.patch.object(to.subproc, "run_with_timeout", return_value=proc):
        rows, err = to.list_channel_videos("Chiefs", 5)
    assert err is None and [r["id"] for r in rows] == ["v1", "v2"]
    assert rows[0]["date"] == "2026-09-14" and rows[0]["views"] == 500
    assert rows[0]["url"] == "https://www.youtube.com/watch?v=v1"


def test_list_channel_videos_reports_errors():
    with mock.patch.object(to.subproc, "run_with_timeout", side_effect=subproc.SubprocTimeout("t")):
        assert to.list_channel_videos("Chiefs", 5) == ([], "channel listing timed out")
    proc = subproc.SubprocResult(returncode=1, stdout="", stderr="ERROR: 404")
    with mock.patch.object(to.subproc, "run_with_timeout", return_value=proc):
        assert to.list_channel_videos("Nope", 5) == ([], "ERROR: 404")


def test_search_falls_back_to_site_search_when_rss_missing():
    ent = nfl.resolve("Bills").as_dict()
    ent["team"]["rss"] = None
    web = [{"title": "Bills news", "url": "https://www.buffalobills.com/news/x", "snippet": "s", "date": "2026-09-18"},
           {"title": "Other", "url": "https://elsewhere.com/y"}]
    with mock.patch("lib.grounding.web_search", return_value=(web, {})) as ws, \
         mock.patch.object(to, "is_ytdlp_installed", return_value=False):
        result = to.search_team_official(ent, "Bills", "2026-09-13", "2026-09-20", config={"x": 1})
    assert ws.call_args.args[0] == "site:buffalobills.com Bills"
    assert [n["url"] for n in result["news"]] == ["https://www.buffalobills.com/news/x"]
    assert result["news_source"]["BUF"] == "web"
    assert any("yt-dlp" in e for e in result["errors"])


def test_search_full_lane_with_transcripts():
    videos = [{"id": "v1", "title": "Reid Postgame Press Conference", "url": "https://y/v1", "date": "2026-09-15", "views": 9}]
    with mock.patch.object(to, "fetch_rss", return_value=to.parse_feed(RSS)), \
         mock.patch.object(to, "is_ytdlp_installed", return_value=True), \
         mock.patch.object(to, "list_channel_videos", return_value=(videos, None)), \
         mock.patch("lib.youtube_yt.fetch_transcripts_parallel", return_value={"v1": "We were sharp on third down."}), \
         mock.patch("lib.youtube_yt.extract_transcript_highlights", return_value=["sharp on third down"]):
        result = to.search_team_official(KC, "Chiefs", "2026-09-13", "2026-09-20", depth="quick", config={})
    assert [n["title"] for n in result["news"]] == ["Week 2 Injury Report | Colts vs. Chiefs"]
    assert result["pressers"][0]["transcript_highlights"] == ["sharp on third down"]
    items = to.parse_team_official_response(result, query="Chiefs")
    kinds = {i["kind"] for i in items}
    assert kinds == {"news", "presser"}
    presser = next(i for i in items if i["kind"] == "presser")
    assert presser["speaker"] == "Reid" or presser["speaker"]
    assert presser["engagement"] == {"views": 9}
    assert to.format_summary_line(items) == "🏟️ Team official: 1 news │ 1 presser (1 transcribed)"


def test_game_entity_covers_both_teams():
    ent = nfl.resolve("Packers vs Lions").as_dict()
    with mock.patch.object(to, "fetch_rss", return_value=[]) as fr, \
         mock.patch.object(to, "is_ytdlp_installed", return_value=False):
        to.search_team_official(ent, "x", "2026-09-13", "2026-09-20", config={})
    assert fr.call_count == 2


def test_no_team_returns_error_and_league_has_no_lane():
    result = to.search_team_official(nfl.resolve("NFL MVP odds").as_dict(), "mvp", "a", "b")
    assert result["error"] == "no team resolved" and result["news"] == []
    assert to.format_summary_line([]) is None


# === pipeline wiring ===


def test_available_and_plan_forcing_only_with_team():
    cfg = {"_nfl": KC}
    assert "team_official" in pipeline.available_sources(cfg, local_only=True)
    assert "team_official" not in pipeline.available_sources({"_nfl": None}, local_only=True)
    assert "team_official" not in pipeline.available_sources({"_nfl": nfl.resolve("NFL MVP odds").as_dict()}, local_only=True)


def test_ensure_nfl_lanes_in_plan_forces_primary_subquery():
    from lib import schema
    plan = schema.QueryPlan(
        intent="concept", freshness_mode="evergreen_ok", cluster_mode="none", raw_topic="Chiefs",
        subqueries=[schema.SubQuery(label="primary", search_query="Chiefs", ranking_query="Chiefs", sources=["reddit"], weight=1.0),
                    schema.SubQuery(label="b", search_query="Chiefs b", ranking_query="Chiefs b", sources=["reddit"], weight=0.5)],
        source_weights={"reddit": 1.0},
    )
    pipeline._ensure_nfl_lanes_in_plan(plan, ["reddit", "team_official"], {"_nfl": KC})
    assert plan.subqueries[0].sources.count("team_official") == 1
    assert "team_official" not in plan.subqueries[1].sources
    assert plan.source_weights["team_official"] == 1.0
    plan2 = schema.QueryPlan(intent="concept", freshness_mode="evergreen_ok", cluster_mode="none", raw_topic="x",
                             subqueries=[schema.SubQuery(label="p", search_query="x", ranking_query="x", sources=["reddit"], weight=1.0)],
                             source_weights={})
    pipeline._ensure_nfl_lanes_in_plan(plan2, ["team_official"], {"_nfl": None})
    assert plan2.subqueries[0].sources == ["reddit"]


def test_retrieve_stream_skips_without_team_and_serves_with_team():
    from lib import schema
    sq = schema.SubQuery(label="p", search_query="Chiefs", ranking_query="Chiefs", sources=["team_official"], weight=1.0)
    common = dict(topic="Chiefs", subquery=sq, source="team_official", depth="quick",
                  date_range=("2026-09-13", "2026-09-20"), runtime=None, mock=False)
    items, artifact = pipeline._retrieve_stream_impl(config={"_nfl": None}, **common)
    assert items == [] and artifact["_source_outcome"]["state"] == schema.SKIPPED_UNCONFIGURED
    fake = {"news": [{"title": "T", "url": "https://u", "date": "2026-09-18", "text": "t", "team": "KC", "team_name": "Kansas City Chiefs"}],
            "pressers": [], "errors": []}
    with mock.patch.object(to, "search_team_official", return_value=fake):
        items, artifact = pipeline._retrieve_stream_impl(config={"_nfl": KC}, **common)
    assert len(items) == 1 and items[0]["kind"] == "news" and not artifact


def test_normalizer_renders_official_item():
    from lib import normalize
    raw = [{"id": "TO-1", "kind": "presser", "title": "Reid Postgame Press Conference", "url": "https://y/v",
            "date": "2026-09-19", "text": "words", "transcript_snippet": "words", "speaker": "Reid",
            "team": "KC", "team_name": "Kansas City Chiefs", "author": "Reid", "engagement": {"views": 5},
            "relevance": 0.8, "why_relevant": "w"}]
    out = normalize.normalize_source_items("team_official", raw, "2026-09-13", "2026-09-20", "strict_recent")
    assert out[0].metadata["official"] is True and out[0].metadata["speaker"] == "Reid"
    assert out[0].container == "Kansas City Chiefs (official)"

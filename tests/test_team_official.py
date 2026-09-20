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


# Real Packers channel titles (2026-09-20). Team channels rarely say "press
# conference"; they lead with the speaker's name.
PACKERS = nfl.resolve("Packers").as_dict()["team"]
CLIP_TITLES = [
    ("Matt LaFleur: Zaire Franklin is 'earning the respect of his teammates by his actions'", "Matt LaFleur"),
    ("Evan Williams: 'He's a tone-setter'", "Evan Williams"),
    ("Devonte Wyatt on facing the Jets: 'We want to come out hungrier'", "Devonte Wyatt"),
    ("Zaire Franklin says focus is on 'going 1-0' heading into Week 2", "Zaire Franklin"),
    ("Cam Achord on having both Bo Melton and Skyy Moore: 'Tremendous advantage'", "Cam Achord"),
    ("Matt LaFleur 1-on-1: 'We didn't capitalize on opportunities'", "Matt LaFleur"),
    ("Jordan Love after loss to the Vikings: 'We'll get back to work'", "Jordan Love"),
    ("Matt LaFleur speaks about loss to the Vikings on the road", "Matt LaFleur"),
    ("Trey Smack is 'excited' for NFL debut in Minnesota Sunday", "Trey Smack"),
]
SHOW_TITLES = [
    "Final Thoughts: Packers at Jets", "Trailer: Packers at Jets", "Packers Daily: Scouting the Jets",
    "Rock Report: Pack Attack!", "Packers Unscripted: Next up, Jets in Jersey", "Mic'd Up: Jayden Reed",
    "Memorable Moments: Brett Favre leads Packers to win over Jets in '94", "Total Packers: 1-on-1 with Keisean Nixon",
    "Three Things: Explosive plays, TE Tucker Kraft, Larry's 'rules of the road'",
    "Christian Watson expects a competitive, physical secondary vs. Jets",
    "Packers host football outreach camp at Syble Hopp School for Special Education",
    "Washington Commanders VS Philaldelphia Eagles | WEEK 1 Highlights",
]


def test_classify_title_recognizes_speaker_led_clips():
    for title, speaker in CLIP_TITLES:
        assert to.classify_title(title, PACKERS) == ("clip", speaker), title


def test_classify_title_rejects_team_shows_and_generic_titles():
    for title in SHOW_TITLES:
        assert to.classify_title(title, PACKERS) is None, title
    assert to.classify_title("", PACKERS) is None


def test_classify_title_explicit_press_conference_wins_even_over_show_words():
    assert to.classify_title("Andy Reid Postgame Press Conference | Week 3") == ("presser", "Andy Reid")
    assert to.classify_title("Postgame Press Conference Highlights") == ("presser", "")


def test_select_pressers_ranks_presser_tier_first_and_applies_window():
    rows = [
        {"id": "a", "title": "Jordan Love: 'Back to work'", "date": "2026-09-15"},
        {"id": "b", "title": "Highlights: Packers vs Vikings", "date": "2026-09-14"},
        {"id": "c", "title": "Matt LaFleur Postgame Press Conference", "date": "2026-09-14"},
        {"id": "d", "title": "Old Coach: 'Ancient quote'", "date": "2026-05-01"},
        {"id": "e", "title": "Undated Player: 'Some quote'", "date": None},
        {"id": "f", "title": "Future Player: 'Time traveler'", "date": "2026-12-01"},
    ]
    kept = to.select_pressers(rows, "2026-09-13", "2026-09-20", cap=10, team=PACKERS)
    assert [r["id"] for r in kept] == ["c", "a", "e"]  # presser tier first, then clips in channel order
    assert [r["kind"] for r in kept] == ["presser", "clip", "clip"]
    assert kept[0]["speaker"] == "Matt LaFleur" and kept[1]["speaker"] == "Jordan Love"
    assert [r["id"] for r in to.select_pressers(rows, "2026-09-13", "2026-09-20", cap=1, team=PACKERS)] == ["c"]


def test_select_pressers_resolves_missing_dates_before_windowing():
    rows = [{"id": "old", "title": "Alex Old: 'Old news'", "date": None},
            {"id": "new", "title": "Blake Fresh: 'Fresh news'", "date": None}]
    lookup = mock.Mock(return_value={"old": "2026-05-01", "new": "2026-09-18"})
    kept = to.select_pressers(rows, "2026-09-13", "2026-09-20", cap=5, resolve_dates=lookup)
    lookup.assert_called_once_with(["old", "new"])
    assert [r["id"] for r in kept] == ["new"] and kept[0]["date"] == "2026-09-18"
    # A failed lookup keeps the clip: the listing is newest-first, so unknown is not old.
    kept = to.select_pressers(rows, "2026-09-13", "2026-09-20", cap=5, resolve_dates=lambda ids: {})
    assert [r["id"] for r in kept] == ["old", "new"]


def test_fetch_upload_dates_parses_yt_dlp_print_output():
    def fake_run(cmd, timeout):
        url = next(a for a in cmd if a.startswith("https://www.youtube.com/watch?v="))  # wrapper appends flags after the URL
        vid = url.rsplit("=", 1)[-1]
        out = {"v1": "20260918\n", "v2": "NA\n"}.get(vid, "")
        return subproc.SubprocResult(returncode=0, stdout=out, stderr="")
    with mock.patch.object(to.subproc, "run_with_timeout", side_effect=fake_run):
        assert to.fetch_upload_dates(["v1", "v2", "v3"]) == {"v1": "2026-09-18", "v2": None, "v3": None}
    assert to.fetch_upload_dates([]) == {}
    with mock.patch.object(to.subproc, "run_with_timeout", side_effect=subproc.SubprocTimeout("t")):
        assert to.fetch_upload_dates(["v1"]) == {"v1": None}


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
    assert presser["speaker"] == "Reid"
    assert presser["engagement"] == {"views": 9}
    assert to.format_summary_line(items) == "🏟️ Team official: 1 news │ 1 presser/clip (1 transcribed)"


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


def test_clean_transcript_unescapes_and_drops_speaker_markers():
    raw = "&gt;&gt; I feel a little old because &amp; the Jets.  &gt;&gt; Next question"
    assert to.clean_transcript(raw) == "I feel a little old because & the Jets. Next question"
    assert to.clean_transcript("") == ""


def test_speaker_from_title_drops_possessive():
    assert to.speaker_from_title("Andy Reid's Locker Room Speech After Chiefs Monday Night Football Win") == "Andy Reid"

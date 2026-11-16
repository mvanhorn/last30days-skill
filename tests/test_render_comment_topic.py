"""Reddit comment quotes must name the topic, or the section's entity in a comparison."""

from lib import render, schema


def _comment(text, *, depth=0, score=50, author="someone"):
    return {"author": author, "score": score, "depth": depth, "excerpt": text}


def _candidate(title, comments, url="https://www.reddit.com/r/x/comments/abc/t/"):
    item = schema.SourceItem(
        item_id=f"i-{url}", source="reddit", title=title, body="", url=url,
        author="op", container="x", engagement={"score": 500},
        metadata={"top_comments": comments},
    )
    cand = schema.Candidate(
        candidate_id=f"c-{url}", item_id=item.item_id, source="reddit", title=title,
        url=url, snippet="", subquery_labels=["q"], native_ranks={"reddit": 1},
        local_relevance=0.8, freshness=50, engagement=0, source_quality=0.5,
        rrf_score=0.01, source_items=[item],
    )
    cand.final_score = 50.0
    return cand


def _report(topic, *cands):
    return schema.Report(
        topic=topic, range_from="2026-10-11", range_to="2026-11-10",
        generated_at="2026-11-10T00:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="t", planner_model="t", rerank_model="t"),
        query_plan=schema.QueryPlan(
            intent="general", freshness_mode="balanced_recent", cluster_mode="debate",
            raw_topic=topic, subqueries=[], source_weights={}),
        ranked_candidates=list(cands), clusters=[], items_by_source={},
        errors_by_source={},
    )


def _text(report):
    return "\n".join(render._render_top_comments(report))


def test_off_topic_top_comment_skipped_next_grounded_quoted():
    cand = _candidate("Ghostty release notes", [
        _comment("completely unrelated chatter about lunch plans", depth=2, score=900),
        _comment("ghostty finally fixed the font rendering bug", depth=2, score=80),
    ])
    other = _candidate("Ghostty tips", [
        _comment("ghostty config tips that saved me hours", depth=2)],
        url="https://www.reddit.com/r/x/comments/def/t/")
    out = _text(_report("ghostty", cand, other))
    assert "lunch plans" not in out
    assert "font rendering bug" in out


def test_short_top_level_reply_under_on_topic_post_qualifies():
    cand = _candidate("Ghostty 2.0 is out", [_comment("this is huge", depth=0)])
    other = _candidate("Ghostty tips", [
        _comment("ghostty config tips that saved me hours", depth=2)],
        url="https://www.reddit.com/r/x/comments/def/t/")
    assert "this is huge" in _text(_report("ghostty", cand, other))


def test_deep_reply_not_naming_topic_skipped():
    cand = _candidate("Ghostty 2.0 is out", [
        _comment("this is huge for everyone involved", depth=3),
        _comment("ghostty startup time is great now", depth=3)])
    other = _candidate("Ghostty tips", [
        _comment("ghostty config tips that saved me hours", depth=2)],
        url="https://www.reddit.com/r/x/comments/def/t/")
    out = _text(_report("ghostty", cand, other))
    assert "huge for everyone" not in out
    assert "startup time" in out


def test_top_level_reply_under_off_topic_post_needs_grounding():
    cand = _candidate("Best pizza places", [_comment("this is huge honestly", depth=0)])
    other = _candidate("Ghostty tips", [
        _comment("ghostty config tips that saved me hours", depth=2)],
        url="https://www.reddit.com/r/x/comments/def/t/")
    assert "this is huge honestly" not in _text(_report("ghostty", cand, other))


def test_comparison_has_no_reply_exemption_and_tags_entity():
    cand = _candidate("Cursor vs Windsurf", [
        _comment("windsurf has better flow state handling", depth=0),
        _comment("this is huge honestly", depth=0),
        _comment("cursor tab completion wins for me", depth=1),
    ])
    out = render._render_top_comments(_report("Cursor vs Windsurf", cand))
    joined = "\n".join(out)
    assert "this is huge honestly" not in joined
    wind = next(line for line in out if "windsurf has better" in line)
    cur = next(line for line in out if "cursor tab completion" in line)
    assert "windsurf" in wind.split(" (about ")[-1].lower()
    assert "cursor" not in wind.split(" (about ")[-1].lower()
    assert "cursor" in cur.split(" (about ")[-1].lower()


def test_single_topic_lines_carry_no_entity_tag():
    cand = _candidate("Ghostty 2.0", [_comment("ghostty is fast", depth=0)])
    other = _candidate("Ghostty tips", [_comment("ghostty tips", depth=0)],
                       url="https://www.reddit.com/r/x/comments/def/t/")
    assert " (about " not in _text(_report("ghostty", cand, other))


def test_thread_with_no_quotable_comment_keeps_its_citation():
    cand = _candidate("Ghostty 2.0", [_comment("unrelated lunch chatter here", depth=3)])
    other = _candidate("Ghostty tips", [
        _comment("ghostty config tips that saved me hours", depth=2)],
        url="https://www.reddit.com/r/x/comments/def/t/")
    report = _report("ghostty", cand, other)
    assert "lunch chatter" not in _text(report)
    assert cand in report.ranked_candidates


def test_cjk_topic_grounds_like_entity_grounded():
    cand = _candidate("東京の天気について", [
        _comment("東京の天気は今日とても暑いです", depth=2),
        _comment("totally unrelated sentence here", depth=2),
        _comment("東京の天気予報はよく外れるよね", depth=2)])
    out = _text(_report("東京の天気", cand))
    assert "東京の天気は今日" in out
    assert "totally unrelated" not in out

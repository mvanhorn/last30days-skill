"""Compact stdout carries what a report is written from, in copyable shape.

Three evidence blocks (the subject's own posts, the Polymarket markets with
each outcome's own label, and a quote pool whose every entry names its
platform, author, count and URL) plus the checklist the command line prints
after the canonical boundary of ``--emit=compact`` stdout and nowhere else.
"""

import io
import json
import re
import tempfile
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import last30days as cli
from lib import html_render, render, report_checklist, schema


def _item(source, item_id, *, title="A post about the topic", body="", url=None,
          author=None, container=None, engagement=None, metadata=None,
          published_at="2026-11-05", snippet=""):
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=body or title,
        url=url or f"https://example.com/{source}/{item_id}",
        author=author,
        container=container,
        published_at=published_at,
        date_confidence="high",
        engagement=engagement or {},
        snippet=snippet,
        metadata=metadata or {},
    )


def _candidate(item, *, extra_items=()):
    return schema.Candidate(
        candidate_id=f"c-{item.item_id}",
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet="",
        subquery_labels=["primary"],
        native_ranks={f"primary:{item.source}": 1},
        local_relevance=0.9,
        freshness=80,
        engagement=50,
        source_quality=1.0,
        rrf_score=0.02,
        rerank_score=80,
        final_score=70,
        sources=[item.source],
        source_items=[item, *extra_items],
    )


def _report(candidates, *, clusters=None, extra_items=(), x_handle="kanyewest",
            topic="Kanye West"):
    items_by_source: dict[str, list] = {}
    for candidate in candidates:
        for item in candidate.source_items:
            items_by_source.setdefault(item.source, []).append(item)
    for item in extra_items:
        items_by_source.setdefault(item.source, []).append(item)
    if clusters is None:
        clusters = [
            schema.Cluster(
                cluster_id=f"cl-{index}",
                title=f"Story {index}",
                candidate_ids=[candidate.candidate_id],
                representative_ids=[candidate.candidate_id],
                sources=[candidate.source],
                score=90 - index,
            )
            for index, candidate in enumerate(candidates, start=1)
        ]
    artifacts = {"pre_research_flags_present": True}
    if x_handle is not None:
        artifacts["resolved"] = {"entity": topic, "x_handle": x_handle}
    return schema.Report(
        topic=topic,
        range_from="2026-10-11",
        range_to="2026-11-10",
        generated_at="2026-11-10T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="test", planner_model="test", rerank_model="test"
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic=topic,
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query=topic, ranking_query=topic,
                    sources=["reddit"],
                )
            ],
            source_weights={"reddit": 1.0},
        ),
        clusters=clusters,
        ranked_candidates=list(candidates),
        items_by_source=items_by_source,
        errors_by_source={},
        artifacts=artifacts,
    )


def _reddit_thread(index, comments):
    return _item(
        "reddit", f"r{index}", title=f"Reddit thread number {index}",
        url=f"https://www.reddit.com/r/hiphopheads/comments/t{index}/",
        container="hiphopheads", engagement={"score": 500, "num_comments": 40},
        metadata={"top_comments": comments},
    )


def _own_post(index, likes, text=None):
    return _item(
        "x", f"own{index}",
        title=text or f"Own post number {index} about the new album",
        url=f"https://x.com/kanyewest/status/{1000 + index}",
        author="kanyewest", engagement={"likes": likes, "reposts": 3},
        published_at=f"2026-11-{index + 1:02d}",
    )


def _section(text, heading):
    assert f"\n{heading}\n" in text, text
    body = text.split(f"\n{heading}\n", 1)[1]
    return re.split(r"\n(?:## |<!-- END EVIDENCE)", body, maxsplit=1)[0]


def _entries(section):
    return [line for line in section.splitlines() if line.startswith("- ")]


def _two_comment_candidates():
    return [
        _candidate(_reddit_thread(1, [
            {"author": "CappaccinoJay", "score": 1315, "excerpt": "Internet feelings vs reality.",
             "url": "https://reddit.com/r/hiphopheads/comments/t1/comment/abc/"},
        ])),
        _candidate(_reddit_thread(2, [
            {"author": "CDC678", "score": 1186, "excerpt": "This stage allows for every seat to be sold."},
        ])),
    ]


# --- the subject's own posts -------------------------------------------------


def test_subject_posts_block_lists_the_handles_posts_most_liked_first():
    own = [_own_post(index, likes) for index, likes in enumerate([40, 9000, 12, 700, 5, 3, 2, 1, 80, 60])]
    other = _item("x", "fan1", title="A fan post about the album rollout", author="PopBase",
                  engagement={"likes": 99999})
    report = _report(_two_comment_candidates(), extra_items=[*own, other])

    text = render.render_compact(report)
    entries = _entries(_section(text, "## Posts by @kanyewest"))

    assert len(entries) == render.SUBJECT_POSTS_LIMIT == 8
    assert entries[0] == (
        '- "Own post number 1 about the new album" 🔵 @kanyewest on X · 9,000 likes'
        " — 2026-11-02 — https://x.com/kanyewest/status/1001"
    )
    likes = [int(re.search(r"· ([\d,]+) likes", line).group(1).replace(",", "")) for line in entries]
    assert likes == sorted(likes, reverse=True)
    assert all("PopBase" not in line for line in entries)
    assert all(re.search(r" — \d{4}-\d{2}-\d{2} — https://x\.com/kanyewest/status/\d+$", line) for line in entries)


def test_subject_posts_block_sits_inside_the_evidence_envelope():
    report = _report(_two_comment_candidates(), extra_items=[_own_post(1, 50)])
    text = render.render_compact(report)
    assert (
        text.index("<!-- EVIDENCE FOR SYNTHESIS")
        < text.index("## Posts by @kanyewest")
        < text.index("<!-- END EVIDENCE FOR SYNTHESIS -->")
    )


def test_subject_posts_match_the_handle_whatever_its_case_or_at_sign():
    post = _own_post(1, 50)
    post.author = "@KanyeWest"
    report = _report(_two_comment_candidates(), extra_items=[post])
    assert "## Posts by @kanyewest" in render.render_compact(report)


def test_subject_post_text_is_one_line_and_bounded():
    long_post = _own_post(1, 50, text="line one\n\nline two " + "word " * 200)
    report = _report(_two_comment_candidates(), extra_items=[long_post])
    (entry,) = _entries(_section(render.render_compact(report), "## Posts by @kanyewest"))
    quote = entry.split('" 🔵', 1)[0]
    assert "line one line two" in quote
    assert len(quote) <= 290


@pytest.mark.parametrize("x_handle", [None, ""])
def test_no_subject_posts_block_without_a_resolved_handle(x_handle):
    report = _report(_two_comment_candidates(), extra_items=[_own_post(1, 50)], x_handle=x_handle)
    assert "## Posts by @" not in render.render_compact(report)


def test_no_subject_posts_block_when_the_handle_posted_nothing():
    report = _report(_two_comment_candidates())
    assert "## Posts by @" not in render.render_compact(report)


# --- Polymarket --------------------------------------------------------------


def _market(index, *, title, outcomes, movement="", question="", remaining=0,
            end_date="2027-01-01", volume=1000.0):
    return _item(
        "polymarket", f"pm{index}", title=title,
        url=f"https://polymarket.com/event/market-{index}", container="Polymarket",
        engagement={"volume": volume, "liquidity": 50.0}, snippet=movement,
        metadata={
            "question": question or title,
            "outcome_prices": outcomes,
            "outcomes_remaining": remaining,
            "end_date": end_date,
        },
    )


def test_polymarket_block_keeps_each_outcomes_own_label_price_movement_and_url():
    binary = _market(
        1, title="Will Kanye West and Bianca Censori separate in 2026?",
        outcomes=[("Yes", 0.065), ("No", 0.94)], movement="down 2.5% this week",
    )
    ladder = _market(
        2, title="Kanye West album first week sales?",
        question="Will Kanye West sell 200-250K in the first week?",
        outcomes=[("150-200K", 0.43), ("200-250K", 0.33), ("100-150K", 0.07)],
        remaining=4, movement="up 13.0% today", volume=90000.0, end_date="2026-12-01",
    )
    report = _report(_two_comment_candidates(), extra_items=[binary, ladder])

    entries = _entries(_section(render.render_compact(report), "## Polymarket Markets"))

    assert entries == [
        "- Kanye West album first week sales? — 150-200K: 43% | 200-250K: 33% | 100-150K: 7.0%"
        " (+4 more outcomes) — up 13.0% today (Will Kanye West sell 200-250K in the first week?)"
        " — closes 2026-12-01 — https://polymarket.com/event/market-2",
        "- Will Kanye West and Bianca Censori separate in 2026? — Yes: 6.5% | No: 94%"
        " — down 2.5% this week — closes 2027-01-01 — https://polymarket.com/event/market-1",
    ]


def test_polymarket_block_prints_no_dollar_amounts():
    market = _market(1, title="Will Kanye West tour in 2027?", outcomes=[("Yes", 0.5), ("No", 0.5)],
                     volume=123456.0)
    section = _section(render.render_compact(_report(_two_comment_candidates(), extra_items=[market])),
                       "## Polymarket Markets")
    assert "$" not in section
    assert "123" not in section


def test_polymarket_block_is_bounded_and_skips_markets_that_do_not_name_the_topic():
    markets = [
        _market(index, title=f"Will Kanye West do thing {index}?", outcomes=[("Yes", 0.5), ("No", 0.5)],
                volume=float(index))
        for index in range(1, 12)
    ]
    off_topic = _market(99, title="Will Mae West be honored?", outcomes=[("Yes", 0.5), ("No", 0.5)],
                        volume=10_000_000.0)
    report = _report(_two_comment_candidates(), extra_items=[*markets, off_topic])
    entries = _entries(_section(render.render_compact(report), "## Polymarket Markets"))
    assert len(entries) == render.POLYMARKET_MARKETS_LIMIT == 8
    assert all("Mae West" not in line for line in entries)
    assert "thing 11?" in entries[0]


def test_polymarket_snippet_that_is_not_a_movement_is_left_out():
    market = _market(1, title="Will Kanye West tour in 2027?", outcomes=[("Yes", 0.5), ("No", 0.5)],
                     movement="Will Kanye West tour in 2027? Will Kanye West tour in 2027?")
    (entry,) = _entries(_section(
        render.render_compact(_report(_two_comment_candidates(), extra_items=[market])),
        "## Polymarket Markets",
    ))
    assert entry == (
        "- Will Kanye West tour in 2027? — Yes: 50% | No: 50% — closes 2027-01-01"
        " — https://polymarket.com/event/market-1"
    )


def test_no_polymarket_block_without_markets():
    assert "## Polymarket Markets" not in render.render_compact(_report(_two_comment_candidates()))


# --- the quote pool ----------------------------------------------------------

QUOTE_ENTRY = re.compile(
    r'^- "(?P<quote>.+)" (?P<dot>\S+) (?P<author>\S+) on (?P<place>r/\w+|[A-Z][\w. ]*?)'
    r"(?: · (?P<count>[\d,]+) (?P<unit>upvotes|likes|points|replies|votes))?"
    r" — (?P<url>https://\S+)(?: — (?:cluster \d+|re: .+))? — (?P<position>top comment|#2 comment|post)(?: — funny)?$"
)


def _mixed_pool_report():
    youtube = _item(
        "youtube", "yt1", title="Kanye's Yeezy show breakdown",
        url="https://www.youtube.com/watch?v=gcv2gecR234", author="The Truth IS",
        engagement={"views": 144689},
        metadata={"top_comments": [
            {"author": "@deesee6009", "score": 27, "excerpt": "Kanye a wolf in sheep's clothing"},
        ]},
    )
    tiktok = _item(
        "tiktok", "tt1", title="Ye being Ye", url="https://www.tiktok.com/@north/video/1",
        author="yeniuswest",
        metadata={"top_comments": [
            {"author": "@haktimel2", "score": 825451, "excerpt": "Ofc shes literally him😭"},
        ]},
    )
    instagram = _item(
        "instagram", "ig1", title="Kanye in LA", url="https://www.instagram.com/reel/abc/",
        author="iamkevinwong",
        metadata={"top_comments": [
            {"author": "glovejones", "score": 410, "excerpt": "He really walked out like that"},
        ]},
    )
    hackernews = _item(
        "hackernews", "42", title="Yeezy site rebuilt", url="https://example.com/article",
        metadata={"hn_url": "https://news.ycombinator.com/item?id=42", "top_comments": [
            {"author": "alice", "score": None, "excerpt": "The checkout flow is one page now."},
        ]},
    )
    fan_post = _item(
        "x", "fan1", title="Kanye sold out AT&T Stadium in nine minutes",
        url="https://x.com/PopBase/status/77", author="PopBase", engagement={"likes": 48213},
    )
    candidates = [
        *_two_comment_candidates(),
        _candidate(youtube), _candidate(tiktok), _candidate(instagram),
        _candidate(hackernews), _candidate(fan_post),
    ]
    return _report(candidates, extra_items=[_own_post(1, 50)])


def test_every_quote_pool_entry_has_dot_author_place_url_and_count_when_one_exists():
    entries = _entries(_section(render.render_compact(_mixed_pool_report()), "## Top Community Comments"))
    assert len(entries) == 7
    parsed = {}
    for entry in entries:
        match = QUOTE_ENTRY.match(entry)
        assert match, entry
        parsed[match["author"]] = match
    assert parsed["u/CappaccinoJay"]["place"] == "r/hiphopheads"
    assert parsed["u/CappaccinoJay"]["count"] == "1,315"
    assert parsed["u/CappaccinoJay"]["url"] == "https://reddit.com/r/hiphopheads/comments/t1/comment/abc/"
    assert (parsed["@deesee6009"]["dot"], parsed["@deesee6009"]["place"]) == ("🔴", "YouTube")
    assert (parsed["@deesee6009"]["count"], parsed["@deesee6009"]["unit"]) == ("27", "likes")
    assert parsed["@deesee6009"]["url"] == "https://www.youtube.com/watch?v=gcv2gecR234"
    assert parsed["@haktimel2"]["place"] == "TikTok"
    assert parsed["@haktimel2"]["count"] == "825,451"
    assert parsed["@glovejones"]["place"] == "Instagram"
    assert parsed["alice"]["place"] == "Hacker News"
    assert parsed["alice"]["count"] is None
    assert parsed["alice"]["url"] == "https://news.ycombinator.com/item?id=42"
    assert parsed["@PopBase"]["place"] == "X"
    assert parsed["@PopBase"]["count"] == "48,213"
    assert parsed["@PopBase"]["url"] == "https://x.com/PopBase/status/77"


def test_quote_pool_entry_copies_into_a_quote_line_with_no_em_dash():
    entries = _entries(_section(render.render_compact(_mixed_pool_report()), "## Top Community Comments"))
    (tiktok,) = [entry for entry in entries if "literally him" in entry]
    assert tiktok.split(" — ", 1)[0] == '- "Ofc shes literally him😭" 🎵 @haktimel2 on TikTok · 825,451 likes'


def test_quote_pool_leaves_the_subjects_own_posts_to_their_block():
    report = _mixed_pool_report()
    own = _own_post(2, 999999, text="My own post that everyone liked a lot")
    report.ranked_candidates.append(_candidate(own))
    report.items_by_source["x"].append(own)
    text = render.render_compact(report)
    assert "My own post that everyone liked" in _section(text, "## Posts by @kanyewest")
    assert "My own post that everyone liked" not in _section(text, "## Top Community Comments")


def test_comment_without_its_own_url_links_the_item_it_sits_on_not_another_one():
    """A fused candidate's URL belongs to its first item; the comment's does not."""
    reddit = _reddit_thread(1, [
        {"author": "CappaccinoJay", "score": 1315, "excerpt": "Internet feelings vs reality."},
    ])
    youtube = _item(
        "youtube", "yt9", title="A video", url="https://www.youtube.com/watch?v=VIDEO9",
        metadata={"top_comments": [
            {"author": "@soundsbyo", "score": 409, "excerpt": "this is so clear technology is insane"},
        ]},
    )
    fused = _candidate(reddit, extra_items=[youtube])
    lines = render._render_top_comments(_report([fused]))
    (line,) = [line for line in lines if "soundsbyo" in line]
    assert "https://www.youtube.com/watch?v=VIDEO9" in line
    assert "reddit.com" not in line



def test_quote_pool_text_is_one_line():
    candidates = _two_comment_candidates()
    candidates[1].source_items[0].metadata["top_comments"][0]["excerpt"] = "It's back!\n\nAlso very sorry."
    section = _section(render.render_compact(_report(candidates)), "## Top Community Comments")
    assert '- "It\'s back! Also very sorry." 🟠 u/CDC678 on r/hiphopheads · 1,186 upvotes — ' in section


def test_nothing_solid_run_prints_none_of_the_three_blocks():
    report = _mixed_pool_report()
    market = _market(1, title="Will Kanye West tour in 2027?", outcomes=[("Yes", 0.5), ("No", 0.5)])
    report.items_by_source["polymarket"] = [market]
    for candidate in report.ranked_candidates:
        candidate.final_score = 0
        candidate.explanation = "fallback-local-score (entity-miss demotion)"
    text = render.render_compact(report)
    assert "Nothing solid this window" in text
    for heading in ("## Posts by @", "## Polymarket Markets", "## Top Community Comments"):
        assert heading not in text


# --- the checklist -----------------------------------------------------------


def _args(emit="compact", save_dir=None, output=None, **extra):
    values = dict(
        topic=["Kanye West"], competitors=None, competitors_list=None, competitors_plan=None,
        drill=False, register=None, emit=emit, output=output, save_dir=save_dir, save_suffix="",
        json_profile="agent", publish_html=False, hiring_signals=False,
    )
    values.update(extra)
    return types.SimpleNamespace(**values)


def _stdout(report, monkeypatch, *, claude_code, emit="compact", save_dir=None, entity_reports=None,
            no_checklist=False, **extra):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("LAST30DAYS_NO_REPORT_CHECKLIST", raising=False)
    if claude_code:
        monkeypatch.setenv("CLAUDECODE", "1")
    if no_checklist:
        monkeypatch.setenv("LAST30DAYS_NO_REPORT_CHECKLIST", "1")
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = cli._render_save_and_print(
            _args(emit=emit, save_dir=save_dir, **extra), report, entity_reports, None, {},
        )
    assert rc == 0
    return out.getvalue()


def _checklist(stdout):
    assert f"\n{report_checklist.HEADING}\n" in stdout
    return stdout.split(f"\n{report_checklist.HEADING}\n", 1)[1]


def test_checklist_is_the_last_block_of_compact_stdout_after_the_boundary(monkeypatch):
    stdout = _stdout(_mixed_pool_report(), monkeypatch, claude_code=False)
    boundary = stdout.index("# END OF last30days CANONICAL OUTPUT")
    heading = stdout.index(report_checklist.HEADING)
    assert stdout.index("✅ All agents reported back!") < boundary < heading
    assert stdout.count(report_checklist.HEADING) == 1
    block = _checklist(stdout)
    assert "\n## " not in block and "\n# " not in block
    lines = [line for line in block.splitlines() if line.strip()]
    assert len(lines) <= 8
    assert stdout.endswith(lines[-1] + "\n")


def test_checklist_states_the_format_rules_as_facts(monkeypatch):
    block = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=False))
    assert "badge line" in block and "🌐 last30days" in block
    assert "bold headline phrase" in block and "`**Headline** - text`" in block
    assert '`> "quote" 🟠 u/author on r/sub · 1,315 upvotes`' in block
    assert "`## Top Community Comments`" in block
    assert "`## Posts by @kanyewest`" in block
    assert "The report's best six quotes each get their own line under their paragraph" in block
    assert "six in the whole report" in block
    assert "Other good quotes may stay inside a paragraph, attributed and linked" in block
    assert "no quote is used both ways" in block
    assert "per story" not in block and "never inside a paragraph" not in block
    assert "```text" in block and "fence included" in block
    assert "I'm now an expert on" in block
    assert "User and host instructions come first" in block


def test_checklist_mentions_lists_only_on_claude_code(monkeypatch):
    plain = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=False))
    for needle in ("markdown list", "Claude Code", "`1\\.`", "`•`"):
        assert needle not in plain
    on_claude_code = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=True))
    assert "no markdown lists anywhere" in on_claude_code
    # A note with bullets after the report breaks every link in the message.
    assert "notes you add before or after it carry no bullets" in on_claude_code
    assert "whenever you show the report in a later message" in on_claude_code
    assert "`1\\.`" in on_claude_code and "`•`" in on_claude_code
    assert "raw `name (url)` text" in on_claude_code
    assert len([line for line in on_claude_code.splitlines() if line.strip()]) <= 8


def test_checklist_itself_holds_no_markdown_list(monkeypatch):
    block = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=True))
    for line in block.splitlines():
        assert not re.match(r"\s*(?:[-*+] |\d+\. )", line), line


def test_checklist_jobs_line_follows_the_jobs_digest_block(monkeypatch):
    from lib import jobs_digest

    without = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=False))
    assert "no `## Jobs Digest` block" in without
    assert "no Jobs Page Secret Signals section" in without

    raw = [
        {"id": f"GH{index}", "title": f"Engineer {index}", "department": f"Dept {index}",
         "location": "Remote", "url": f"https://boards.greenhouse.io/acme/jobs/{index}",
         "description": f"Own system {index}.", "date": "2026-11-01", "posted_date_true": True}
        for index in range(6)
    ]
    report = _mixed_pool_report()
    report.artifacts["jobs"] = {
        "tier": "ats", "provider": "greenhouse", "company": "Acme", "owned": True,
        "open_roles": 6, "new_in_window": 6,
        "digest": jobs_digest.build_digest(raw, jobs_digest.select_roles(raw)),
    }
    stdout = _stdout(report, monkeypatch, claude_code=False)
    assert "\n## Jobs Digest\n" in stdout
    with_digest = _checklist(stdout)
    assert "has a `## Jobs Digest` block" in with_digest
    assert "`hiring-signals.md`" in with_digest


def test_checklist_claims_no_precedence(monkeypatch):
    block = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=True))
    for pattern in (
        r"\bSUPERSEDED\b", r"\bOVERRIDDEN\b", r"overrides", r"IGNORE", r"skill-specified rule wins",
        r"\bMUST\b", r"\bYOU ARE\b", r"regardless of",
    ):
        assert not re.search(pattern, block), pattern


def test_exec_register_and_comparison_checklists_leave_out_the_quote_line(monkeypatch):
    report = _mixed_pool_report()
    exec_block = _checklist(_stdout(report, monkeypatch, claude_code=True, register="exec"))
    comparison_block = _checklist(_stdout(
        report, monkeypatch, claude_code=True,
        entity_reports=[("Kanye West", report), ("Drake", report)],
    ))
    for block in (exec_block, comparison_block):
        assert '> "quote"' not in block
        assert "bold headline phrase" not in block
        assert "badge line" in block
        assert "no markdown lists anywhere" in block
        assert "fence included" in block


def test_checklist_is_absent_from_the_saved_file_and_the_output_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        save_dir = Path(tmp) / "saved"
        output = Path(tmp) / "out" / "report.md"
        stdout = _stdout(
            _mixed_pool_report(), monkeypatch, claude_code=True,
            save_dir=str(save_dir), output=str(output),
        )
        assert report_checklist.HEADING in stdout
        (saved,) = list(save_dir.glob("*.md"))
        assert report_checklist.HEADING not in saved.read_text(encoding="utf-8")
        assert report_checklist.HEADING not in output.read_text(encoding="utf-8")
        # The saved file is still the full dump, with the footer citing it.
        assert "## All Items by Source" in saved.read_text(encoding="utf-8")


@pytest.mark.parametrize("emit", ["md", "json", "html", "context", "brief"])
def test_checklist_is_absent_from_every_other_emit_mode(monkeypatch, emit):
    stdout = _stdout(_mixed_pool_report(), monkeypatch, claude_code=True, emit=emit)
    assert report_checklist.HEADING not in stdout
    assert "Before you write" not in stdout
    if emit == "json":
        json.loads(stdout)


def test_checklist_is_absent_from_render_functions(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    report = _mixed_pool_report()
    assert report_checklist.HEADING not in render.render_compact(report)
    assert report_checklist.HEADING not in render.render_full(report, save_path="~/x.md")
    assert report_checklist.HEADING not in html_render.render_html(report)
    assert render.render_compact(report).rstrip().endswith("# END OF last30days CANONICAL OUTPUT")


def test_wrapper_env_var_turns_the_checklist_off(monkeypatch):
    stdout = _stdout(_mixed_pool_report(), monkeypatch, claude_code=True, no_checklist=True)
    assert report_checklist.HEADING not in stdout
    assert stdout.rstrip().endswith("# END OF last30days CANONICAL OUTPUT")


def test_hiring_signals_run_prints_no_checklist(monkeypatch):
    stdout = _stdout(_mixed_pool_report(), monkeypatch, claude_code=True, hiring_signals=True)
    assert report_checklist.HEADING not in stdout


# --- quality floors (added last so the sections above stay readable) ----------


def test_subject_posts_block_leaves_out_reposts():
    repost = _own_post(1, 0, text="RT @ComplexMusic: History was MADE in Houston tonight")
    report = _report(_two_comment_candidates(), extra_items=[repost, _own_post(2, 40)])
    (entry,) = _entries(_section(render.render_compact(report), "## Posts by @kanyewest"))
    assert "Own post number 2" in entry


def test_quote_pool_takes_no_unliked_post_and_no_wordless_comment():
    quiet_post = _item("x", "quiet", title="Como te amo Kanye West de verdade",
                       author="ordonena", engagement={"likes": 1})
    emoji = _reddit_thread(3, [{"author": "hearts", "score": 900, "excerpt": "❤️❤️❤️❤️❤️❤️❤️❤️"}])
    report = _report([*_two_comment_candidates(), _candidate(quiet_post), _candidate(emoji)])
    section = _section(render.render_compact(report), "## Top Community Comments")
    assert "ordonena" not in section
    assert "u/hearts" not in section
    assert len(_entries(section)) == 2




def test_quote_pool_keeps_hacker_news_comments_with_no_or_zero_points():
    story = _item("hackernews", "42", title="A story", url="https://example.com/article", metadata={
        "hn_url": "https://news.ycombinator.com/item?id=42",
        "top_comments": [
            {"author": "alice", "score": None, "excerpt": "The checkout flow is one page now."},
            {"author": "bob", "score": 0, "excerpt": "A measured zero is still shown here."},
        ],
    })
    report = _report([*_two_comment_candidates(), _candidate(story)])
    section = _section(render.render_compact(report), "## Top Community Comments")
    assert '" 🟡 alice on Hacker News — ' in section
    assert '" 🟡 bob on Hacker News · 0 points — ' in section



# --- round 2: recommendation runs, copy wording, quote text, story floor ------


@pytest.mark.parametrize("topic", [
    "best noise cancelling headphones", "Top AI coding agents", "what are the best CRMs for startups",
    "what vector database should I use", "recommended standing desks",
])
def test_recommendation_run_checklist_leaves_out_story_quote_and_headline_lines(monkeypatch, topic):
    report = _mixed_pool_report()
    report.topic = topic
    report.query_plan.raw_topic = topic
    block = _checklist(_stdout(report, monkeypatch, claude_code=True))
    assert '> "quote"' not in block
    assert "bold headline phrase" not in block
    assert "story" not in block
    for kept in ("badge line", "no markdown lists anywhere", "fence included", "I'm now an expert on"):
        assert kept in block


@pytest.mark.parametrize(("topic", "intent"), [
    ("Kanye West", "general"),
    ("Sundar Pichai best moments", "opinion"),
    # A name that opens "Top" or "Best", under a plan that says it is news.
    ("Top Gun Maverick sequel news", "breaking_news"),
    ("Best Buy earnings", "factual"),
])
def test_ordinary_topics_keep_the_story_quote_line(monkeypatch, topic, intent):
    report = _mixed_pool_report()
    report.topic = topic
    report.query_plan.raw_topic = topic
    report.query_plan.intent = intent
    assert '> "quote"' in _checklist(_stdout(report, monkeypatch, claude_code=False))


def test_checklist_says_copy_through_the_count_and_that_six_is_a_ceiling(monkeypatch):
    block = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=False))
    assert "Copy the entry through its count (through the platform name when it has no count)" in block
    assert "up to its URL" not in block
    assert "a ceiling, not a target" in block


def test_checklist_names_the_marker_for_key_patterns_and_for_the_invitation(monkeypatch):
    block = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=True))
    assert "KEY PATTERNS as `1\\.`, invitation lines as `•`" in block


def test_posts_entry_ends_its_copyable_part_at_the_count():
    report = _report(_two_comment_candidates(), extra_items=[_own_post(1, 108107)])
    (entry,) = _entries(_section(render.render_compact(report), "## Posts by @kanyewest"))
    copyable, date, url = entry.split(" — ")
    assert copyable.endswith("🔵 @kanyewest on X · 108,107 likes")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
    assert url.startswith("https://x.com/kanyewest/status/")


def test_leading_reply_handles_are_stripped_from_post_text_only():
    own = _own_post(1, 50, text="@yzyupdates @brazil_ye Bro This was a vibe, ask @north about it")
    reply = _item("x", "fan2", title="@kanyewest @PopBase this is the best tour of the decade",
                  url="https://x.com/fan/status/9", author="fan", engagement={"likes": 400})
    mention_only = _own_post(2, 900, text="@yzyupdates @brazil_ye")
    comment = _reddit_thread(3, [
        {"author": "replier", "score": 300, "excerpt": "@grok is this true or is it made up"},
    ])
    report = _report(
        [*_two_comment_candidates(), _candidate(reply), _candidate(comment)],
        extra_items=[own, mention_only],
    )
    text = render.render_compact(report)
    (post,) = _entries(_section(text, "## Posts by @kanyewest"))
    assert post.startswith('- "Bro This was a vibe, ask @north about it" 🔵 @kanyewest on X · 50 likes')
    pool = _section(text, "## Top Community Comments")
    assert '- "this is the best tour of the decade" 🔵 @fan on X · 400 likes' in pool
    # A comment on another platform keeps its text as written.
    assert '- "@grok is this true or is it made up" 🟠 u/replier' in pool


def test_quote_text_has_exactly_one_pair_of_outer_quotes():
    candidates = _two_comment_candidates()
    candidates[1].source_items[0].metadata["top_comments"][0]["excerpt"] = (
        '"If you have 100 more kids..." 😂😂😂😂'
    )
    own = _own_post(1, 50, text='He said "no" and then "maybe" to the label')
    text = render.render_compact(_report(candidates, extra_items=[own]))
    assert "- \"'If you have 100 more kids...' 😂😂😂😂\" 🟠 u/CDC678" in _section(text, "## Top Community Comments")
    assert "- \"He said 'no' and then 'maybe' to the label\" 🔵 @kanyewest" in _section(text, "## Posts by @kanyewest")
    for line in _entries(_section(text, "## Top Community Comments")):
        assert line.count('"') == 2, line


def test_long_quote_is_cut_at_a_sentence_end_when_one_fits():
    long = "The satellites are already up. " + "They image through cloud at night. " + "filler " * 60
    candidates = _two_comment_candidates()
    candidates[1].source_items[0].metadata["top_comments"][0]["excerpt"] = long
    section = _section(render.render_compact(_report(candidates)), "## Top Community Comments")
    assert '- "The satellites are already up. They image through cloud at night." 🟠 u/CDC678' in section


def test_long_quote_with_no_sentence_end_is_cut_at_a_word_with_one_ellipsis():
    long = " ".join(f"word{index}" for index in range(80))
    candidates = _two_comment_candidates()
    candidates[1].source_items[0].metadata["top_comments"][0]["excerpt"] = long
    (line,) = [
        line for line in _entries(_section(render.render_compact(_report(candidates)), "## Top Community Comments"))
        if "word0" in line
    ]
    quote = line.split('" 🟠', 1)[0][3:]
    assert len(quote) <= 240
    assert quote.endswith("...") and not quote.endswith("....")
    assert re.search(r"word\d+\.\.\.$", quote)
    assert quote[:-3] == long[: len(quote) - 3]


def test_quote_text_carries_no_raw_url():
    candidates = _two_comment_candidates()
    candidates[1].source_items[0].metadata["top_comments"][0]["excerpt"] = (
        "ICEYE already announced this: https://www.iceye.com/newsroom/press-releases/iceye-and-nokia "
        "and see www.example.com/page too. Worth a read."
    )
    (line,) = [
        line for line in _entries(_section(render.render_compact(_report(candidates)), "## Top Community Comments"))
        if "ICEYE" in line
    ]
    quote = line.split('" 🟠', 1)[0]
    assert "http" not in quote and "www." not in quote and "iceye.com" not in quote
    assert quote == '- "ICEYE already announced this: and see too. Worth a read.'




# --- round 3: the pool is chosen and ordered by position, not by likes --------


def _thread(source, index, scores, *, container=None):
    return _item(
        source, f"{source}{index}", title=f"{source} item {index}",
        url=f"https://example.com/{source}/{index}", container=container,
        metadata={"top_comments": [
            {"author": f"{source}{index}rank{rank}", "score": score,
             "excerpt": f"Rank {rank} remark on {source} item {index}."}
            for rank, score in enumerate(scores, start=1)
        ]},
    )


def _cluster(index, candidates):
    return schema.Cluster(
        cluster_id=f"cl-{index}", title=f"Story {index}",
        candidate_ids=[candidate.candidate_id for candidate in candidates],
        representative_ids=[candidates[0].candidate_id],
        sources=sorted({candidate.source for candidate in candidates}), score=90 - index,
    )


def _pool_authors(lines):
    return [re.search(r"(\w+rank\d)", line.split('" ', 1)[1]).group(1) for line in lines if "rank" in line]


def test_each_item_gives_its_top_two_comments_and_nothing_below():
    report = _report([_candidate(_thread("reddit", 1, [900, 800, 700, 600], container="sub"))])
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    assert _pool_authors(entries) == ["reddit1rank1", "reddit1rank2"]
    assert entries[0].endswith(" — cluster 1 — top comment")
    assert entries[1].endswith(" — cluster 1 — #2 comment")
    assert entries[0].split(" — ")[0].endswith("🟠 u/reddit1rank1 on r/sub · 900 upvotes")


def test_pool_runs_every_top_comment_in_story_order_then_every_second_never_by_likes():
    quiet = _candidate(_thread("tiktok", 1, [1200, 40]))
    loud = _candidate(_thread("reddit", 2, [20000, 19000, 18000, 17000, 16000], container="sub"))
    video = _candidate(_thread("youtube", 3, [7, 3]))
    report = _report([quiet, loud, video])
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    assert _pool_authors(entries) == [
        "tiktok1rank1", "reddit2rank1", "youtube3rank1",
        "tiktok1rank2", "reddit2rank2", "youtube3rank2",
    ]
    assert [line.rsplit(" — ", 1)[1] for line in entries] == ["top comment"] * 3 + ["#2 comment"] * 3
    assert [re.search(r"cluster (\d)", line).group(1) for line in entries] == ["1", "2", "3", "1", "2", "3"]


def test_items_follow_their_clusters_rank_not_the_candidate_list():
    first = _candidate(_thread("reddit", 1, [5, 4], container="sub"))
    second = _candidate(_thread("youtube", 2, [50000, 40000]))
    # Ranked candidates list the second story's item first; the clusters do not.
    report = _report([second, first], clusters=[_cluster(1, [first]), _cluster(2, [second])])
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    assert _pool_authors(entries) == ["reddit1rank1", "youtube2rank1", "reddit1rank2", "youtube2rank2"]


def test_position_is_the_items_own_stored_order_not_a_resort_by_votes():
    """YouTube and Hacker News store the platform's order, which is not by count."""
    video = _candidate(_thread("youtube", 1, [12, 900, 5000]))
    other = _candidate(_thread("reddit", 2, [30, 20], container="sub"))
    entries = _entries(_section(render.render_compact(_report([video, other])), "## Top Community Comments"))
    assert _pool_authors(entries) == ["youtube1rank1", "reddit2rank1", "youtube1rank2", "reddit2rank2"]


def test_posts_follow_the_top_comments_of_their_cluster_and_are_labelled_post():
    thread = _candidate(_thread("reddit", 1, [300, 200], container="sub"))
    post = _candidate(_item("x", "fan1", title="Kanye sold out AT&T Stadium in nine minutes",
                            url="https://x.com/PopBase/status/77", author="PopBase",
                            engagement={"likes": 48213}))
    video = _candidate(_thread("youtube", 2, [90, 80]))
    report = _report(
        [post, thread, video],
        clusters=[_cluster(1, [post, thread]), _cluster(2, [video])],
    )
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    assert [line.rsplit(" — ", 1)[1] for line in entries] == [
        "top comment", "post", "top comment", "#2 comment", "#2 comment",
    ]
    assert "u/reddit1rank1" in entries[0]
    assert entries[1].endswith("🔵 @PopBase on X · 48,213 likes — https://x.com/PopBase/status/77 — cluster 1 — post")
    assert "youtube2rank1" in entries[2]


def test_pool_covers_shown_clusters_before_other_items_within_the_cap():
    shown = [_candidate(_thread("reddit", index, [30, 20], container="sub")) for index in range(1, 9)]
    others = [_candidate(_thread("tiktok", index, [90000, 80000])) for index in range(9, 49)]
    clusters = [_cluster(index, [candidate]) for index, candidate in enumerate([*shown, *others], start=1)]
    report = _report([*shown, *others], clusters=clusters)
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    assert len(entries) == render.QUOTE_POOL_LIMIT == 40
    authors = _pool_authors(entries)
    # All sixteen comments of the eight shown stories are in, top and second.
    # The other 24 places go to other items' top comments, in ranked order.
    assert authors[:8] == [f"reddit{index}rank1" for index in range(1, 9)]
    assert authors[8:32] == [f"tiktok{index}rank1" for index in range(9, 33)]
    assert authors[32:] == [f"reddit{index}rank2" for index in range(1, 9)]
    assert all(' — re: "tiktok item' in line for line in entries[8:32])


@pytest.mark.parametrize(
    "source", ["instagram", "tiktok", "youtube", "reddit", "threads", "bluesky", "linkedin", "x"]
)
def test_item_whose_top_comment_has_no_vote_gives_the_pool_nothing(source):
    spam = _candidate(_thread(source, 1, [0, 5]))
    unscored = _candidate(_thread(source, 2, [None, 5]))
    partly = _candidate(_thread(source, 3, [6, 0]))
    report = _report([*_two_comment_candidates(), spam, unscored, partly])
    authors = _pool_authors(_entries(_section(render.render_compact(report), "## Top Community Comments")))
    assert authors == [f"{source}3rank1"]


def test_hacker_news_gives_its_first_two_comments_by_thread_position():
    story = _candidate(_thread("hackernews", 1, [None, 0, 400]))
    report = _report([*_two_comment_candidates(), story])
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    assert _pool_authors(entries) == ["hackernews1rank1", "hackernews1rank2"]
    (first,) = [line for line in entries if "hackernews1rank1" in line]
    assert '" 🟡 hackernews1rank1 on Hacker News — ' in first


def test_unquotable_top_comment_gives_its_position_to_the_next():
    thread = _thread("reddit", 1, [900, 800], container="sub")
    thread.metadata["top_comments"][0]["excerpt"] = "😂😂😂😂😂😂😂😂😂😂😂😂"
    report = _report([*_two_comment_candidates(), _candidate(thread)])
    (entry,) = [
        line for line in _entries(_section(render.render_compact(report), "## Top Community Comments"))
        if "reddit1rank" in line
    ]
    assert "reddit1rank2" in entry and entry.endswith(" — top comment")


def test_duplicate_text_does_not_hide_the_copy_that_is_eligible():
    """The first copy met is rejected (nobody voted for it); the later one stays."""
    rejected = _thread("instagram", 1, [0, 0])
    kept = _thread("reddit", 2, [500, 40], container="sub")
    for item in (rejected, kept):
        item.metadata["top_comments"][0]["excerpt"] = "He really said the quiet part out loud."
    report = _report([*_two_comment_candidates(), _candidate(rejected), _candidate(kept)])
    section = _section(render.render_compact(report), "## Top Community Comments")
    assert section.count("He really said the quiet part out loud.") == 1
    assert '"He really said the quiet part out loud." 🟠 u/reddit2rank1 on r/sub · 500 upvotes' in section


def test_checklist_and_rule_rank_by_position_not_likes(monkeypatch):
    block = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=False))
    assert "each thread's or video's top comment first, then its #2" in block
    assert "like counts do not rank a comment from one place against another" in block
    assert "a ceiling, not a target" in block
    assert "highest-voted" not in block


def test_funny_candidates_lead_their_storys_position_and_are_tagged():
    """The fun score belongs to the candidate, so its comments share the tag."""
    plain = _candidate(_thread("reddit", 1, [900, 800], container="sub"))
    funny = _candidate(_thread("youtube", 2, [40, 30]))
    funny.fun_score = render._QUOTE_POOL_FUNNY_FLOOR
    nearly = _candidate(_thread("tiktok", 3, [70, 60]))
    nearly.fun_score = render._BEST_TAKE_FUNNY_FLOOR - 1
    later = _candidate(_thread("instagram", 4, [9, 8]))
    later.fun_score = 95.0
    report = _report(
        [plain, funny, nearly, later],
        clusters=[_cluster(1, [plain, funny, nearly]), _cluster(2, [later])],
    )
    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))
    # Position still leads, and a funny entry never jumps to another story.
    assert _pool_authors(entries) == [
        "youtube2rank1", "reddit1rank1", "tiktok3rank1", "instagram4rank1",
        "youtube2rank2", "reddit1rank2", "tiktok3rank2", "instagram4rank2",
    ]
    assert entries[0].endswith(" — cluster 1 — top comment — funny")
    assert entries[4].endswith(" — cluster 1 — #2 comment — funny")
    assert entries[3].endswith(" — cluster 2 — top comment — funny")
    assert [line.endswith(" — funny") for line in entries] == [
        True, False, False, True, True, False, False, True,
    ]
    assert entries[0].split(" — ")[0].endswith("🔴 @youtube2rank1 on YouTube · 40 likes")


def test_busy_story_does_not_use_the_slots_of_the_stories_after_it():
    busy = [_candidate(_thread("reddit", index, [50, 40], container="sub")) for index in range(1, 46)]
    second = _candidate(_thread("youtube", 60, [9, 8]))
    third = _candidate(_thread("tiktok", 70, [7, 6]))
    post = _candidate(_item("x", "fan70", title="A fan post about the third story tonight",
                            url="https://x.com/fan/status/70", author="fan", engagement={"likes": 25}))
    clusters = [_cluster(1, busy), _cluster(2, [second]), _cluster(3, [third, post])]
    report = _report([*busy, second, third, post], clusters=clusters)

    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))

    assert len(entries) == render.QUOTE_POOL_LIMIT
    authors = _pool_authors(entries)
    assert "youtube60rank1" in authors and "tiktok70rank1" in authors
    # The busy story fills what is left after every story has its top comment,
    # in its own order, and the pool still prints story by story.
    assert authors[:38] == [f"reddit{index}rank1" for index in range(1, 39)]
    assert authors[38:] == ["youtube60rank1", "tiktok70rank1"]
    assert all(line.endswith(" — top comment") for line in entries)


def test_each_shown_story_gets_one_slot_before_any_gets_two():
    stories = [
        [_candidate(_thread("reddit", 10 * story + index, [50, 40], container="sub")) for index in range(3)]
        for story in range(1, 4)
    ]
    clusters = [_cluster(number, members) for number, members in enumerate(stories, start=1)]
    report = _report([cand for members in stories for cand in members], clusters=clusters)

    three = render._render_top_comments(report, limit=3, clusters=clusters)
    assert _pool_authors(three[2:]) == ["reddit10rank1", "reddit20rank1", "reddit30rank1"]

    five = render._render_top_comments(report, limit=5, clusters=clusters)
    assert _pool_authors(five[2:]) == [
        "reddit10rank1", "reddit11rank1", "reddit20rank1", "reddit21rank1", "reddit30rank1",
    ]


def test_second_comments_are_shared_round_by_round_once_top_comments_are_in():
    stories = [
        [_candidate(_thread("reddit", 10 * story + index, [50, 40], container="sub")) for index in range(2)]
        for story in range(1, 4)
    ]
    clusters = [_cluster(number, members) for number, members in enumerate(stories, start=1)]
    report = _report([cand for members in stories for cand in members], clusters=clusters)
    lines = render._render_top_comments(report, limit=9, clusters=clusters)
    authors = _pool_authors(lines[2:])
    assert authors[:6] == [f"reddit{n}rank1" for n in (10, 11, 20, 21, 30, 31)]
    # Three slots left for six second comments: one for each story.
    assert authors[6:] == ["reddit10rank2", "reddit20rank2", "reddit30rank2"]


# --- round 5: what is not a community comment, and the item bar --------------


def _comments_item(source, index, comments, *, author=None, container=None):
    return _item(
        source, f"{source}{index}", title=f"{source} item {index}",
        url=f"https://example.com/{source}/{index}", author=author, container=container,
        metadata={"top_comments": comments},
    )


def _pool(report):
    text = render.render_compact(report)
    if "\n## Top Community Comments\n" not in text:
        return []
    return _entries(_section(text, "## Top Community Comments"))


def test_github_comment_needs_a_reaction_like_any_vote_counting_platform():
    issue = _comments_item("github", 1, [
        {"author": "Asif2BD", "score": 0, "excerpt": "This breaks on every install since Tuesday."},
        {"author": "helper", "score": 9, "excerpt": "Pinning the old version works around it."},
    ])
    entries = _pool(_report([*_two_comment_candidates(), _candidate(issue)]))
    assert not [line for line in entries if "GitHub" in line]


@pytest.mark.parametrize(("author", "text"), [
    ("Asif2BD", "<!-- proskills:review v1 psk-id=abc --> **ProSkills automated review** (deterministic, no AI)."),
    ("Asif2BD", "<!- - proskills:review v1 - -> A review body that looks like a comment."),
    ("someone", "ProSkills Automated Review: three findings in this pull request."),
    ("dependabot[bot]", "Bumps the minor group with four updates to the lockfile."),
    ("github-actions", "Coverage went down by two percent on this branch."),
    ("AutoModerator", "Welcome to the sub! Please read the rules before posting."),
    ("review-bot", "Looks good to me, merging when the checks pass."),
    ("friendly", "I am a bot, and this action was performed automatically."),
])
@pytest.mark.parametrize("source", ["github", "reddit", "youtube", "hackernews"])
def test_bot_and_markup_comments_are_never_quoted(source, author, text):
    item = _comments_item(source, 1, [
        {"author": author, "score": 900, "excerpt": text},
        {"author": "person", "score": 40, "excerpt": "A real person's reaction to the thing."},
    ])
    entries = _pool(_report([*_two_comment_candidates(), _candidate(item)]))
    assert not [line for line in entries if author in line or text[:20] in line]
    # The next comment takes the position the bot comment held.
    (person,) = [line for line in entries if "person" in line]
    assert person.endswith(" — top comment")


def test_usernames_that_only_contain_bot_are_people():
    item = _comments_item("reddit", 1, [
        {"author": "Talbot_Abbott", "score": 900, "excerpt": "Robots are not what this thread is about."},
    ], container="sub")
    entries = _pool(_report([*_two_comment_candidates(), _candidate(item)]))
    assert [line for line in entries if "u/Talbot_Abbott" in line]


@pytest.mark.parametrize(("source", "item_author", "comment_author"), [
    ("youtube", "Engineering Explained", "@EngineeringExplained"),
    ("tiktok", "yeniuswest", "@YeniusWest"),
    ("instagram", "@iamkevinwong", "iamkevinwong"),
    ("reddit", "thread_op", "u/Thread_OP"),
    ("hackernews", "submitter", "Submitter"),
])
def test_the_items_own_author_is_skipped_and_the_next_comment_takes_the_position(
    source, item_author, comment_author,
):
    item = _comments_item(source, 1, [
        {"author": comment_author, "score": 683, "excerpt": "Addressing technical questions from the video!"},
        {"author": "viewer1", "score": 120, "excerpt": "The best community reaction here."},
        {"author": "viewer2", "score": 60, "excerpt": "The second community reaction here."},
        {"author": "viewer3", "score": 30, "excerpt": "The third community reaction here."},
    ], author=item_author)
    entries = _pool(_report([_candidate(item)]))
    assert ["viewer1" in entries[0], "viewer2" in entries[1], len(entries)] == [True, True, 2]
    assert entries[0].endswith(" — top comment") and entries[1].endswith(" — #2 comment")
    assert "Addressing technical questions" not in "\n".join(entries)


@pytest.mark.parametrize(
    "source", ["reddit", "youtube", "tiktok", "instagram", "github", "threads", "bluesky", "linkedin", "x"]
)
def test_item_joins_the_pool_only_when_its_top_community_comment_has_five_votes(source):
    small = _comments_item(source, 1, [
        {"author": "lol", "score": 1, "excerpt": "$500,000,000.00 on midterms LOL"},
        {"author": "also", "score": 1, "excerpt": "Another remark on the small thread."},
    ])
    four = _comments_item(source, 2, [{"author": "four", "score": 4, "excerpt": "Four people liked this remark."}])
    five = _comments_item(source, 3, [
        {"author": "five", "score": 5, "excerpt": "Five people liked this remark."},
        {"author": "one", "score": 1, "excerpt": "One person liked this second remark."},
    ])
    entries = _pool(_report([*_two_comment_candidates(), _candidate(small), _candidate(four), _candidate(five)]))
    joined = "\n".join(entries)
    assert "midterms" not in joined and "Another remark" not in joined and "Four people" not in joined
    # Past the bar, the item's #2 still needs only one vote.
    assert "Five people liked this remark." in joined and "One person liked this second remark." in joined


def test_item_bar_does_not_apply_to_hacker_news():
    story = _comments_item("hackernews", 1, [
        {"author": "alice", "score": None, "excerpt": "The checkout flow is one page now."},
        {"author": "bob", "score": 0, "excerpt": "A measured zero is still shown here."},
    ])
    joined = "\n".join(_pool(_report([*_two_comment_candidates(), _candidate(story)])))
    assert "alice on Hacker News" in joined and "bob on Hacker News" in joined


def test_markdown_emphasis_is_stripped_from_quote_text():
    item = _comments_item("youtube", 1, [
        {"author": "viewer", "score": 683,
         "excerpt": "*Addressing technical questions!* It is **really** __not__ a _torque_ thing, ~~sorry~~ ok. snake_case and 2 * 3 stay."},
    ])
    own = _own_post(1, 50, text="This is **the** best *show* yet")
    text = render.render_compact(_report([*_two_comment_candidates(), _candidate(item)], extra_items=[own]))
    assert (
        '- "Addressing technical questions! It is really not a torque thing, sorry ok. '
        'snake_case and 2 * 3 stay." 🔴 @viewer on YouTube · 683 likes'
    ) in _section(text, "## Top Community Comments")
    assert '- "This is the best show yet" 🔵 @kanyewest' in _section(text, "## Posts by @kanyewest")


@pytest.mark.parametrize("marker", [">", "&gt;", "\\>", "> ", "  >"])
def test_quoted_parent_lines_are_dropped_and_the_commenters_words_kept(marker):
    item = _comments_item("reddit", 1, [
        {"author": "replier", "score": 300,
         "excerpt": f"{marker}Cybercab costs 20,000\n\nTesla does not have a US$20,000 car.\n{marker}second quoted line\nNot even close."},
    ], container="sub")
    (entry,) = [line for line in _pool(_report([*_two_comment_candidates(), _candidate(item)])) if "replier" in line]
    assert entry.startswith('- "Tesla does not have a US$20,000 car. Not even close." 🟠 u/replier on r/sub · 300 upvotes')


def test_comment_that_only_quotes_its_parent_is_skipped_and_the_next_takes_its_position():
    item = _comments_item("reddit", 1, [
        {"author": "echo", "score": 900, "excerpt": "> Cybercab costs 20,000 and nothing else is said"},
        {"author": "emoji", "score": 800, "excerpt": "😂😂😂😂😂😂😂😂😂😂😂😂"},
        {"author": "person", "score": 700, "excerpt": "Tesla does not have a car at that price."},
        {"author": "fourth", "score": 600, "excerpt": "A second remark in the commenter's own words."},
        {"author": "fifth", "score": 500, "excerpt": "A third remark that stays out of the pool."},
    ], container="sub")
    entries = [line for line in _pool(_report([_candidate(item)]))]
    assert [re.search(r"u/(\w+)", line).group(1) for line in entries] == ["person", "fourth"]
    assert entries[0].endswith(" — top comment") and entries[1].endswith(" — #2 comment")

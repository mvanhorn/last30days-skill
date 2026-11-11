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
    r" — (?P<url>https://\S+)(?: — (?:cluster \d+|re: .+))?$"
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


def test_quote_pool_is_bounded_and_gives_every_shown_cluster_its_best_comment():
    candidates = []
    # Eight quiet story clusters, then one loud thread with many strong comments.
    for index in range(1, 9):
        candidates.append(_candidate(_reddit_thread(index, [
            {"author": f"quiet{index}", "score": 2, "excerpt": f"Quiet remark on story number {index}."},
        ])))
    for index in range(9, 49):
        candidates.append(_candidate(_reddit_thread(index, [
            {"author": f"loud{index}", "score": 5000 + index, "excerpt": f"Loud remark number {index} here."},
        ])))
    report = _report(candidates)

    entries = _entries(_section(render.render_compact(report), "## Top Community Comments"))

    assert len(entries) == render.QUOTE_POOL_LIMIT == 40
    for index in range(1, 9):
        (entry,) = [line for line in entries if f"u/quiet{index} " in line]
        assert entry.endswith(f" — cluster {index}")
    # An entry from a cluster the output does not show names its thread.
    loud = [line for line in entries if "u/loud" in line]
    assert len(loud) == 32
    assert all(re.search(r' — re: "Reddit thread number \d+"$', line) for line in loud)


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
    assert "never inside a paragraph" in block
    assert "up to six per story" in block
    assert "```text" in block and "fence included" in block
    assert "I'm now an expert on" in block
    assert "User and host instructions come first" in block


def test_checklist_mentions_lists_only_on_claude_code(monkeypatch):
    plain = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=False))
    for needle in ("markdown list", "Claude Code", "`1\\.`", "`•`"):
        assert needle not in plain
    on_claude_code = _checklist(_stdout(_mixed_pool_report(), monkeypatch, claude_code=True))
    assert "no markdown lists anywhere" in on_claude_code
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


def test_quote_pool_tail_needs_votes_but_a_shown_clusters_own_comments_do_not():
    comments = [
        {"author": f"voter{index}", "score": score, "excerpt": f"Remark number {index} on the same video."}
        for index, score in enumerate([900, 400, 90, 30, 2, 0])
    ]
    video = _item("youtube", "yt1", title="One video", url="https://www.youtube.com/watch?v=ONE",
                  metadata={"top_comments": comments})
    candidate = _candidate(video)

    in_cluster = _entries(_section(render.render_compact(_report([candidate])), "## Top Community Comments"))
    # Ranks one to three have no floor, rank four clears the tail floor, and
    # the two-vote and zero-vote comments past them are left out.
    assert [re.search(r"@(voter\d)", line).group(1) for line in in_cluster] == [
        "voter0", "voter1", "voter2", "voter3",
    ]


@pytest.mark.parametrize(
    "source", ["instagram", "tiktok", "youtube", "x", "reddit", "threads", "bluesky", "linkedin"]
)
def test_quote_pool_takes_no_zero_vote_comment_from_a_platform_that_counts_votes(source):
    """Even as its platform's top-ranked comment: nobody picked it."""
    post = _item(source, "p1", title="A post", url=f"https://example.com/{source}/p1", metadata={
        "top_comments": [
            {"author": "spammer", "score": 0, "excerpt": "Come try our fitness class this week!"},
            {"author": "unscored", "score": None, "excerpt": "Nobody measured a vote on this one."},
            {"author": "onevote", "score": 1, "excerpt": "One person liked this remark."},
        ],
    })
    report = _report([*_two_comment_candidates(), _candidate(post)])
    section = _section(render.render_compact(report), "## Top Community Comments")
    assert "spammer" not in section
    assert "unscored" not in section
    assert "onevote" in section


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


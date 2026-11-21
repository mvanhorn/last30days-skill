"""Job postings stay out of the freshness note and do not flood the saved file.

Evidence, live runs on 2026-11-10: SpaceX printed "Recent evidence is thin:
only 278 of 3039 dated items are from the last 7 days." when 2,898 of those
items were job postings, and its saved raw file was 2.4 MB, 2.3 MB of it one
"### Jobs (2898 items)" section.
"""

from lib import jobs_digest, render, schema

WINDOW = ("2026-10-12", "2026-11-11")
BOILERPLATE = "SpaceX was founded under the belief that a future where humanity is out exploring the stars. " * 4


def _post(index, published):
    return schema.SourceItem(
        item_id=f"R{index}", source="reddit", title=f"Starship thread {index}", body="",
        url=f"https://www.reddit.com/r/spacex/comments/{index}", published_at=published,
    )


def _job(index, published="2026-03-01"):
    return schema.SourceItem(
        item_id=f"GH{index}", source="jobs", title=f"Engineer {index}", body=BOILERPLATE,
        url=f"https://boards.greenhouse.io/spacex/jobs/{index}", published_at=published,
        container=f"Team {index}", snippet=BOILERPLATE,
    )


def _candidate(item):
    return schema.Candidate(
        candidate_id=f"c-{item.item_id}", item_id=item.item_id, source=item.source,
        title=item.title, url=item.url, snippet=item.snippet or "", subquery_labels=["primary"],
        native_ranks={}, local_relevance=0.5, freshness=50, engagement=None,
        source_quality=0.5, rrf_score=0.1, sources=[item.source], source_items=[item],
        final_score=40.0,
    )


def _report(posts, jobs, *, ranked=(), board=None):
    items_by_source = {}
    if posts:
        items_by_source["reddit"] = posts
    if jobs:
        items_by_source["jobs"] = jobs
    return schema.Report(
        topic="SpaceX", range_from=WINDOW[0], range_to=WINDOW[1],
        generated_at="2026-11-11T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
        query_plan=schema.QueryPlan(
            intent="company", freshness_mode="balanced_recent", cluster_mode="none",
            raw_topic="SpaceX", subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=[_candidate(item) for item in ranked],
        items_by_source=items_by_source, errors_by_source={},
        artifacts={"jobs": board} if board else {},
    )


# --- Defect 1: freshness ---


def test_old_job_postings_do_not_make_recent_evidence_look_thin():
    # 6 of 8 conversation items are from the last week; 200 postings are older.
    posts = [_post(index, "2026-11-08") for index in range(6)] + [_post(8, "2026-10-15"), _post(9, "2026-10-16")]
    report = _report(posts, [_job(index) for index in range(200)])
    assert render._assess_data_freshness(report) is None
    assert "Recent evidence is thin" not in "\n".join(render._render_emoji_footer(report, None))


def test_freshness_note_counts_only_conversation_items():
    posts = [_post(index, "2026-11-08") for index in range(3)] + [_post(10 + index, "2026-10-15") for index in range(5)]
    report = _report(posts, [_job(index) for index in range(200)])
    assert render._assess_data_freshness(report) == (
        "Recent evidence is thin: only 3 of 8 dated items are from the last 7 days."
    )


def test_fresh_job_postings_do_not_hide_thin_recent_evidence():
    posts = [_post(1, "2026-11-08")] + [_post(10 + index, "2026-10-15") for index in range(9)]
    report = _report(posts, [_job(index, "2026-11-09") for index in range(200)])
    assert render._assess_data_freshness(report) == (
        "Limited recent data: only 1 of 10 dated items are from the last 7 days."
    )


# --- Defect 2: the saved raw file ---


def _board(jobs):
    raw = [
        {
            "id": item.item_id, "title": item.title, "url": item.url,
            "description": f"Own line {index}.", "description_blocks": f"Own line {index}.",
            "date": None, "posted_date_true": False, "provider": "greenhouse",
            "department": item.container, "location": "Hawthorne",
        }
        for index, item in enumerate(jobs)
    ]
    return {
        "company": "SpaceX", "provider": "greenhouse", "tier": "ats", "owned": True,
        "open_roles": len(jobs), "new_in_window": None,
        "digest": jobs_digest.build_digest(raw, jobs_digest.select_roles(raw)),
    }


def test_saved_file_lists_the_ranked_postings_and_counts_the_rest():
    jobs = [_job(index) for index in range(500)]
    report = _report([_post(1, "2026-11-08")], jobs, ranked=[jobs[7], jobs[3]], board=_board(jobs))
    text = render.render_full(report)
    section = text.split("### Jobs (500 items)", 1)[1].split("\n## ", 1)[0]
    assert section.count("**GH") == 2
    assert section.index("**GH7**") < section.index("**GH3**")
    assert (
        "_498 more job postings on the board are not listed here: only ranked postings are. "
        "The Jobs Digest above holds the board's distinct roles._"
    ) in section
    assert len(text) < 60_000


def test_saved_file_carries_the_jobs_digest():
    jobs = [_job(index) for index in range(500)]
    report = _report([_post(1, "2026-11-08")], jobs, ranked=[jobs[7]], board=_board(jobs))
    text = render.render_full(report)
    assert "## Jobs Digest" in text
    assert "- Board: SpaceX | greenhouse | 500 open roles" in text
    assert text.index("## Jobs Digest") < text.index("## All Items by Source")


def test_saved_file_never_lists_more_than_the_limit_of_postings():
    jobs = [_job(index) for index in range(300)]
    report = _report([], jobs, ranked=jobs[:120])
    section = render.render_full(report).split("### Jobs (300 items)", 1)[1]
    assert section.count("**GH") == render.FULL_DUMP_JOBS_LIMIT
    assert f"{300 - render.FULL_DUMP_JOBS_LIMIT} more job postings" in section


def test_saved_file_with_no_ranked_posting_says_so():
    jobs = [_job(index) for index in range(300)]
    section = render.render_full(_report([], jobs)).split("### Jobs (300 items)", 1)[1]
    assert "**GH" not in section
    assert "_No job posting was ranked, so none of the board's 300 is listed here._" in section


def test_saved_file_still_lists_every_item_of_other_sources():
    posts = [_post(index, "2026-11-08") for index in range(60)]
    text = render.render_full(_report(posts, []))
    assert text.count("**R") == 60
    assert "not listed here" not in text

"""The role-unique jobs digest: what is removed, what is selected, how it is bounded."""

from unittest.mock import patch

from lib import jobs, jobs_digest, pipeline, render, schema

WINDOW = ("2026-10-11", "2026-11-10")
SHARED = "Acme is an equal opportunity employer."


def role(index, *, title=None, department="Engineering", location="Remote", text="", date=None, true_date=False):
    return {
        "id": f"GH{index}",
        "title": title or f"Software Engineer {index}",
        "url": f"https://boards.greenhouse.io/acme/jobs/{index}",
        "description": text,
        "description_blocks": text,
        "date": date,
        "posted_date_true": true_date,
        "provider": "greenhouse",
        "department": department,
        "location": location,
    }


def _texts(items):
    digest = jobs_digest.build_digest(items, jobs_digest.select_roles(items))
    return {entry["title"]: entry["text"] for entry in digest["roles"]}


def test_sentence_repeated_across_roles_is_removed_and_a_unique_one_is_kept():
    items = [
        role(index, text=f"Own the number {index} widget line. {SHARED}" if index < 40 else f"Own the number {index} widget line.")
        for index in range(50)
    ]
    texts = _texts(items)
    assert texts
    assert all(SHARED not in text for text in texts.values())
    assert texts["Software Engineer 3"] == "Own the number 3 widget line."


def test_one_role_board_keeps_its_text():
    texts = _texts([role(1, text=f"Own the widget line. {SHARED}")])
    assert texts == {"Software Engineer 1": f"Own the widget line. {SHARED}"}


def test_role_posted_in_several_locations_appears_once_with_its_text():
    items = [
        role(index, title="Field Engineer", location=city, text="Install widgets on customer sites.")
        for index, city in enumerate(["Paris", "Lyon", "Nice", "Lille"])
    ]
    digest = jobs_digest.build_digest(items, jobs_digest.select_roles(items))
    assert [entry["title"] for entry in digest["roles"]] == ["Field Engineer"]
    assert digest["roles"][0]["text"] == "Install widgets on customer sites."
    assert digest["distinct_roles"] == 1


def test_large_board_stays_under_the_role_cap_and_character_ceiling():
    items = [
        role(
            index,
            title=f"Engineer {index}, Team {index % 97}",
            department=f"Dept {index % 61}",
            location=f"City {index % 300}",
            text=f"Build system {index}. " * 80,
        )
        for index in range(2900)
    ]
    selected = jobs_digest.select_roles(items)
    digest = jobs_digest.build_digest(items, selected)
    assert len(selected) == jobs_digest.DIGEST_MAX_ROLES
    assert all(len(entry["text"]) <= jobs_digest.DIGEST_ROLE_CHARS for entry in digest["roles"])
    report = _report({"company": "Acme", "provider": "greenhouse", "open_roles": 2900, "digest": digest})
    block = "\n".join(render._render_jobs_digest(report))
    assert block.startswith("## Jobs Digest")
    assert len(block) <= jobs_digest.DIGEST_MAX_CHARS


def test_selection_prefers_rare_departments_locations_and_founding_titles():
    items = [role(index, location="Remote") for index in range(60)]
    items.append(role(100, title="Analyst", department="Money as a Service"))
    items.append(role(101, title="Software Engineer 101", location="Ulaanbaatar"))
    items.append(role(102, title="Founding Designer"))
    selected = jobs_digest.select_roles(items, limit=3)
    assert {entry["item"]["id"] for entry in selected} == {"GH100", "GH101", "GH102"}


def test_board_without_descriptions_yields_titles_and_no_passages():
    payload = {"content": [
        {"id": str(index), "name": f"Account Executive {index}", "company": {"name": "Acme"},
         "department": {"label": "Sales"}, "releasedDate": "2026-11-01T00:00:00.000Z"}
        for index in range(4)
    ]}
    items = jobs.parse_smartrecruiters_response(payload, slug="acme")
    digest = jobs_digest.build_digest(items, jobs_digest.select_roles(items))
    assert len(digest["roles"]) == 4
    assert all(entry["text"] == "" for entry in digest["roles"])
    report = _report({"company": "Acme", "provider": "smartrecruiters", "open_roles": 4, "digest": digest})
    block = render._render_jobs_digest(report)
    assert block[2].startswith("- Board: Acme | smartrecruiters | 4 open roles")
    assert sum("Account Executive" in line for line in block) == 4
    assert not any(line.rstrip().endswith(":") for line in block[2:])


def _report(board=None, items=None):
    return schema.Report(
        topic="Acme",
        range_from=WINDOW[0],
        range_to=WINDOW[1],
        generated_at="2026-11-10T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
        query_plan=schema.QueryPlan(
            intent="product", freshness_mode="balanced_recent", cluster_mode="none",
            raw_topic="Acme", subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=[],
        items_by_source={"jobs": items} if items else {},
        errors_by_source={},
        artifacts={"jobs": board} if board else {},
    )


def _greenhouse_board(count=5):
    return {"jobs": [
        {
            "id": index,
            "title": f"Software Engineer {index}",
            "company_name": "Acme",
            "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{index}",
            "content": f"&lt;p&gt;Own the number {index} widget line.&lt;/p&gt;&lt;p&gt;{SHARED}&lt;/p&gt;",
            "first_published": "2026-11-01T09:00:00-04:00" if index < 2 else "2026-03-01T09:00:00-04:00",
            "updated_at": "2026-11-09T09:00:00-04:00",
            "departments": [{"name": "Engineering"}],
            "location": {"name": "Remote"},
        }
        for index in range(count)
    ]}


def _search(careers_html, board, company="Acme", **kwargs):
    with patch("lib.jobs.http.get_text", return_value=careers_html), \
            patch("lib.jobs.http.get", return_value=board):
        return jobs.search_jobs(company, WINDOW, {}, web_backend="none", **kwargs)


def test_board_linked_from_the_careers_page_is_owned_and_carries_a_digest():
    items, artifact = _search('<a href="https://boards.greenhouse.io/acme">Jobs</a>', _greenhouse_board())
    assert artifact["owned"] is True
    assert artifact["board_company"] == "Acme"
    assert artifact["open_roles"] == 5
    assert artifact["new_in_window"] == 2
    texts = [entry["text"] for entry in artifact["digest"]["roles"]]
    assert "Own the number 0 widget line." in texts
    assert all(SHARED not in text for text in texts)


def test_guessed_slug_board_naming_another_company_is_unowned_and_has_no_digest():
    board = _greenhouse_board()
    for job in board["jobs"]:
        job["company_name"] = "Acme Pest Control"
    items, artifact = _search(None, board)
    assert len(items) == 5
    assert artifact["owned"] is False
    assert artifact["board_company"] == "Acme Pest Control"
    assert artifact["open_roles"] == 5
    assert "digest" not in artifact
    assert render._render_jobs_digest(_report(artifact)) == []


def test_guessed_slug_board_naming_the_company_is_owned():
    _items, artifact = _search(None, _greenhouse_board(), company="Acme Inc")
    assert artifact["owned"] is True
    assert artifact["digest"]["roles"]


def test_resolver_site_is_where_the_careers_page_is_looked_for():
    fetched = []

    def careers(url, **kwargs):
        fetched.append(url)
        return '<a href="https://boards.greenhouse.io/nvidia">Jobs</a>' if "nvidia.com" in url else None

    with patch("lib.jobs.http.get_text", side_effect=careers), \
            patch("lib.jobs.http.get", return_value=_greenhouse_board()) as board:
        _items, artifact = jobs.search_jobs(
            "Nvidia", WINDOW, {}, web_backend="none", site="https://www.nvidia.com/en-us/",
        )
    assert fetched[0] == "https://www.nvidia.com/careers"
    assert board.call_args.args[0] == "https://boards-api.greenhouse.io/v1/boards/nvidia/jobs"
    assert artifact["owned"] is True


def test_jobs_lane_searches_the_resolved_company_for_a_person_topic():
    subquery = schema.SubQuery(
        label="primary", search_query="Jensen Huang", ranking_query="Jensen Huang", sources=["jobs"],
    )
    config = {
        "_company": {"company": "Nvidia", "company_site": "https://www.nvidia.com", "company_via_person": True},
        "_comparison_entity_count": 3,
    }
    with patch.object(jobs, "search_jobs", return_value=([], {})) as search:
        pipeline._retrieve_stream_impl(
            topic="Jensen Huang", subquery=subquery, source="jobs", config=config,
            depth="default", date_range=WINDOW, runtime=None, mock=False,
            web_backend="none", raw_topic="Jensen Huang",
        )
    assert search.call_args.args[0] == "Nvidia"
    assert search.call_args.kwargs["site"] == "https://www.nvidia.com"
    assert search.call_args.kwargs["entity_count"] == 3


def test_three_entity_comparison_gets_a_third_of_the_board_budget():
    seen = {}

    def board(token, *, deadline):
        seen["deadline"] = deadline
        return [], {}

    page = '<a href="https://acme.wd5.myworkdayjobs.com/AcmeCareers">Jobs</a>'
    with patch("lib.jobs.http.get_text", return_value=page), \
            patch("lib.jobs.time.monotonic", return_value=100.0), \
            patch.object(jobs, "search_workday_board", side_effect=board):
        jobs.search_jobs("Acme", WINDOW, {}, web_backend="none", entity_count=3)
        assert seen["deadline"] == 100.0 + jobs.WORKDAY_BUDGET_SECONDS / 3
        jobs.search_jobs("Acme", WINDOW, {}, web_backend="none")
        assert seen["deadline"] == 100.0 + jobs.WORKDAY_BUDGET_SECONDS


def test_past_as_of_run_skips_the_jobs_lane_and_says_why():
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic="Acme", config={}, mock=True, web_backend="none",
            requested_sources=["reddit", "jobs"], as_of_date="2024-03-01", depth="default",
        )
    assert "jobs" not in {call.kwargs["source"] for call in retrieve.call_args_list}
    outcome = report.source_status["jobs"]
    assert outcome.attempted is False
    assert "past window" in outcome.detail
    assert "jobs" not in report.errors_by_source
    assert "💼" not in "\n".join(render._render_emoji_footer(report, None))


def test_weak_title_themes_keep_the_digest_and_the_footer_line():
    raw = [
        role(index, title=f"Clerk {index}", department=f"Dept {index}", text=f"Do task {index}.")
        for index in range(20)
    ]
    digest = jobs_digest.build_digest(raw, jobs_digest.select_roles(raw))
    board = {"company": "Acme", "provider": "greenhouse", "tier": "ats", "open_roles": 20,
             "new_in_window": None, "owned": True, "digest": digest}
    items = [
        schema.SourceItem(
            item_id=item["id"], source="jobs", title=item["title"], body=item["title"],
            url=item["url"], container=item["department"],
        )
        for item in raw
    ]
    bundle = schema.RetrievalBundle(artifacts={"jobs": board})
    bundle.add_items("primary", "jobs", items)

    summary = pipeline._apply_hiring_signal_gate(bundle, explicit=False, topic="Acme")

    assert summary["include"] is False
    assert "jobs" not in bundle.items_by_source
    report = _report(bundle.artifacts["jobs"])
    report.artifacts["hiring_signals"] = summary
    text = render.render_compact(report)
    assert "## Hiring Signals" not in text
    assert "## Jobs Digest" in text
    assert "Do task 7." in text
    assert "💼 Jobs: 20 open roles" in text

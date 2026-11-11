"""The jobs lane inside one time budget, and what it says when the time runs out.

Evidence, four live runs on v3.34.1 on 2026-11-10:

- "Disney" (``--ticker "DIS:Walt Disney"``, official site
  thewaltdisneycompany.com): "Jobs timeout: jobs timed out after 180s". The
  official site never reached the lane, so it guessed disney.com, disney.ai
  and disney.io; the last two never answered, at 30 s a page.
- "Sundar Pichai" (``--ticker "GOOGL:Alphabet"``): "Jobs timeout: URL Error:
  timed out" after 134 s, from sundarpichai.com and sundarpichai.ai.
- "Elon Musk" (``--ticker "TSLA:Tesla,SPCX:SpaceX"``): no jobs, because two
  host companies named no one company.
- "Waymo": the roles the run ranked highest were not among the digest's 40.
"""

import socket
import threading
import urllib.error
from unittest.mock import patch

import last30days as cli
from lib import health, http, jobs, jobs_digest, pipeline, render, schema, yahoo_finance

WINDOW = ("2026-10-12", "2026-11-11")
CAREERS = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>'


class Clock:
    """A clock the test moves: a stubbed request costs time without waiting."""

    def __init__(self, now: float = 1000.0) -> None:
        self.start = self.now = now
        self._lock = threading.Lock()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds

    @property
    def elapsed(self) -> float:
        return self.now - self.start


def _not_found(url, *_args, **_kwargs):
    raise http.HTTPError("HTTP 404: Not Found", status_code=404)


# --- One budget for the whole lane ---


def test_slow_board_returns_the_rows_read_with_a_floor_count_and_a_coverage_note(capsys):
    clock = Clock()
    listing_calls = []

    def post(url, json_data, headers=None, **_kwargs):
        clock.advance(4.0)  # every listing page takes four seconds
        offset = json_data["offset"]
        listing_calls.append(offset)
        rows = [
            {
                "title": f"Role {index}",
                "externalPath": f"/job/Remote/Role-{index}_JR{index}",
                "locationsText": "Remote",
                "bulletFields": [f"JR{index}"],
            }
            for index in range(offset, offset + 20)
        ]
        return {"total": 2000 if offset == 0 else 0, "jobPostings": rows}

    with patch("lib.jobs.http.get_text", return_value=CAREERS), \
            patch("lib.jobs.http.post", side_effect=post), \
            patch("lib.jobs.http.get", side_effect=AssertionError("no time is left for detail calls")):
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="none", site="https://www.acme.com",
            budget=jobs.LaneBudget(45.0, clock=clock),
        )

    # About eleven pages fit in 45 s; eight run at once, so a few more start.
    assert 20 < len(items) < 2000
    assert len(listing_calls) <= 12 + jobs.WORKDAY_MAX_WORKERS
    assert clock.elapsed <= 45.0 + 4.0 * jobs.WORKDAY_MAX_WORKERS
    assert artifact["tier"] == "ats"
    assert artifact["open_roles"] == len(items)
    assert artifact["open_roles_floor"] is True
    assert artifact["new_in_window"] is None
    assert "45s time budget ran out" in artifact["coverage_note"]
    assert f"{len(items):,} rows read of the 2,000 the board lists" in artifact["coverage_note"]
    assert artifact["digest"]["roles"]
    assert "board read in part" in artifact["digest"]["note"]
    assert "role text fetched for 0 of 40 selected roles" in artifact["digest"]["note"]
    assert "_source_outcome" not in artifact
    assert "[Jobs] Acme: workday board acme.wd5/AcmeCareers: board read in part" in capsys.readouterr().err


def test_budget_spent_with_nothing_found_names_the_step_on_stderr_and_in_the_outcome(capsys):
    clock = Clock()
    pages = []

    def slow_page(url, **_kwargs):
        pages.append(url)
        clock.advance(20.0)
        return None

    with patch("lib.jobs.http.get_text", side_effect=slow_page), \
            patch("lib.jobs.http.get", side_effect=AssertionError("no time is left to probe")):
        items, artifact = jobs.search_jobs(
            "Kanye West", WINDOW, {}, web_backend="none",
            budget=jobs.LaneBudget(45.0, clock=clock),
        )

    assert items == []
    assert pages == [
        "https://kanyewest.com/careers", "https://kanyewest.com/jobs", "https://kanyewest.ai/careers",
    ]
    outcome = artifact["_source_outcome"]
    assert outcome["state"] == health.TIMEOUT
    assert "45s budget" in outcome["detail"]
    assert "before the careers page https://kanyewest.ai/jobs" in outcome["detail"]
    assert artifact["_source_out_of_time"] is True
    assert f"[Jobs] {outcome['detail']}" in capsys.readouterr().err


def test_every_careers_request_carries_a_short_timeout_and_the_lanes_deadline():
    seen = []

    def page(url, **kwargs):
        seen.append((url, kwargs))
        return None

    with patch("lib.jobs.http.get_text", side_effect=page), \
            patch("lib.jobs.http.get", side_effect=_not_found):
        jobs.search_jobs("Kanye West", WINDOW, {}, web_backend="none")
        jobs.search_jobs("Acme", WINDOW, {}, web_backend="none", site="https://www.acme.com")

    assert len(seen) == 8
    for url, kwargs in seen:
        limit = jobs.PAGE_TIMEOUT_SECONDS if "acme.com" in url else jobs.GUESS_TIMEOUT_SECONDS
        assert kwargs["timeout"] == limit <= 8
        assert kwargs["retries"] == 1
        assert kwargs["deadline_monotonic"] is not None


def test_every_board_api_call_carries_a_short_timeout_and_the_lanes_deadline():
    seen = []

    def api(url, *_args, **kwargs):
        seen.append(kwargs)
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=api):
        jobs.search_jobs("Acme", WINDOW, {}, web_backend="none")

    assert len(seen) == 3  # one slug, three providers
    for kwargs in seen:
        assert kwargs["timeout"] == jobs.API_TIMEOUT_SECONDS <= 10
        assert kwargs["retries"] == 2
        assert kwargs["deadline_monotonic"] is not None


# --- A dead host fails fast ---


def _timed_out(*_args, **_kwargs):
    raise urllib.error.URLError(TimeoutError("timed out"))


def _dead_hosts(dead, asked):
    def opener(req, timeout=None, **_kwargs):
        asked.append((req.full_url, timeout))
        if req.full_url.split("/")[2] in dead:
            _timed_out()
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    return opener


def test_guessed_host_that_never_answers_is_asked_once_briefly_and_is_not_an_outage(capsys):
    asked = []
    dead = {"sundarpichai.com", "sundarpichai.ai", "sundarpichai.io"}
    budget = jobs.LaneBudget(45.0)
    with patch.object(http, "_open_request", side_effect=_dead_hosts(dead, asked)), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        items, artifact = jobs.search_jobs(
            "Sundar Pichai", WINDOW, {}, web_backend="none", budget=budget,
        )

    careers = [(url, timeout) for url, timeout in asked if "sundarpichai." in url]
    assert [url for url, _timeout in careers] == [
        "https://sundarpichai.com/careers",
        "https://sundarpichai.ai/careers",
        "https://sundarpichai.io/careers",
    ]
    assert all(timeout <= jobs.GUESS_TIMEOUT_SECONDS for _url, timeout in careers)
    assert items == []
    assert failures == []
    assert "_source_outcome" not in artifact
    assert budget.slow == [
        f"{host} did not answer in {jobs.GUESS_TIMEOUT_SECONDS}s"
        for host in ("sundarpichai.com", "sundarpichai.ai", "sundarpichai.io")
    ]
    # The caller that owns the budget says once that no board was found.
    assert capsys.readouterr().err == ""


def test_official_site_that_never_answers_is_asked_once_and_is_recorded():
    asked = []
    with patch.object(http, "_open_request", side_effect=_dead_hosts({"www.acme.com"}, asked)), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        items, _artifact = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="none", site="https://www.acme.com",
        )

    assert [url for url, _timeout in asked if "acme.com" in url] == ["https://www.acme.com/careers"]
    assert asked[0][1] <= jobs.PAGE_TIMEOUT_SECONDS
    assert items == []
    assert failures and all(failure.outcome_state == health.TIMEOUT for failure in failures)


def test_name_that_does_not_resolve_on_a_guessed_host_is_not_retried():
    asked = []

    def opener(req, timeout=None, **_kwargs):
        asked.append(req.full_url)
        raise urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided"))

    with patch.object(http, "_open_request", side_effect=opener), patch.object(http.time, "sleep"):
        html, no_answer = jobs._careers_text("https://kanyewest.io/careers", guessed=True)
    assert html is None and no_answer is True
    assert asked == ["https://kanyewest.io/careers"]


def test_slow_hosts_are_named_when_they_spend_the_budget(capsys):
    clock = Clock()

    def opener(req, timeout=None, **_kwargs):
        clock.advance(float(jobs.GUESS_TIMEOUT_SECONDS))  # the host uses its whole timeout
        _timed_out()

    with patch.object(http, "_open_request", side_effect=opener), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        items, artifact = jobs.search_jobs(
            "Kanye West", WINDOW, {}, web_backend="none",
            budget=jobs.LaneBudget(10.0, clock=clock),
        )

    assert items == []
    assert failures == []
    detail = artifact["_source_outcome"]["detail"]
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT
    assert "kanyewest.com did not answer in 4s" in detail
    assert "kanyewest.ai did not answer in 4s" in detail
    assert "10s budget" in detail
    assert detail in capsys.readouterr().err


# --- A careers system the lane does not read ---

# google.com/careers on 2026-11-10: Google's own careers system. No supported
# board is named on it or on the job search it links to.
OWN_SYSTEM = """
<h1>Join our team</h1>
<a href="/about/careers/applications/jobs/results">Search jobs</a>
<a href="https://www.linkedin.com/company/google/jobs/">LinkedIn</a>
"""


def test_careers_site_with_no_supported_board_returns_quietly(capsys):
    fetched = []

    def page(url, **_kwargs):
        fetched.append(url)
        return OWN_SYSTEM if "google.com" in url else None

    with patch("lib.jobs.http.get_text", side_effect=page), \
            patch("lib.jobs.http.get", side_effect=_not_found), \
            http.capture_failures() as failures:
        items, artifact = jobs.search_jobs(
            "Google", WINDOW, {}, web_backend="none", site="https://www.google.com",
        )

    assert fetched == [
        "https://www.google.com/careers",
        "https://www.google.com/about/careers/applications/jobs/results",
    ]
    assert items == []
    assert artifact["tier"] == "web"
    assert failures == []
    assert "_source_outcome" not in artifact
    assert "_source_outcome_detail" not in artifact
    assert "[Jobs] no supported job board found for Google" in capsys.readouterr().err


# --- The official site the run already knows ---


def test_official_site_reaches_the_jobs_lane_when_wikidata_named_no_company():
    assert cli._company_for_jobs({}, None, "Disney", official_site="thewaltdisneycompany.com") == {
        "topic_site": "thewaltdisneycompany.com",
    }
    resolved = {"company": "Nvidia", "company_site": "https://www.nvidia.com/"}
    assert cli._company_for_jobs(resolved, None, "Nvidia", official_site="example.com") == resolved

    (_items, _artifact), _names, mock = _run_lane(
        "Disney", {"topic_site": "thewaltdisneycompany.com"}, lambda *_a, **_k: ([], {"tier": "web"}),
    )
    assert mock.call_args_list[0].kwargs["site"] == "thewaltdisneycompany.com"


# --- Two companies named by the host ---


def _rows(count=6, host="boards.greenhouse.io/spacex"):
    return [
        {"id": f"GH{index}", "title": f"Engineer {index}", "url": f"https://{host}/jobs/{index}"}
        for index in range(count)
    ]


def _run_lane(topic, company, search, config=None):
    subquery = schema.SubQuery(label="primary", search_query=topic, ranking_query=topic, sources=["jobs"])
    config = {"_company": company, **(config or {})}
    with patch.object(jobs, "search_jobs", side_effect=search) as mock:
        result = pipeline._retrieve_stream_impl(
            topic=topic, subquery=subquery, source="jobs", config=config,
            depth="default", date_range=WINDOW, runtime=None, mock=False,
            web_backend="auto", raw_topic=topic,
        )
    return result, [call.args[0] for call in mock.call_args_list], mock


def test_two_host_ticker_companies_are_both_handed_to_the_lane_in_order():
    two = yahoo_finance.parse_host_tickers("TSLA:Tesla,SPCX:SpaceX")
    assert cli._company_for_jobs({}, two, "Elon Musk") == {"host_companies": ["Tesla", "SpaceX"]}
    # The topic's own name is never one of them.
    assert cli._company_for_jobs({}, two, "Tesla") == {"host_companies": ["SpaceX"]}


def test_each_host_company_is_tried_in_order_and_the_first_board_found_is_used():
    board = {"tier": "ats", "provider": "greenhouse", "company": "SpaceX", "open_roles": 6}

    def search(name, *_args, **_kwargs):
        return (_rows(), board) if name == "SpaceX" else ([], {"tier": "web"})

    (items, artifact), names, mock = _run_lane(
        "Elon Musk", {"host_companies": ["Tesla", "SpaceX"]}, search,
    )
    assert names == ["Elon Musk", "Tesla", "SpaceX"]
    assert len(items) == 6
    assert artifact["board_of"] == "SpaceX"
    # One budget for all three; finding the earlier companies' boards may
    # use only a share of it, so the last still has its turn.
    budgets = [call.kwargs["budget"] for call in mock.call_args_list]
    assert isinstance(budgets[0], jobs.LaneBudget)
    assert budgets[0] is budgets[1] is budgets[2]
    shares = [call.kwargs["discovery_seconds"] for call in mock.call_args_list]
    assert shares[0] <= jobs.JOBS_BUDGET_SECONDS / 3 and shares[1] <= jobs.JOBS_BUDGET_SECONDS / 2
    assert shares[2] is None
    assert [call.kwargs["web_backend"] for call in mock.call_args_list] == ["auto", "none", "none"]


def test_first_host_company_with_a_board_ends_the_search():
    board = {"tier": "ats", "provider": "greenhouse", "company": "Tesla", "open_roles": 6}
    (_items, artifact), names, _mock = _run_lane(
        "Elon Musk", {"host_companies": ["Tesla", "SpaceX"]},
        lambda name, *_a, **_k: (_rows(), board) if name == "Tesla" else ([], {"tier": "web"}),
    )
    assert names == ["Elon Musk", "Tesla"]
    assert artifact["board_of"] == "Tesla"


def test_host_companies_cut_off_by_the_budget_leave_the_reason_on_the_topics_result():
    outcome = {"state": health.TIMEOUT, "detail": "Tesla: jobs lane ran out of time", "attempted": True}

    def search(name, *_args, **_kwargs):
        if name == "Elon Musk":
            return [], {"tier": "web", "company": "Elon Musk"}
        return [], {"tier": "web", "_source_outcome": outcome, "_source_out_of_time": True}

    (items, artifact), names, _mock = _run_lane(
        "Elon Musk", {"host_companies": ["Tesla", "SpaceX"]}, search,
    )
    assert names == ["Elon Musk", "Tesla", "SpaceX"]
    assert items == []
    assert artifact["company"] == "Elon Musk"
    assert artifact["_source_outcome"] == outcome
    assert artifact["_source_out_of_time"] is True


def test_real_lane_tries_the_second_company_inside_the_same_budget():
    # The topic and the first company answer nowhere; the second has a board
    # that names it. No request is real: every one goes through the opener.
    spacex = (
        b'{"jobs":[{"id":1,"title":"Propulsion Engineer","company_name":"SpaceX",'
        b'"absolute_url":"https://boards.greenhouse.io/spacex/jobs/1",'
        b'"first_published":"2026-11-01T00:00:00Z","content":"Build engines."}]}'
    )

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return spacex

    def opener(req, timeout=None, **_kwargs):
        if req.full_url.startswith("https://boards-api.greenhouse.io/v1/boards/spacex/"):
            return Response()
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    subquery = schema.SubQuery(
        label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["jobs"],
    )
    lane = {}
    with patch.object(http, "_open_request", side_effect=opener), patch.object(http.time, "sleep"):
        items, artifact = pipeline._retrieve_stream_impl(
            topic="Elon Musk", subquery=subquery, source="jobs",
            config={"_company": {"host_companies": ["Tesla", "SpaceX"]}, "_jobs_lane": lane},
            depth="default", date_range=WINDOW, runtime=None, mock=False,
            web_backend="none", raw_topic="Elon Musk",
        )
    assert [item["title"] for item in items] == ["Propulsion Engineer"]
    assert artifact["board_of"] == "SpaceX"
    assert artifact["owned"] is True  # the board states the company's name
    assert artifact["digest"]["roles"][0]["title"] == "Propulsion Engineer"
    assert lane["budget"].settled is True


def test_footer_and_digest_name_the_company_whose_board_it_is():
    digest = {"distinct_roles": 1, "roles": [{
        "title": "Propulsion Engineer", "department": "", "location": "", "posted": "",
        "url": "https://boards.greenhouse.io/spacex/jobs/1", "urls": [], "text": "Build engines.",
    }]}
    board = {
        "tier": "ats", "provider": "greenhouse", "company": "SpaceX", "board_of": "SpaceX",
        "open_roles": 1234, "new_in_window": 56, "owned": True, "digest": digest,
    }
    report = _report("Elon Musk", board)
    assert render._jobs_board_footer_line(report) == (
        "💼 Jobs: 1,234 open roles │ 56 new in the L30 │ board: SpaceX"
    )
    assert "- Board: SpaceX | greenhouse | 1,234 open roles" in "\n".join(render._render_jobs_digest(report))
    # The topic's own board keeps the line it always had.
    del board["board_of"]
    assert render._jobs_board_footer_line(report) == "💼 Jobs: 1,234 open roles │ 56 new in the L30"


def _report(topic, board, items=None):
    return schema.Report(
        topic=topic,
        range_from=WINDOW[0],
        range_to=WINDOW[1],
        generated_at="2026-11-10T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
        query_plan=schema.QueryPlan(
            intent="product", freshness_mode="balanced_recent", cluster_mode="none",
            raw_topic=topic, subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=[],
        items_by_source={"jobs": items} if items else {},
        errors_by_source={},
        artifacts={"jobs": board},
    )


# --- One budget for every pass of a run ---


def test_second_pass_of_the_same_run_does_not_search_again_once_the_lane_has_its_answer():
    lane = {}
    calls = []

    def search(name, *_args, **_kwargs):
        calls.append(name)
        return [], {"tier": "web"}

    for _pass in range(2):
        (items, artifact), _names, _mock = _run_lane(
            "Kanye West", {}, search, config={"_jobs_lane": lane},
        )
    assert calls == ["Kanye West"]
    assert (items, artifact) == ([], {})


def test_second_pass_after_a_spent_budget_asks_nothing():
    clock = Clock()
    lane = {"budget": jobs.LaneBudget(45.0, clock=clock)}
    clock.advance(46.0)
    (items, artifact), names, _mock = _run_lane(
        "Kanye West", {}, lambda *_a, **_k: ([], {"tier": "web"}), config={"_jobs_lane": lane},
    )
    assert names == []
    assert (items, artifact) == ([], {})


def test_comparison_entities_share_the_budget_equally():
    lane = {}
    _run_lane(
        "Acme", {}, lambda *_a, **_k: ([], {"tier": "web"}),
        config={"_jobs_lane": lane, "_comparison_entity_count": 3},
    )
    assert lane["budget"].seconds == jobs.JOBS_BUDGET_SECONDS / 3


def test_the_lanes_budget_is_45_seconds_and_far_under_the_generic_source_cap():
    assert jobs.JOBS_BUDGET_SECONDS == 45.0
    assert jobs.JOBS_BUDGET_SECONDS <= pipeline.STREAM_FUTURE_TIMEOUT_SECONDS / 4


# --- Ownership ---


def test_board_under_a_guessed_slug_that_names_another_company_is_passed_over():
    # boards-api.greenhouse.io/v1/boards/disney on 2026-11-10: a two-role
    # template board of "Sgt. Pepper's Lonely Hearts Club Band".
    namesake = {"jobs": [{
        "id": 1, "title": "MASTER TEMPLATE", "company_name": "Sgt. Pepper's Lonely Hearts Club Band",
        "absolute_url": "https://job-boards.greenhouse.io/disney/jobs/1",
    }]}

    def api(url, *_args, **_kwargs):
        if url == "https://boards-api.greenhouse.io/v1/boards/disney/jobs":
            return namesake
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=api):
        items, artifact = jobs.search_jobs("Disney", WINDOW, {}, web_backend="none")
    assert items == []
    assert artifact["tier"] == "web"
    assert "not-the-company:greenhouse:disney" in artifact["attempted"]


def test_board_under_a_guessed_slug_with_a_longer_form_of_the_name_counts_but_gives_no_digest():
    listed = {"jobs": [{
        "id": 1, "title": "Engineer", "company_name": "Meta Platforms",
        "absolute_url": "https://job-boards.greenhouse.io/meta/jobs/1", "content": "Build things.",
    }]}
    calls = []

    def api(url, *_args, **_kwargs):
        calls.append(url)
        if url == "https://boards-api.greenhouse.io/v1/boards/meta/jobs":
            return listed
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=api):
        items, artifact = jobs.search_jobs("Meta", WINDOW, {}, web_backend="none")
    assert len(items) == 1
    assert artifact["open_roles"] == 1
    assert artifact["found_via"] == "slug-guess"
    assert artifact["owned"] is False
    assert "digest" not in artifact
    # The probe's rows are the board: it is not read a second time.
    assert calls == ["https://boards-api.greenhouse.io/v1/boards/meta/jobs"]


# --- The digest carries the roles the run ranked highest ---


def _board_row(index, *, department, location, title=None):
    return {
        "id": f"GH{index}",
        "title": title or f"Role {index}",
        "url": f"https://boards.greenhouse.io/waymo/jobs/{index}",
        "description": f"Own the number {index} line.",
        "description_blocks": f"Own the number {index} line.",
        "date": "2026-11-09",
        "posted_date_true": True,
        "provider": "greenhouse",
        "department": department,
        "location": location,
    }


def _waymo_board():
    """Forty-five one-of-a-kind roles the rarity pick takes, then fifteen in
    one department and one city that it leaves out."""
    rare = [
        _board_row(index, department=f"Dept {index}", location=f"City {index}") for index in range(45)
    ]
    common = [
        _board_row(index, department="Engineering", location="Mountain View", title=f"Engineer {index}")
        for index in range(45, 60)
    ]
    return rare + common


def test_roles_the_run_ranked_take_up_to_ten_of_the_forty_digest_places():
    rows = _waymo_board()
    selected = jobs_digest.select_roles(rows)
    digest = jobs_digest.build_digest(rows, selected)
    picked = [entry["url"] for entry in digest["roles"]]
    assert len(picked) == 40
    common = [row["url"] for row in rows[45:]]
    assert not set(common) & set(picked)

    ranked = [*common[:12], picked[3], "https://boards.greenhouse.io/other/jobs/1"]
    out = jobs_digest.add_engine_roles(digest, rows, ranked)

    urls = [entry["url"] for entry in out["roles"]]
    assert len(urls) == jobs_digest.DIGEST_MAX_ROLES == 40
    assert jobs_digest.ENGINE_ROLE_SLOTS == 10
    assert urls[:10] == common[:10]
    assert urls[10:] == picked[:30]
    assert all(entry.get("engine") for entry in out["roles"][:10])
    assert out["roles"][0]["text"] == "Own the number 45 line."
    assert out["roles"][0]["posted"] == "2026-11-09"
    # The ten places are spent: a role ranked after them is not reserved.
    assert out["roles"][13]["url"] == picked[3] and not out["roles"][13].get("engine")
    assert out["engine_roles"] == 10
    assert out["distinct_roles"] == digest["distinct_roles"]
    # The digest it was given is not changed.
    assert [entry["url"] for entry in digest["roles"]] == picked
    assert not any(entry.get("engine") for entry in digest["roles"])


def test_digest_with_no_ranked_roles_is_the_rarity_pick():
    rows = _waymo_board()
    digest = jobs_digest.build_digest(rows, jobs_digest.select_roles(rows))
    out = jobs_digest.add_engine_roles(digest, rows, [])
    assert out["roles"] == digest["roles"]
    assert out["engine_roles"] == 0


def _job_candidate(row, score):
    item = schema.SourceItem(
        item_id=row["id"], source="jobs", title=row["title"], body="", url=row["url"],
    )
    return schema.Candidate(
        candidate_id=f"c{row['id']}", item_id=item.item_id, source="jobs", title=item.title,
        url=item.url, snippet="", subquery_labels=["primary"], native_ranks={},
        local_relevance=0.5, freshness=50, engagement=None, source_quality=0.5,
        rrf_score=0.1, sources=["jobs"], source_items=[item], final_score=score,
    )


def test_ranked_postings_and_hiring_signal_roles_are_taken_in_turn():
    rows = _waymo_board()
    ranked = [_job_candidate(rows[index], 90 - index) for index in (50, 51, 52)]
    summary = {
        "mode": "standard", "include": True, "rejected_job_keys": [],
        "strategic_candidates": [{"title": "Engineer 55", "url": rows[55]["url"]}],
        "signals": [{"theme": "go-to-market", "evidence": [
            {"title": "Engineer 56", "url": rows[56]["url"]},
            {"title": "Engineer 50", "url": rows[50]["url"]},
        ]}],
    }
    assert pipeline._engine_ranked_job_urls(ranked, summary) == [
        rows[index]["url"] for index in (50, 55, 51, 56, 52)
    ]
    # A Hiring Signals block the run does not print names no roles.
    assert pipeline._engine_ranked_job_urls(ranked, {**summary, "include": False}) == [
        rows[index]["url"] for index in (50, 51, 52)
    ]
    # A posting the ranking rejected is not one it ranked.
    from lib import fusion
    rejected = {**summary, "rejected_job_keys": [fusion.candidate_key(ranked[0].source_items[0])]}
    assert rows[50]["url"] not in pipeline._engine_ranked_job_urls(ranked, rejected)[:1]


def test_pipeline_adds_the_ranked_roles_to_the_boards_digest_and_drops_the_rows():
    rows = _waymo_board()
    digest = jobs_digest.build_digest(rows, jobs_digest.select_roles(rows))
    board = {"tier": "ats", "provider": "greenhouse", "company": "Waymo", "open_roles": 60,
             "owned": True, "digest": digest}
    bundle = schema.RetrievalBundle(artifacts={"jobs": board})
    bundle.jobs_board_rows = rows
    ranked = [_job_candidate(rows[index], 90 - index) for index in (58, 59)]

    pipeline._add_ranked_roles_to_jobs_digest(bundle, ranked, None)

    roles = bundle.artifacts["jobs"]["digest"]["roles"]
    assert [entry["title"] for entry in roles[:2]] == ["Engineer 58", "Engineer 59"]
    assert len(roles) == 40
    assert bundle.jobs_board_rows == []

    text = "\n".join(render._render_jobs_digest(_report("Waymo", bundle.artifacts["jobs"])))
    assert "- Roles listed: 40 of 60 distinct roles, the first 2 are the roles this run's own ranking" in text
    assert text.index("Engineer 58") < text.index("Role 0")


def test_board_with_no_digest_gets_no_ranked_roles():
    rows = _waymo_board()
    board = {"tier": "ats", "provider": "greenhouse", "company": "Waymo", "open_roles": 60, "owned": False}
    bundle = schema.RetrievalBundle(artifacts={"jobs": board})
    bundle.jobs_board_rows = rows
    pipeline._add_ranked_roles_to_jobs_digest(bundle, [_job_candidate(rows[58], 90)], None)
    assert "digest" not in bundle.artifacts["jobs"]
    assert bundle.jobs_board_rows == []


def test_ranked_roles_a_board_gave_no_text_for_are_counted_in_the_note():
    # Workday gives a role's text one call at a time, made for the rarity
    # picks before anything is ranked.
    rows = _waymo_board()
    for row in rows[45:]:
        row["description"] = row["description_blocks"] = ""
        row["whole_posting"] = True
    digest = jobs_digest.build_digest(rows, jobs_digest.select_roles(rows))
    out = jobs_digest.add_engine_roles({**digest, "note": "earlier note"}, rows, [rows[50]["url"], rows[51]["url"]])
    assert [entry["title"] for entry in out["roles"][:2]] == ["Engineer 50", "Engineer 51"]
    assert out["note"].startswith("earlier note; 2 of the ranked roles listed first have no role text")


# --- The web fallback: only real postings, never ads or landing pages ---

AD = (
    "https://duckduckgo.com/y.js?ad_domain=career%2Dedge.net&ad_provider=bingv7aa"
    "&ad_type=txad&click_metadata=fDVA_zkAVS87wM1gPeREjxYWU"
)
# What the web search gave for "Disney careers jobs hiring" on 2026-11-10.
DISNEY_WEB = [
    {"title": "Search our Job Opportunities at DISNEY - Disney Careers",
     "url": "https://www.disneycareers.com/en/search-jobs", "snippet": "Search jobs at Disney."},
    {"title": "Disney® Careers - Hiring All Positions", "url": AD,
     "snippet": "Hiring Immediately. Full & Part-Time Disney Jobs."},
    {"title": "Disney Jobs (Positions Open) - Disney Careers (Applications)",
     "url": AD.replace("career%2Dedge.net", "jobmatcher.io"), "snippet": "Hiring Now."},
    {"title": "Careers at Disney | Disney Careers", "url": "https://jobs.disneycareers.com/", "snippet": ""},
]
# And for "Sundar Pichai careers jobs hiring", on v3.34.1 and after.
PICHAI_WEB = [
    {"title": "Sundar Pichai - Wikipedia", "url": "https://en.wikipedia.org/wiki/Sundar_Pichai",
     "snippet": "Pichai began his career as a materials engineer."},
    {"title": "How to get a job at Google, according to the CEO | Fortune",
     "url": "https://fortune.com/2024/10/12/how-to-get-a-job-at-google-ceo-sundar-pichai-entry-level-hiring/",
     "snippet": "Hiring advice."},
    {"title": "11,000+Sundar Pichai In Inr Jobs in United States - LinkedIn",
     "url": "https://www.linkedin.com/jobs/sundar-pichai-in-inr-jobs", "snippet": "Jobs."},
    {"title": "Careers", "url": "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fcareers", "snippet": ""},
]


def _web(results):
    return patch("lib.jobs.grounding.web_search", return_value=(results, {"label": "keyless"}))


def test_web_fallback_with_ads_landing_pages_and_wikipedia_ends_with_nothing(capsys):
    for company, results in (("Disney", DISNEY_WEB), ("Sundar Pichai", PICHAI_WEB)):
        with _web(results), \
                patch("lib.jobs.http.get_text", return_value=None), \
                patch("lib.jobs.http.get", side_effect=_not_found), \
                http.capture_failures() as failures:
            items, artifact = jobs.search_jobs(company, WINDOW, {}, web_backend="keyless")
        assert items == []
        assert artifact["tier"] == "web" and artifact["resultCount"] == 0
        assert "_source_outcome" not in artifact
        assert failures == []
        err = capsys.readouterr().err
        assert err.count("[Jobs]") == 1
        assert f"[Jobs] no supported job board found for {company}" in err


def test_web_fallback_keeps_a_posting_on_a_recognised_board_that_names_the_company():
    results = [
        *DISNEY_WEB,
        {"title": "Senior Engineer - Acme", "url": "https://boards.greenhouse.io/acme/jobs/4012345",
         "snippet": "Join Acme."},
        {"title": "Designer", "url": "https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-0123456789ab",
         "snippet": ""},
        {"title": "Analyst at Acme", "url": "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/Remote/Analyst_JR1",
         "snippet": ""},
        # A board's front page is a landing page, and another company's
        # posting is not this company's.
        {"title": "Jobs at Acme", "url": "https://boards.greenhouse.io/acme", "snippet": ""},
        {"title": "Engineer who loves Acme products", "url": "https://jobs.ashbyhq.com/other/0b1c2d3e-aaaa-bbbb-cccc-0123456789ab",
         "snippet": "Acme"},
    ]
    with _web(results):
        items, _artifact = jobs.search_jobs_web("Acme", WINDOW, {}, backend="keyless")
    assert [item["url"] for item in items] == [
        "https://boards.greenhouse.io/acme/jobs/4012345",
        "https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-0123456789ab",
        "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/Remote/Analyst_JR1",
    ]


def test_careers_page_search_skips_ad_and_redirect_links():
    with _web(DISNEY_WEB[1:3] + [PICHAI_WEB[3]] + DISNEY_WEB[:1]):
        assert jobs._search_for_careers_url("Disney", WINDOW, {}, backend="keyless") == (
            "https://www.disneycareers.com/en/search-jobs"
        )
    with _web(DISNEY_WEB[1:3]):
        assert jobs._search_for_careers_url("Disney", WINDOW, {}, backend="keyless") is None


def test_run_with_no_valid_jobs_prints_no_jobs_footer_line_or_source():
    report = _report("Disney", None)
    report.artifacts = {}
    footer = "\n".join(render._render_emoji_footer(report, None))
    assert "💼" not in footer and "Jobs" not in footer


def test_topic_and_host_companies_with_no_board_get_one_stderr_line(capsys):
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=_not_found), \
            _web(PICHAI_WEB):
        subquery = schema.SubQuery(
            label="primary", search_query="Sundar Pichai", ranking_query="Sundar Pichai", sources=["jobs"],
        )
        items, artifact = pipeline._retrieve_stream_impl(
            topic="Sundar Pichai", subquery=subquery, source="jobs",
            config={"_company": {"host_companies": ["Alphabet"]}},
            depth="default", date_range=WINDOW, runtime=None, mock=False,
            web_backend="keyless", raw_topic="Sundar Pichai",
        )
    assert items == []
    assert "_source_outcome" not in artifact
    err = capsys.readouterr().err
    assert err.count("[Jobs]") == 1
    assert "[Jobs] no supported job board found for Sundar Pichai (also tried: Alphabet)" in err


# --- Review findings on the first version of this change ---


def test_web_fallback_for_a_company_with_no_board_stays_inside_its_discovery_share():
    # A person topic with host companies: the topic's own search may use a
    # share of the budget, and the web fallback is part of that search. With
    # the whole budget it could leave the later companies no time.
    clock = Clock()
    budget = jobs.LaneBudget(45.0, clock=clock)
    seen = {}

    def web(company, *_args, **_kwargs):
        seen["remaining"] = budget.remaining()
        return [], {}

    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=_not_found), \
            patch.object(jobs, "search_jobs_web", side_effect=web):
        jobs.search_jobs(
            "Elon Musk", WINDOW, {}, web_backend="keyless", budget=budget, discovery_seconds=15.0,
        )
    assert seen["remaining"] <= 15.0
    # The share ends with the call; the next company starts from the budget.
    assert budget.remaining() == 45.0


def test_found_board_is_read_with_the_whole_budget_not_the_discovery_share():
    clock = Clock()
    budget = jobs.LaneBudget(45.0, clock=clock)
    seen = {}

    def board(token, *, deadline):
        seen["remaining"] = budget.remaining()
        return [], {}

    with patch("lib.jobs.http.get_text", return_value=CAREERS), \
            patch.object(jobs, "search_workday_board", side_effect=board):
        jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="none", site="https://www.acme.com",
            budget=budget, discovery_seconds=15.0,
        )
    assert seen["remaining"] == 45.0


def test_top_ranked_role_at_the_end_of_the_rarity_picks_is_not_trimmed_away():
    rows = _waymo_board()
    digest = jobs_digest.build_digest(rows, jobs_digest.select_roles(rows))
    picked = [entry["url"] for entry in digest["roles"]]
    # The run's best role is the rarity pick in the last place; one
    # lower-ranked role is added in front.
    out = jobs_digest.add_engine_roles(digest, rows, [picked[39], rows[50]["url"]])
    urls = [entry["url"] for entry in out["roles"]]
    assert len(urls) == 40
    assert picked[39] in urls and rows[50]["url"] in urls
    assert picked[38] not in urls  # the rarest unranked role gave up its place
    assert next(entry for entry in out["roles"] if entry["url"] == picked[39])["engine"] is True
    assert out["engine_roles"] == 2


def test_ranked_roles_beyond_the_reserved_places_are_not_reserved():
    rows = _waymo_board()
    digest = jobs_digest.build_digest(rows, jobs_digest.select_roles(rows))
    picked = [entry["url"] for entry in digest["roles"]]
    # Every pick is ranked, then a role off the list: the ten reserved places
    # are the ten best ranked, all already listed, so nothing is added.
    out = jobs_digest.add_engine_roles(digest, rows, [*picked, rows[50]["url"]])
    assert [entry["url"] for entry in out["roles"]] == picked
    assert out["engine_roles"] == jobs_digest.ENGINE_ROLE_SLOTS


def test_hard_deadline_of_a_request_is_its_own_time_limit_not_the_lanes_remaining_time():
    import time as real_time
    seen = []

    def page(url, **kwargs):
        seen.append((kwargs["timeout"], kwargs["deadline_monotonic"] - real_time.monotonic()))
        return None

    def api(url, *_args, **kwargs):
        seen.append((kwargs["timeout"] * kwargs["retries"] + 5, kwargs["deadline_monotonic"] - real_time.monotonic()))
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    with patch("lib.jobs.http.get_text", side_effect=page), patch("lib.jobs.http.get", side_effect=api):
        jobs.search_jobs("Kanye West", WINDOW, {}, web_backend="none")
    assert len(seen) == 6 + 6
    for limit, hard in seen:
        # A host that stalls in DNS or trickles bytes is cut off here.
        assert hard <= limit


def test_hard_deadline_never_passes_the_lanes_remaining_time():
    import time as real_time
    clock = Clock()
    budget = jobs.LaneBudget(45.0, clock=clock)
    clock.advance(43.0)
    token = jobs._BUDGET.set(budget)
    try:
        bounds = jobs._bounds(jobs.PAGE_TIMEOUT_SECONDS)
    finally:
        jobs._BUDGET.reset(token)
    assert bounds["deadline_monotonic"] - real_time.monotonic() <= 2.0


def test_listing_page_of_a_paged_board_has_its_own_hard_deadline():
    import time as real_time
    seen = []

    def post(url, json_data, headers=None, **kwargs):
        seen.append(kwargs["deadline_monotonic"] - real_time.monotonic())
        return {"total": 1, "jobPostings": []}

    with patch("lib.jobs.http.post", side_effect=post):
        jobs.search_workday_board("acme.wd5/AcmeCareers")
    assert seen and seen[0] <= jobs.API_TIMEOUT_SECONDS * 2 + 5


def _stalled_greenhouse_working_ashby(url, *_args, **_kwargs):
    if "greenhouse" in url:
        raise http.DeadlineExceeded()  # this request reached its own limit
    if url == "https://api.ashbyhq.com/posting-api/job-board/acme":
        return {"jobs": [{"id": "1", "title": "Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/1"}]}
    raise http.HTTPError("HTTP 404: Not Found", status_code=404)


def test_probe_that_reaches_its_own_time_limit_does_not_end_the_probing():
    # Greenhouse stalls for its ~22 s; half the lane's budget is left and the
    # company's board is on Ashby.
    budget = jobs.LaneBudget(45.0)
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=_stalled_greenhouse_working_ashby):
        items, artifact = jobs.search_jobs("Acme", WINDOW, {}, web_backend="none", budget=budget)
    assert [item["title"] for item in items] == ["Engineer"]
    assert artifact["tier"] == "ats" and artifact["provider"] == "ashby"
    assert budget.stopped_at == ""
    assert "_source_out_of_time" not in artifact


def test_probe_that_reached_its_own_limit_with_no_board_elsewhere_is_named_and_can_be_retried(capsys):
    asked = []

    def api(url, *_args, **_kwargs):
        asked.append(url.split("/")[2])
        if "greenhouse" in url:
            raise http.DeadlineExceeded()
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    budget = jobs.LaneBudget(45.0)
    with patch("lib.jobs.http.get_text", return_value=None), patch("lib.jobs.http.get", side_effect=api):
        items, artifact = jobs.search_jobs("Acme", WINDOW, {}, web_backend="none", budget=budget)
    assert asked == ["boards-api.greenhouse.io", "api.ashbyhq.com", "api.lever.co"]
    assert items == []
    outcome = artifact["_source_outcome"]
    assert outcome["state"] == health.TIMEOUT
    assert "the greenhouse API did not answer for the slug acme" in outcome["detail"]
    assert "budget" not in outcome["detail"]
    # The lane had time left: the thin-results retry may ask again.
    assert "_source_out_of_time" not in artifact
    assert budget.stopped_at == ""
    assert outcome["detail"] in capsys.readouterr().err


def test_probe_cut_off_by_the_lanes_own_deadline_still_ends_the_probing():
    clock = Clock()
    budget = jobs.LaneBudget(45.0, clock=clock)
    asked = []

    def api(url, *_args, **_kwargs):
        asked.append(url)
        clock.advance(50.0)  # the lane's budget ran out during this request
        raise http.DeadlineExceeded()

    with patch("lib.jobs.http.get_text", return_value=None), patch("lib.jobs.http.get", side_effect=api):
        items, artifact = jobs.search_jobs("Acme", WINDOW, {}, web_backend="none", budget=budget)
    assert len(asked) == 1
    assert items == []
    assert "45s budget" in artifact["_source_outcome"]["detail"]
    assert artifact["_source_out_of_time"] is True


def test_discovery_share_running_out_is_not_the_lane_running_out():
    # The topic used its share; the lane still has time for the next company
    # and for a retry, so the source is not marked out of time.
    clock = Clock()
    budget = jobs.LaneBudget(45.0, clock=clock)

    def page(url, **_kwargs):
        clock.advance(6.0)
        return None

    with patch("lib.jobs.http.get_text", side_effect=page), \
            patch("lib.jobs.http.get", side_effect=AssertionError("the share is spent")):
        items, artifact = jobs.search_jobs(
            "Kanye West", WINDOW, {}, web_backend="none", budget=budget, discovery_seconds=10.0,
        )
    assert items == []
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT
    assert "_source_out_of_time" not in artifact
    assert not budget.spent()

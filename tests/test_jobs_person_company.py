"""Jobs for a person who leads a company, and for a topic with no board.

Evidence, two live runs on 2026-11-10: "Jensen Huang" with
``--ticker NVDA:Nvidia`` printed "Jobs: 0 results", and "Kanye West" printed
"Jobs unreachable: URL Error: [Errno 8] nodename nor servname provided".
"""

import socket
import urllib.error
from unittest.mock import MagicMock, patch

import last30days as cli
from lib import http, jobs, pipeline, schema, yahoo_finance

WINDOW = ("2026-10-12", "2026-11-11")

# nvidia.com/careers on 2026-11-10: a landing page that names no hiring
# system and links to the job search on a host of its own.
LANDING = """
<a href="https://www.nvidia.com/en-eu/about-nvidia/careers/">Europe</a>
<a href="https://www.nvidia.com/en-us/about-nvidia/careers/life-at-nvidia/">Life at NVIDIA</a>
<a href="https://jobs.nvidia.com/careers">Open Positions</a>
<h2>Join our team</h2>
"""
# jobs.nvidia.com/careers names the Workday board in its sign-in link.
JOB_SEARCH = '<a href="https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/login">Sign in</a>'


def _workday_rows(count=6):
    return [
        {
            "id": f"JR{index}", "title": f"Engineer {index}", "provider": "workday",
            "url": f"https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/JR{index}",
            "description": "", "date": None, "department": f"Team {index}", "location": "Santa Clara",
        }
        for index in range(count)
    ]


def _pages(pages):
    fetched = []

    def get_text(url, **_kwargs):
        fetched.append(url)
        return pages.get(url)

    return fetched, get_text


def _board(token, *, deadline):
    assert token == "nvidia.wd5/NVIDIAExternalCareerSite"
    return _workday_rows(), {"board_total": 6, "board_capped": False, "board_cut_short": False}


# --- Defect 3: the board one link past the official careers page ---


def test_board_named_on_the_jobs_page_the_official_careers_page_links_to_is_found_and_owned():
    fetched, get_text = _pages({
        "https://www.nvidia.com/careers": LANDING,
        "https://jobs.nvidia.com/careers": JOB_SEARCH,
    })
    with patch("lib.jobs.http.get_text", side_effect=get_text), \
            patch.object(jobs, "search_workday_board", side_effect=_board), \
            patch.object(jobs, "fetch_workday_details", return_value=(0, 0)), \
            patch("lib.jobs.http.get", side_effect=AssertionError("no slug is guessed")):
        items, artifact = jobs.search_jobs(
            "Nvidia", WINDOW, {}, web_backend="none", site="https://www.nvidia.com/",
        )
    assert fetched == ["https://www.nvidia.com/careers", "https://jobs.nvidia.com/careers"]
    assert len(items) == 6
    assert artifact["tier"] == "ats"
    assert artifact["provider"] == "workday"
    assert artifact["found_via"] == "site"
    assert artifact["owned"] is True
    assert artifact["digest"]["roles"]


def test_jobs_page_on_another_companys_domain_is_not_followed():
    landing = '<a href="https://jobs.other.example/careers">Open Positions</a><h2>Join our team</h2>'
    fetched, get_text = _pages({"https://www.nvidia.com/careers": landing})
    with patch("lib.jobs.http.get_text", side_effect=get_text), \
            patch("lib.jobs.http.get", side_effect=http.HTTPError("missing", status_code=404)):
        items, artifact = jobs.search_jobs(
            "Nvidia", WINDOW, {}, web_backend="none", site="https://www.nvidia.com/",
        )
    assert "https://jobs.other.example/careers" not in fetched
    assert items == []
    assert artifact["tier"] == "web"


def test_board_reached_from_a_guessed_domain_stays_unowned_and_has_no_digest():
    # No official site is known: nvidia.com is a guess from the name, and a
    # Workday board states no company name, so nothing says whose board it is.
    fetched, get_text = _pages({
        "https://nvidia.com/careers": LANDING.replace("www.nvidia.com", "nvidia.com"),
        "https://jobs.nvidia.com/careers": JOB_SEARCH,
    })
    with patch("lib.jobs.http.get_text", side_effect=get_text), \
            patch.object(jobs, "search_workday_board", side_effect=_board):
        items, artifact = jobs.search_jobs("Nvidia", WINDOW, {}, web_backend="none")
    assert len(items) == 6
    assert artifact["found_via"] == "guess"
    assert artifact["owned"] is False
    assert "digest" not in artifact


# --- Defect 3: the host's company when Wikidata names none ---


def _run_jobs_lane(company, search):
    subquery = schema.SubQuery(
        label="primary", search_query="Jensen Huang", ranking_query="Jensen Huang", sources=["jobs"],
    )
    with patch.object(jobs, "search_jobs", side_effect=search) as mock:
        result = pipeline._retrieve_stream_impl(
            topic="Jensen Huang", subquery=subquery, source="jobs", config={"_company": company},
            depth="default", date_range=WINDOW, runtime=None, mock=False,
            web_backend="auto", raw_topic="Jensen Huang",
        )
    return result, [call.args[0] for call in mock.call_args_list], mock


def test_host_ticker_company_is_searched_when_wikidata_named_none_and_the_topic_has_no_board():
    board = {"tier": "ats", "provider": "workday", "open_roles": 6}

    def search(name, *_args, **_kwargs):
        return (_workday_rows(), board) if name == "Nvidia" else ([], {"tier": "web"})

    (items, artifact), names, mock = _run_jobs_lane({"host_company": "Nvidia"}, search)
    assert names == ["Jensen Huang", "Nvidia"]
    assert len(items) == 6 and artifact is board
    # No official site comes with the host's name, and no web search is spent.
    assert mock.call_args.kwargs.get("site", "") == ""
    assert mock.call_args.kwargs["web_backend"] == "none"


def test_topic_with_its_own_board_never_reaches_the_host_ticker_company():
    # "Waymo" with --ticker GOOGL:Alphabet: Waymo's board, not its parent's.
    own = {"tier": "ats", "provider": "greenhouse", "open_roles": 3}
    (_items, artifact), names, _mock = _run_jobs_lane(
        {"host_company": "Alphabet"}, lambda name, *_a, **_k: ([{"id": "1"}], own),
    )
    assert names == ["Jensen Huang"]
    assert artifact is own


def test_host_ticker_company_with_no_board_leaves_the_topics_result():
    topic_result = ([], {"tier": "web", "resultCount": 0})
    result, names, _mock = _run_jobs_lane(
        {"host_company": "Nvidia"},
        lambda name, *_a, **_k: topic_result if name == "Jensen Huang" else ([], {"tier": "web"}),
    )
    assert names == ["Jensen Huang", "Nvidia"]
    assert result == topic_result


def test_wikidata_company_is_searched_alone():
    _result, names, _mock = _run_jobs_lane(
        {"company": "Nvidia", "company_site": "https://www.nvidia.com/", "host_company": "Other"},
        lambda name, *_a, **_k: ([], {"tier": "web"}),
    )
    assert names == ["Nvidia"]


def test_jobs_company_takes_the_one_host_ticker_name_only_when_wikidata_named_none():
    one = yahoo_finance.parse_host_tickers("NVDA:Nvidia")
    assert cli._company_for_jobs({}, one, "Jensen Huang") == {"host_company": "Nvidia"}
    resolved = {"company": "Nvidia", "company_site": "https://www.nvidia.com/"}
    assert cli._company_for_jobs(resolved, one, "Jensen Huang") == resolved
    # The topic is the company: its own name is already what is searched.
    assert cli._company_for_jobs({}, one, "Nvidia") == {}
    # Two companies, or an unusable pair, name no one company.
    two = yahoo_finance.parse_host_tickers("TSLA:Tesla,SPCX:SpaceX")
    assert cli._company_for_jobs({}, two, "Elon Musk") == {}
    assert cli._company_for_jobs({}, yahoo_finance.parse_host_tickers("NVDA"), "Jensen Huang") == {}
    assert cli._company_for_jobs({}, None, "Jensen Huang") == {}


# --- Defect 3: the digest of the led company's board survives ranking ---


def _job_candidate(index):
    item = schema.SourceItem(
        item_id=f"JR{index}", source="jobs", title=f"Engineer {index}", body="",
        url=f"https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/JR{index}",
    )
    return schema.Candidate(
        candidate_id=f"c{index}", item_id=item.item_id, source="jobs", title=item.title,
        url=item.url, snippet="", subquery_labels=["primary"], native_ranks={},
        local_relevance=0.1, freshness=50, engagement=None, source_quality=0.5,
        rrf_score=0.1, sources=["jobs"], source_items=[item],
    )


def test_postings_dropped_for_not_naming_a_person_are_not_rejected_from_their_companys_digest():
    # The live run after the board was found: ranking dropped all 40 pooled
    # Nvidia postings for not naming "Jensen Huang", which emptied the digest.
    candidates = [_job_candidate(index) for index in range(3)]
    board = {"company": "Nvidia", "owned": True, "tier": "ats"}
    assert pipeline._rejected_job_keys(candidates, [], board, "Jensen Huang") == []


def test_postings_ranking_dropped_on_the_topic_companys_own_board_stay_rejected():
    candidates = [_job_candidate(index) for index in range(3)]
    board = {"company": "Nvidia", "owned": True, "tier": "ats"}
    rejected = pipeline._rejected_job_keys(candidates, candidates[:1], board, "Nvidia")
    assert len(rejected) == 2
    # A board that is not known to be the company's keeps its rejections too.
    unowned = {"company": "Nvidia", "owned": False, "tier": "ats"}
    assert len(pipeline._rejected_job_keys(candidates, [], unowned, "Jensen Huang")) == 3
    assert len(pipeline._rejected_job_keys(candidates, [], None, "Jensen Huang")) == 3


# --- Defect 4: a careers host that does not exist is "no board", not an outage ---


def _dns_failure(*_args, **_kwargs):
    raise urllib.error.URLError(
        socket.gaierror(8, "nodename nor servname provided, or not known")
    )


def _refused(*_args, **_kwargs):
    raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))


def _open_by_host(careers_open):
    """Guessed careers hosts fail with ``careers_open``; every ATS API says 404."""
    def opener(req, timeout=None, **_kwargs):
        host = req.full_url.split("/")[2]
        if host in {"kanyewest.com", "kanyewest.ai", "kanyewest.io"}:
            return careers_open()
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    return opener


def _search_capturing(opener, company="Kanye West"):
    with patch.object(http, "_open_request", side_effect=opener), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        result = jobs.search_jobs(company, WINDOW, {}, web_backend="none")
    return result, failures


def test_guessed_careers_host_that_does_not_resolve_is_no_board_found():
    (items, artifact), failures = _search_capturing(_open_by_host(_dns_failure))
    assert items == []
    assert artifact["tier"] == "web"
    assert failures == []


def test_guessed_careers_host_that_refuses_the_connection_is_no_board_found():
    (items, _artifact), failures = _search_capturing(_open_by_host(_refused))
    assert items == []
    assert failures == []


def test_host_that_does_not_resolve_is_asked_for_one_page_only():
    asked = []

    def opener(req, timeout=None, **_kwargs):
        asked.append(req.full_url)
        return _open_by_host(_dns_failure)(req, timeout)

    _search_capturing(opener)
    assert not [url for url in asked if url.endswith("/jobs") and "kanyewest" in url]


def test_careers_host_that_times_out_is_still_reported():
    def timed_out():
        raise urllib.error.URLError(TimeoutError("timed out"))

    _result, failures = _search_capturing(_open_by_host(timed_out))
    assert failures and all(f.outcome_state == http.health.TIMEOUT for f in failures)


def test_outage_of_a_known_hiring_system_is_still_an_error():
    def opener(req, timeout=None, **_kwargs):
        host = req.full_url.split("/")[2]
        if host == "boards-api.greenhouse.io":
            return _dns_failure()
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    with patch.object(http, "_open_request", side_effect=opener), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        try:
            jobs.search_jobs("Kanye West", WINDOW, {}, web_backend="none")
            raised = None
        except http.HTTPError as exc:
            raised = exc
    assert raised is not None and raised.outcome_state == http.health.UNREACHABLE
    assert [f for f in failures if f.no_host]


def test_dns_failure_outside_a_guessed_host_is_recorded_as_before():
    with patch.object(http, "_open_request", side_effect=_dns_failure), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        assert http.get_text("https://api.example.com/x", retries=1) is None
    assert len(failures) == 1 and failures[0].no_host


# --- A known official site that does not answer is an outage, not "no board" ---

OFFICIAL = "https://www.acmecorp.com/"


def _search_official(opener):
    with patch.object(http, "_open_request", side_effect=opener), \
            patch.object(http.time, "sleep"), \
            http.capture_failures() as failures:
        result = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="none", site=OFFICIAL,
        )
    return result, failures


def _official_host_fails(careers_open):
    def opener(req, timeout=None, **_kwargs):
        if req.full_url.split("/")[2] == "www.acmecorp.com":
            return careers_open()
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    return opener


def test_official_site_that_does_not_resolve_is_recorded_as_a_failure():
    (items, _artifact), failures = _search_official(_official_host_fails(_dns_failure))
    assert items == []
    assert failures and all(
        f.no_host and f.outcome_state == http.health.UNREACHABLE for f in failures
    )


def test_official_site_that_refuses_the_connection_is_recorded_as_a_failure():
    _result, failures = _search_official(_official_host_fails(_refused))
    assert failures and all(f.no_host for f in failures)


def test_jobs_page_linked_from_the_official_site_that_does_not_resolve_is_recorded():
    landing = '<a href="https://jobs.acmecorp.com/careers">Open Positions</a><h2>Join our team</h2>'

    def opener(req, timeout=None, **_kwargs):
        host = req.full_url.split("/")[2]
        if host == "www.acmecorp.com":
            response = MagicMock()
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            response.read.return_value = landing.encode("utf-8")
            response.status = 200
            response.headers = {}
            return response
        if host == "jobs.acmecorp.com":
            return _dns_failure()
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    _result, failures = _search_official(opener)
    assert [f for f in failures if f.no_host]

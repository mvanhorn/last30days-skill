"""The jobs lane runs without the host remembering to name it.

Evidence, four live runs on 2026-11-10 (v3.35.0):

- "Lyft", "McDonalds" and "Palmer Luckey": the host's ``--plan`` never named
  ``jobs``, so the lane never ran. Lyft's Greenhouse board answers in 5 s.
- "Palmer Luckey": no company was resolved for him (Anduril is private, so
  there is no ``--ticker``).
- "Anduril" with ``--official-site=anduril.com``: the careers page names no
  hiring system, the slug ``anduril`` is a 404, and the board is
  ``andurilindustries`` ("Anduril Industries", the name the site's own
  structured data states).
- "Traeger": the careers page links ``/careers/board``, which links the
  Greenhouse board ``traegergrills``.
"""

from unittest.mock import patch

import pytest

import last30days as cli
from lib import http, jobs, pipeline, schema, yahoo_finance

WINDOW = ("2026-10-12", "2026-11-11")


def plan_with(*source_lists):
    return {
        "intent": "opinion",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "debate",
        "subqueries": [
            {"label": "primary" if not i else f"part-{i}", "search_query": "test topic",
             "ranking_query": "test topic", "sources": sources, "weight": 1.0}
            for i, sources in enumerate(source_lists)
        ],
    }


def _run_with_plan(topic, plan, *, config=None, depth="default", **kwargs):
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic=topic, config=dict(config or {}), mock=True, web_backend="none",
            depth=depth, external_plan=plan, **kwargs,
        )
    return report, {call.kwargs["source"] for call in retrieve.call_args_list}


def _planned(report):
    """Subquery sources minus the installed lanes a mock run backfills."""
    return [
        [s for s in sq.sources if s not in pipeline.HOST_PLAN_BACKFILL_SOURCES]
        for sq in report.query_plan.subqueries
    ]


# --- Fix 1: the engine adds jobs to a host plan about a company ---


def test_company_signal_names_each_thing_that_makes_a_run_about_a_company():
    usable = yahoo_finance.parse_host_tickers("LYFT:Lyft")
    unusable = yahoo_finance.parse_host_tickers("LYFT")
    assert "--official-site" in cli._company_signal({}, None, "lyft.com", [])
    assert "--ticker" in cli._company_signal({}, usable, "", [])
    assert "--company" in cli._company_signal({}, None, "", ["Anduril"])
    assert "Nvidia" in cli._company_signal({"company": "Nvidia"}, None, "", [])
    # A musician with no company, a concept, a ticker that could not be read.
    assert cli._company_signal({}, None, "", []) == ""
    assert cli._company_signal({}, unusable, "", []) == ""


@pytest.mark.parametrize("signal", ["--official-site", "--ticker", "--company", "the company Nvidia"])
def test_host_plan_about_a_company_gains_jobs_on_its_primary_subquery(signal, capsys):
    report, fetched = _run_with_plan(
        "Lyft", plan_with(["hackernews"], ["reddit"]), config={"_company_signal": signal},
    )
    assert _planned(report) == [["hackernews", "jobs"], ["reddit"]]
    assert "jobs" in fetched
    err = capsys.readouterr().err
    assert "[Planner] Added jobs to primary" in err
    assert signal in err
    assert "EXCLUDE_SOURCES=jobs" in err


def test_host_plan_with_no_company_signal_does_not_gain_jobs(capsys):
    report, fetched = _run_with_plan("Kanye West", plan_with(["hackernews"], ["reddit"]))
    assert _planned(report) == [["hackernews"], ["reddit"]]
    assert "jobs" not in fetched
    assert "Added jobs" not in capsys.readouterr().err


def test_excluded_jobs_is_not_added_to_a_host_plan_about_a_company(capsys):
    report, fetched = _run_with_plan(
        "Lyft", plan_with(["hackernews"]),
        config={"_company_signal": "--official-site", "EXCLUDE_SOURCES": "jobs"},
    )
    assert _planned(report)[0] == ["hackernews"]
    assert "jobs" not in fetched
    assert "Added jobs" not in capsys.readouterr().err


def test_search_list_without_jobs_keeps_jobs_out_of_a_host_plan_about_a_company():
    report, fetched = _run_with_plan(
        "Lyft", plan_with(["hackernews"]), config={"_company_signal": "--official-site"},
        requested_sources=["hackernews", "reddit"],
    )
    assert _planned(report)[0] == ["hackernews"]
    assert "jobs" not in fetched


def test_jobs_the_host_named_in_a_later_subquery_is_not_added_again(capsys):
    report, _fetched = _run_with_plan(
        "Lyft", plan_with(["hackernews"], ["reddit", "jobs"]),
        config={"_company_signal": "--official-site"},
    )
    assert _planned(report) == [["hackernews"], ["reddit", "jobs"]]
    assert "Added jobs" not in capsys.readouterr().err


def test_quick_host_plan_keeps_its_two_source_budget():
    report, fetched = _run_with_plan(
        "Lyft", plan_with(["hackernews"]), config={"_company_signal": "--official-site"},
        depth="quick",
    )
    assert _planned(report)[0] == ["hackernews"]
    assert "jobs" not in fetched


# --- Fix 2: --company ---


def test_company_flag_is_read_as_at_most_two_names(capsys):
    assert cli._host_company_names("Anduril") == ["Anduril"]
    assert cli._host_company_names(" Tesla ,SpaceX, ") == ["Tesla", "SpaceX"]
    assert cli._host_company_names("") == []
    assert cli._host_company_names("Tesla,SpaceX,Neuralink") == ["Tesla", "SpaceX"]
    assert "Neuralink" in capsys.readouterr().err


def test_company_flag_feeds_the_jobs_lane_for_a_person_with_no_ticker():
    assert cli._company_for_jobs({}, None, "Palmer Luckey", host_names=["Anduril"]) == {
        "host_companies": ["Anduril"],
    }
    # The topic's own name is never one of them, and Wikidata's company stays.
    assert cli._company_for_jobs({}, None, "Anduril", host_names=["Anduril"]) == {}
    resolved = {"company": "Nvidia"}
    assert cli._company_for_jobs(resolved, None, "Jensen Huang", host_names=["Other"]) == resolved


def test_company_flag_and_ticker_companies_are_one_list_without_repeats():
    pairs = yahoo_finance.parse_host_tickers("TSLA:Tesla Inc.,SPCX:SpaceX")
    assert cli._company_for_jobs({}, pairs, "Elon Musk", host_names=["Tesla", "xAI"]) == {
        "host_companies": ["Tesla", "xAI", "SpaceX"],
    }


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


def test_person_with_a_host_company_gets_that_companys_board():
    board = {"tier": "ats", "provider": "greenhouse", "company": "Anduril", "open_roles": 2}

    def search(name, *_args, **_kwargs):
        return ([{"id": "1"}, {"id": "2"}], dict(board)) if name == "Anduril" else ([], {"tier": "web"})

    (items, artifact), names, _mock = _run_lane("Palmer Luckey", {"host_companies": ["Anduril"]}, search)
    assert names == ["Palmer Luckey", "Anduril"]
    assert len(items) == 2
    assert artifact["board_of"] == "Anduril"


def test_two_host_companies_are_tried_in_order_and_the_first_board_wins():
    def search(name, *_args, **_kwargs):
        if name == "SpaceX":
            return [{"id": "1"}], {"tier": "ats", "provider": "greenhouse", "open_roles": 1}
        return [], {"tier": "web"}

    (_items, artifact), names, _mock = _run_lane(
        "Elon Musk", {"host_companies": ["Tesla", "SpaceX", "xAI"]}, search,
    )
    assert names == ["Elon Musk", "Tesla", "SpaceX"]
    assert artifact["board_of"] == "SpaceX"


# --- Fix 3: --jobs-board ---


@pytest.mark.parametrize("url, expected", [
    ("https://job-boards.greenhouse.io/andurilindustries", ("greenhouse", "andurilindustries")),
    ("https://boards.greenhouse.io/lyft/jobs/8311413?gh_jid=8311413", ("greenhouse", "lyft")),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
    ("job-boards.greenhouse.io/traegergrills", ("greenhouse", "traegergrills")),
    ("https://jobs.lever.co/acme", ("lever", "acme")),
    ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
    ("https://apply.workable.com/acme/", ("workable", "acme")),
    ("https://jobs.smartrecruiters.com/AcmeCorporation", ("smartrecruiters", "AcmeCorporation")),
    ("https://careers.smartrecruiters.com/AcmeCorporation", ("smartrecruiters", "AcmeCorporation")),
    (
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite",
        ("workday", "nvidia.wd5/NVIDIAExternalCareerSite"),
    ),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/JR1", ("workday", "nvidia.wd5/NVIDIAExternalCareerSite")),
])
def test_board_url_gives_the_provider_and_slug_of_each_supported_system(url, expected):
    assert jobs.parse_board_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://jobs.mcdonalds.com/",
    "https://career41.sapsf.com/career?company=mcdonaldsc",
    "https://www.anduril.com/open-roles",
    # A supported system's name in the path or query of another host.
    "https://example.com/?next=jobs.lever.co/acme",
    "https://jobs.lever.co.example.com/acme",
    "https://nvidia.wd5.myworkdayjobs.com/",
    "not a url",
    "",
])
def test_url_that_is_not_a_supported_board_gives_nothing(url):
    assert jobs.parse_board_url(url) == (None, None)


def _greenhouse(slug, company_name, count=3):
    return {"jobs": [
        {
            "id": index, "title": f"Engineer {index}", "company_name": company_name,
            "absolute_url": f"https://boards.greenhouse.io/{slug}/jobs/{index}",
            "content": f"Build thing {index}.", "first_published": "2026-11-01T00:00:00Z",
        }
        for index in range(1, count + 1)
    ]}


def _greenhouse_api(boards):
    """``http.get`` for Greenhouse: the named boards answer, every other slug
    and every other provider is a 404. The URLs asked are recorded."""
    asked = []

    def get(url, *_args, **_kwargs):
        asked.append(url)
        for slug, payload in boards.items():
            if url == f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs":
                return payload
            if url == f"https://boards-api.greenhouse.io/v1/boards/{slug}":
                return {"name": payload["jobs"][0]["company_name"]}
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    return asked, get


ANDURIL_BOARD = "https://job-boards.greenhouse.io/andurilindustries"


def test_host_board_that_names_the_company_is_owned_and_gives_a_digest():
    asked, get = _greenhouse_api({"andurilindustries": _greenhouse("andurilindustries", "Anduril Industries")})
    with patch("lib.jobs.http.get_text", side_effect=AssertionError("no page is needed")), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_board_url(
            ANDURIL_BOARD, ["Palmer Luckey", "Anduril"], WINDOW, {},
        )
    # The board's own record says whose it is before its job list is read.
    assert asked == [
        "https://boards-api.greenhouse.io/v1/boards/andurilindustries",
        "https://boards-api.greenhouse.io/v1/boards/andurilindustries/jobs",
    ]
    assert len(items) == 3
    assert artifact["tier"] == "ats"
    assert artifact["found_via"] == "host"
    assert artifact["owned"] is True
    assert artifact["company"] == "Anduril"
    assert artifact["board_company"] == "Anduril Industries"
    assert artifact["open_roles"] == 3
    assert artifact["digest"]["roles"]


def test_host_board_that_names_another_company_is_counted_with_no_digest(capsys):
    _asked, get = _greenhouse_api({"andurilindustries": _greenhouse("andurilindustries", "Sgt. Pepper's Band")})
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_board_url(
            ANDURIL_BOARD, ["Palmer Luckey", "Anduril"], WINDOW, {},
        )
    assert len(items) == 3
    assert artifact["open_roles"] == 3
    assert artifact["owned"] is False
    assert "digest" not in artifact
    err = capsys.readouterr().err
    assert "[Jobs] --jobs-board" in err
    assert "Sgt. Pepper's Band" in err
    assert "no digest" in err


def test_host_board_whose_name_only_starts_like_the_company_is_not_owned():
    # "Metabase" is not "Meta": the company's name must be whole words.
    _asked, get = _greenhouse_api({"metabase": _greenhouse("metabase", "Metabase")})
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=get):
        _items, artifact = jobs.search_board_url(
            "https://boards.greenhouse.io/metabase", ["Meta"], WINDOW, {},
        )
    assert artifact["owned"] is False


WORKDAY_URL = "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers"


def _workday_board(token, *, deadline, **_kwargs):
    rows = [
        {
            "id": f"JR{index}", "title": f"Engineer {index}", "provider": "workday",
            "url": f"https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/JR{index}",
            "description": "", "date": None, "department": f"Team {index}", "location": "Austin",
        }
        for index in range(4)
    ]
    return rows, {"board_total": 4, "board_capped": False, "board_cut_short": False}


def test_host_board_with_no_name_of_its_own_is_owned_when_the_official_site_links_it():
    pages = {"https://www.zenith.example/careers": f'<h2>Open roles</h2><a href="{WORKDAY_URL}">Jobs</a>'}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch.object(jobs, "search_workday_board", side_effect=_workday_board), \
            patch.object(jobs, "fetch_workday_details", return_value=(0, 0)):
        items, artifact = jobs.search_board_url(
            WORKDAY_URL, ["Zenith"], WINDOW, {}, site="https://www.zenith.example",
        )
    assert len(items) == 4
    assert artifact["found_via"] == "site"
    assert artifact["owned"] is True
    assert artifact["digest"]["roles"]


def test_host_board_with_no_name_that_the_official_site_does_not_link_is_not_owned(capsys):
    pages = {"https://www.zenith.example/careers": "<h2>Open roles</h2>"}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch.object(jobs, "search_workday_board", side_effect=_workday_board):
        items, artifact = jobs.search_board_url(
            WORKDAY_URL, ["Zenith"], WINDOW, {}, site="https://www.zenith.example",
        )
    assert len(items) == 4
    assert artifact["owned"] is False
    assert "digest" not in artifact
    assert "does not link" in capsys.readouterr().err


def test_host_board_whose_slug_is_the_companys_name_is_owned():
    # Ashby, Lever, Workable and Workday state no company name: the board's
    # own slug is the name it gives itself.
    rows = [{"id": "1", "title": "Engineer", "provider": "lever", "url": "https://jobs.lever.co/acme/1",
             "description": "Build.", "date": None, "department": "Eng", "location": "Austin"}]
    with patch("lib.jobs.http.get_text", side_effect=AssertionError("no page is needed")), \
            patch.object(jobs, "_fetch_ats", return_value=rows):
        _items, artifact = jobs.search_board_url("https://jobs.lever.co/acme", ["Jane Doe", "Acme"], WINDOW, {})
    assert artifact["owned"] is True
    assert artifact["company"] == "Acme"


def test_unsupported_board_url_is_ignored_with_one_line_and_no_request(capsys):
    with patch("lib.jobs.http.get_text", side_effect=AssertionError("nothing is fetched")), \
            patch("lib.jobs.http.get", side_effect=AssertionError("nothing is fetched")):
        assert jobs.search_board_url("https://jobs.mcdonalds.com/", ["McDonalds"], WINDOW, {}) == ([], {})
    err = capsys.readouterr().err
    assert err.count("[Jobs]") == 1
    assert "--jobs-board ignored" in err
    assert "jobs.mcdonalds.com" in err


def test_host_board_that_cannot_be_read_gives_nothing_and_says_so(capsys):
    _asked, get = _greenhouse_api({})
    with patch("lib.jobs.http.get", side_effect=get):
        assert jobs.search_board_url(ANDURIL_BOARD, ["Anduril"], WINDOW, {}) == ([], {})
    assert "--jobs-board" in capsys.readouterr().err


def test_owned_host_board_ends_the_lane_before_any_other_search():
    board = {"tier": "ats", "provider": "greenhouse", "company": "Anduril", "owned": True, "open_roles": 3}
    with patch.object(jobs, "search_board_url", return_value=([{"id": "1"}], dict(board))) as host:
        (items, artifact), names, _mock = _run_lane(
            "Palmer Luckey", {"host_companies": ["Anduril"]},
            lambda *_a, **_k: pytest.fail("the lane already has its board"),
            config={"_jobs_board": ANDURIL_BOARD},
        )
    assert host.call_args.args[:2] == (ANDURIL_BOARD, ["Palmer Luckey", "Anduril"])
    assert names == []
    assert len(items) == 1
    assert artifact["board_of"] == "Anduril"


def test_unowned_host_board_is_kept_only_when_the_lane_finds_no_board_itself():
    unowned = {"tier": "ats", "provider": "greenhouse", "company": "Palmer Luckey", "owned": False, "open_roles": 9}
    found = {"tier": "ats", "provider": "ashby", "company": "Anduril", "owned": True, "open_roles": 2}

    def nothing(*_args, **_kwargs):
        return [], {"tier": "web"}

    def anduril(name, *_args, **_kwargs):
        return ([{"id": "a"}], dict(found)) if name == "Anduril" else ([], {"tier": "web"})

    with patch.object(jobs, "search_board_url", return_value=([{"id": "1"}], dict(unowned))):
        (_items, artifact), names, _mock = _run_lane(
            "Palmer Luckey", {"host_companies": ["Anduril"]}, nothing,
            config={"_jobs_board": ANDURIL_BOARD},
        )
        assert names == ["Palmer Luckey", "Anduril"]
        assert artifact["open_roles"] == 9 and artifact["owned"] is False

        (_items, artifact), _names, _mock = _run_lane(
            "Palmer Luckey", {"host_companies": ["Anduril"]}, anduril,
            config={"_jobs_board": ANDURIL_BOARD},
        )
        assert artifact["provider"] == "ashby"
        assert artifact["board_of"] == "Anduril"


# --- Fix 4: boards the lane finds without the host ---

# www.anduril.com/careers on 2026-11-10: no hiring system is named (the job
# list is read from a search index by script). The page's structured data
# states the organization's name, and a social link carries the same token.
ANDURIL_CAREERS = """
<title>Careers | Anduril</title>
<meta property="og:site_name" content="Anduril">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","url":"https://www.anduril.com/careers","name":"Careers",
  "isPartOf":{"@type":"WebSite","name":"Transforming US & allied military capabilities",
   "publisher":{"@type":"Organization","name":"Anduril Industries","url":"https://www.anduril.com"}}}]}
</script>
<a href="https://www.facebook.com/andurilindustries/">Facebook</a>
<h2>Open Roles</h2>
"""


def test_names_a_careers_page_states_for_its_organization_are_read():
    assert jobs._stated_names(ANDURIL_CAREERS) == ["Anduril Industries", "Anduril"]
    assert jobs._stated_names("<h1>Careers</h1>") == []


def test_board_under_the_name_the_official_site_states_is_found_and_owned():
    asked, get = _greenhouse_api({"andurilindustries": _greenhouse("andurilindustries", "Anduril Industries")})
    pages = {"https://anduril.com/careers": ANDURIL_CAREERS}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_jobs(
            "Anduril", WINDOW, {}, web_backend="none", site="anduril.com",
        )
    # The company's own name is tried first; the site's name only after it.
    assert asked[0] == "https://boards-api.greenhouse.io/v1/boards/anduril/jobs"
    assert asked[-1] == "https://boards-api.greenhouse.io/v1/boards/andurilindustries/jobs"
    assert len(items) == 3
    assert artifact["tier"] == "ats"
    assert artifact["board_token"] == "andurilindustries"
    assert artifact["found_via"] == "site-name"
    assert artifact["owned"] is True
    assert artifact["digest"]["roles"]


def test_board_under_the_stated_name_must_state_that_name_itself():
    # The slug the site's name gives belongs to someone else.
    _asked, get = _greenhouse_api({"andurilindustries": _greenhouse("andurilindustries", "Anduril Bakery")})
    pages = {"https://anduril.com/careers": ANDURIL_CAREERS}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch("lib.jobs.http.get", side_effect=get):
        _items, artifact = jobs.search_jobs("Anduril", WINDOW, {}, web_backend="none", site="anduril.com")
    assert artifact.get("owned") is not True
    assert "digest" not in artifact


def test_stated_name_that_is_not_a_form_of_the_companys_is_not_probed():
    page = ANDURIL_CAREERS.replace("Anduril Industries", "Hosting Partners").replace(
        'content="Anduril"', 'content="Hosting Partners"',
    )
    asked, get = _greenhouse_api({"hostingpartners": _greenhouse("hostingpartners", "Hosting Partners")})
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: page if url.endswith("/careers") else None), \
            patch("lib.jobs.http.get", side_effect=get):
        items, _artifact = jobs.search_jobs("Anduril", WINDOW, {}, web_backend="none", site="anduril.com")
    assert items == []
    assert not any("hostingpartners" in url for url in asked)


# www.traeger.com/careers on 2026-11-10 links its job list at /careers/board,
# and only that page links the Greenhouse postings.
TRAEGER_CAREERS = '<h2>Join our team</h2><a href="/careers">Careers</a><a href="/careers/board">See open positions</a>'
TRAEGER_BOARD = (
    '<a href="https://job-boards.greenhouse.io/traegergrills/jobs/8311413">Finance Manager</a>'
)


def test_board_page_under_the_careers_page_is_followed_to_the_board():
    asked, get = _greenhouse_api({"traegergrills": _greenhouse("traegergrills", "Traeger Grills")})
    pages = {
        "https://traeger.com/careers": TRAEGER_CAREERS,
        "https://traeger.com/careers/board": TRAEGER_BOARD,
    }
    fetched = []

    def get_text(url, **_kwargs):
        fetched.append(url)
        return pages.get(url)

    with patch("lib.jobs.http.get_text", side_effect=get_text), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_jobs("Traeger", WINDOW, {}, web_backend="none", site="traeger.com")
    assert fetched == ["https://traeger.com/careers", "https://traeger.com/careers/board"]
    assert asked == ["https://boards-api.greenhouse.io/v1/boards/traegergrills/jobs"]
    assert len(items) == 3
    assert artifact["found_via"] == "site"
    assert artifact["owned"] is True


# --- Fix 5: an official site that cannot be fetched ---


def test_official_site_that_refuses_the_request_still_gets_its_slug_probed():
    # mcdonalds.com answers 403 to the engine: no page, so no careers link.
    asked, get = _greenhouse_api({"lyft": _greenhouse("lyft", "Lyft")})
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_jobs("Lyft", WINDOW, {}, web_backend="none", site="lyft.com")
    assert asked == ["https://boards-api.greenhouse.io/v1/boards/lyft/jobs"]
    assert len(items) == 3
    assert artifact["owned"] is True
    assert artifact["digest"]["roles"]


def test_official_site_that_refuses_the_request_does_not_stop_a_host_board():
    _asked, get = _greenhouse_api({"lyft": _greenhouse("lyft", "Lyft")})
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_board_url(
            "https://boards.greenhouse.io/lyft", ["Lyft"], WINDOW, {}, site="lyft.com",
        )
    assert len(items) == 3
    assert artifact["owned"] is True


# --- The flags exist and reach the run ---


def test_parser_takes_company_and_jobs_board():
    args = cli.build_parser().parse_args([
        "Palmer Luckey", "--company", "Anduril", "--jobs-board", ANDURIL_BOARD,
    ])
    assert args.company == "Anduril"
    assert args.jobs_board == ANDURIL_BOARD


def test_company_and_jobs_board_are_part_of_what_a_cached_report_was_run_with():
    args = cli.build_parser().parse_args([
        "Palmer Luckey", "--company", "Anduril", "--jobs-board", ANDURIL_BOARD,
    ])
    options = cli._cache_research_options(args)
    assert options["company"] == "Anduril"
    assert options["jobs_board"] == ANDURIL_BOARD


# --- Review of PR #1410 ---

# Finding 1: --jobs-board alone starts the lane.


def test_jobs_board_alone_makes_the_run_about_a_company():
    assert "--jobs-board" in cli._company_signal({}, None, "", [], jobs_board=ANDURIL_BOARD)
    assert cli._company_signal({}, None, "", [], jobs_board="  ") == ""


# Finding 2: only a confirmed official site's stated name makes a board owned.

APEX_CAREERS = """
<script type="application/ld+json">{"@type":"Organization","name":"Apex Tool Group"}</script>
<h2>Open Roles</h2>
"""


def test_name_stated_on_a_guessed_domain_finds_a_board_but_does_not_make_it_owned():
    # apex.com is a guess from the name "Apex" and belongs to another business.
    asked, get = _greenhouse_api({"apextoolgroup": _greenhouse("apextoolgroup", "Apex Tool Group")})
    pages = {"https://apex.com/careers": APEX_CAREERS}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_jobs("Apex", WINDOW, {}, web_backend="none")
    assert asked[-1] == "https://boards-api.greenhouse.io/v1/boards/apextoolgroup/jobs"
    assert len(items) == 3
    assert artifact["open_roles"] == 3
    assert artifact["found_via"] == "slug-guess"
    assert artifact["owned"] is False
    assert "digest" not in artifact


def test_same_page_on_the_confirmed_official_site_makes_the_board_owned():
    _asked, get = _greenhouse_api({"apextoolgroup": _greenhouse("apextoolgroup", "Apex Tool Group")})
    pages = {"https://apex.com/careers": APEX_CAREERS}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch("lib.jobs.http.get", side_effect=get):
        _items, artifact = jobs.search_jobs("Apex", WINDOW, {}, web_backend="none", site="apex.com")
    assert artifact["found_via"] == "site-name"
    assert artifact["owned"] is True


def test_each_host_company_gets_the_official_site_the_resolver_returns():
    def resolve(name, *, listing=True):
        assert listing is False
        if name == "Anduril":
            return {"company": "Anduril Industries", "company_site": "https://www.anduril.com/"}
        return {}

    with patch.object(cli.company_resolve, "resolve_company", side_effect=resolve):
        sites = cli._host_company_sites(["Anduril", "Nobody Inc"], mock=False, config={})
        assert sites == {"Anduril": "https://www.anduril.com/"}
        # No request on a mock run or with the jobs lane excluded.
        assert cli._host_company_sites(["Anduril"], mock=True, config={}) == {}
        assert cli._host_company_sites(["Anduril"], mock=False, config={"EXCLUDE_SOURCES": "jobs"}) == {}
    assert cli._company_for_jobs(
        {}, None, "Palmer Luckey", host_names=["Anduril"], host_sites=sites,
    ) == {"host_companies": ["Anduril"], "host_company_sites": sites}


def test_host_company_is_searched_from_its_resolved_official_site():
    seen = {}

    def search(name, *_args, **kwargs):
        seen[name] = kwargs.get("site", "")
        return [], {"tier": "web"}

    _run_lane(
        "Palmer Luckey",
        {"host_companies": ["Anduril", "Saronic"],
         "host_company_sites": {"Anduril": "https://www.anduril.com/"}},
        search,
    )
    assert seen == {"Palmer Luckey": "", "Anduril": "https://www.anduril.com/", "Saronic": ""}


# Finding 3: an unverified host board does not get the lane's whole budget.


def _workday_pages(total):
    posted = []

    def post(url, *, json_data, **_kwargs):
        posted.append(json_data["offset"])
        return {"total": total, "jobPostings": [
            {"title": f"Engineer {json_data['offset'] + index}",
             "externalPath": f"/job/JR{json_data['offset'] + index}"}
            for index in range(min(20, total - json_data["offset"]))
        ]}

    return posted, post


def test_unowned_host_board_is_read_one_page_only(capsys):
    posted, post = _workday_pages(2000)
    with patch("lib.jobs.http.get_text", return_value=None), \
            patch("lib.jobs.http.post", side_effect=post):
        items, artifact = jobs.search_board_url(WORKDAY_URL, ["Zenith"], WINDOW, {})
    assert posted == [0]
    assert len(items) == 20
    assert artifact["owned"] is False
    assert artifact["open_roles_floor"] is True
    assert "first page" in artifact["coverage_note"]
    assert "no digest" in capsys.readouterr().err


def test_owned_host_board_is_read_in_full():
    posted, post = _workday_pages(60)
    pages = {"https://www.zenith.example/careers": f'<h2>Open roles</h2><a href="{WORKDAY_URL}">Jobs</a>'}
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch("lib.jobs.http.post", side_effect=post), \
            patch.object(jobs, "fetch_workday_details", return_value=(0, 0)):
        items, artifact = jobs.search_board_url(
            WORKDAY_URL, ["Zenith"], WINDOW, {}, site="https://www.zenith.example",
        )
    assert sorted(posted) == [0, 20, 40]
    assert len(items) == 60
    assert artifact["owned"] is True


def test_unowned_smartrecruiters_board_is_named_and_read_from_its_first_page():
    asked = []

    def get(url, *, params=None, **_kwargs):
        asked.append(dict(params or {}))
        return {"totalFound": 900, "content": [
            {"id": str(index), "name": f"Cook {index}", "company": {"name": "Other Burgers"}}
            for index in range(100)
        ]}

    with patch("lib.jobs.http.get_text", return_value=None), patch("lib.jobs.http.get", side_effect=get):
        items, artifact = jobs.search_board_url(
            "https://jobs.smartrecruiters.com/OtherBurgers", ["McDonalds"], WINDOW, {},
        )
    assert asked == [{"limit": "100"}]
    assert len(items) == 100
    assert artifact["owned"] is False and artifact["board_company"] == "Other Burgers"


def test_host_board_gets_a_share_of_the_budget_not_all_of_it():
    seen = {}

    def host(*_args, **kwargs):
        seen.update(kwargs)
        return [], {}

    with patch.object(jobs, "search_board_url", side_effect=host):
        _run_lane(
            "Palmer Luckey", {"host_companies": ["Anduril"]}, lambda *_a, **_k: ([], {"tier": "web"}),
            config={"_jobs_board": ANDURIL_BOARD},
        )
    # The board, the topic and one host company: a third each.
    assert 0 < seen["discovery_seconds"] <= jobs.JOBS_BUDGET_SECONDS / 3 + 0.5


def test_owned_greenhouse_host_board_is_read_with_the_whole_budget_not_the_share():
    # Anduril's job list is megabytes: read inside a 15 s share it timed out.
    limits = []
    budget = jobs.LaneBudget()

    def get(url, *_args, **_kwargs):
        limits.append((url.rsplit("/", 1)[-1], budget.step_deadline < budget.deadline))
        if url.endswith("/jobs"):
            return _greenhouse("andurilindustries", "Anduril Industries")
        return {"name": "Anduril Industries"}

    with patch("lib.jobs.http.get", side_effect=get):
        _items, artifact = jobs.search_board_url(
            ANDURIL_BOARD, ["Palmer Luckey", "Anduril"], WINDOW, {},
            budget=budget, discovery_seconds=15,
        )
    assert artifact["owned"] is True
    # The record is asked inside the share; the owned board's rows are not.
    assert limits == [("andurilindustries", True), ("jobs", False)]


def test_unowned_greenhouse_host_board_is_read_inside_its_share():
    limits = []
    budget = jobs.LaneBudget()

    def get(url, *_args, **_kwargs):
        limits.append(budget.step_deadline < budget.deadline)
        if url.endswith("/jobs"):
            return _greenhouse("andurilindustries", "Other Co")
        return {"name": "Other Co"}

    with patch("lib.jobs.http.get", side_effect=get):
        _items, artifact = jobs.search_board_url(
            ANDURIL_BOARD, ["Anduril"], WINDOW, {}, budget=budget, discovery_seconds=15,
        )
    assert artifact["owned"] is False
    assert limits == [True, True]
    # The share is lifted again for whatever the lane does next.
    assert budget.step_deadline == budget.deadline


def test_unowned_host_board_is_shown_under_the_name_it_states():
    unowned = {"tier": "ats", "provider": "greenhouse", "company": "Palmer Luckey",
               "board_company": "Anduril Industries", "owned": False, "open_roles": 9}
    with patch.object(jobs, "search_board_url", return_value=([{"id": "1"}], dict(unowned))):
        (_items, artifact), _names, _mock = _run_lane(
            "Palmer Luckey", {}, lambda *_a, **_k: ([], {"tier": "web"}),
            config={"_jobs_board": ANDURIL_BOARD},
        )
    assert artifact["board_of"] == "Anduril Industries"


# --- --company "Name:site": the host passes the company's official site ---


def test_company_entry_is_a_name_or_a_name_and_its_site():
    assert cli._host_companies("Anduril") == [{"name": "Anduril", "site": ""}]
    assert cli._host_companies("Anduril:anduril.com") == [
        {"name": "Anduril", "site": "https://anduril.com/"},
    ]
    assert cli._host_companies(" Tesla : tesla.com , SpaceX:spacex.com ") == [
        {"name": "Tesla", "site": "https://tesla.com/"},
        {"name": "SpaceX", "site": "https://spacex.com/"},
    ]
    # A URL is a site too, normalized the way --official-site is.
    assert cli._host_companies("Anduril:https://www.Anduril.com/careers?x=1") == [
        {"name": "Anduril", "site": "https://www.anduril.com/"},
    ]
    assert cli._host_company_names("Tesla:tesla.com,SpaceX") == ["Tesla", "SpaceX"]
    assert cli._host_companies("") == []


def test_company_entry_that_cannot_be_read_is_ignored_with_a_line(capsys):
    # No name before the colon: nothing says whose site it is.
    assert cli._host_companies(":anduril.com,SpaceX") == [{"name": "SpaceX", "site": ""}]
    err = capsys.readouterr().err
    assert "[Resolve] --company" in err and "anduril.com" in err
    # A site that is not a domain: the name stands, without a site.
    assert cli._host_companies("Anduril:not a site") == [{"name": "Anduril", "site": ""}]
    err = capsys.readouterr().err
    assert "[Resolve] --company" in err and "not a site" in err
    # A third entry is one too many.
    assert [c["name"] for c in cli._host_companies("A:a.com,B:b.com,C:c.com")] == ["A", "B"]
    assert "C" in capsys.readouterr().err


def test_site_the_host_passed_is_used_without_asking_the_resolver():
    def resolve(name, *, listing=True):
        assert name == "SpaceX", "a company that came with its site is not looked up"
        return {"company": "SpaceX", "company_site": "https://www.spacex.com/"}

    companies = cli._host_companies("Tesla:tesla.com,SpaceX")
    with patch.object(cli.company_resolve, "resolve_company", side_effect=resolve):
        sites = cli._host_company_sites(companies, mock=False, config={})
    assert sites == {"Tesla": "https://tesla.com/", "SpaceX": "https://www.spacex.com/"}
    # The host's own site needs no lookup, so a mock run keeps it.
    assert cli._host_company_sites(companies, mock=True, config={}) == {"Tesla": "https://tesla.com/"}


def test_company_named_with_a_site_and_again_by_ticker_is_one_company_with_its_site():
    pairs = yahoo_finance.parse_host_tickers("TSLA:Tesla Inc.,SPCX:SpaceX")
    companies = cli._host_companies("Tesla:tesla.com,SpaceX:spacex.com")
    sites = cli._host_company_sites(companies, mock=True, config={})
    assert cli._company_for_jobs(
        {}, pairs, "Elon Musk", host_names=[c["name"] for c in companies], host_sites=sites,
    ) == {
        "host_companies": ["Tesla", "SpaceX"],
        "host_company_sites": {"Tesla": "https://tesla.com/", "SpaceX": "https://spacex.com/"},
    }


def test_site_passed_for_the_topic_itself_is_the_topics_site():
    companies = cli._host_companies("Anduril:anduril.com")
    sites = cli._host_company_sites(companies, mock=True, config={})
    assert cli._company_for_jobs(
        {}, None, "Anduril", host_names=["Anduril"], host_sites=sites,
    ) == {"topic_site": "https://anduril.com/"}
    # --official-site, when given, stays the topic's site.
    assert cli._company_for_jobs(
        {}, None, "Anduril", official_site="anduril.io", host_names=["Anduril"], host_sites=sites,
    ) == {"topic_site": "anduril.io"}


def _palmer_luckey_lane(company_flag):
    """The real lane for "Palmer Luckey" with ``--company``: anduril.com's
    careers page names no hiring system and states "Anduril Industries"."""
    _asked, get = _greenhouse_api({"andurilindustries": _greenhouse("andurilindustries", "Anduril Industries")})
    pages = {"https://anduril.com/careers": ANDURIL_CAREERS}
    companies = cli._host_companies(company_flag)
    with patch.object(cli.company_resolve, "resolve_company", return_value={}):
        sites = cli._host_company_sites(companies, mock=False, config={})
    company = cli._company_for_jobs(
        {}, None, "Palmer Luckey", host_names=[c["name"] for c in companies], host_sites=sites,
    )
    subquery = schema.SubQuery(
        label="primary", search_query="Palmer Luckey", ranking_query="Palmer Luckey", sources=["jobs"],
    )
    with patch("lib.jobs.http.get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch("lib.jobs.http.get", side_effect=get):
        return pipeline._retrieve_stream_impl(
            topic="Palmer Luckey", subquery=subquery, source="jobs", config={"_company": company},
            depth="default", date_range=WINDOW, runtime=None, mock=False,
            web_backend="none", raw_topic="Palmer Luckey",
        )


def test_person_whose_company_came_with_its_site_gets_a_digest():
    items, artifact = _palmer_luckey_lane("Anduril:anduril.com")
    assert len(items) == 3
    assert artifact["found_via"] == "site-name"
    assert artifact["owned"] is True
    assert artifact["digest"]["roles"]
    assert artifact["board_of"] == "Anduril"


def test_person_whose_company_came_with_no_site_gets_counts_only():
    # Wikidata has no site for "Anduril", and the guessed anduril.com cannot vouch.
    items, artifact = _palmer_luckey_lane("Anduril")
    assert len(items) == 3
    assert artifact["open_roles"] == 3
    assert artifact["owned"] is False
    assert "digest" not in artifact


# careers.google.com on 2026-11-10 links a PDF under /jobs/; read as a page
# it raised UnicodeDecodeError and the run printed "Jobs timeout".


def test_file_linked_from_a_careers_page_is_not_read_as_a_jobs_page():
    landing = (
        '<h2>Join our team</h2>'
        '<a href="https://careers.acme.com/jobs/dist/legal/KnowYourRights.pdf">Rights</a>'
        '<a href="https://careers.acme.com/jobs/results/">Search jobs</a>'
    )
    assert jobs._jobs_links(landing, "https://www.acme.com/careers") == [
        "https://careers.acme.com/jobs/results/",
    ]


def test_page_that_is_not_text_is_no_page_and_no_failure():
    def get_text(url, **_kwargs):
        if url.endswith("/careers"):
            return '<h2>Join our team</h2><a href="/jobs/list">Jobs</a>'
        raise UnicodeDecodeError("utf-8", b"\xe2", 0, 1, "invalid continuation byte")

    with patch("lib.jobs.http.get_text", side_effect=get_text), \
            patch("lib.jobs.http.get", side_effect=http.HTTPError("missing", status_code=404)), \
            http.capture_failures() as failures:
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {}, web_backend="none", site="https://www.acme.com",
        )
    assert items == [] and artifact["tier"] == "web"
    assert failures == []

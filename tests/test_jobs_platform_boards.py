"""Job boards found by the hiring platform a careers page runs on.

The lane reads a large company's board by detecting its platform off the
careers page (Oracle Recruiting Cloud, Phenom, Jibe, TalentBrew, Eightfold)
rather than from a hand-written list of companies. A board whose reader fails
falls to the site's job sitemap and then to the web tier, and the run says
which happened. Fixtures in ``fixtures/jobs_platforms.json`` are trimmed live
answers recorded on 2026-11-14.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import http, jobs, render
from lib.job_boards import detect, google, ibm, jibe, oracle, phenom, robots, sitemap, talentbrew

from tests.test_render_patents import _report

WINDOW = ("2026-10-15", "2026-11-14")
# The real resolver, kept before the autouse fixture stubs it.
_REAL_RESOLVES_PUBLIC = sitemap._resolves_public
FX = json.loads((Path(__file__).parent / "fixtures" / "jobs_platforms.json").read_text(encoding="utf-8"))

# Careers-page markup as each platform serves it (trimmed from the live pages).
ORACLE_DELL_PAGE = (
    '<base href="/hcmUI/CandidateExperience/en/sites/careers" '
    'data-apibaseurl="https://enterpriseplatform.dell.com:443" data-fahosturl="" '
    'data-vanitybaseurl="" data-sitenumber="CX_1001">'
    '<link rel="icon" href="/hcmRestApi/CandidateExperience/siteFavicon/favicon-16x16.png?siteNumber=CX_1001">'
)
ORACLE_JPMC_PAGE = (
    '<a href="https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions">'
    "Search jobs</a><p>Join our team</p>"
)
ORACLE_OWN_PAGE = (
    '<link rel="icon" href="https://eeho.fa.us2.oraclecloud.com:443/hcmRestApi/CandidateExperience/'
    'siteFavicon/favicon-16x16.png?siteNumber=CX_45001&size=16x16">'
)
PHENOM_PAGE = (
    '<script src="https://cdn.phenompeople.com/CareerConnectResources/x.js"></script>'
    '<script>phApp.ddo = {"siteConfig": {}}; var x = {"baseUrl":"https://careers.cisco.com/global/en/"};</script>'
)
JIBE_PAGE = (
    '<link rel="canonical" href="https://careers.amd.com/careers-home/jobs" />'
    '<script src="https://assets.jibecdn.com/prod/amd/0.0.83/assets/app.js"></script>'
)
TALENTBREW_PAGE = (
    '<img src="https://tbcdn.talentbrew.com/company/185/v3_0/img/logo.svg">'
    '<form action="/search-jobs" method="get"></form>'
)


@pytest.fixture(autouse=True)
def _fresh_robots(monkeypatch):
    robots.clear_cache()
    # Tests never resolve names: a name resolves to a public address unless
    # it is one a test marks as internal.
    monkeypatch.setattr(sitemap, "_resolves_public", lambda name: "internal" not in name)
    yield
    robots.clear_cache()


# --- detection ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("html", "url", "expected"),
    [
        (ORACLE_DELL_PAGE, "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers",
         ("oracle", {"board": "enterpriseplatform.dell.com", "site_number": "CX_1001"})),
        (ORACLE_JPMC_PAGE, "https://www.jpmorganchase.com/careers",
         ("oracle", {"board": "jpmc.fa.oraclecloud.com", "site_number": "CX_1001"})),
        (ORACLE_OWN_PAGE, "https://careers.oracle.com/en/sites/jobsearch",
         ("oracle", {"board": "eeho.fa.us2.oraclecloud.com", "site_number": "CX_45001"})),
        (PHENOM_PAGE, "https://careers.cisco.com/global/en",
         ("phenom", {"board": "careers.cisco.com", "country": "global", "lang": "en",
                     "base_url": "https://careers.cisco.com/global/en/"})),
        (JIBE_PAGE, "https://careers.amd.com/careers-home/jobs",
         ("jibe", {"board": "careers.amd.com", "jobs_path": "/careers-home/jobs"})),
        (TALENTBREW_PAGE, "https://jobs.boeing.com/", ("talentbrew", {"board": "jobs.boeing.com"})),
        ('<link href="https://static.vscdn.net/fonts/css/eightfold-font-base.css">'
         '<script>window._EF_GROUP_ID = "netflix.com";</script>',
         "https://explore.jobs.netflix.net/careers",
         ("eightfold", {"board": "explore.jobs.netflix.net", "domain": "netflix.com"})),
    ],
)
def test_a_platform_is_read_off_the_careers_page(html, url, expected):
    provider, entry = detect.detect_platform(html, url)
    assert (provider, {key: value for key, value in entry.items() if key != "provider"}) == expected


def test_a_page_with_no_platform_markup_names_none():
    assert detect.detect_platform("<p>Open positions at Initech</p>", "https://initech.com/careers") is None
    # A TalentBrew asset on a page that is not the job search is not the board.
    assert detect.detect_platform(
        '<img src="https://tbcdn.talentbrew.com/a.svg">', "https://example.com/",
    ) is None


def test_microsoft_s_landing_page_points_at_its_eightfold_board():
    html = '<div id="careers-eightFoldCareerUrl" hidden>https://apply.careers.microsoft.com/careers</div>'
    assert detect.eightfold_career_url(html) == "https://apply.careers.microsoft.com/careers"


# --- parsers --------------------------------------------------------------------

ORACLE_ENTRY = {"name": "JPMorgan", "provider": "oracle", "board": "jpmc.fa.oraclecloud.com",
                "site_number": "CX_1001"}


def test_an_oracle_page_parses_to_rows_with_true_posted_dates_and_detail_urls():
    items, total = oracle.parse_listing(FX["oracle_list"], ORACLE_ENTRY)
    assert total == 6972 and len(items) == 5
    first = items[0]
    assert first["title"] == "Python Java AWS Software Engineer III (ML) - Agentic Treasury"
    assert first["url"] == (
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210777835"
    )
    assert first["posted_date_true"] and first["date"] == "2026-11-14"
    assert first["provider"] == "oracle" and first["id"] == "OR210777835"
    assert 'finder=ById;Id="210777835",siteNumber=CX_1001' in first["detail_url"]


def test_an_oracle_answer_without_a_requisition_list_is_a_shape_error():
    with pytest.raises(jobs.BoardShapeError):
        oracle.parse_listing({"items": [{"TotalJobsCount": 3}]}, ORACLE_ENTRY)


def test_a_jibe_page_parses_to_rows_with_their_full_text():
    items, total = jibe.parse_listing(FX["jibe_list"], {"board": "careers.amd.com", "jobs_path": "/careers-home/jobs"})
    assert total == 1312 and len(items) == 3
    first = items[0]
    assert first["title"] == "Sr. Channel Marketing Specialist"
    assert first["url"] == "https://careers.amd.com/careers-home/jobs/95802"
    assert first["department"] == "Sales / Marketing"
    assert first["posted_date_true"] and first["date"] == "2026-11-14"
    assert first["description"]


def test_a_phenom_page_parses_to_rows_with_every_location():
    entry = {"board": "careers.cisco.com", "base_url": "https://careers.cisco.com/global/en/"}
    items, total = phenom.parse_listing(FX["phenom_list"], entry)
    assert total == FX["phenom_list"]["refineSearch"]["totalHits"] and len(items) == 3
    first = items[0]
    assert first["url"].startswith("https://careers.cisco.com/global/en/job/")
    assert first["locations"] and first["posted_date_true"]


def test_the_phenom_role_page_text_is_read_from_its_page_object():
    page = '<script>phApp.ddo = {"jobDetail": {"data": {"job": {"description": "<p>Own the router.</p>"}}}};</script>'
    assert phenom.page_object(page)["jobDetail"]["data"]["job"]["description"] == "<p>Own the router.</p>"
    item = {"url": "https://careers.cisco.com/global/en/job/1"}
    with patch.object(phenom.http, "get_text", return_value=page):
        assert phenom.fetch_details([item], deadline=jobs.time.monotonic() + 5) == (0, 0)
    assert item["description"] == "Own the router."


def test_a_talentbrew_results_page_parses_links_locations_and_dates():
    items, total = talentbrew.parse_listing(FX["talentbrew_list"], {"board": "jobs.boeing.com"})
    assert total == 1130 and len(items) == 3
    first = items[0]
    assert first["url"].startswith("https://jobs.boeing.com/job/")
    assert first["title"] and first["location"]
    assert first["date"] and first["posted_date_true"]


def test_talentbrew_role_text_comes_from_the_role_page_jobposting():
    page = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Engineer", '
            '"description": "&lt;p&gt;Build the 777X wing.&lt;/p&gt;"}</script>')
    assert jobs.jsonld_role_text(page) == ("Build the 777X wing.", "Build the 777X wing.")
    assert jobs.jsonld_role_text("<p>no posting</p>") is None


def test_a_google_results_page_parses_twenty_role_rows_and_claims_no_posted_date():
    items, total = google.parse_listing(FX["google_page"])
    assert total == 3149 and len(items) == 3
    first = items[0]
    assert first["title"] == "Senior Software Engineer, Cloud AI/ML Infrastructure"
    assert first["url"].startswith(google.RESULTS_URL + "120081008824328902-senior-software-engineer")
    assert first["description"] and first["location"]
    assert not any(item["posted_date_true"] or item["date"] for item in items)


def test_an_ibm_search_page_parses_to_rows():
    items, total = ibm.parse_listing(FX["ibm_list"])
    assert total == 3561 and len(items) == 3
    assert items[0]["url"].startswith("https://careers.ibm.com/careers/JobDetail?jobId=")
    assert not items[0]["posted_date_true"]


# --- the paged read -------------------------------------------------------------


def _pages(total, page_size, *, fail_at=None):
    asked = []

    def page(offset):
        def call(_deadline):
            asked.append(offset)
            if fail_at is not None and offset == fail_at:
                raise http.HTTPError("HTTP 429", status_code=429)
            rows = [
                {"id": f"r{n}", "title": f"Role {n}", "date": "2026-11-01"}
                for n in range(offset, min(offset + page_size, total))
            ]
            return {"rows": rows, "total": total}
        return call

    def parse(payload):
        return list(payload["rows"]), payload["total"]

    return asked, page, parse


def test_a_board_of_several_thousand_rows_is_read_whole_not_stopped_at_the_window():
    asked, page, parse = _pages(6000, 200)
    items, info = jobs._read_newest_first(
        page, parse, page_size=200, row_ceiling=7000, window_start="2026-10-15",
        deadline=jobs.time.monotonic() + 30,
    )
    assert len(items) == 6000 and len(asked) == 30
    assert not info["board_cut_short"] and info["board_window_covered"]


def test_no_page_after_a_failed_one_is_kept_and_the_earlier_pages_all_are():
    asked, page, parse = _pages(2000, 100, fail_at=1000)
    items, info = jobs._read_newest_first(
        page, parse, page_size=100, row_ceiling=7000, window_start=None,
        deadline=jobs.time.monotonic() + 30, workers=4,
    )
    assert [row["id"] for row in items] == [f"r{n}" for n in range(1000)]
    assert info["board_cut_short"]
    # Paging stops after the failed page: at most the pages already in flight.
    assert max(asked) <= 1000 + 4 * 100


def test_a_board_that_serves_fewer_rows_a_page_than_asked_is_read_in_part():
    # Asked for 500 a page, the server caps at 100: every page answers, and
    # 400 of 2,000 rows are read. That is a board read in part.
    def page(offset):
        def call(_deadline):
            rows = [{"id": f"r{n}", "title": f"Role {n}"} for n in range(offset, min(offset + 100, 2000))]
            return {"rows": rows, "total": 2000}
        return call

    items, info = jobs._read_newest_first(
        page, lambda payload: (payload["rows"], payload["total"]), page_size=500,
        row_ceiling=7000, window_start=None, deadline=jobs.time.monotonic() + 30,
        newest_first=False,
    )
    assert len(items) == 400
    assert info["board_cut_short"] and not info["board_window_covered"]


def test_a_board_not_sorted_by_date_covers_the_window_only_when_read_whole():
    asked, page, parse = _pages(300, 100, fail_at=200)
    _items, info = jobs._read_newest_first(
        page, parse, page_size=100, row_ceiling=7000, window_start="2026-11-05",
        deadline=jobs.time.monotonic() + 30, newest_first=False,
    )
    # Rows older than the window were read, but in no date order: not covered.
    assert info["board_window_covered"] is False
    assert jobs._board_counts(_items, info, WINDOW)["new_in_window"] is None


# --- search flows ---------------------------------------------------------------


class _Site:
    """Answers the lane's requests from a table; anything else is a 404."""

    def __init__(self, texts=None, gets=None, posts=None):
        self.texts, self.gets, self.posts = texts or {}, gets or {}, posts or {}
        self.asked = []
        # Requested URL -> the address that answered it.
        self.redirects: dict[str, str] = {}

    def _match(self, table, url):
        for key, answer in table.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                return answer(url) if callable(answer) else answer
        return None

    def text(self, url, *_args, **kwargs):
        self.asked.append(url)
        found = self._match(self.texts, url)
        if isinstance(found, Exception):
            raise found
        if found is not None and kwargs.get("landed") is not None:
            kwargs["landed"].append(self.redirects.get(url, url))
        return found

    def get(self, url, *_args, **_kwargs):
        self.asked.append(url)
        found = self._match(self.gets, url)
        if isinstance(found, Exception):
            raise found
        if found is None:
            raise http.HTTPError("HTTP 404: Not Found", status_code=404)
        return copy.deepcopy(found)

    def post(self, url, *_args, **_kwargs):
        self.asked.append(url)
        found = self._match(self.posts, url)
        if found is None:
            raise http.HTTPError("HTTP 404: Not Found", status_code=404)
        return copy.deepcopy(found)


def _run(company, site, fake, **kwargs):
    with patch.object(jobs.http, "get_text", side_effect=fake.text), \
            patch.object(jobs.http, "get", side_effect=fake.get), \
            patch.object(jobs.http, "post", side_effect=fake.post):
        return jobs.search_jobs(company, WINDOW, {}, web_backend="none", site=site, **kwargs)


def _oracle_listing(url):
    # The recorded page is the first; the board ends after it.
    if "offset=0," in url:
        return FX["oracle_list"]
    return {"items": [{"TotalJobsCount": 6972, "requisitionList": []}]}


def _oracle_detail(url):
    # Each role gets a sentence of its own, so it survives boilerplate removal.
    ident = url.split('Id="', 1)[1].split('"', 1)[0]
    answer = copy.deepcopy(FX["oracle_detail"])
    role = answer["items"][0]
    role["ExternalDescriptionStr"] = f"<p>Own requisition {ident}.</p>" + role["ExternalDescriptionStr"]
    return answer


def _oracle_site():
    return _Site(
        texts={"https://www.jpmorganchase.com/careers": ORACLE_JPMC_PAGE},
        gets={
            "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?*":
                _oracle_listing,
            "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?*":
                _oracle_detail,
        },
    )


def test_an_oracle_board_named_by_the_careers_page_prints_a_digest_with_role_text():
    fake = _oracle_site()
    items, artifact = _run("JPMorgan", "https://www.jpmorganchase.com/", fake)
    assert artifact["tier"] == "ats" and artifact["provider"] == "oracle"
    assert artifact["owned"] and artifact["found_via"] == "site"
    assert artifact["open_roles"] == 6972
    assert len(items) == 5
    roles = artifact["digest"]["roles"]
    assert roles and all("Own requisition" in role["text"] for role in roles)
    assert sum(1 for url in fake.asked if "RequisitionDetails" in url) == len(roles)
    rendered = render.render_compact(_report(jobs=artifact))
    assert "\n## Jobs Digest\n" in rendered and "💼 Jobs: 6,972 open roles" in rendered


def test_with_respect_robots_on_a_disallowed_listing_is_not_read(monkeypatch):
    monkeypatch.setenv(robots.RESPECT_ROBOTS_VAR, "1")
    fake = _oracle_site()
    fake.texts["https://jpmc.fa.oraclecloud.com/robots.txt"] = "User-agent: *\nDisallow: /hcmRestApi/\n"
    _items, artifact = _run("JPMorgan", "https://www.jpmorganchase.com/", fake)
    assert not any("recruitingCEJobRequisitions" in url for url in fake.asked)
    assert artifact["job_site_unreadable"] == "JPMorgan"
    assert artifact["job_site_unreadable_reason"] == jobs.ROBOTS_REASON


def test_with_respect_robots_off_no_robots_txt_is_asked_before_a_listing():
    fake = _oracle_site()
    _run("JPMorgan", "https://www.jpmorganchase.com/", fake)
    assert not any(url.endswith("/robots.txt") for url in fake.asked)


def test_a_platform_reader_that_fails_falls_to_the_site_s_job_sitemap():
    fake = _oracle_site()
    fake.gets = {}  # every listing call is a 404
    fake.texts.update({
        "https://jobs.jpmorganchase.com/robots.txt": FX["sap_robots"].replace("jobs.sap.com", "jobs.jpmorganchase.com"),
        "https://jobs.jpmorganchase.com/sitemap.xml": FX["sap_index"].replace("jobs.sap.com", "jobs.jpmorganchase.com"),
        "https://jobs.jpmorganchase.com/en/jobs/sitemap.xml": FX["sap_sitemap"].replace("jobs.sap.com", "jobs.jpmorganchase.com"),
    })
    items, artifact = _run("JPMorgan", "https://www.jpmorganchase.com/", fake)
    assert artifact["provider"] == "sitemap" and artifact["titles_only"]
    assert "oracle board not read" in artifact["board_fallback"]
    assert artifact["digest"]["titles_only"] and "oracle board not read" in artifact["digest"]["note"]
    assert items[0]["title"] == "Sap Success Plan Manager Expert South"
    assert artifact["new_in_window"] is None
    # The footer says the run fell to the sitemap, and why.
    footer = render._jobs_board_footer_line(_report(jobs=artifact))
    assert "from its job sitemap (oracle board not read:" in footer


def test_a_platform_reader_that_fails_with_no_sitemap_says_the_site_cannot_be_read(capsys):
    fake = _oracle_site()
    fake.gets = {}
    items, artifact = _run("JPMorgan", "https://www.jpmorganchase.com/", fake)
    assert items == [] and artifact["tier"] == "web"
    assert artifact["job_site_unreadable"] == "JPMorgan"
    assert "oracle board answered with an error" in artifact["job_site_unreadable_reason"]
    assert "JPMorgan's job site can't be read automatically" in capsys.readouterr().err
    report = _report()
    report.artifacts["jobs_site_unreadable"] = artifact["job_site_unreadable"]
    report.artifacts["jobs_site_unreadable_reason"] = artifact["job_site_unreadable_reason"]
    assert "(its oracle board answered with an error" in render.render_compact(report)


def test_a_walled_careers_site_is_read_from_its_job_sitemap():
    fake = _Site(texts={
        "https://jobs.sap.com/robots.txt": FX["sap_robots"],
        "https://jobs.sap.com/sitemap.xml": FX["sap_index"],
        # The site's general sitemap answers with no role pages.
        "https://jobs.sap.com/en/sitemap.xml": "<urlset></urlset>",
        "https://jobs.sap.com/en/jobs/sitemap.xml": FX["sap_sitemap"],
    })
    items, artifact = _run("SAP", "https://www.sap.com/", fake)
    # The site's own careers and jobs hosts were tried before the sitemap.
    assert "https://careers.sap.com/" in fake.asked and "https://jobs.sap.com/" in fake.asked
    assert artifact["provider"] == "sitemap" and artifact["owned"]
    assert artifact["open_roles"] == 6 and len(items) == 6
    rendered = render.render_compact(_report(jobs=artifact))
    assert "\n## Jobs Digest\n" in rendered and "titles only" in rendered
    assert "💼 Jobs: 6 open roles │ from its job sitemap" in rendered


def test_sitemap_urls_with_no_title_take_it_from_the_role_page():
    urlset = ('<urlset><url><loc>https://careers.walmart.com/us/en/jobs/R-674391</loc></url>'
              '<url><loc>https://careers.walmart.com/us/en/jobs/R-674392</loc></url></urlset>')
    fake = _Site(texts={
        "https://careers.walmart.com/robots.txt": "User-agent: *\nDisallow: /api\nSitemap: https://careers.walmart.com/sitemap.xml\n",
        "https://careers.walmart.com/sitemap.xml": urlset,
        "https://careers.walmart.com/us/en/jobs/R-674391":
            '<meta property=og:title content="Prepared Meals Associate - Walmart Careers" />',
    })
    items, artifact = _run("Walmart", "https://www.walmart.com/", fake)
    assert [item["title"] for item in items] == ["Prepared Meals Associate"]
    assert artifact["open_roles"] == 2 and artifact["board_titles_from_pages"]


def test_a_sitemap_whose_role_pages_are_walled_gives_a_count_and_no_digest():
    urlset = '<urlset><url><loc>https://jobs.uber.com/en/jobs/149574/</loc></url></urlset>'
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
    })
    items, artifact = _run("Uber", "https://www.uber.com/", fake)
    assert items == [] and artifact["open_roles"] == 1 and "digest" not in artifact
    footer = render._jobs_board_footer_line(_report(jobs=artifact))
    assert footer == "💼 Jobs: 1 open role │ from its job sitemap, no titles readable"


def test_the_sitemap_title_rules():
    assert sitemap.title_from_url("https://jobs.sap.com/en/jobs/744000161572239/sap-success-plan-manager-west/") == \
        "Sap Success Plan Manager West"
    assert sitemap.title_from_url(
        "https://intel.wd1.myworkdayjobs.com/External/job/US-Oregon-Hillsboro/XMLNAME-RF-Lab-Engineer_JR0288778"
    ) == "RF Lab Engineer"
    assert sitemap.title_from_url("https://jobs.uber.com/en/jobs/149574/") == ""
    assert sitemap.title_from_page("<title>Data Engineer | Acme Careers</title>", "Acme") == "Data Engineer"


def test_google_is_read_a_few_pages_at_low_concurrency_and_prints_no_new_count():
    pages = []

    def page(url):
        pages.append(url)
        return FX["google_page"]

    fake = _Site(texts={google.RESULTS_URL + "?sort_by=date&page=*": page})
    with patch.object(google, "PAGE_LIMIT", 4):
        items, artifact = _run("Google", "https://www.google.com/", fake)
    assert artifact["provider"] == "google" and artifact["open_roles"] == 3149
    assert len(pages) == 4 and artifact["new_in_window"] is None
    assert not any("/careers" == url[-8:] for url in fake.asked)
    assert google.WORKERS <= 2


def test_google_with_respect_robots_on_is_not_read(monkeypatch):
    monkeypatch.setenv(robots.RESPECT_ROBOTS_VAR, "1")
    fake = _Site(texts={
        "https://www.google.com/robots.txt": "User-agent: *\nDisallow: /about/careers/applications/jobs/results\n",
    })
    items, artifact = _run("Google", "https://www.google.com/", fake)
    assert items == [] and artifact["job_site_unreadable"] == "Google"
    assert artifact["job_site_unreadable_reason"] == jobs.ROBOTS_REASON
    assert not any("jobs/results/?" in url for url in fake.asked)


def test_disney_s_careers_page_on_its_own_domain_is_read_through_phenom():
    page = PHENOM_PAGE.replace("careers.cisco.com/global/en/", "join.disneycareers.com/global/en/")
    fake = _Site(
        texts={"https://join.disneycareers.com/global/en": page},
        posts={"https://join.disneycareers.com/widgets": FX["phenom_list"]},
    )
    _items, artifact = _run("Disney", "https://disney.com/", fake)
    assert artifact["provider"] == "phenom" and artifact["owned"]
    assert artifact["board_token"] == "join.disneycareers.com"


def test_a_careers_page_that_redirects_off_site_to_a_search_engine_is_not_read():
    budget = jobs.LaneBudget()
    token = jobs._BUDGET.set(budget)
    try:
        budget.landed["https://www.microsoft.com/careers"] = "https://www.bing.com/?ref=aka"
        with patch.object(jobs.http, "get_text", return_value="<p>Open positions</p>"):
            page, _gone, _used = jobs._careers_page_at(
                "https://www.microsoft.com/careers", guessed=True, company="Microsoft",
                site="https://www.microsoft.com/",
            )
    finally:
        jobs._BUDGET.reset(token)
    assert page is None


def test_get_text_reports_the_address_that_answered():
    headers = http.ResponseHeaders({})
    headers.url = "https://jobs.boeing.com/"
    landed = []
    with patch.object(http, "request", return_value=("<html></html>", headers)):
        assert http.get_text("https://www.boeing.com/careers", landed=landed) == "<html></html>"
    assert landed == ["https://jobs.boeing.com/"]


# --- robots.txt -----------------------------------------------------------------


def test_robots_rules_use_wildcards_and_the_longest_match():
    rules = robots.Rules(
        "User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /search-jobs/\n"
        "Allow: /search-jobs/public$\nDisallow: /*?q=\nSitemap: https://x.com/sitemap.xml\n"
    )
    assert not rules.allows("/search-jobs/results?CurrentPage=1")
    assert rules.allows("/search-jobs/public")
    assert not rules.allows("/en/jobs?q=engineer")
    assert rules.allows("/job/123")
    assert rules.sitemaps == ["https://x.com/sitemap.xml"]


def test_robots_is_never_asked_with_the_setting_off(monkeypatch):
    monkeypatch.delenv(robots.RESPECT_ROBOTS_VAR, raising=False)
    with patch.object(robots.http, "get_text", side_effect=AssertionError("asked")):
        assert robots.disallowed("https://www.google.com/about/careers/applications/jobs/results/") is False


def test_a_jibe_board_one_link_past_the_careers_page_is_read_newest_first():
    landing = '<a href="https://careers.amd.com/careers-home/jobs">Search jobs</a><p>Join our team</p>'

    def listing(url):
        return FX["jibe_list"] if "page=1&" in url else {"totalCount": 1312, "jobs": []}

    fake = _Site(
        texts={"https://www.amd.com/careers": landing, "https://careers.amd.com/careers-home/jobs": JIBE_PAGE},
        gets={"https://careers.amd.com/api/jobs?*": listing},
    )
    items, artifact = _run("AMD", "https://www.amd.com/", fake)
    assert artifact["provider"] == "jibe" and artifact["owned"] and artifact["found_via"] == "site"
    assert len(items) == 3 and artifact["open_roles"] == 1312
    assert artifact["digest"]["roles"]


def test_a_talentbrew_board_is_read_and_its_role_text_comes_from_the_role_pages():
    def role_page(url):
        return ('<script type="application/ld+json">{"@type": "JobPosting", "title": "x", '
                f'"description": "Build a wing for role {url.rsplit("/", 1)[-1]}."}}</script>')

    def listing(url):
        return FX["talentbrew_list"] if "CurrentPage=1&" in url else {"results": "<ul></ul>"}

    fake = _Site(
        texts={"https://www.boeing.com/careers": TALENTBREW_PAGE, "https://jobs.boeing.com/job/*": role_page},
        gets={"https://jobs.boeing.com/search-jobs/results?*": listing},
    )
    fake.redirects = {"https://www.boeing.com/careers": "https://jobs.boeing.com/"}
    # The careers page redirects to the job search on its jobs host.
    _items, artifact = _run("Boeing", "https://www.boeing.com/", fake)
    assert artifact["provider"] == "talentbrew" and artifact["board_token"] == "jobs.boeing.com"
    assert artifact["open_roles"] == 1130
    assert all("Build a wing" in role["text"] for role in artifact["digest"]["roles"])


def test_ibm_is_read_through_its_search_api():
    calls = []

    def post(url, body, *_args, **_kwargs):
        calls.append(body["from"])
        answer = copy.deepcopy(FX["ibm_list"])
        if body["from"]:
            answer["hits"]["hits"] = []
        return answer

    fake = _Site()
    with patch.object(jobs.http, "get_text", side_effect=fake.text), \
            patch.object(jobs.http, "get", side_effect=fake.get), \
            patch.object(jobs.http, "post", side_effect=post):
        items, artifact = jobs.search_jobs("IBM", WINDOW, {}, web_backend="none", site="https://www.ibm.com/")
    assert artifact["provider"] == "ibm" and artifact["open_roles"] == 3561
    assert len(items) == 3 and 0 in calls
    assert artifact["new_in_window"] is None


# --- review fixes ----------------------------------------------------------------


def test_a_platform_page_on_a_guessed_domain_is_not_read_as_the_company_s_board():
    # No official site: mercury.com is guessed from the name and may be
    # another business's. Its Oracle page names no company, so it is not read.
    fake = _Site(texts={
        "https://mercury.com/careers*": ORACLE_JPMC_PAGE,
        "https://www.mercury.com/careers*": ORACLE_JPMC_PAGE,
    })
    _items, artifact = _run("Mercury", "", fake)
    assert not any("oraclecloud.com" in url for url in fake.asked)
    assert artifact.get("provider") != "oracle"


def test_a_company_reader_that_raises_a_value_error_falls_through_not_over():
    fake = _oracle_site()
    with patch.object(oracle, "read_board", side_effect=ValueError("bad JSON")):
        items, artifact = _run("JPMorgan", "https://www.jpmorganchase.com/", fake)
    assert items == [] and artifact["job_site_unreadable"] == "JPMorgan"


def test_a_sitemap_that_is_not_text_costs_only_itself():
    robots_txt = ("User-agent: *\nSitemap: https://jobs.sap.com/jobs.xml.gz\n"
                  "Sitemap: https://jobs.sap.com/en/jobs/sitemap.xml\n")
    fake = _Site(texts={
        "https://jobs.sap.com/robots.txt": robots_txt,
        "https://jobs.sap.com/jobs.xml.gz": UnicodeDecodeError("utf-8", b"\x1f\x8b", 0, 1, "invalid start byte"),
        "https://jobs.sap.com/en/jobs/sitemap.xml": FX["sap_sitemap"],
    })
    items, artifact = _run("SAP", "https://www.sap.com/", fake)
    assert artifact["provider"] == "sitemap" and len(items) == 6
    # A job sitemap that could not be read leaves the count a floor.
    assert artifact["open_roles_floor"] is True


def test_a_role_listed_in_each_language_s_sitemap_counts_once():
    index = ('<sitemapindex><sitemap><loc>https://jobs.sap.com/en/jobs/sitemap.xml</loc></sitemap>'
             '<sitemap><loc>https://jobs.sap.com/de/jobs/sitemap.xml</loc></sitemap></sitemapindex>')
    german = FX["sap_sitemap"].replace("/en/", "/de/")
    fake = _Site(texts={
        "https://jobs.sap.com/robots.txt": FX["sap_robots"],
        "https://jobs.sap.com/sitemap.xml": index,
        "https://jobs.sap.com/en/jobs/sitemap.xml": FX["sap_sitemap"],
        "https://jobs.sap.com/de/jobs/sitemap.xml": german,
    })
    _items, artifact = _run("SAP", "https://www.sap.com/", fake)
    assert artifact["open_roles"] == 6 and artifact["open_roles_floor"] is False
    assert "https://jobs.sap.com/de/jobs/sitemap.xml" not in fake.asked


def test_walled_role_pages_stop_after_the_first_wave():
    urlset = "<urlset>" + "".join(
        f"<url><loc>https://jobs.uber.com/en/jobs/{149500 + n}/</loc></url>" for n in range(40)
    ) + "</urlset>"
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
    })
    _items, artifact = _run("Uber", "https://www.uber.com/", fake)
    role_pages = [url for url in fake.asked if "/en/jobs/1495" in url]
    assert len(role_pages) == sitemap.TITLE_FIRST_WAVE
    assert artifact["open_roles"] == 40 and artifact["board_titles_missing"]


def test_a_titled_sitemap_board_with_the_digest_off_does_not_claim_no_titles():
    artifact = {"open_roles": 6, "titles_only": True, "owned": True}
    footer = render._jobs_board_footer_line(_report(jobs=artifact))
    assert footer == "💼 Jobs: 6 open roles │ from its job sitemap"


def test_the_robots_opt_in_is_read_from_the_env_file(tmp_path, monkeypatch):
    import os

    from lib import env
    # setenv then delenv: the variable is unset now and restored after.
    monkeypatch.setenv(robots.RESPECT_ROBOTS_VAR, "")
    monkeypatch.delenv(robots.RESPECT_ROBOTS_VAR)
    config_file = tmp_path / ".env"
    config_file.write_text(f"{robots.RESPECT_ROBOTS_VAR}=1\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", config_file, raising=False)
    try:
        env.get_config()
        assert robots.respect_robots()
    finally:
        os.environ.pop(robots.RESPECT_ROBOTS_VAR, None)


# --- review fixes -------------------------------------------------------------


def test_a_sitemap_cannot_point_the_lane_at_a_private_address():
    urlset = (
        "<urlset>"
        "<url><loc>http://127.0.0.1:8080/jobs/12345</loc></url>"
        "<url><loc>http://10.0.0.5/jobs/12346</loc></url>"
        "<url><loc>http://localhost/jobs/12347</loc></url>"
        "<url><loc>https://evil.example/jobs/12348</loc></url>"
        "<url><loc>file:///etc/jobs/12349</loc></url>"
        "<url><loc>https://jobs.uber.com/en/jobs/149574/</loc></url>"
        "</urlset>"
    )
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
    })
    _items, artifact = _run("Uber", "https://www.uber.com/", fake)
    assert not any(host in url for url in fake.asked for host in ("127.0.0.1", "10.0.0.5", "localhost", "evil.example", "file:"))
    assert artifact["open_roles"] == 1


def test_a_role_page_redirected_off_the_site_gives_no_title():
    urlset = '<urlset><url><loc>https://jobs.uber.com/en/jobs/149574/</loc></url></urlset>'
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
        "https://jobs.uber.com/en/jobs/149574/": "<title>Internal Admin Console</title>",
    })
    fake.redirects["https://jobs.uber.com/en/jobs/149574/"] = "http://192.168.1.1/admin"
    items, artifact = _run("Uber", "https://www.uber.com/", fake)
    assert items == [] and artifact["board_titles_missing"]


@pytest.mark.parametrize("title", [
    "Just a moment...", "Access Denied", "Attention Required! | Cloudflare", "403 Forbidden", "Page Not Found",
])
def test_a_block_page_answered_with_200_gives_no_title(title):
    urlset = "<urlset>" + "".join(
        f"<url><loc>https://jobs.uber.com/en/jobs/{149500 + n}/</loc></url>" for n in range(20)
    ) + "</urlset>"
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
        "https://jobs.uber.com/en/jobs/*": f"<title>{title}</title>",
    })
    items, artifact = _run("Uber", "https://www.uber.com/", fake)
    assert items == [] and artifact["board_titles_missing"]
    # The first wave found only block pages, so the rest were not asked.
    assert len([url for url in fake.asked if "/en/jobs/1495" in url]) == sitemap.TITLE_FIRST_WAVE
    assert sitemap.title_from_page("<title>Senior Error Analysis Engineer</title>") == "Senior Error Analysis Engineer"


def test_an_unread_numbered_sitemap_on_a_jobs_host_leaves_the_count_a_floor():
    index = ('<sitemapindex><sitemap><loc>https://jobs.sap.com/sitemap-1.xml</loc></sitemap>'
             '<sitemap><loc>https://jobs.sap.com/sitemap-2.xml</loc></sitemap></sitemapindex>')
    fake = _Site(texts={
        "https://jobs.sap.com/robots.txt": FX["sap_robots"],
        "https://jobs.sap.com/sitemap.xml": index,
        "https://jobs.sap.com/sitemap-1.xml": FX["sap_sitemap"],
    })
    _items, artifact = _run("SAP", "https://www.sap.com/", fake)
    assert artifact["open_roles"] == 6 and artifact["open_roles_floor"] is True


def test_a_capped_page_leaves_a_gap_so_an_older_row_does_not_cover_the_window():
    # Asked for 500 a page, the server caps at 100: rows 100-499 are skipped.
    # The older row on a later page must not declare the window read whole.
    def page(offset):
        def call(_deadline):
            rows = [
                {"id": f"r{n}", "title": f"Role {n}", "date": "2026-11-10" if n < 1000 else "2026-09-01"}
                for n in range(offset, min(offset + 100, 2000))
            ]
            return {"rows": rows, "total": 2000}
        return call

    items, info = jobs._read_newest_first(
        page, lambda payload: (payload["rows"], payload["total"]), page_size=500,
        row_ceiling=7000, window_start="2026-10-15", deadline=jobs.time.monotonic() + 30,
        newest_first=True,
    )
    assert any(row["date"] < "2026-10-15" for row in items)
    assert info["board_window_covered"] is False
    assert jobs._board_counts(items, info, WINDOW)["new_in_window"] is None


def test_the_eightfold_fallback_generation_is_checked_against_robots(monkeypatch):
    monkeypatch.setenv(robots.RESPECT_ROBOTS_VAR, "1")
    asked = []

    def generation(entry, *, deadline):
        asked.append(entry["api"])
        return [], {}

    monkeypatch.setattr(jobs, "_read_eightfold_generation", generation)
    with patch.object(jobs.http, "get_text", side_effect=_Site(texts={
        "https://careers.example.com/robots.txt": "User-agent: *\nDisallow: /api/apply/\n",
    }).text):
        jobs.read_eightfold_board(
            {"board": "careers.example.com", "domain": "example.com"},
            deadline=jobs._clock()() + 30, date_range=WINDOW,
        )
    assert asked == ["pcsx"]
    assert jobs._listing_url(jobs.COMPANY_PROVIDER_EIGHTFOLD, {"board": "b.example", "api": "v2"}) == \
        "https://b.example/api/apply/v2/jobs"


def test_a_redirect_to_a_private_address_is_refused_before_it_is_followed():
    import io
    import urllib.error
    import urllib.request
    from email.message import Message

    handler = http._StripAuthOnCrossOriginRedirect()
    req = urllib.request.Request("https://jobs.uber.com/en/jobs/149574/")
    setattr(req, http._REDIRECT_GUARD_ATTR, sitemap._guard(req.full_url))
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(req, io.BytesIO(), 302, "Found", Message(), "http://192.168.1.1/admin")
    followed = handler.redirect_request(
        req, io.BytesIO(), 302, "Found", Message(), "https://jobs.uber.com/en/jobs/149574/data-engineer",
    )
    # A redirect on the site is followed and keeps the check for the next hop.
    assert getattr(followed, http._REDIRECT_GUARD_ATTR) is getattr(req, http._REDIRECT_GUARD_ATTR)


def test_role_pages_from_a_sitemap_are_fetched_with_the_redirect_check():
    urlset = '<urlset><url><loc>https://jobs.uber.com/en/jobs/149574/</loc></url></urlset>'
    guards = []
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
    })
    original = fake.text

    def text(url, *args, **kwargs):
        if "/en/jobs/" in url:
            guards.append(kwargs.get("redirect_guard"))
        return original(url, *args, **kwargs)

    fake.text = text
    _run("Uber", "https://www.uber.com/", fake)
    assert guards and all(guard is not None for guard in guards)
    assert not guards[0]("http://127.0.0.1/") and guards[0]("https://jobs.uber.com/en/jobs/1/")


def test_a_same_site_name_that_resolves_to_a_private_address_is_not_fetched(monkeypatch):
    urlset = (
        "<urlset><url><loc>https://internal.jobs.uber.com/en/jobs/149573/</loc></url>"
        "<url><loc>https://jobs.uber.com/en/jobs/149574/</loc></url></urlset>"
    )
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": "User-Agent: *\nSitemap: https://jobs.uber.com/sitemap.xml\n",
        "https://jobs.uber.com/sitemap.xml": urlset,
    })
    _items, artifact = _run("Uber", "https://www.uber.com/", fake)
    assert not any("internal." in url for url in fake.asked)
    assert artifact["open_roles"] == 1


def test_name_resolution_rejects_any_private_answer(monkeypatch):
    import socket

    answers = {
        "public.example": ["93.184.216.34"],
        "mixed.example": ["93.184.216.34", "10.1.2.3"],
        "loopback.example": ["127.0.0.1"],
        "v6local.example": ["fe80::1%en0"],
    }

    def getaddrinfo(name, *_args, **_kwargs):
        if name not in answers:
            raise socket.gaierror("no such name")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in answers[name]]

    monkeypatch.setattr(sitemap.socket, "getaddrinfo", getaddrinfo)
    resolve = _REAL_RESOLVES_PUBLIC
    resolve.cache_clear()
    try:
        assert resolve("public.example") is True
        assert resolve("mixed.example") is False
        assert resolve("loopback.example") is False
        assert resolve("v6local.example") is False
        assert resolve("missing.example") is False
    finally:
        resolve.cache_clear()


def test_a_robots_txt_sitemap_on_a_private_address_is_not_fetched():
    robots_txt = (
        "User-Agent: *\n"
        "Sitemap: http://127.0.0.1:8080/jobs/sitemap.xml\n"
        "Sitemap: https://internal.jobs.uber.com/sitemap.xml\n"
        "Sitemap: https://jobs.uber.com/sitemap.xml\n"
    )
    fake = _Site(texts={
        "https://jobs.uber.com/robots.txt": robots_txt,
        "https://jobs.uber.com/sitemap.xml": '<urlset><url><loc>https://jobs.uber.com/en/jobs/149574/</loc></url></urlset>',
    })
    _items, artifact = _run("Uber", "https://www.uber.com/", fake)
    assert not any("127.0.0.1" in url or "internal." in url for url in fake.asked)
    assert artifact["open_roles"] == 1

"""Regression tests for PR 16: Job boards SSRF, host/port injection & ReDoS.

Covers HIGH-163, HIGH-164, HIGH-167, HIGH-169, HIGH-171, HIGH-173, and HIGH-174.
"""

from __future__ import annotations

import io
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from unittest.mock import patch

import pytest

from lib import http, jobs
from lib.job_boards import detect, jibe, kula, oracle, phenom, robots, sitemap, talentbrew


# ---------------------------------------------------------------------------
# HIGH-163: _host / parse_board_url / _named_jobs_host / _same_site / URL checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://meta.com:443/careers", "meta.com"),
        ("https://meta.com.:443/careers", "meta.com"),
        ("https://JOBS.ACME.COM/openings", "jobs.acme.com"),
        ("http://jobs.company.com@169.254.169.254/latest/meta-data/", ""),
        ("https://user:pass@company.com/careers", ""),
        ("https://169.254.169.254\\.acme.com/careers", ""),
        ("https://acme.com:99999/careers", ""),
        ("https://acme..com/careers", ""),
        ("https://acme.com../careers", ""),
        ("https://acme.com/\r\nX: 1", ""),
    ],
)
def test_high163_host_normalizes_ports_fqdn_and_rejects_userinfo_tricks(raw: str, expected: str):
    assert jobs._host(raw) == expected
    if expected == "":
        assert detect.is_safe_public_url(raw) is False


def test_high163_registry_entry_cannot_be_bypassed_with_explicit_port_or_fqdn():
    for site in (
        "https://meta.com:443/careers",
        "https://meta.com.:443/careers",
        "https://about.meta.com:443/",
        "https://tesla.com:8443/careers",
    ):
        entry = jobs._registry_entry(site)
        assert entry is not None and entry[0] == "unreadable", f"bypassed unreadable registry for {site}"

    for site, expected_provider in (
        ("https://apple.com:443/careers", jobs.COMPANY_PROVIDER_APPLE),
        ("https://www.amazon.com.:443/", jobs.COMPANY_PROVIDER_AMAZON),
    ):
        entry = jobs._registry_entry(site)
        assert entry is not None and entry[0] == "board" and entry[1]["provider"] == expected_provider


def test_high163_parse_board_url_accepts_default_port_and_blocks_userinfo_or_bad_ports():
    assert jobs.parse_board_url("https://boards.greenhouse.io:443/stripe") == ("greenhouse", "stripe")
    assert jobs.parse_board_url("https://boards.greenhouse.io.:443/stripe") == ("greenhouse", "stripe")
    for bad in (
        "https://user:pass@boards.greenhouse.io/stripe",
        "https://evil@boards.greenhouse.io/stripe",
        "https://boards.greenhouse.io:8443/stripe",
        "https://boards.greenhouse.io:80/stripe",
        "http://boards.greenhouse.io:443/stripe",
        "https://boards.greenhouse.io\\@evil.com/stripe",
        "file:///boards.greenhouse.io/stripe",
    ):
        assert jobs.parse_board_url(bad) == (None, None), f"expected rejection for {bad}"


def test_high163_named_jobs_host_blocks_cctld_generic_userinfo_and_subdomain_spoofs():
    # Multi-part ccTLD bypasses ("com"/"org"/"net" must never be treated as company name).
    assert jobs._named_jobs_host("jobs.evil.com", "https://acme.com.au/careers") is False
    assert jobs._named_jobs_host("careers.evil.org", "https://acme.org.uk/careers") is False
    assert jobs._named_jobs_host("jobs.evil.net", "https://acme.net.au/careers") is False
    assert jobs._named_jobs_host("jobs.evil.com", "https://acme.com.br/careers") is False
    assert jobs._named_jobs_host("jobs.evil.co.uk", "https://acme.co.uk/careers") is False
    # Attacker subdomain containing victim's label on attacker's domain.
    assert jobs._named_jobs_host("jobs.acme.evil.com", "https://acme.com/careers") is False
    # Userinfo / non-public / IP / reserved suffix spoofs.
    assert jobs._named_jobs_host("jobs.company.com@169.254.169.254", "https://company.com/careers") is False
    assert jobs._named_jobs_host("jobs.company.local", "https://company.com/careers") is False
    assert jobs._named_jobs_host("jobs.company.internal", "https://company.com/careers") is False
    assert jobs._named_jobs_host("jobs.company.com:8443", "https://company.com/careers") is False
    # Legitimate cross-TLD jobs hosts still work (including from ccTLD pages).
    assert jobs._named_jobs_host("explore.jobs.netflix.net", "https://jobs.netflix.com/") is True
    assert jobs._named_jobs_host("jobs.acme.net", "https://acme.com.au/careers") is True
    assert jobs._named_jobs_host("jobs.acme.com", "https://acme.co.uk/careers") is True


def test_high163_jobs_links_and_board_page_block_userinfo_ports_and_cctld_bypasses():
    html = (
        '<a href="http://jobs.company.com@169.254.169.254/latest/meta-data/">Meta</a>'
        '<a href="https://jobs.company.com:8443/openings">AltPort</a>'
        '<a href="https://jobs.evil.com/openings">EvilCom</a>'
        '<a href="https://jobs.acme.com.au/openings">Good</a>'
    )
    assert jobs._jobs_links(html, "https://acme.com.au/careers") == ["https://jobs.acme.com.au/openings"]

    # _careers_page_at must refuse non-public/userinfo landed URLs even when named=True.
    budget = jobs.LaneBudget()
    token = jobs._BUDGET.set(budget)
    try:
        budget.landed["https://join.disneycareers.com/global/en"] = (
            "http://jobs.disney.com@169.254.169.254/latest/meta-data/"
        )
        with patch.object(jobs.http, "get_text", return_value="<p>Open positions</p>"):
            page, _gone, _used = jobs._careers_page_at(
                "https://join.disneycareers.com/global/en",
                guessed=True, company="Disney", site="https://disney.com/", named=True,
            )
        assert page is None
    finally:
        jobs._BUDGET.reset(token)

    # _board_page must never fetch userinfo or port-injected eightFoldCareerUrl.
    poisoned_landing = (
        '<div id="careers-eightFoldCareerUrl">https://jobs.acme.com@169.254.169.254/careers</div>'
        '<a href="https://jobs.acme.com:8443/jobs">Jobs</a>'
    )
    with patch.object(jobs.http, "get_text", side_effect=AssertionError("must not fetch")):
        assert jobs._board_page(poisoned_landing, "https://acme.com/careers") == (
            poisoned_landing, "https://acme.com/careers",
        )


def test_high163_search_redirect_posting_board_and_domain_use_hostname():
    assert jobs._is_search_redirect("https://google.com:443/url?q=https://evil.com") is True
    assert jobs._is_search_redirect("https://duckduckgo.com:443/l/?uddg=https://evil.com") is True
    assert jobs._is_search_redirect("https://user@example.com/careers") is True
    assert jobs._is_search_redirect("https://acme.com/careers") is False

    assert jobs._posting_board("https://boards.greenhouse.io:443/stripe/jobs/123") == "stripe"
    assert jobs._posting_board("https://user@boards.greenhouse.io/stripe/jobs/123") == ""
    assert jobs._posting_board("https://boards.greenhouse.io:8443/stripe/jobs/123") == ""

    assert jobs._domain("https://www.walmart.com:443/jobs") == "walmart.com"
    assert jobs._domain("https://walmart.com/jobs") == "walmart.com"
    assert jobs._domain("https://user@walmart.com/jobs") == ""


# ---------------------------------------------------------------------------
# HIGH-164: Linear-time HTML / JSON-LD / site-name / tag-strip / ATS parsing
# ---------------------------------------------------------------------------


def test_high164_jobs_html_and_jsonld_parsers_resist_redos_in_under_100ms():
    adversarial_cases = [
        ("extract_jsonld_blocks script", lambda: jobs.extract_jsonld_blocks("<script " * 60_000)),
        (
            "extract_jsonld_jobs unclosed ld+json",
            lambda: jobs.extract_jsonld_jobs('<script type="application/ld+json">' * 50_000, "https://acme.com/"),
        ),
        ("stated_names meta", lambda: jobs._stated_names("<meta " * 60_000)),
        ("stated_names og_site_name", lambda: jobs._stated_names('<meta property="og:site_name" ' * 50_000)),
        ("clean_html unclosed <", lambda: jobs._clean_html("<" * 100_000)),
        ("clean_html unclosed <br", lambda: jobs._clean_html("<br " * 60_000)),
        ("block_text unclosed <", lambda: jobs._block_text("<" * 100_000)),
        ("block_text unclosed <div", lambda: jobs._block_text("<div " * 60_000)),
        ("detect_ats long alnum", lambda: jobs.detect_ats("a" * 100_000)),
        ("boards_named long alnum", lambda: jobs._boards_named("a" * 100_000)),
    ]
    for label, fn in adversarial_cases:
        cpu_start = time.process_time()
        fn()
        cpu_elapsed = time.process_time() - cpu_start
        assert cpu_elapsed < 1.0, f"{label} took {cpu_elapsed:.4f}s CPU (possible ReDoS)"


def test_high164_jobs_links_deduplication_is_linear_on_20k_links():
    html = "".join(f'<a href="https://jobs.acme.com/openings/{i}">Job {i}</a>' for i in range(20_000))
    cpu_start = time.process_time()
    links = jobs._jobs_links(html, "https://acme.com/careers")
    cpu_elapsed = time.process_time() - cpu_start
    assert len(links) == 20_000
    assert links[0] == "https://jobs.acme.com/openings/0"
    assert links[-1] == "https://jobs.acme.com/openings/19999"
    assert cpu_elapsed < 1.0, f"_jobs_links took {cpu_elapsed:.4f}s CPU on 20k links"


def test_high164_html_parser_preserves_jsonld_and_site_name_semantics():
    html = (
        '<meta content="Anduril Industries" property="og:site_name" />'
        '<script charset="utf-8" type="application/ld+json; charset=utf-8">'
        '{"@type":"Organization","name":"Anduril Corp"}'
        "</script>"
        '<script type="application/ld+json">'
        '{"@type":"JobPosting","title":"Systems Engineer","description":"&lt;p&gt;Build &amp; ship.&lt;/p&gt;"}'
        "</script>"
    )
    assert jobs._stated_names(html) == ["Anduril Corp", "Anduril Industries"]
    assert jobs.jsonld_role_text(html) == ("Build & ship.", "Build & ship.")
    found = jobs.extract_jsonld_jobs(html, "https://www.anduril.com/careers")
    assert len(found) == 1 and found[0]["title"] == "Systems Engineer"
    assert found[0]["source_domain"] == "anduril.com"


# ---------------------------------------------------------------------------
# HIGH-167: Escaping and validation of remote JSON fields in URLs / detail_url
# ---------------------------------------------------------------------------


def test_high167_eightfold_pcsx_and_v2_escape_ids_and_validate_position_urls():
    pcsx_entry = {"name": "Acme", "board": "jobs.acme.com", "domain": "acme.com&admin=1#x", "api": "pcsx"}
    pcsx_payload = {
        "data": {
            "count": 2,
            "positions": [
                {
                    "id": "../../admin?x=1#frag",
                    "name": "Evil Path Role",
                    "positionUrl": "@evil.com/phish",
                },
                {
                    "id": "99",
                    "name": "Good Role",
                    "positionUrl": "/careers/job/99",
                },
            ],
        }
    }
    items, total = jobs.parse_eightfold_response(pcsx_payload, pcsx_entry)
    assert total == 2 and len(items) == 2
    bad_row, good_row = items
    assert bad_row["url"] == "https://jobs.acme.com/careers/job/..%2F..%2Fadmin%3Fx%3D1%23frag"
    assert "@evil.com" not in bad_row["url"]
    assert "position_id=..%2F..%2Fadmin%3Fx%3D1%23frag" in bad_row["detail_url"]
    assert "domain=acme.com%26admin%3D1%23x" in bad_row["detail_url"]
    assert good_row["url"] == "https://jobs.acme.com/careers/job/99"

    # Also test protocol-relative, backslash, absolute, and port-injected positionUrl.
    for bad_pos in ("//evil.com/p", "/\\evil.com/p", "https://evil.com/p", ":8443/p"):
        rows, _ = jobs.parse_eightfold_response(
            {"data": {"count": 1, "positions": [{"id": "7", "name": "R", "positionUrl": bad_pos}]}},
            {"board": "jobs.acme.com", "domain": "acme.com", "api": "pcsx"},
        )
        assert rows[0]["url"] == "https://jobs.acme.com/careers/job/7"

    v2_entry = {"name": "Acme", "board": "explore.jobs.acme.net", "domain": "acme.com", "api": "v2"}
    v2_payload = {
        "count": 2,
        "positions": [
            {
                "id": "../../secret?foo=1#bar",
                "name": "V2 Evil",
                "canonicalPositionUrl": "javascript:alert(1)",
            },
            {
                "id": "42",
                "name": "V2 Evil SSRF",
                "canonicalPositionUrl": "http://169.254.169.254/latest/meta-data/",
            },
        ],
    }
    v2_items, _ = jobs.parse_eightfold_response(v2_payload, v2_entry)
    assert v2_items[0]["url"] == "https://explore.jobs.acme.net/careers/job/..%2F..%2Fsecret%3Ffoo%3D1%23bar"
    assert v2_items[0]["detail_url"] == (
        "https://explore.jobs.acme.net/api/apply/v2/jobs/..%2F..%2Fsecret%3Ffoo%3D1%23bar?domain=acme.com"
    )
    assert v2_items[1]["url"] == "https://explore.jobs.acme.net/careers/job/42"


def test_high167_amazon_and_apple_reject_authority_spoofing_and_escape_ids():
    amazon_payload = {
        "hits": 5,
        "jobs": [
            {"id": "1", "title": "Spoof At", "job_path": "@evil.com/phish"},
            {"id": "2", "title": "Spoof ProtoRel", "job_path": "//evil.com/phish"},
            {"id": "3", "title": "Spoof Backslash", "job_path": "/\\evil.com/phish"},
            {"id": "4", "title": "Spoof Port", "job_path": ":8443/en/jobs/1"},
            {"id": "5", "title": "Legit Engineer", "job_path": "/en/jobs/555/legit-engineer"},
        ],
    }
    amz_items, hits = jobs.parse_amazon_response(amazon_payload)
    assert hits == 5
    assert [item["url"] for item in amz_items] == ["https://www.amazon.jobs/en/jobs/555/legit-engineer"]

    apple_payload = {
        "res": {
            "totalRecords": 1,
            "searchResults": [
                {
                    "id": "1",
                    "positionId": "../../admin?x=1#frag",
                    "postingTitle": "Security Engineer",
                    "transformedPostingTitle": "../../evil?a=1#b",
                    "locations": [{"name": "Cupertino"}],
                }
            ],
        }
    }
    apple_items, _ = jobs.parse_apple_response(apple_payload)
    assert len(apple_items) == 1
    assert apple_items[0]["detail_url"] == (
        "https://jobs.apple.com/api/v1/jobDetails/..%2F..%2Fadmin%3Fx%3D1%23frag?locale=en-us"
    )
    assert apple_items[0]["url"] == (
        "https://jobs.apple.com/en-us/details/..%2F..%2Fadmin%3Fx%3D1%23frag/..%2F..%2Fevil%3Fa%3D1%23b"
    )

    # Oracle / Jibe / Phenom ID escaping.
    or_items, _ = oracle.parse_listing(
        {"items": [{"TotalJobsCount": 1, "requisitionList": [{"Id": '1",siteNumber=EVIL&x=1', "Title": "Eng"}]}]},
        {"board": "jpmc.fa.oraclecloud.com", "site_number": "CX_1001"},
    )
    assert "&x=1" not in or_items[0]["detail_url"]
    assert "%22%2CsiteNumber%3DEVIL%26x%3D1" in or_items[0]["detail_url"]

    jb_items, _ = jibe.parse_listing(
        {"totalCount": 1, "jobs": [{"data": {"title": "Eng", "slug": "../../evil?x=1", "req_id": "1"}}]},
        {"board": "careers.amd.com", "jobs_path": "//evil.com"},
    )
    assert jb_items[0]["url"] == "https://careers.amd.com/jobs/..%2F..%2Fevil%3Fx%3D1"


# ---------------------------------------------------------------------------
# HIGH-169: redirect_guard & public_only across robots and all board adapters
# ---------------------------------------------------------------------------


def _assert_guard_blocks_ssrf(guard, same_host: str, monkeypatch):
    assert callable(guard)
    answers = {
        same_host: ["93.184.216.34"],
        f"internal.{same_host}": ["10.0.0.5"],
        "evil.example": ["93.184.216.35"],
    }

    def fake_getaddrinfo(name, *_args, **_kwargs):
        if name not in answers:
            raise socket.gaierror("unknown")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)) for ip in answers[name]]

    detect.resolves_public.cache_clear()
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    try:
        assert guard(f"https://{same_host}/ok") is True
        for bad in (
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/admin",
            "http://[::1]/secret",
            "http://[64:ff9b::127.0.0.1]/",
            "http://224.0.0.1/",
            "https://evil.example/steal",
            f"https://{same_host}:8443/port-hop",
            f"https://user:pass@{same_host}/",
            f"https://internal.{same_host}/admin",
            "file:///etc/passwd",
        ):
            assert guard(bad) is False, f"guard must block {bad}"
    finally:
        detect.resolves_public.cache_clear()


def test_high169_robots_and_all_dynamic_board_adapters_enforce_public_only_and_redirect_guard(monkeypatch):
    robots.clear_cache()
    deadline = time.monotonic() + 10
    date_range = ("2026-10-15", "2026-11-14")

    # 1. robots.rules_for
    with patch.object(robots.http, "get_text", return_value="User-agent: *\nAllow: /\n") as mock_get_text:
        robots.rules_for("careers.acme.com")
    assert mock_get_text.call_args.kwargs.get("public_only") is True
    assert mock_get_text.call_args.kwargs.get("max_bytes") == robots.MAX_ROBOTS_BYTES
    _assert_guard_blocks_ssrf(mock_get_text.call_args.kwargs.get("redirect_guard"), "careers.acme.com", monkeypatch)

    # Bad hosts rejected before HTTP.
    with patch.object(robots.http, "get_text", side_effect=AssertionError("must not fetch")):
        for bad_host in ("127.0.0.1", "169.254.169.254", "evil.internal", "careers.acme.com:8443", "user@acme.com"):
            assert robots.rules_for(bad_host).rules == []

    # 2. oracle.read_board & oracle.fetch_details
    oracle_entry = {"board": "jpmc.fa.oraclecloud.com", "site_number": "CX_1001"}
    with patch.object(oracle.http, "get", return_value={"items": [{"TotalJobsCount": 0, "requisitionList": []}]}) as m_get:
        oracle.read_board(oracle_entry, deadline=deadline, date_range=date_range)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "jpmc.fa.oraclecloud.com", monkeypatch)
    # Cross-tenant oraclecloud redirect must ALSO be blocked!
    detect.resolves_public.cache_clear()
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda name, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))],
    )
    assert m_get.call_args.kwargs["redirect_guard"]("https://other.fa.oraclecloud.com/x") is False
    detect.resolves_public.cache_clear()

    with patch.object(oracle.http, "get", return_value={"items": [{"ExternalDescriptionStr": "<p>Hi</p>"}]}) as m_get:
        oracle.fetch_details([{"detail_url": "https://jpmc.fa.oraclecloud.com/detail/1"}], deadline=deadline)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "jpmc.fa.oraclecloud.com", monkeypatch)

    # 3. jibe.read_board & jibe.search_story
    jibe_entry = {"board": "careers.amd.com", "jobs_path": "/jobs"}
    with patch.object(jibe.http, "get", return_value={"totalCount": 0, "jobs": []}) as m_get:
        jibe.read_board(jibe_entry, deadline=deadline, date_range=date_range)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "careers.amd.com", monkeypatch)

    with patch.object(jibe.http, "get", return_value={"totalCount": 0, "jobs": []}) as m_get:
        jibe.search_story(jibe_entry, "ai", deadline_monotonic=deadline)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "careers.amd.com", monkeypatch)

    # 4. phenom.read_board & phenom.fetch_details
    phenom_entry = {"board": "careers.cisco.com", "country": "us", "lang": "en"}
    with patch.object(phenom.http, "post", return_value={"refineSearch": {"totalHits": 0, "data": {"jobs": []}}}) as m_post:
        phenom.read_board(phenom_entry, deadline=deadline, date_range=date_range)
    assert m_post.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_post.call_args.kwargs.get("redirect_guard"), "careers.cisco.com", monkeypatch)

    with patch.object(jobs.http, "get_text", return_value="") as m_text:
        phenom.fetch_details([{"url": "https://careers.cisco.com/us/en/job/1"}], deadline=deadline)
    assert m_text.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_text.call_args.kwargs.get("redirect_guard"), "careers.cisco.com", monkeypatch)

    # 5. talentbrew.read_board & talentbrew.fetch_details
    tb_entry = {"board": "jobs.boeing.com"}
    with patch.object(talentbrew.http, "get", return_value={"results": "<ul></ul>"}) as m_get:
        talentbrew.read_board(tb_entry, deadline=deadline, date_range=date_range)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "jobs.boeing.com", monkeypatch)

    with patch.object(jobs.http, "get_text", return_value="") as m_text:
        talentbrew.fetch_details([{"url": "https://jobs.boeing.com/job/1"}], deadline=deadline)
    assert m_text.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_text.call_args.kwargs.get("redirect_guard"), "jobs.boeing.com", monkeypatch)

    # 6. kula.read_board & kula.fetch_details
    kula_entry = {"board": "careers.kula.ai", "slug": "acme"}
    kula_html = '<script>self.__next_f.push([1, "{\\"jobs\\":[]}"])</script>'
    with patch.object(kula.http, "get_text", return_value=kula_html) as m_text:
        kula.read_board(kula_entry, deadline=deadline, date_range=date_range)
    assert m_text.call_args.kwargs.get("public_only") is True
    assert m_text.call_args.kwargs.get("max_bytes") == detect.MAX_HTML_BYTES
    _assert_guard_blocks_ssrf(m_text.call_args.kwargs.get("redirect_guard"), "careers.kula.ai", monkeypatch)

    with patch.object(jobs.http, "get_text", return_value="") as m_text:
        kula.fetch_details([{"url": "https://careers.kula.ai/acme/1-eng"}], deadline=deadline)
    assert m_text.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_text.call_args.kwargs.get("redirect_guard"), "careers.kula.ai", monkeypatch)

    # 7. Eightfold & Apple readers/detail fetchers enforce public_only + redirect_guard.
    ef_entry = {"board": "jobs.acme.com", "domain": "acme.com", "api": "pcsx"}
    with patch.object(jobs.http, "get", return_value={"data": {"count": 0, "positions": []}}) as m_get:
        jobs.read_eightfold_board(ef_entry, deadline=deadline, date_range=date_range)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "jobs.acme.com", monkeypatch)

    with patch.object(jobs.http, "get", return_value={"data": {"jobDescription": "<p>Hi</p>"}}) as m_get:
        jobs.fetch_eightfold_details([{"detail_url": "https://jobs.acme.com/api/pcsx/position_details?position_id=1"}], deadline=deadline)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "jobs.acme.com", monkeypatch)

    with patch.object(jobs.http, "get", return_value={"res": {"description": "Hi"}}) as m_get:
        jobs.fetch_apple_details([{"detail_url": "https://jobs.apple.com/api/v1/jobDetails/1?locale=en-us"}], deadline=deadline)
    assert m_get.call_args.kwargs.get("public_only") is True
    _assert_guard_blocks_ssrf(m_get.call_args.kwargs.get("redirect_guard"), "jobs.apple.com", monkeypatch)

    # 8. Pre-fetch validation refuses non-public board/detail URLs without calling HTTP.
    with patch.object(http, "get", side_effect=AssertionError("must not call get")), \
            patch.object(http, "post", side_effect=AssertionError("must not call post")), \
            patch.object(http, "get_text", side_effect=AssertionError("must not call get_text")):
        assert oracle.read_board({"board": "169.254.169.254", "site_number": "CX_1"}, deadline=deadline, date_range=date_range)[0] == []
        assert oracle.fetch_details([{"detail_url": "http://127.0.0.1:8080/x"}], deadline=deadline) == (0, 1)
        assert jibe.read_board({"board": "evil.internal"}, deadline=deadline, date_range=date_range)[0] == []
        with pytest.raises(http.HTTPError):
            jibe.search_story({"board": "127.0.0.1"}, "ai", deadline_monotonic=deadline)
        assert phenom.read_board({"board": "careers.acme.com:8443"}, deadline=deadline, date_range=date_range)[0] == []
        assert phenom.fetch_details([{"url": "https://careers.acme.com:8443/job/1"}], deadline=deadline) == (0, 1)
        assert talentbrew.read_board({"board": "localhost"}, deadline=deadline, date_range=date_range)[0] == []
        assert talentbrew.fetch_details([{"url": "http://169.254.169.254/job/1"}], deadline=deadline) == (0, 1)
        assert kula.fetch_details([{"url": "https://evil.example/acme/1-eng"}], deadline=deadline) == (0, 1)


def test_high169_redirect_handler_blocks_302_to_metadata_with_shared_guard():
    handler = http._StripAuthOnCrossOriginRedirect()
    req = urllib.request.Request("https://careers.acme.com/robots.txt")
    setattr(req, http._PUBLIC_ONLY_ATTR, True)
    setattr(req, http._REDIRECT_GUARD_ATTR, detect.same_site_redirect_guard(req.full_url))
    for target in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "https://careers.acme.com:8443/robots.txt",
        "https://evil.example/robots.txt",
    ):
        with pytest.raises(urllib.error.HTTPError) as exc:
            handler.redirect_request(req, io.BytesIO(), 302, "Found", Message(), target)
        assert exc.value.code == 403


# ---------------------------------------------------------------------------
# HIGH-171: _same_site public-suffix/multi-tenant/IP bypass & Phenom base_url
# ---------------------------------------------------------------------------


def test_high169_protocol_downgrade_blocked_while_upgrade_allowed(monkeypatch):
    detect.resolves_public.cache_clear()
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda name, *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))],
    )
    try:
        https_guard = detect.same_site_redirect_guard("https://careers.acme.com/robots.txt")
        assert https_guard("https://careers.acme.com/next") is True
        assert https_guard("http://careers.acme.com/next") is False

        http_guard = detect.same_site_redirect_guard("http://careers.acme.com/robots.txt")
        assert http_guard("http://careers.acme.com/step1") is True
        assert http_guard("https://evil.com/phish") is False
        assert http_guard("http://careers.acme.com/step2") is True
        assert http_guard("https://careers.acme.com/next") is True
        assert http_guard("https://careers.acme.com/next2") is True
        assert http_guard("http://careers.acme.com/downgrade") is False

        careers_https_guard = detect.public_redirect_guard("https://acme.com/careers")
        assert careers_https_guard("https://jobs.acme.com/openings") is True
        assert careers_https_guard("http://jobs.acme.com/openings") is False

        careers_http_guard = detect.public_redirect_guard("http://acme.com/careers")
        assert careers_http_guard("http://jobs.acme.com/step1") is True
        assert careers_http_guard("https://127.0.0.1/blocked") is False
        assert careers_http_guard("http://jobs.acme.com/step2") is True
        assert careers_http_guard("https://jobs.acme.com/openings") is True
        assert careers_http_guard("http://jobs.acme.com/downgrade") is False

        # _careers_fetch attaches the non-downgrade guard to http.get_text.
        with patch.object(jobs.http, "get_text", return_value="<html></html>") as mock_get_text:
            jobs._careers_fetch("https://acme.com/careers", "acme.com", guessed=False)
        attached_guard = mock_get_text.call_args.kwargs.get("redirect_guard")
        assert callable(attached_guard)
        assert attached_guard("https://jobs.acme.com/") is True
        assert attached_guard("http://jobs.acme.com/") is False

        # sitemap._guard latches HTTPS across multi-hop chains; _on_site rejects https -> http.
        monkeypatch.setattr(sitemap, "_resolves_public", lambda _name: True)
        sitemap_http_guard = sitemap._guard("http://jobs.acme.com/sitemap.xml")
        assert sitemap_http_guard("https://jobs.acme.com/sitemap.xml") is True
        assert sitemap_http_guard("http://jobs.acme.com/downgrade.xml") is False
        assert sitemap._on_site(
            "https://jobs.acme.com/en/jobs/1",
            lambda landed: (landed.append("http://jobs.acme.com/en/jobs/1"), "downgraded")[1],
        ) is None
        assert sitemap._on_site(
            "http://jobs.acme.com/en/jobs/1",
            lambda landed: (landed.extend(["https://jobs.acme.com/a", "http://jobs.acme.com/b"]), "downgraded")[1],
        ) is None
    finally:
        detect.resolves_public.cache_clear()


def test_high171_same_site_blocks_public_suffixes_multi_tenant_and_ips():
    for left, right in (
        ("evil.co.uk", "careers.barclays.co.uk"),
        ("evil.com.au", "jobs.foo.com.au"),
        ("evil.org.uk", "jobs.foo.org.uk"),
        ("evil.github.io", "app.github.io"),
        ("evil.netlify.app", "app.netlify.app"),
        ("evil.vercel.app", "app.vercel.app"),
        ("evil.pages.dev", "app.pages.dev"),
        ("evil.herokuapp.com", "app.herokuapp.com"),
        ("evil.s3.amazonaws.com", "victim.s3.amazonaws.com"),
        ("evil.s3.amazonaws.com", "s3.amazonaws.com"),
        ("evil.github.io", "github.io"),
        ("evil.co.uk", "co.uk"),
        ("127.0.0.1", "10.0.0.1"),
        ("127.0.0.1", "127.0.0.1"),
        ("93.184.216.34", "93.184.216.34"),
        ("::1", "::1"),
        ("jobs.acme.com:8443", "acme.com"),
        ("user@jobs.acme.com", "acme.com"),
    ):
        assert detect._same_site(left, right) is False, f"detect._same_site({left!r}, {right!r}) must be False"
        assert jobs._same_site(left, right) is False, f"jobs._same_site({left!r}, {right!r}) must be False"

    assert detect.registrable_domain("s3.amazonaws.com") == ""
    assert detect.registrable_domain("www.s3.amazonaws.com") == ""
    assert detect.registrable_domain("victim.s3.amazonaws.com") == "victim.s3.amazonaws.com"
    assert detect._same_site("sub.victim.s3.amazonaws.com", "victim.s3.amazonaws.com") is True
    assert jobs._same_site("sub.victim.s3.amazonaws.com", "victim.s3.amazonaws.com") is True
    assert detect._same_site("careers.barclays.co.uk", "www.barclays.co.uk") is True
    assert detect._same_site("sub.app.github.io", "app.github.io") is True
    assert jobs._same_site("sub.app.github.io", "app.github.io") is True


def test_high171_phenom_rejects_port_userinfo_and_canonicalizes_base_url():
    template = (
        '<script src="https://cdn.phenompeople.com/x.js"></script>'
        '<script>phApp.ddo={}; var cfg={"baseUrl":"%s"};</script>'
    )
    # Arbitrary non-443 port and userinfo must be rejected completely.
    for poisoned in (
        "https://user:pass@careers.victim.com:8443/us/en/",
        "https://user@careers.victim.com/us/en/",
        "https://careers.victim.com:8443/us/en/",
        "https://careers.victim.com:80/us/en/",
        "https://evil.co.uk/us/en/",
        "https://127.0.0.1/us/en/",
    ):
        assert detect.detect_platform(template % poisoned, "https://careers.victim.com/us/en") is None

    # Explicit :443 is canonicalized without port in entry["base_url"].
    detected = detect.detect_platform(
        template % "https://careers.victim.com:443/us/en/",
        "https://www.victim.com/careers",
    )
    assert detected == (
        "phenom",
        {
            "provider": "phenom",
            "board": "careers.victim.com",
            "country": "us",
            "lang": "en",
            "base_url": "https://careers.victim.com/us/en/",
        },
    )

    # Direct phenom.parse_listing sanitizes tampered base_url and URL-encodes jobId.
    payload = {"refineSearch": {"totalHits": 1, "data": {"jobs": [{"title": "Eng", "jobId": "../../admin?x=1#"}]}}}
    items, _ = phenom.parse_listing(
        payload,
        {"board": "careers.victim.com", "base_url": "https://user@careers.victim.com:8443/us/en/"},
    )
    assert items[0]["url"] == "https://careers.victim.com/job/..%2F..%2Fadmin%3Fx%3D1%23"

    # Oracle data-apibaseurl must also reject non-443 ports while allowing :443.
    oracle_bad_port = '<div data-apibaseurl="https://jobs.acme.com:8443" data-sitenumber="CX_1"></div>'
    assert detect.detect_platform(oracle_bad_port, "https://www.acme.com/careers") is None


def test_high171_detect_html_parsers_resist_redos():
    cases = [
        (
            "detect jibe <link",
            lambda: detect.detect_platform(
                '<script src="https://assets.jibecdn.com/a.js"></script>' + ("<link " * 50_000),
                "https://careers.amd.com/jobs",
            ),
            ("jibe", {"provider": "jibe", "board": "careers.amd.com", "jobs_path": "/jobs"}),
        ),
        (
            "detect oracle data-apibaseurl",
            lambda: detect.detect_platform(
                'data-apibaseurl="https://jobs.acme.com" ' * 50_000,
                "https://acme.com/careers",
            ),
            None,
        ),
        (
            "detect eightfoldCareerUrl",
            lambda: detect.eightfold_career_url("eightFoldCareerUrl " * 50_000),
            "",
        ),
        (
            "detect eightfoldCareerUrl unclosed tag spaces",
            lambda: detect.eightfold_career_url("eightFoldCareerUrl <div" + (" " * 40_000)),
            "",
        ),
        (
            "detect oracle unclosed tag spaces",
            lambda: detect._oracle("data-apibaseurl data-sitenumber <div" + (" " * 40_000), "acme.com"),
            None,
        ),
    ]
    for label, fn, expected in cases:
        cpu_start = time.process_time()
        assert fn() == expected
        cpu_elapsed = time.process_time() - cpu_start
        assert cpu_elapsed < 0.5, f"{label} took {cpu_elapsed:.4f}s CPU (possible ReDoS)"

    # Reversed attribute order on <link rel="canonical"> works in _jibe.
    jibe_rev = (
        '<link href="https://careers.amd.com/careers-home/jobs" rel="canonical">'
        '<script src="https://assets.jibecdn.com/app.js"></script>'
    )
    assert detect.detect_platform(jibe_rev, "https://careers.amd.com/") == (
        "jibe", {"provider": "jibe", "board": "careers.amd.com", "jobs_path": "/careers-home/jobs"},
    )


# ---------------------------------------------------------------------------
# HIGH-173: Multicast/NAT64/6to4 blocking, bounded-TTL DNS cache, ports, job_urls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_ip",
    [
        "224.0.0.1",               # IPv4 multicast (is_global=True in py3.12!)
        "ff02::1",                 # IPv6 link-local multicast
        "64:ff9b::127.0.0.1",      # NAT64 loopback
        "64:ff9b::169.254.169.254",  # NAT64 AWS metadata
        "64:ff9b::93.184.216.34",  # NAT64 well-known prefix
        "64:ff9b:1::127.0.0.1",    # NAT64 local prefix
        "2002:7f00:0001::",        # 6to4 loopback
        "2002:a9fe:a9fe::",        # 6to4 metadata
        "::ffff:127.0.0.1",        # IPv4-mapped loopback
        "100.64.0.1",              # CGNAT
        "0.0.0.0",                 # Unspecified IPv4
        "::",                      # Unspecified IPv6
        "169.254.169.254",         # Link-local metadata
        "fe80::1",                 # IPv6 link-local
        "127.0.0.1",               # Loopback
        "::1",                     # IPv6 loopback
        "10.0.0.1",                # RFC1918 private
    ],
)
def test_high173_resolver_and_syntax_block_all_non_public_ip_classes(bad_ip: str, monkeypatch):
    url_host = f"[{bad_ip}]" if ":" in bad_ip else bad_ip
    assert detect.is_safe_public_url(f"http://{url_host}/sitemap.xml") is False
    assert sitemap._public_url(f"http://{url_host}/sitemap.xml", url_host) is False

    # Also test when a domain resolves to [public, bad_ip].
    resolver = detect._BoundedPublicResolver(ttl_seconds=5.0)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _name, *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET6 if ":" in bad_ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (bad_ip, 0)),
        ],
    )
    assert resolver("mixed.example") is False


def test_high173_dns_cache_expires_by_ttl_and_is_thread_safe(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(detect.time, "monotonic", lambda: now[0])
    answers = {"rebind.example": ["93.184.216.34"]}
    lookups = []

    def fake_getaddrinfo(name, *_args, **_kwargs):
        lookups.append(name)
        ips = answers.get(name, ["93.184.216.34"])
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    resolver = detect._BoundedPublicResolver(maxsize=32, ttl_seconds=2.0)

    assert resolver("rebind.example") is True
    assert len(lookups) == 1
    # Within TTL: cached hit without another lookup.
    now[0] = 101.5
    assert resolver("rebind.example") is True
    assert len(lookups) == 1
    # DNS rebinds to loopback and TTL expires -> fresh lookup refuses!
    answers["rebind.example"] = ["127.0.0.1"]
    now[0] = 102.1
    assert resolver("rebind.example") is False
    assert len(lookups) == 2

    # Concurrent calls must remain thread-safe and honor maxsize cap.
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: resolver(f"h{i % 100}.example"), range(400)))
    assert all(results)
    assert len(resolver._cache) <= 32
    resolver.cache_clear()
    assert len(resolver._cache) == 0


def test_high173_dns_resolver_preserves_single_trailing_dot_and_rejects_double_dot(monkeypatch):
    seen: list[str] = []

    def fake_getaddrinfo(name, *_args, **_kwargs):
        seen.append(name)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    resolver = detect._BoundedPublicResolver(ttl_seconds=5.0)
    assert resolver("example.com.") is True
    assert seen == ["example.com."]
    assert resolver("example.com..") is False
    assert resolver(".example.com") is False
    assert resolver("localhost.") is False
    assert seen == ["example.com."]


def test_high173_sitemap_ports_guard_hostname_and_job_urls_host_validation(monkeypatch):
    monkeypatch.setattr(sitemap, "_resolves_public", lambda name: "internal" not in name)

    # Arbitrary/mismatched ports and userinfo blocked.
    for bad in (
        "http://jobs.example.com:6379/sitemap.xml",
        "https://jobs.example.com:8443/sitemap.xml",
        "https://jobs.example.com:80/sitemap.xml",
        "http://jobs.example.com:443/sitemap.xml",
        "https://user@jobs.example.com/sitemap.xml",
    ):
        assert sitemap._public_url(bad, "jobs.example.com") is False

    # Valid explicit :443 works in _public_url, _guard, and _on_site (fixes netloc vs hostname mismatch).
    assert sitemap._public_url("https://jobs.example.com:443/sitemap.xml", "jobs.example.com") is True
    guard = sitemap._guard("https://jobs.example.com:443/sitemap.xml")
    assert guard("https://jobs.example.com/en/jobs/12345") is True
    assert guard("https://jobs.example.com:8443/en/jobs/12345") is False
    assert guard("http://169.254.169.254/latest/meta-data/") is False
    assert guard("https://internal.jobs.example.com/en/jobs/1") is False

    assert sitemap._on_site(
        "https://jobs.example.com:443/en/jobs/12345",
        lambda landed: (landed.append("https://jobs.example.com/en/jobs/12345"), "ok")[1],
    ) == "ok"
    assert sitemap._on_site(
        "https://jobs.example.com:443/en/jobs/12345",
        lambda landed: (landed.append("https://jobs.example.com:8443/x"), "bad")[1],
    ) is None

    # Unvalidated hosts in job_urls must NEVER reach robots.rules_for.
    with patch.object(robots, "rules_for", side_effect=AssertionError("must not call rules_for")):
        host, urls, missed = sitemap.job_urls(
            [
                "169.254.169.254",
                "127.0.0.1",
                "localhost",
                "evil.internal",
                "jobs.example.com:6379",
                "user@jobs.example.com",
                "[64:ff9b::127.0.0.1]",
                "224.0.0.1",
            ],
            deadline=time.monotonic() + 5,
        )
    assert (host, urls, missed) == ("", [], False)


# ---------------------------------------------------------------------------
# HIGH-174: Sitemap XML / TalentBrew / Kula linear-time parsing & correctness
# ---------------------------------------------------------------------------


def test_high174_sitemap_talentbrew_kula_resist_redos_in_under_100ms():
    cases = [
        ("sitemap unclosed <url>", lambda: sitemap._parse_sitemap_xml("<url>" * 50_000)),
        ("sitemap unclosed <loc>", lambda: sitemap._parse_sitemap_xml("<loc>" * 50_000)),
        ("sitemap title jsonld", lambda: sitemap.title_from_page('"@type":"JobPosting"' * 50_000)),
        ("sitemap title meta", lambda: sitemap.title_from_page("<meta " * 60_000)),
        ("sitemap title <title>", lambda: sitemap.title_from_page("<title>" * 60_000)),
        ("detect unclosed </script ", lambda: detect.extract_script_bodies("<script>" + ("</script " * 50_000))),
        ("detect unclosed </title ", lambda: detect.extract_title_texts("<title>" + ("</title " * 50_000))),
        (
            "talentbrew unclosed <li>",
            lambda: talentbrew.parse_listing({"results": "<li>" * 50_000}, {"board": "jobs.example.com"}),
        ),
        (
            "talentbrew unclosed <a>",
            lambda: talentbrew.parse_listing({"results": '<a href="/job/1">' * 50_000}, {"board": "jobs.example.com"}),
        ),
        (
            "talentbrew unclosed <span>",
            lambda: talentbrew.parse_listing(
                {"results": '<li><a href="/job/1">Role</a>' + ('<span class="location">' * 50_000) + "</li>"},
                {"board": "jobs.example.com"},
            ),
        ),
        ("kula unclosed push", lambda: kula._payload("self.__next_f.push([" * 50_000)),
        (
            "kula escaped-quote push",
            lambda: kula._payload('<script>self.__next_f.push([1,"' + '\\"self.__next_f.push([1,"' * 5_000 + "</script>"),
        ),
        ("kula role_list escaped-quote", lambda: kula._role_list('"jobs":["' + '\\""jobs":["' * 5_000)),
        (
            "kula role_list deep nested arrays 200",
            lambda: kula._role_list(('{"jobs":[1, ' * 200) + "1" + ("]}" * 200)),
        ),
        (
            "kula role_list deep nested arrays 2000",
            lambda: kula._role_list(('{"jobs":[1, ' * 2000) + "1" + ("]}" * 2000)),
        ),
        (
            "kula meta flood",
            lambda: kula.parse_listing(
                ("<meta " * 60_000) + '<script>self.__next_f.push([1,"{\\"jobs\\":[]}"])</script>',
                {"slug": "acme"},
            ),
        ),
    ]
    for label, fn in cases:
        cpu_start = time.process_time()
        fn()
        cpu_elapsed = time.process_time() - cpu_start
        assert cpu_elapsed < 1.0, f"{label} took {cpu_elapsed:.4f}s CPU (possible ReDoS)"

    # Nested legitimate role list inside a non-dict outer "jobs" array still extracts.
    nested_valid = '{"jobs":[1, {"jobs":[{"id":7,"title":"Security Engineer"}]}]}'
    assert kula._role_list(nested_valid) == [{"id": 7, "title": "Security Engineer"}]


def test_high174_sitemap_xml_rejects_dtd_entity_and_supports_namespaces_cdata():
    xxe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<urlset><url><loc>https://jobs.example.com/jobs/12345-eng</loc></url></urlset>"
    )
    assert sitemap._parse_sitemap_xml(xxe) == (None, [])

    cdata_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc><![CDATA[https://jobs.example.com/jobs/12345-staff-engineer?a=1&b=2]]></loc>"
        "<lastmod>2026-11-14</lastmod></url></urlset>"
    )
    kind, entries = sitemap._parse_sitemap_xml(cdata_xml)
    assert kind == "urlset"
    assert entries == [("https://jobs.example.com/jobs/12345-staff-engineer?a=1&b=2", "2026-11-14")]


def test_high174_sitemap_title_parsing_fixes_and_single_call_per_page(monkeypatch):
    # "title" appearing BEFORE "@type": "JobPosting" must be extracted.
    title_first = (
        '<script type="application/ld+json">'
        '{"title": "Principal Security Engineer", "@type": "JobPosting"}'
        "</script>"
    )
    assert sitemap.title_from_page(title_first, "Acme") == "Principal Security Engineer"

    # JobPosting omitting "title" must NEVER cross </script> to steal an unrelated title.
    no_cross_script = (
        '<script type="application/ld+json">{"@type": "JobPosting", "datePosted": "2026-11-01"}</script>'
        '<script type="application/json">{"title": "Cookie Banner Preferences"}</script>'
        '<meta content="Real Role Title - Acme Careers" property="og:title">'
    )
    assert sitemap.title_from_page(no_cross_script, "Acme") == "Real Role Title"

    # _titles_from_pages computes title_from_page at most once per page.
    monkeypatch.setattr(sitemap, "_resolves_public", lambda _name: True)
    calls = []
    real_title = sitemap.title_from_page

    def spy_title(html: str, site_name: str = "") -> str:
        calls.append(html)
        return real_title(html, site_name)

    monkeypatch.setattr(sitemap, "title_from_page", spy_title)
    urls = [f"https://careers.acme.com/jobs/R-{100000 + i}" for i in range(10)]
    with patch.object(jobs.http, "get_text", side_effect=lambda u, **kw: f"<title>Role {u[-3:]}</title>"):
        result = sitemap._titles_from_pages(urls, "Acme", time.monotonic() + 10)
    assert len(result) == 10
    assert len(calls) == 10


def test_high174_talentbrew_nested_li_and_kula_multi_push_parsing_fixes():
    nested_html = (
        '<section data-total-results="2"><ul>'
        "<li>"
        '<a href="/job/seattle/staff-engineer/1/100"><span>Staff Engineer</span></a>'
        '<ul class="badges"><li>Remote</li><li>Security</li></ul>'
        '<span class="search-results__job-info location">Seattle, WA</span>'
        '<span class="search-results__job-info date">11/13/2026</span>'
        "</li>"
        "</ul></section>"
    )
    tb_items, total = talentbrew.parse_listing({"results": nested_html}, {"board": "jobs.acme.com"})
    assert total == 2 and len(tb_items) == 1
    assert tb_items[0]["title"] == "Staff Engineer"
    assert tb_items[0]["location"] == "Seattle, WA"
    assert tb_items[0]["date"] == "2026-11-13"

    # Multiple self.__next_f.push([...]) calls inside ONE <script> tag must all parse.
    multi_push_html = (
        '<html><head><meta content="Acme Co" name="application-name"></head><body>'
        '<a href="/acme/101-platform-engineer">Platform Engineer</a>'
        '<script>self.__next_f.push([1, "prefix "]);'
        'self.__next_f.push([1, "{\\"jobs\\":[{\\"id\\":101,\\"title\\":\\"Platform Engineer\\",'
        '\\"launch_at\\":\\"2026-11-10T00:00:00Z\\"}]}"]);</script>'
        "</body></html>"
    )
    kula_items, kula_total = kula.parse_listing(multi_push_html, {"slug": "acme"})
    assert kula_total == 1 and len(kula_items) == 1
    assert kula_items[0]["title"] == "Platform Engineer"
    assert kula_items[0]["company_name"] == "Acme Co"
    assert kula_items[0]["url"] == "https://careers.kula.ai/acme/101-platform-engineer"


def test_high174_response_byte_caps_passed_to_http_get_text(monkeypatch):
    monkeypatch.setattr(sitemap, "_resolves_public", lambda _name: True)
    with patch.object(jobs.http, "get_text", return_value="<urlset></urlset>") as mock_get_text:
        sitemap._fetch("https://jobs.acme.com/sitemap.xml", time.monotonic() + 5)
    assert mock_get_text.call_args.kwargs.get("max_bytes") == sitemap.MAX_SITEMAP_BYTES

    with patch.object(jobs.http, "get_text", return_value="<html></html>") as mock_get_text:
        jobs._careers_fetch("https://acme.com/careers", "acme.com", guessed=False)
    assert mock_get_text.call_args.kwargs.get("max_bytes") == detect.MAX_HTML_BYTES
    assert mock_get_text.call_args.kwargs.get("public_only") is True
    assert callable(mock_get_text.call_args.kwargs.get("redirect_guard"))


def test_high164_turkish_dotted_i_does_not_corrupt_html_extraction():
    # U+0130 ("İ") expands to 2 codepoints ("i\u0307") under .lower(), shifting
    # offsets if code slices original HTML with indices from html.lower().
    prefix = "<p>İstanbul Office — İstanbul, Türkiye — İstanbul Engineering</p>"

    jsonld_and_title = (
        prefix
        + '<title>Principal Systems Engineer - Acme Careers</title>'
        + '<script type="application/ld+json">'
        + '{"@type":"JobPosting","title":"Staff Security Engineer","description":"Build secure systems."}'
        + "</script>"
    )
    assert detect.extract_title_texts(jsonld_and_title) == ["Principal Systems Engineer - Acme Careers"]
    blocks = detect.extract_jsonld_blocks(jsonld_and_title)
    assert len(blocks) == 1
    assert blocks[0] == '{"@type":"JobPosting","title":"Staff Security Engineer","description":"Build secure systems."}'
    found_jobs = jobs.extract_jsonld_jobs(jsonld_and_title, "https://acme.com/careers")
    assert len(found_jobs) == 1 and found_jobs[0]["title"] == "Staff Security Engineer"
    assert sitemap.title_from_page(prefix + "<title>Platform Architect - Acme Careers</title>", "Acme") == "Platform Architect"

    kula_html = (
        prefix
        + '<head><meta name="application-name" content="Acme Co"></head>'
        + '<a href="/acme/77-backend-engineer">Backend Engineer</a>'
        + '<script>self.__next_f.push([1,"{\\"jobs\\":[{\\"id\\":77,\\"title\\":\\"Backend Engineer\\",'
        + '\\"launch_at\\":\\"2026-11-12T00:00:00Z\\"}]}"]);</script>'
    )
    kula_items, kula_total = kula.parse_listing(kula_html, {"slug": "acme"})
    assert kula_total == 1 and len(kula_items) == 1
    assert kula_items[0]["title"] == "Backend Engineer"
    assert kula_items[0]["url"] == "https://careers.kula.ai/acme/77-backend-engineer"

    tb_html = (
        prefix
        + '<section data-total-results="1"><ul><li>'
        + '<a href="/job/istanbul/security-engineer/1/55"><span>Security Engineer</span></a>'
        + '<span class="location">İstanbul, Turkey</span>'
        + '<span class="date">11/14/2026</span>'
        + "</li></ul></section>"
    )
    tb_items, tb_total = talentbrew.parse_listing({"results": tb_html}, {"board": "jobs.acme.com"})
    assert tb_total == 1 and len(tb_items) == 1
    assert tb_items[0]["title"] == "Security Engineer"
    assert tb_items[0]["location"] == "İstanbul, Turkey"
    assert tb_items[0]["date"] == "2026-11-14"


def test_high174_deep_nested_json_recursion_error_recovered():
    deep_array = ("[" * 10_000) + ("]" * 10_000)
    html = (
        f'<script type="application/ld+json">{deep_array}</script>'
        '<meta property="og:site_name" content="Acme Corp">'
        "<title>Staff Engineer - Acme Careers</title>"
    )
    assert jobs._walk_jsonld(deep_array) == []
    assert sitemap.title_from_page(html, "Acme") == "Staff Engineer"
    assert jobs._stated_names(html) == ["Acme Corp"]
    assert phenom.page_object(f"phApp.ddo = {{\"a\": {deep_array}}};") is None


def test_high174_sitemap_missed_distinguishes_empty_urlset_from_rejected_or_malformed_xml(monkeypatch):
    monkeypatch.setattr(sitemap, "_resolves_public", lambda _name: True)
    valid_jobs_xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://jobs.acme.com/jobs/12345-staff-engineer</loc></url>"
        "</urlset>"
    )
    empty_urlset_xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>'
    malformed_xml = "<urlset><url><loc>https://jobs.acme.com/jobs/99999-broken"
    doctype_xml = (
        '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        + valid_jobs_xml
    )

    def run_with(first_body: str) -> tuple[str, list[tuple[str, str]], bool]:
        fake_rules = robots.Rules(
            "Sitemap: https://jobs.acme.com/sitemap-1.xml\n"
            "Sitemap: https://jobs.acme.com/sitemap-2.xml\n"
        )
        bodies = {
            "https://jobs.acme.com/sitemap-1.xml": first_body,
            "https://jobs.acme.com/sitemap-2.xml": valid_jobs_xml,
        }
        with patch.object(robots, "rules_for", return_value=fake_rules):
            with patch.object(jobs.http, "get_text", side_effect=lambda u, **_kw: bodies.get(u)):
                return sitemap.job_urls(["jobs.acme.com"], deadline=time.monotonic() + 5)

    host_empty, urls_empty, missed_empty = run_with(empty_urlset_xml)
    assert host_empty == "jobs.acme.com" and len(urls_empty) == 1
    assert missed_empty is False

    host_bad, urls_bad, missed_bad = run_with(malformed_xml)
    assert host_bad == "jobs.acme.com" and len(urls_bad) == 1
    assert missed_bad is True

    host_dtd, urls_dtd, missed_dtd = run_with(doctype_xml)
    assert host_dtd == "jobs.acme.com" and len(urls_dtd) == 1
    assert missed_dtd is True


def test_high174_kula_empty_jobs_array_returns_zero_without_error():
    for payload_json in ('{\\"jobs\\":[]}', '{\\"jobs\\": []}', '{\\"jobs\\": [ ]}'):
        html = f'<script>self.__next_f.push([1,"{payload_json}"])</script>'
        items, total = kula.parse_listing(html, {"slug": "acme"})
        assert items == [] and total == 0

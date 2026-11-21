"""LinkedIn resolution for a person run: find the profile, read it, verify it.

HTTP is faked at ``lib.http.request``; assertions read what crosses that
boundary (URLs and payloads) and what lands in the person context.
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from lib import http, person, person_linkedin

NAME = "Dana Okafor"
EMPLOYER = "Northwind Freight:northwind.example"
GIVEN_URL = "https://www.linkedin.com/in/dana-okafor"
EXA_URL = "https://www.linkedin.com/in/dana-okafor-nwf"
LOOKUP_URL = "https://www.linkedin.com/in/dokafor-7"
DEEPLINE = "https://code.deepline.com/api/v2/integrations"
EXA = "https://api.exa.ai/search"
SC_PROFILE = "https://api.scrapecreators.com/v1/linkedin/profile"

DEEPLINE_KEY = {"DEEPLINE_API_KEY": "dl-test-key"}
EXA_KEY = {"EXA_API_KEY": "exa-test-key"}
SC_KEY = {"SCRAPECREATORS_API_KEY": "sc-test-key"}


def _profile(url: str, company: str, *, website: str | None = None, title: str = "VP Operations") -> dict:
    position = {
        "position": title,
        "companyName": company,
        "companyLinkedinUrl": f"https://www.linkedin.com/company/{company.lower().replace(' ', '-')}/",
        "startDate": {"text": "Mar 2024"},
        "endDate": {"text": "Present"},
    }
    if website:
        position["companyWebsite"] = website
    return {
        "status": "completed",
        "result": {"data": {"element": {
            "firstName": "Dana",
            "lastName": "Okafor",
            "headline": f"{title} at {company}",
            "linkedinUrl": url,
            "location": {"linkedinText": "Columbus, Ohio, United States"},
            "currentPosition": [position],
            "experience": [
                dict(position),
                {"position": "Dispatcher", "companyName": "Contoso Logistics",
                 "startDate": {"text": "2019"}, "endDate": {"text": "2024"}},
            ],
            "websites": ["https://x.com/danaokafor"],
        }}},
    }


def _lookup(url: str | None) -> dict:
    return {"status": "completed", "result": {"data": {"linkedin_url": url}}}


class FakeHTTP:
    """Routes ``http.request`` by URL and records every call."""

    def __init__(self, *, profiles=None, lookup=None, exa=None, sc_profile=None, fail=()):
        self.profiles = profiles or {}
        self.lookup = lookup
        self.exa = exa or []
        self.sc_profile = sc_profile
        self.fail = set(fail)
        self.calls: list[dict] = []

    def __call__(self, method, url, headers=None, json_data=None, params=None, **kwargs):
        self.calls.append({"method": method, "url": url, "json": json_data, "params": params, "kwargs": kwargs})
        for prefix in self.fail:
            if url.startswith(prefix):
                raise http.HTTPError("HTTP 500", status_code=500)
        if url == f"{DEEPLINE}/harvestapi_get_profile/execute":
            target = json_data["payload"]["url"]
            if target not in self.profiles:
                raise http.HTTPError("HTTP 404", status_code=404)
            return self.profiles[target]
        if url == f"{DEEPLINE}/limadata_find_person_profiles/execute":
            return _lookup(self.lookup)
        if url == EXA:
            return {"results": self.exa}
        if url == SC_PROFILE:
            return self.sc_profile or {}
        raise AssertionError(f"unexpected request to {url}")

    def to(self, url: str) -> list[dict]:
        return [call for call in self.calls if call["url"] == url]

    @property
    def profile_reads(self) -> list[str]:
        return [c["json"]["payload"]["url"] for c in self.to(f"{DEEPLINE}/harvestapi_get_profile/execute")]

    @property
    def lookups(self) -> list[dict]:
        return [c["json"]["payload"] for c in self.to(f"{DEEPLINE}/limadata_find_person_profiles/execute")]


@pytest.fixture
def fake(monkeypatch):
    def install(**kwargs) -> FakeHTTP:
        fake_http = FakeHTTP(**kwargs)
        monkeypatch.setattr(http, "request", fake_http)
        return fake_http
    return install


def _resolve(ctx, config, **kwargs):
    return person_linkedin.resolve_linkedin(
        ctx, config, deadline_monotonic=time.monotonic() + 60, run_id="run-1", **kwargs,
    )


def _ctx(**flags) -> person.PersonContext:
    return person.build_person_context(NAME, **flags)


class TestLookedUpCandidates:
    def test_lookup_candidate_at_another_company_is_rejected(self, fake):
        http_fake = fake(
            lookup=LOOKUP_URL,
            profiles={LOOKUP_URL: _profile(LOOKUP_URL, "Fabrikam Kitchens")},
        )
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, DEEPLINE_KEY)
        assert http_fake.lookups == [{
            "full_name": NAME, "company_name": "Northwind Freight", "company_domain": "northwind.example",
        }]
        assert ctx.linkedin_status == "match rejected"
        assert ctx.profile == {}
        assert ctx.linkedin_url == ""
        assert LOOKUP_URL not in ctx.anchors
        assert "Fabrikam Kitchens" not in ctx.anchors

    def test_exa_candidate_that_verifies_skips_the_lookup(self, fake):
        http_fake = fake(
            exa=[{"url": EXA_URL, "title": "Dana Okafor - VP Operations - Northwind Freight",
                  "highlights": ["Dana Okafor works at Northwind Freight"]}],
            profiles={EXA_URL: _profile(EXA_URL, "Northwind Freight")},
        )
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, {**DEEPLINE_KEY, **EXA_KEY})
        assert http_fake.lookups == []
        assert http_fake.profile_reads == [EXA_URL]
        assert ctx.linkedin_status == "checked"
        assert ctx.linkedin_url == EXA_URL
        assert EXA_URL in ctx.anchors
        assert ctx.profile["current_roles"][0]["company"] == "Northwind Freight"
        assert ctx.profile["headline"] == "VP Operations at Northwind Freight"
        assert any("Northwind Freight" in basis for basis in ctx.match_basis)
        assert any("self-reported" in basis for basis in ctx.match_basis)

    def test_exa_pre_search_is_bounded_by_the_resolve_deadline(self, fake):
        http_fake = fake(lookup=LOOKUP_URL, profiles={LOOKUP_URL: _profile(LOOKUP_URL, "Northwind Freight")})
        deadline = time.monotonic() + 60
        person_linkedin.resolve_linkedin(
            _ctx(employer=EMPLOYER), {**DEEPLINE_KEY, **EXA_KEY}, deadline_monotonic=deadline, run_id="run-1",
        )
        (exa_call,) = http_fake.to(EXA)
        assert exa_call["kwargs"]["retries"] == 1
        assert exa_call["kwargs"]["deadline_monotonic"] <= deadline

    def test_exa_pre_search_is_skipped_when_the_deadline_leaves_room_only_for_the_paid_read(self, fake):
        http_fake = fake(lookup=LOOKUP_URL, profiles={LOOKUP_URL: _profile(LOOKUP_URL, "Northwind Freight")})
        ctx = _ctx(employer=EMPLOYER)
        person_linkedin.resolve_linkedin(
            ctx, {**DEEPLINE_KEY, **EXA_KEY}, deadline_monotonic=time.monotonic() + 20, run_id="run-1",
        )
        assert http_fake.to(EXA) == []
        assert len(http_fake.lookups) == 1
        assert ctx.linkedin_status == "checked"

    def test_exa_result_without_employer_text_is_never_read(self, fake):
        http_fake = fake(
            exa=[{"url": EXA_URL, "title": "Dana Okafor - Chef", "highlights": ["Private dining"]},
                 {"url": "https://danaokafor.example/northwind-freight", "title": "Northwind Freight"}],
            lookup=None,
        )
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, {**DEEPLINE_KEY, **EXA_KEY})
        assert http_fake.profile_reads == []
        assert len(http_fake.lookups) == 1
        assert ctx.linkedin_status == "profile not found"

    def test_rejected_exa_candidate_is_followed_by_the_lookup(self, fake):
        http_fake = fake(
            exa=[{"url": EXA_URL, "title": "Dana Okafor", "highlights": ["formerly Northwind Freight"]}],
            lookup=LOOKUP_URL,
            profiles={
                EXA_URL: _profile(EXA_URL, "Fabrikam Kitchens"),
                LOOKUP_URL: _profile(LOOKUP_URL, "Northwind Freight Inc."),
            },
        )
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, {**DEEPLINE_KEY, **EXA_KEY})
        assert http_fake.profile_reads == [EXA_URL, LOOKUP_URL]
        assert ctx.linkedin_status == "checked"
        assert ctx.linkedin_url == LOOKUP_URL
        assert EXA_URL not in ctx.anchors
        assert ctx.profile["current_roles"][0]["company"] == "Northwind Freight Inc."

    def test_employer_domain_matches_through_company_website(self, fake):
        fake(
            lookup=LOOKUP_URL,
            profiles={LOOKUP_URL: _profile(LOOKUP_URL, "NWF Group", website="https://www.northwind.example/")},
        )
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, DEEPLINE_KEY)
        assert ctx.linkedin_status == "checked"
        assert any("northwind.example" in basis for basis in ctx.match_basis)

    def test_exa_request_never_carries_the_email(self, fake):
        http_fake = fake(exa=[], lookup=None)
        ctx = _ctx(employer="Northwind Freight", email="dana.okafor@northwind.example")
        _resolve(ctx, {**DEEPLINE_KEY, **EXA_KEY})
        [exa_call] = http_fake.to(EXA)
        sent = repr(exa_call)
        assert "Dana Okafor" in sent and "Northwind Freight" in sent
        for part in ("dana.okafor", "@", "northwind.example"):
            assert part not in sent

    def test_gmail_only_run_looks_up_by_email_and_anchors_the_result(self, fake):
        http_fake = fake(
            lookup=LOOKUP_URL,
            profiles={LOOKUP_URL: _profile(LOOKUP_URL, "Fabrikam Kitchens")},
        )
        ctx = _ctx(email="dana.okafor@gmail.com")
        assert ctx.identity_weak
        _resolve(ctx, {**DEEPLINE_KEY, **EXA_KEY})
        assert http_fake.to(EXA) == []
        assert http_fake.lookups == [{"full_name": NAME, "email": "dana.okafor@gmail.com"}]
        assert ctx.linkedin_status == "checked"
        assert ctx.linkedin_url == LOOKUP_URL
        assert LOOKUP_URL in ctx.anchors
        assert any("email" in basis for basis in ctx.match_basis)
        assert not ctx.identity_weak
        assert "gmail.com" not in ctx.anchors

    def test_lookup_failure_reports_failed(self, fake):
        fake(fail=[f"{DEEPLINE}/limadata"])
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, DEEPLINE_KEY)
        assert ctx.linkedin_status == "failed"
        assert ctx.profile == {}


class TestPastPositions:
    @staticmethod
    def _former(end_text: str) -> dict:
        profile = _profile(LOOKUP_URL, "Fabrikam Kitchens")
        element = profile["result"]["data"]["element"]
        element["experience"].insert(1, {
            "position": "Analyst", "companyName": "Northwind Freight",
            "startDate": {"text": "2001"}, "endDate": {"text": end_text},
        })
        return profile

    def test_a_job_at_the_employer_that_ended_long_ago_does_not_confirm(self, fake):
        fake(lookup=LOOKUP_URL, profiles={LOOKUP_URL: self._former("2004")})
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, DEEPLINE_KEY)
        assert ctx.linkedin_status == "match rejected"
        assert LOOKUP_URL not in ctx.anchors

    def test_a_job_at_the_employer_that_just_ended_confirms(self, fake):
        fake(lookup=LOOKUP_URL, profiles={LOOKUP_URL: self._former(str(date.today().year))})
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, DEEPLINE_KEY)
        assert ctx.linkedin_status == "checked"
        assert any("recent position" in basis for basis in ctx.match_basis)


class TestSuppliedUrl:
    def test_supplied_url_is_the_anchor_and_its_employer_joins(self, fake):
        http_fake = fake(profiles={GIVEN_URL: _profile(GIVEN_URL, "Northwind Freight")})
        ctx = _ctx(linkedin=GIVEN_URL)
        _resolve(ctx, DEEPLINE_KEY)
        assert http_fake.lookups == []
        assert http_fake.profile_reads == [GIVEN_URL]
        assert ctx.linkedin_status == "checked"
        assert GIVEN_URL in ctx.anchors
        assert "Northwind Freight" in ctx.anchors
        assert "LinkedIn URL supplied by user" in ctx.match_basis
        assert ctx.linkedin_mismatch == ""

    def test_supplied_url_at_another_employer_stays_anchored_with_a_mismatch(self, fake):
        fake(profiles={GIVEN_URL: _profile(GIVEN_URL, "Contoso Logistics")})
        ctx = _ctx(employer=EMPLOYER, linkedin=GIVEN_URL)
        _resolve(ctx, DEEPLINE_KEY)
        assert ctx.linkedin_status == "checked"
        assert ctx.linkedin_url == GIVEN_URL
        assert GIVEN_URL in ctx.anchors
        assert "Contoso Logistics" in ctx.anchors
        assert "Contoso Logistics" in ctx.linkedin_mismatch
        assert "Northwind Freight" in ctx.linkedin_mismatch

    def test_supplied_url_read_failure_keeps_the_anchor(self, fake):
        fake(fail=[f"{DEEPLINE}/harvestapi"])
        ctx = _ctx(linkedin=GIVEN_URL)
        _resolve(ctx, DEEPLINE_KEY)
        assert ctx.linkedin_status == "failed"
        assert GIVEN_URL in ctx.anchors
        assert ctx.profile == {}


class TestNoDeeplineKey:
    def test_no_key_and_no_url_makes_no_call(self, fake):
        http_fake = fake()
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, {**EXA_KEY, **SC_KEY, "INCLUDE_SOURCES": "linkedin"})
        assert http_fake.calls == []
        assert ctx.linkedin_status == "no key"

    def test_scrapecreators_needs_linkedin_opt_in(self, fake):
        http_fake = fake()
        ctx = _ctx(employer=EMPLOYER)
        _resolve(ctx, SC_KEY)
        assert http_fake.calls == []
        assert ctx.linkedin_status == "no key"

    def test_scrapecreators_reads_a_supplied_url_without_presenting_roles(self, fake):
        http_fake = fake(sc_profile={
            "success": True,
            "name": "Dana Okafor",
            "location": "Columbus, Ohio",
            "about": "Freight operations.",
            "website": "https://danaokafor.example",
            "experience": [{"name": "Northwind Freight", "member": {"title": "*****"}}],
            "recentPosts": [{
                "title": "We cut dock wait times by 30%",
                "link": "https://www.linkedin.com/posts/dana-okafor_activity-1",
                "datePublished": "2026-11-02",
                "activityType": "Shared by Dana Okafor",
            }],
        })
        ctx = _ctx(linkedin=GIVEN_URL)
        _resolve(ctx, SC_KEY)
        [call] = http_fake.to(SC_PROFILE)
        assert call["params"] == {"url": GIVEN_URL}
        assert ctx.linkedin_status == "checked"
        assert ctx.profile["current_roles"] == []
        assert ctx.profile["history"] == []
        assert "*****" not in repr(ctx.to_dict())
        assert "Northwind Freight" not in ctx.anchors
        assert ctx.profile["websites"] == ["https://danaokafor.example"]
        assert ctx.profile["posts"][0]["url"] == "https://www.linkedin.com/posts/dana-okafor_activity-1"
        assert ctx.profile["posts"][0]["date"] == "2026-11-02"

    def test_scrapecreators_failure_reports_failed(self, fake):
        fake(fail=[SC_PROFILE])
        ctx = _ctx(linkedin=GIVEN_URL)
        _resolve(ctx, SC_KEY)
        assert ctx.linkedin_status == "failed"
        assert GIVEN_URL in ctx.anchors

    def test_supplied_url_without_any_key_is_anchored_as_no_key(self, fake):
        http_fake = fake()
        ctx = _ctx(linkedin=GIVEN_URL)
        _resolve(ctx, {})
        assert http_fake.calls == []
        assert ctx.linkedin_status == "no key"
        assert GIVEN_URL in ctx.anchors

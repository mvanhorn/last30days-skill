"""The person-employee fixture: a person run for an ordinary employee.

Dana Okafor works at Northwind Freight (northwind.example). The run resolves
her LinkedIn profile by lookup, reads her bio and own links from the
employer's team page, and reads her LinkedIn posts, some from before the
window. A web result about a different Dana Okafor, a chef in another city,
shares only the name. Everything here is hand-written and fictional.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from . import harness

FIXTURE = "person-employee"
NAMESAKE_URL = "https://web.example.test/person-employee/tasting-counter"
OLDER_POST_URLS = (
    "https://www.linkedin.com/feed/update/urn:li:activity:7400000000000000103/",
    "https://www.linkedin.com/feed/update/urn:li:activity:7400000000000000104/",
)
# Older than the year the Person Profile looks back: shown nowhere.
EXPIRED_POST_URL = "https://www.linkedin.com/feed/update/urn:li:activity:7400000000000000105/"


@pytest.fixture(scope="module")
def run():
    from lib import render

    [fixture] = [f for f in harness.load_fixtures() if f.name == FIXTURE]
    result = harness.evaluate_fixture(fixture)
    text = render.render_compact(result.report)
    start = text.index("## Person Profile")
    end = text.index("\n## ", start + 1)
    return result, text, text[start:end]


def _ranked_urls(report) -> set[str]:
    urls = {candidate.url for candidate in report.ranked_candidates}
    urls.update(
        item.url for candidate in report.ranked_candidates for item in candidate.source_items
    )
    return urls


def test_older_posts_stay_out_of_ranked_results(run):
    result, _text, _profile = run
    assert result.scores["recency_compliance"] == 1.0
    ranked = _ranked_urls(result.report)
    linkedin = {item.url for item in result.report.items_by_source["linkedin_profile"]}
    assert len(linkedin) == 2
    for url in (*OLDER_POST_URLS, EXPIRED_POST_URL):
        assert url not in ranked
        assert url not in linkedin


def test_namesake_is_kept_out_of_the_report(run):
    result, text, profile = run
    assert NAMESAKE_URL not in _ranked_urls(result.report)
    assert NAMESAKE_URL not in {item.url for item in result.report.items_by_source["grounding"]}
    assert NAMESAKE_URL not in text
    assert "Lisbon" not in text
    assert "Dropped as namesakes: 1 item" in profile


def test_person_profile_renders_what_the_person_lanes_found(run):
    result, text, profile = run
    assert text.count("## Person Profile") == 1
    assert "Current role: VP Operations at Northwind Freight" in profile
    assert "profile current employer matches Northwind Freight" in profile
    # The URL came from a lookup, so the profile must not claim the user gave it.
    assert "given by the user" not in profile
    assert "(source: [https://northwind.example/team]" in profile
    person = result.report.artifacts["person_profile"]
    assert person["bio"]["title"] == "VP Operations"
    assert person["derived_handles"] == {"x": "nwf_dana_fx", "github": "dana-okafor-fixture"}
    assert "LinkedIn posts in the window: 2." in profile


def test_older_posts_appear_only_under_the_person_profile(run):
    _result, text, profile = run
    for url in OLDER_POST_URLS:
        assert url in profile
        assert text.count(url) == profile.count(url)
    assert EXPIRED_POST_URL not in text


def test_fixture_names_none_of_the_private_names():
    """Local only: names listed one per line in PERSON_FIXTURE_DENYLIST
    must not appear in this fixture's files."""
    names_file = os.environ.get("PERSON_FIXTURE_DENYLIST")
    if not names_file:
        pytest.skip("PERSON_FIXTURE_DENYLIST is not set")
    names = [
        line.strip()
        for line in Path(names_file).expanduser().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    fixture_dir = harness.FIXTURES_DIR / FIXTURE
    found = [
        f"{path.name}: {name}"
        for path in sorted(fixture_dir.iterdir())
        if path.is_file()
        for name in names
        if name.casefold() in path.read_text(encoding="utf-8").casefold()
    ]
    assert not found, "private names in the fixture:\n" + "\n".join(found)

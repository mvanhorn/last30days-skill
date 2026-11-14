"""What the patent lane makes of the lists: groups, counts, entries, index."""

from __future__ import annotations

import copy
from datetime import date

from lib import render
from tests.test_patents_client import (
    DETAILS,
    PDF,
    FakeUSPTO,
    guid_of,
    make_row,
    recorded,
    run,
)
from tests.test_render_patents import _report

ARTIFACT_KEYS = {
    "company", "company_via_person", "window", "groups", "other_names", "counts",
    "entries", "index", "partial", "status",
}
COUNT_KEYS = {
    "applications", "grants", "name_matched_applications", "name_matched_grants",
    "first_publications", "name_matched_first_publications", "continuing",
    "previously_published_grants", "designs", "other_kinds", "floor",
}
ENTRY_KEYS = [
    "guid", "number", "title", "url", "kind", "kind_code", "published", "filed",
    "earliest_priority", "cpc", "filed_as", "group_kind", "applicant_location",
    "abstract", "first_claim",
]
INDEX_KEYS = ["title", "url", "kind", "published", "cpc", "filed_as", "group_kind"]

# Filed 2026-01-15 (make_row's default) with a parent more than 13 months older.
CONTINUING = {"related": ["2023-02-01"]}


def numbers(items: list[dict]) -> list[str]:
    return [guid_of(item["url"]) for item in items]


# --- Apple, as recorded -----------------------------------------------------


def apple_lists() -> dict[str, list[dict]]:
    """The recorded Apple lists plus the stranger the full live list held."""
    stranger = make_row(
        "US-20260300001-A1", "2026-10-20", name="Deep Apple Therapeutics, Inc.", cpc="A61K31/00",
    )
    return {
        "US-PGPUB": recorded("Apple", "US-PGPUB") + [stranger],
        "USPAT": recorded("Apple", "USPAT"),
    }


def test_apple_artifact_has_the_agreed_shape(monkeypatch):
    FakeUSPTO(lists=apple_lists()).install(monkeypatch)

    artifact = run({"company": "Apple Inc.", "company_via_person": 1})["artifact"]

    assert set(artifact) == ARTIFACT_KEYS
    assert artifact["company"] == "Apple Inc."
    assert artifact["company_via_person"] is True
    assert artifact["window"] == {"from": "2026-10-14", "to": "2026-11-13"}
    assert set(artifact["counts"]) == COUNT_KEYS
    assert artifact["partial"] is None
    assert artifact["status"] == "digest"
    assert len(artifact["entries"]) == 12
    assert len(artifact["index"]) == 30
    for entry in artifact["entries"]:
        assert list(entry) == ENTRY_KEYS
    for item in artifact["index"]:
        assert list(item) == INDEX_KEYS
        assert isinstance(item["cpc"], str)


def test_apple_spellings_are_one_confirmed_group_and_the_stranger_is_another_name(monkeypatch):
    FakeUSPTO(lists=apple_lists()).install(monkeypatch)

    artifact = run()["artifact"]

    assert artifact["groups"] == [
        {"name": "Apple Inc.", "kind": "confirmed", "applications": 88, "grants": 88},
    ]
    assert artifact["other_names"] == [{"name": "Deep Apple Therapeutics, Inc.", "count": 1}]
    assert artifact["counts"]["applications"] == 88
    assert artifact["counts"]["grants"] == 88
    assert artifact["counts"]["name_matched_applications"] == 0
    shown = artifact["entries"] + artifact["index"]
    assert {item["filed_as"] for item in shown} == {"Apple Inc."}
    assert "20260300001" not in numbers(shown)


def test_apple_continuing_applications_are_counted_and_never_shown(monkeypatch):
    lists = apple_lists()
    FakeUSPTO(lists=lists).install(monkeypatch)

    artifact = run()["artifact"]

    counts = artifact["counts"]
    # 88 applications: 61 first publications and 27 continuing. 88 grants:
    # 68 B2 and one continuing B1, 13 designs, 6 B1 with no earlier publication.
    assert counts["continuing"] == 27
    assert counts["previously_published_grants"] == 69
    assert counts["designs"] == 13
    assert counts["other_kinds"] == 0
    assert counts["first_publications"] == 67
    assert counts["floor"] is False
    continuing = {
        row["publicationReferenceDocumentNumber"]
        for row in lists["US-PGPUB"]
        if row["relatedApplFilingDate"]
        and (
            date.fromisoformat(row["applicationFilingDate"][0][:10])
            - date.fromisoformat(min(row["relatedApplFilingDate"])[:10])
        ).days > 396
    }
    assert len(continuing) == 27
    shown = set(numbers(artifact["entries"] + artifact["index"]))
    assert not shown & continuing
    # Recorded documents USPTO's own continuity data marks as continuations.
    assert not shown & {"20260349259", "20260338957", "20260342141", "20260322621"}


def test_an_entry_carries_the_recorded_text(monkeypatch):
    FakeUSPTO(lists=apple_lists()).install(monkeypatch)

    entry = run()["artifact"]["entries"][0]

    assert entry["guid"] == "US-20260359001-A1"
    assert entry["number"] == "20260359001"
    assert entry["title"] == "SPEECH PREDICTION BASED ON MOTION"
    assert entry["url"] == PDF + "20260359001"
    assert (entry["kind"], entry["kind_code"]) == ("application", "A1")
    assert entry["published"] == "2026-11-12"
    assert entry["filed"] == "2026-04-23"
    assert entry["earliest_priority"] == "2025-05-12"
    assert entry["cpc"] == ["G06F3/165", "G06F3/012", "G06F3/013", "G06F3/017", "G06F3/167"]
    assert (entry["filed_as"], entry["group_kind"]) == ("Apple Inc.", "confirmed")
    assert entry["applicant_location"] == "Cupertino, CA, US"
    assert entry["abstract"].startswith("Some examples of the disclosure are directed to")
    assert len(entry["abstract"]) <= 400
    assert entry["first_claim"].startswith("1. A method comprising: at a first computer system")
    # The recorded claim 1 is longer than the cap; it names the idea before the cut.
    assert "an intention of the user to speak" in entry["first_claim"]
    assert 690 <= len(entry["first_claim"]) <= 700
    assert "<" not in entry["abstract"] + entry["first_claim"]


def test_text_is_capped_and_a_grant_reads_the_assignee_location(monkeypatch):
    row = make_row("US-12790001-B1", "2026-11-10", kind="B1")
    detail = {
        "abstractHtml": "<p>An&nbsp;&amp; abstract</p>\n " + "word " * 200,
        "claimsHtml": "<b>1</b>. A device " + "that works " * 200 + "<br /> <b>2</b>. Other.",
        "applicantName": ["Somebody Else LLC", "Apple Inc."],
        "applicantCity": ["Austin", "Cupertino"],
        "assigneeName": ["Somebody Else LLC", "APPLE INC."],
        "assigneeCity": ["Seoul", "Cupertino"],
        "assigneeState": ["N/A", "CA"],
        "assigneeCountry": ["KR", "US"],
        "cpcInventive": [f"G06F3/0{n} 20130101" for n in range(8)],
        "continuityData": ["us-provisional-application US 63000001 20250101\n<br />"],
    }
    FakeUSPTO(lists={"USPAT": [row]}, details={"US-12790001-B1": detail}).install(monkeypatch)

    [entry] = run()["artifact"]["entries"]

    assert entry["kind"] == "grant"
    assert entry["abstract"].startswith("An & abstract word word")
    assert 395 <= len(entry["abstract"]) <= 400
    assert entry["first_claim"].startswith("1. A device that works")
    assert 690 <= len(entry["first_claim"]) <= 700
    assert entry["applicant_location"] == "Cupertino, CA, US"
    assert len(entry["cpc"]) == 5
    assert entry["cpc"][0] == "G06F3/00"


def test_a_row_without_its_own_number_takes_it_from_the_guid(monkeypatch):
    rows = [
        make_row("US-20260310001-A1", "2026-11-05"),
        make_row("US-12790002-B1", "2026-11-03", kind="B1"),
    ]
    for row in rows:
        row["publicationReferenceDocumentNumber"] = None
    FakeUSPTO(lists={"US-PGPUB": rows[:1], "USPAT": rows[1:]}).install(monkeypatch)

    entries = run()["artifact"]["entries"]

    assert [(entry["number"], entry["url"]) for entry in entries] == [
        ("20260310001", PDF + "20260310001"),
        ("12790002", PDF + "12790002"),
    ]


def test_earliest_priority_is_the_earliest_claimed_or_related_date(monkeypatch):
    row = make_row("US-20260310002-A1", "2026-11-05", related=["2025-09-01"])
    row["priorityClaimsDate"] = ["2025-06-30T00:00:00Z", "2025-08-01T00:00:00Z"]
    bare = make_row("US-20260310003-A1", "2026-11-04")
    bare["applicationFilingDate"] = None
    FakeUSPTO(lists={"US-PGPUB": [row, bare]}).install(monkeypatch)

    first, second = run()["artifact"]["entries"]

    assert first["earliest_priority"] == "2025-06-30"
    assert (second["filed"], second["earliest_priority"]) == (None, None)


# --- kinds and counts -------------------------------------------------------


def test_a_company_whose_only_filing_is_a_continuation_gets_counts_only(monkeypatch):
    # Filed more than three years after its parent application.
    [row] = [
        row for row in recorded("Figure AI", "US-PGPUB") if row["guid"] == "US-20260326306-A1"
    ]
    fake = FakeUSPTO(lists={"US-PGPUB": [row]}).install(monkeypatch)

    artifact = run({"company": "Figure AI"})["artifact"]

    assert artifact["status"] == "counts-only"
    assert artifact["entries"] == []
    assert artifact["index"] == []
    assert artifact["partial"] is None
    assert artifact["counts"]["continuing"] == 1
    assert artifact["counts"]["first_publications"] == 0
    assert artifact["groups"] == [
        {"name": "Figure AI Inc.", "kind": "confirmed", "applications": 1, "grants": 0},
    ]
    # Nothing to order and nothing to read: no baseline search, no detail call.
    assert [call["kind"] for call in fake.calls] == ["session", "search", "search"]


def test_recorded_figure_ai_grants_are_all_previously_published(monkeypatch):
    FakeUSPTO(lists={"USPAT": recorded("Figure AI", "USPAT")}).install(monkeypatch)

    artifact = run({"company": "Figure AI"})["artifact"]

    assert artifact["status"] == "counts-only"
    assert artifact["counts"]["grants"] == 2
    assert artifact["counts"]["previously_published_grants"] == 2


def test_each_kind_lands_in_its_bucket(monkeypatch):
    applications = [
        make_row("US-20260320001-A1", "2026-11-05"),
        make_row("US-20260320002-A1", "2026-11-05", **CONTINUING),
        make_row("US-20260320003-A9", "2026-11-05", kind="A9"),
        # A parent 396 days before filing is not yet "more than 13 months".
        make_row("US-20260320004-A1", "2026-11-04", related=["2024-12-15"]),
        make_row("US-20260320005-A1", "2026-11-04", related=["2024-12-14"]),
    ]
    grants = [
        make_row("US-12790011-B1", "2026-11-03", kind="B1"),
        make_row("US-12790012-B1", "2026-11-03", kind="B1", **CONTINUING),
        make_row("US-12790013-B2", "2026-11-03", kind="B2"),
        make_row("US-D1150001-S", "2026-11-03", kind="S"),
        make_row("US-RE50001-E", "2026-11-03", kind="E"),
    ]
    FakeUSPTO(lists={"US-PGPUB": applications, "USPAT": grants}).install(monkeypatch)

    artifact = run()["artifact"]

    counts = artifact["counts"]
    assert counts["applications"] == 5
    assert counts["grants"] == 5
    assert counts["first_publications"] == 3
    assert counts["continuing"] == 2
    assert counts["previously_published_grants"] == 2
    assert counts["designs"] == 1
    assert counts["other_kinds"] == 2
    assert [entry["guid"] for entry in artifact["entries"]] == [
        "US-20260320001-A1", "US-20260320004-A1", "US-12790011-B1",
    ]
    assert artifact["index"] == []


def test_a_b1_grant_with_a_continuation_in_its_continuity_data_is_replaced(monkeypatch):
    lists = apple_lists()
    grant = next(row for row in lists["USPAT"] if row["guid"] == "US-12792808-B1")
    grant["datePublished"] = "2026-11-13T00:00:00Z"  # newest, so it is read first
    detail = {
        **copy.deepcopy(DETAILS["US-12786790-B2"]),
        "guid": "US-12792808-B1",
        "continuityData": [
            "continuation parent-doc US 16904342 20200617 US 11500001 child-doc US 18000001\n<br />"
        ],
    }
    fake = FakeUSPTO(lists=lists, details={"US-12792808-B1": detail}).install(monkeypatch)

    artifact = run()["artifact"]

    fetched = fake.detail_guids()
    assert fetched[0] == "US-12792808-B1"
    assert len(fetched) == 13
    entries = [entry["guid"] for entry in artifact["entries"]]
    assert entries == fetched[1:]
    assert "12792808" not in numbers(artifact["index"])
    assert artifact["partial"] is None
    counts = artifact["counts"]
    assert counts["first_publications"] == 66
    # A continuing B1 is a grant of something already published, as at list level.
    assert counts["previously_published_grants"] == 70
    assert counts["continuing"] == 27


def test_every_continuity_form_demotes_and_a_provisional_does_not(monkeypatch):
    forms = {
        "US-20260330001-A1": ["parent US continuation 17838218 20220612 GRANTED \n child US 1\n<br />"],
        "US-20260330002-A1": ["parent WO continuation PCT/JP2024/042963 20241204 PENDING \n child US 2"],
        "US-20260330003-A1": ["parent US continuation-in-part 17000001 20220101 PENDING"],
        "US-20260330004-A1": ["parent US division 17000002 20220101 GRANTED"],
        "US-20260330005-A1": ["divisional parent-doc US 17000003 20220101"],
        "US-20260330006-A1": ["us-provisional-application US 63804555 20250512\n<br />"],
        "US-20260330007-A1": [],
        "US-20260330008-A1": None,
    }
    rows = [make_row(guid, "2026-11-05") for guid in forms]
    details = {guid: {"abstractHtml": "x", "continuityData": data} for guid, data in forms.items()}
    FakeUSPTO(lists={"US-PGPUB": rows}, details=details).install(monkeypatch)

    artifact = run()["artifact"]

    assert [entry["guid"] for entry in artifact["entries"]] == [
        "US-20260330006-A1", "US-20260330007-A1", "US-20260330008-A1",
    ]
    assert artifact["counts"]["continuing"] == 5
    assert artifact["counts"]["first_publications"] == 3
    assert artifact["partial"] is None


def test_refills_stop_after_six_extra_requests(monkeypatch):
    rows = [make_row(f"US-202603400{n:02d}-A1", f"2026-11-{12 - n // 4:02d}") for n in range(30)]
    continuing = {"abstractHtml": "x", "continuityData": ["parent US continuation 1 20200101"]}
    details = {row["guid"]: continuing for row in rows[:10]}
    fake = FakeUSPTO(lists={"US-PGPUB": rows}, details=details).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(fake.of("detail")) == 18
    assert len(artifact["entries"]) == 8
    assert artifact["counts"]["continuing"] == 10
    assert artifact["counts"]["first_publications"] == 20
    assert artifact["partial"] == {"missing_text": 4}
    assert len(artifact["index"]) == 12


# --- groups -----------------------------------------------------------------


def test_microsoft_licensing_filings_are_the_company_s_own(monkeypatch):
    FakeUSPTO().install(monkeypatch)

    artifact = run({"company": "Microsoft"})["artifact"]

    assert artifact["groups"] == [{
        "name": "Microsoft Technology Licensing, LLC", "kind": "confirmed",
        "applications": 25, "grants": 25,
    }]
    counts = artifact["counts"]
    assert (counts["applications"], counts["grants"]) == (25, 25)
    assert (counts["name_matched_applications"], counts["name_matched_grants"]) == (0, 0)
    # The 25 applications: 18 first publications and 7 continuing. The 25
    # grants are all B2, each the grant of an application published earlier.
    assert counts["first_publications"] == 18
    assert counts["name_matched_first_publications"] == 0
    assert (counts["continuing"], counts["previously_published_grants"]) == (7, 25)
    assert artifact["status"] == "digest"
    assert len(artifact["entries"]) == 12
    for item in artifact["entries"] + artifact["index"]:
        assert item["group_kind"] == "confirmed"
        assert item["filed_as"] == "Microsoft Technology Licensing, LLC"


def test_an_operating_company_s_filings_count_as_the_company_s_in_the_footer(monkeypatch):
    opco = "OpenAI OpCo, LLC"
    applications = [
        *[make_row(f"US-2026043000{n}-A1", "2026-11-05", name=opco) for n in range(3)],
        make_row("US-20260430010-A1", "2026-11-04", name=opco, **CONTINUING),
    ]
    grants = [make_row("US-12790041-B1", "2026-11-03", name=opco, kind="B1")]
    FakeUSPTO(lists={"US-PGPUB": applications, "USPAT": grants}).install(monkeypatch)

    artifact = run({"company": "OpenAI"})["artifact"]

    assert artifact["groups"] == [
        {"name": opco, "kind": "confirmed", "applications": 4, "grants": 1},
    ]
    report = _report(artifact, topic="OpenAI")
    assert render._patents_footer_line(report) == (
        "📜 Patents: 4 applications published │ 1 granted │ 4 first US publications"
    )
    block = "\n".join(render._patent_digest_block(report))
    assert "filed as OpenAI OpCo, LLC" in block
    assert "not verified" not in block and "confirmed" not in block.casefold()


BLOCK_IMAGING = "Block Imaging, Inc."


def test_name_matched_filings_stay_out_of_the_company_s_counts(monkeypatch):
    applications = [
        make_row("US-20260410001-A1", "2026-11-06", name="Block, Inc."),
        *[make_row(f"US-2026041001{n}-A1", "2026-11-05", name=BLOCK_IMAGING) for n in range(5)],
        make_row("US-20260410020-A1", "2026-11-04", name=BLOCK_IMAGING, **CONTINUING),
        make_row("US-20260410021-A9", "2026-11-04", name=BLOCK_IMAGING, kind="A9"),
    ]
    grants = [
        make_row("US-12790021-B2", "2026-11-03", name=BLOCK_IMAGING, kind="B2"),
        make_row("US-D1150002-S", "2026-11-03", name=BLOCK_IMAGING, kind="S"),
    ]
    FakeUSPTO(lists={"US-PGPUB": applications, "USPAT": grants}).install(monkeypatch)

    artifact = run({"company": "Block"})["artifact"]

    assert artifact["groups"] == [
        {"name": "Block, Inc.", "kind": "confirmed", "applications": 1, "grants": 0},
        {"name": BLOCK_IMAGING, "kind": "name-matched", "applications": 7, "grants": 2},
    ]
    assert artifact["counts"] == {
        "applications": 1, "grants": 0,
        "name_matched_applications": 7, "name_matched_grants": 2,
        "first_publications": 1, "name_matched_first_publications": 5,
        "continuing": 0, "previously_published_grants": 0, "designs": 0, "other_kinds": 0,
        "floor": False,
    }
    # The entries still hold the name-matched filings, labelled.
    assert [entry["group_kind"] for entry in artifact["entries"]] == [
        "confirmed", *["name-matched"] * 5,
    ]


def test_a_demoted_candidate_leaves_the_first_publications_of_its_own_group_kind(monkeypatch):
    applications = [
        make_row("US-20260420001-A1", "2026-11-06", name="Block, Inc."),
        make_row("US-20260420002-A1", "2026-11-06", name="Block, Inc."),
        make_row("US-20260420011-A1", "2026-11-05", name=BLOCK_IMAGING),
        make_row("US-20260420012-A1", "2026-11-05", name=BLOCK_IMAGING),
    ]
    grants = [make_row("US-12790031-B1", "2026-11-04", name=BLOCK_IMAGING, kind="B1")]
    continuing = {"abstractHtml": "x", "continuityData": ["parent US continuation 1 20200101"]}
    demoted = ["US-20260420002-A1", "US-20260420012-A1", "US-12790031-B1"]
    FakeUSPTO(
        lists={"US-PGPUB": applications, "USPAT": grants},
        details={guid: continuing for guid in demoted},
    ).install(monkeypatch)

    artifact = run({"company": "Block"})["artifact"]

    assert [entry["guid"] for entry in artifact["entries"]] == [
        "US-20260420001-A1", "US-20260420011-A1",
    ]
    counts = artifact["counts"]
    # Confirmed: two candidates, one found to be a continuation.
    assert (counts["first_publications"], counts["continuing"]) == (1, 1)
    # Name-matched: three candidates, two demoted, and neither joins a count
    # of the company's own filings.
    assert counts["name_matched_first_publications"] == 1
    assert counts["previously_published_grants"] == 0


def test_alphabet_is_searched_and_confirmed_under_its_other_names(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)
    company = {"company": "Alphabet Inc.", "company_names": ["Google", "Waymo"]}

    artifact = run(company)["artifact"]

    assert [call["query"] for call in fake.of("search")] == [
        '("alphabet" OR "google" OR "waymo").aanm. AND @pd>="20261014"<="20261113"',
        '("alphabet" OR "google" OR "waymo").asnm. AND @pd>="20261014"<="20261113"',
    ]
    assert artifact["company"] == "Alphabet Inc."
    assert artifact["groups"] == [
        {"name": "Google LLC", "kind": "confirmed", "applications": 25, "grants": 25},
        {"name": "Waymo LLC", "kind": "confirmed", "applications": 16, "grants": 23},
    ]
    assert artifact["counts"]["applications"] == 41
    assert {entry["filed_as"] for entry in artifact["entries"]} <= {"Google LLC", "Waymo LLC"}


def test_other_names_that_only_extend_the_company_name_are_not_queried(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)
    company = {
        "company": "Apple Inc.",
        "company_names": ["Apple", "Apple Computer, Inc.", "Beats Electronics", "beats electronics llc", None]
        + [f"Unit {n}" for n in range(20)],
    }

    run(company)

    phrases = fake.of("search")[0]["query"].split(").aanm.")[0].lstrip("(").split(" OR ")
    assert phrases[:2] == ['"apple"', '"beats electronics"']
    assert len(phrases) == 1 + 12


def test_strangers_alone_are_the_none_state(monkeypatch):
    rows = [row for row in recorded("Block", "US-PGPUB") if row["applicantName"] == ["QUICK BLOCK LIMITED"]]
    rows += [make_row(f"US-2026035000{n}-A1", "2026-11-05", name="Chopping Block &amp; Co.") for n in range(3)]
    fake = FakeUSPTO(lists={"US-PGPUB": rows}).install(monkeypatch)

    result = run({"company": "Block"})

    assert result == {
        "state": "none",
        "other_names": [
            {"name": "Chopping Block & Co.", "count": 3},
            {"name": "QUICK BLOCK LIMITED", "count": 1},
        ],
    }
    assert [call["kind"] for call in fake.calls] == ["session", "search", "search"]


def test_a_company_with_nothing_in_the_window_is_the_none_state(monkeypatch):
    FakeUSPTO().install(monkeypatch)

    assert run({"company": "Anthropic"}) == {"state": "none", "other_names": []}


def test_other_names_are_the_five_largest(monkeypatch):
    rows = [
        make_row(f"US-20260360{size}{n:02d}-A1", "2026-11-05", name=f"Big Apple Ventures {size}")
        for size in range(1, 8) for n in range(size)
    ]
    FakeUSPTO(lists={"US-PGPUB": rows}).install(monkeypatch)

    other = run()["other_names"]

    assert [item["count"] for item in other] == [7, 6, 5, 4, 3]
    assert other[0]["name"] == "Big Apple Ventures 7"


def test_a_co_applicant_does_not_make_a_group(monkeypatch):
    FakeUSPTO().install(monkeypatch)
    company = {"company": "IBM", "company_names": ["International Business Machines"]}

    artifact = run(company)["artifact"]

    assert artifact["groups"] == [{
        "name": "International Business Machines Corporation", "kind": "confirmed",
        "applications": 25, "grants": 25,
    }]
    assert artifact["other_names"] == []
    assert {item["filed_as"] for item in artifact["entries"] + artifact["index"]} == {
        "International Business Machines Corporation",
    }


def test_a_row_sits_in_the_best_group_among_its_names(monkeypatch):
    rows = [
        make_row("US-20260370001-A1", "2026-11-06", name=["Apple Retail Holdings LLC", "Apple Inc."]),
        make_row("US-20260370002-A1", "2026-11-05", name="Apple Retail Holdings LLC"),
        make_row("US-20260370003-A1", "2026-11-04", name=["Stranger Ltd", "APPLE RETAIL HOLDINGS, LLC"]),
        make_row("US-20260370004-A1", "2026-11-03"),
    ]
    rows[3]["applicantName"] = "Apple Inc"  # a string, not a list
    rows.append(make_row("US-20260370005-A1", "2026-11-02", name=None))
    FakeUSPTO(lists={"US-PGPUB": rows}).install(monkeypatch)

    artifact = run()["artifact"]

    assert artifact["groups"] == [
        {"name": "Apple Inc.", "kind": "confirmed", "applications": 2, "grants": 0},
        {"name": "Apple Retail Holdings LLC", "kind": "name-matched", "applications": 2, "grants": 0},
    ]
    assert artifact["other_names"] == []
    assert artifact["counts"]["applications"] == 2
    assert artifact["counts"]["name_matched_applications"] == 2
    assert artifact["counts"]["first_publications"] == 2
    assert artifact["counts"]["name_matched_first_publications"] == 2
    assert [(entry["filed_as"], entry["group_kind"]) for entry in artifact["entries"]] == [
        ("Apple Inc.", "confirmed"),
        ("Apple Retail Holdings LLC", "name-matched"),
        ("Apple Retail Holdings LLC", "name-matched"),
        ("Apple Inc.", "confirmed"),
    ]


def test_at_most_eight_groups_confirmed_first_then_largest(monkeypatch):
    rows = [make_row("US-20260380000-A1", "2026-11-05")]
    for size in range(1, 11):
        rows += [
            make_row(f"US-202603801{size:02d}{n}-A1", "2026-11-05", name=f"Apple Division {size} LLC")
            for n in range(size)
        ]
    FakeUSPTO(lists={"US-PGPUB": rows}).install(monkeypatch)

    artifact = run()["artifact"]

    assert [(group["name"], group["applications"]) for group in artifact["groups"]] == [
        ("Apple Inc.", 1),
        *[(f"Apple Division {size} LLC", size) for size in range(10, 3, -1)],
    ]
    assert artifact["counts"]["name_matched_applications"] == sum(range(4, 11))


def test_every_confirmed_name_is_counted_however_many_there_are(monkeypatch):
    units = [f"Orchard Unit {n}" for n in range(1, 10)]
    rows = [make_row("US-20260381000-A1", "2026-11-05")]
    rows += [
        make_row(f"US-2026038110{n}-A1", "2026-11-05", name=f"{unit} LLC")
        for n, unit in enumerate(units)
    ]
    rows.append(make_row("US-20260381200-A1", "2026-11-05", name="Apple Retail Holdings LLC"))
    FakeUSPTO(lists={"US-PGPUB": rows}).install(monkeypatch)

    artifact = run({"company": "Apple Inc.", "company_names": units})["artifact"]

    confirmed = [group for group in artifact["groups"] if group["kind"] == "confirmed"]
    assert len(confirmed) == 10
    assert artifact["counts"]["applications"] == 10
    assert artifact["counts"]["first_publications"] == 10
    # With the confirmed names past the cap, no name-matched name is kept.
    assert [group["name"] for group in artifact["groups"] if group["kind"] != "confirmed"] == []


# --- which candidates get text ----------------------------------------------


def window_rows() -> list[dict]:
    """Fourteen candidates in a common class, newest first, and an older one
    in a class the company has not filed in."""
    rows = [
        make_row(f"US-202603900{n:02d}-A1", f"2026-11-{12 - n // 2:02d}", cpc="G06F3/01;H04L9/32")
        for n in range(14)
    ]
    rows.append(make_row("US-20260390099-A1", "2026-10-15", cpc="A61B5/02;G06F3/01"))
    return rows


def baseline_rows(count: int) -> list[dict]:
    return [
        make_row(f"US-202503{n:05d}-A1", "2026-03-01", cpc="G06F3/01;G06F3/02;H04L9/32")
        for n in range(count)
    ]


def test_a_thin_baseline_orders_entries_newest_first(monkeypatch):
    rows = window_rows()
    FakeUSPTO(lists={"US-PGPUB": rows}, baseline=baseline_rows(19)).install(monkeypatch)

    artifact = run()["artifact"]

    assert [entry["guid"] for entry in artifact["entries"]] == [row["guid"] for row in rows[:12]]
    assert numbers(artifact["index"]) == [row["guid"].split("-")[1] for row in rows[12:]]


def test_a_baseline_past_its_third_of_the_budget_still_reads_newest_first(monkeypatch):
    rows = window_rows()
    # A full baseline would put the unseen class first; this one takes 31 of
    # the lane's 90 seconds, past its own share of 30.
    fake = FakeUSPTO(
        lists={"US-PGPUB": rows}, baseline=baseline_rows(30), seconds={"baseline": 31.0},
    ).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(fake.of("baseline")) == 1
    assert [entry["guid"] for entry in artifact["entries"]] == [row["guid"] for row in rows[:12]]
    assert all(entry["abstract"] and entry["first_claim"] for entry in artifact["entries"])
    assert artifact["partial"] is None


def test_an_unseen_class_gets_a_slot_ahead_of_newer_common_filings(monkeypatch):
    rows = window_rows()
    FakeUSPTO(lists={"US-PGPUB": rows}, baseline=baseline_rows(30)).install(monkeypatch)

    artifact = run()["artifact"]

    assert [entry["guid"] for entry in artifact["entries"]] == [
        "US-20260390099-A1", *[row["guid"] for row in rows[:11]],
    ]
    assert artifact["entries"][0]["published"] == "2026-10-15"


def test_half_the_slots_go_to_rare_classes_and_half_to_the_newest(monkeypatch):
    common = [
        make_row(f"US-202604000{n:02d}-A1", f"2026-11-{12 - n:02d}", cpc="G06F3/01") for n in range(10)
    ]
    unseen = [
        make_row(f"US-202604001{n:02d}-A1", f"2026-10-{24 - n:02d}", cpc=f"A61B5/{n:02d}") for n in range(8)
    ]
    # Seen once in the baseline: rarer than the common class, less rare than unseen.
    seldom = make_row("US-20260400200-A1", "2026-11-13", cpc="B60W30/00")
    baseline = baseline_rows(30) + [make_row("US-20250399999-A1", "2026-02-01", cpc="B60W10/00")]
    fake = FakeUSPTO(
        lists={"US-PGPUB": common + unseen + [seldom]}, baseline=baseline,
    ).install(monkeypatch)

    artifact = run()["artifact"]

    assert [entry["guid"] for entry in artifact["entries"]] == [
        *[row["guid"] for row in unseen[:6]],
        seldom["guid"],
        *[row["guid"] for row in common[:5]],
    ]
    assert len(fake.of("baseline")) == 1


def test_baseline_rows_filed_by_strangers_do_not_make_a_class_look_common(monkeypatch):
    rows = window_rows()
    strangers = [
        make_row(f"US-202503{n:05d}-A1", "2026-03-01", name="Deep Apple Therapeutics, Inc.", cpc="A61B5/02")
        for n in range(100, 140)
    ]
    FakeUSPTO(lists={"US-PGPUB": rows}, baseline=baseline_rows(30) + strangers).install(monkeypatch)

    assert run()["artifact"]["entries"][0]["guid"] == "US-20260390099-A1"

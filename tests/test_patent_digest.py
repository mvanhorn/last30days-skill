"""The bounded lines of the patent evidence block, built from the lane's artifact."""

import copy

from lib import patent_digest, patents

PDF = "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/"

# Trimmed from a live Nvidia run on 2026-11-13: two of its twelve entries and
# two of its forty index items, counts as returned.
NVIDIA = {
    "company": "Nvidia",
    "company_via_person": False,
    "window": {"from": "2026-10-14", "to": "2026-11-13"},
    "groups": [
        {"name": "NVIDIA Corporation", "kind": "confirmed", "applications": 160, "grants": 86},
        {"name": "NVIDIA Corparation", "kind": "name-matched", "applications": 0, "grants": 1},
    ],
    "other_names": [],
    "counts": {
        "applications": 160, "grants": 86,
        "name_matched_applications": 0, "name_matched_grants": 1,
        "first_publications": 98, "continuing": 73, "previously_published_grants": 75,
        "designs": 1, "other_kinds": 0, "floor": False,
    },
    "entries": [
        {
            "guid": "US-20260321219-A1", "number": "20260321219",
            "title": "DYNAMICALLY UPDATABLE PERSISTENT DISPLAYS FOR HARDWARE RESOURCES",
            "url": f"{PDF}20260321219", "kind": "application", "kind_code": "A1",
            "published": "2026-10-15", "filed": "2025-04-15", "earliest_priority": None,
            "cpc": ["G09G3/344", "G09G3/3453", "G09G3/035"],
            "filed_as": "NVIDIA Corporation", "group_kind": "confirmed",
            "applicant_location": "Santa Clara, CA, US",
            "abstract": (
                "Persistent displays such as E-ink persistent displays can convey information "
                "related to hardware components such as servers, GPUs, and network switches."
            ),
            "first_claim": (
                "1. A display device, comprising: a persistent display; a data connection to "
                "receive state data from a hardware device with which the persistent display "
                "is associated."
            ),
        },
        {
            "guid": "US-20260316792-A1", "number": "20260316792",
            "title": "TARGET OBJECT AND FEATURE MINING",
            "url": f"{PDF}20260316792", "kind": "application", "kind_code": "A1",
            "published": "2026-10-15", "filed": "2025-06-30", "earliest_priority": "2025-04-10",
            "cpc": ["B60W60/0027", "G08G1/0133"],
            "filed_as": "NVIDIA Corporation", "group_kind": "confirmed",
            "applicant_location": "Santa Clara, CA, US",
            "abstract": "Autonomous and/or semi-autonomous machine performance benefits from learning.",
            "first_claim": "1. A method comprising: receiving first data describing agent trajectories.",
        },
    ],
    "index": [
        {
            "title": "COMPILER-SCHEDULED TRIANGULAR UPDATE NEURAL NETWORK",
            "url": f"{PDF}20260359632", "kind": "application", "published": "2026-11-12",
            "cpc": "G06N3/08", "filed_as": "NVIDIA Corporation", "group_kind": "confirmed",
        },
        {
            "title": "TRIANGULAR UPDATE NEURAL NETWORK",
            "url": f"{PDF}20260359641", "kind": "grant", "published": "2026-11-12",
            "cpc": "G06N3/084; G06N3/048", "filed_as": "NVIDIA Corparation",
            "group_kind": "name-matched",
        },
    ],
    "partial": None,
    "status": "digest",
}


def _entry(number, *, filed_as="Microsoft Corporation", group_kind="confirmed", **fields):
    return {
        "guid": f"US-{number}-A1", "number": str(number), "title": f"Filing {number}",
        "url": f"{PDF}{number}", "kind": "application", "kind_code": "A1",
        "published": "2026-11-05", "filed": "2025-05-01", "earliest_priority": None,
        "cpc": ["G06F9/50"], "filed_as": filed_as, "group_kind": group_kind,
        "applicant_location": "Redmond, WA, US",
        "abstract": f"Abstract of {number}.", "first_claim": f"1. Claim of {number}.",
        **fields,
    }


def microsoft():
    licensing = "Microsoft Technology Licensing, LLC"
    return {
        "company": "Microsoft",
        "company_via_person": False,
        "window": {"from": "2026-10-14", "to": "2026-11-13"},
        "groups": [
            {"name": "Microsoft Corporation", "kind": "confirmed", "applications": 0, "grants": 1},
            {"name": licensing, "kind": "name-matched", "applications": 212, "grants": 140},
        ],
        "other_names": [{"name": "Nuance Communications, Inc.", "count": 3}],
        "counts": {
            "applications": 0, "grants": 1,
            "name_matched_applications": 212, "name_matched_grants": 140,
            "first_publications": 3, "continuing": 0, "previously_published_grants": 0,
            "designs": 0, "other_kinds": 0, "floor": False,
        },
        "entries": [
            _entry(1001, filed_as=licensing, group_kind="name-matched"),
            _entry(1002, kind="grant", kind_code="B1"),
            _entry(1003, filed_as=licensing, group_kind="name-matched"),
        ],
        "index": [],
        "partial": None,
        "status": "digest",
    }


def _link(url, title):
    return f"[{title}]({url})"


def _untrusted(text, limit):
    return text[:limit]


def lines(artifact):
    return patent_digest.digest_lines(artifact, link=_link, untrusted=_untrusted)


def _line(out, label):
    found = [line for line in out if line.startswith(label)]
    assert len(found) == 1, (label, out)
    return found[0]


def test_block_lines_come_in_the_documented_order():
    artifact = copy.deepcopy(NVIDIA)
    artifact["other_names"] = [{"name": "Mellanox Technologies, Ltd.", "count": 4}]
    out = lines(artifact)
    assert out[:2] == ["## Patent Digest", ""]
    labels = [
        "- Filings:", "- Name-matched:", "- First US publications:",
        "- [DYNAMICALLY UPDATABLE", "- [TARGET OBJECT",
        "- More first US publications (titles only):",
        "  - [COMPILER-SCHEDULED", "  - [TRIANGULAR UPDATE",
        "- Other names in the search, not read:",
    ]
    positions = [out.index(_line(out, label)) for label in labels]
    assert positions == sorted(positions)
    assert positions[-1] == len(out) - 1
    assert not any(line.startswith("- Note:") for line in out)


def test_filings_line_names_company_window_confirmed_counts_and_names():
    filings = _line(lines(NVIDIA), "- Filings:")
    assert "Nvidia" in filings
    assert "2026-10-14 to 2026-11-13" in filings
    assert "160 applications published" in filings
    assert "86 patents granted" in filings
    assert "NVIDIA Corporation" in filings
    assert "Corparation" not in filings


def test_first_publications_line_counts_what_is_not_listed_and_omits_zeros():
    first = _line(lines(NVIDIA), "- First US publications:")
    assert first.startswith("- First US publications: 98")
    assert "73 continuing applications (continuations, divisionals)" in first
    assert "75 grants of applications published earlier" in first
    assert "1 design patent" in first
    assert "other kind" not in first


def test_entry_line_carries_every_field_and_the_text():
    out = lines(NVIDIA)
    first = _line(out, "- [DYNAMICALLY UPDATABLE")
    assert first.startswith(
        "- [DYNAMICALLY UPDATABLE PERSISTENT DISPLAYS FOR HARDWARE RESOURCES]"
        f"({PDF}20260321219) | application 20260321219 | published 2026-10-15 | "
        "filed 2025-04-15 | CPC G09G3/344; G09G3/3453; G09G3/035 | "
        "filed as NVIDIA Corporation (Santa Clara, CA, US) | abstract: Persistent displays"
    )
    assert "earliest priority" not in first
    assert " | first claim: 1. A display device, comprising" in first
    assert "name-matched" not in first
    second = _line(out, "- [TARGET OBJECT")
    assert "| filed 2025-06-30 | earliest priority 2025-04-10 | CPC" in second


def test_earliest_priority_equal_to_the_filed_date_is_not_repeated():
    artifact = microsoft()
    artifact["entries"] = [_entry(1, earliest_priority="2025-05-01")]
    assert "earliest priority" not in _line(lines(artifact), "- [Filing 1]")


def test_index_lines_carry_kind_date_cpc_and_filed_as():
    out = lines(NVIDIA)
    assert _line(out, "  - [COMPILER-SCHEDULED") == (
        f"  - [COMPILER-SCHEDULED TRIANGULAR UPDATE NEURAL NETWORK]({PDF}20260359632) | "
        "application | published 2026-11-12 | CPC G06N3/08 | filed as NVIDIA Corporation"
    )
    assert _line(out, "  - [TRIANGULAR UPDATE").endswith(
        "| grant | published 2026-11-12 | CPC G06N3/084; G06N3/048 | "
        "filed as NVIDIA Corparation | name-matched"
    )


def test_name_matched_entries_follow_confirmed_ones_and_are_marked():
    out = lines(microsoft())
    order = [line.split("]")[0] for line in out if line.startswith("- [Filing")]
    assert order == ["- [Filing 1002", "- [Filing 1001", "- [Filing 1003"]
    confirmed = _line(out, "- [Filing 1002]")
    assert "| grant 1002 |" in confirmed
    assert "name-matched" not in confirmed
    matched = _line(out, "- [Filing 1001]")
    assert (
        "| filed as Microsoft Technology Licensing, LLC (Redmond, WA, US) | name-matched | "
        "abstract:" in matched
    )


def test_name_matched_line_lists_the_group_and_filings_counts_exclude_it():
    out = lines(microsoft())
    filings = _line(out, "- Filings:")
    assert "0 applications published" in filings and "1 patent granted" in filings
    assert "212" not in filings and "140" not in filings and "Licensing" not in filings
    assert "Microsoft Corporation" in filings
    matched = _line(out, "- Name-matched:")
    assert "Microsoft Technology Licensing, LLC (212 applications, 140 grants)" in matched
    assert "extend the company's name" in matched
    assert "not verified as belonging to it" in matched


def test_no_name_matched_line_without_such_a_group():
    artifact = copy.deepcopy(NVIDIA)
    artifact["groups"] = artifact["groups"][:1]
    assert not any(line.startswith("- Name-matched:") for line in lines(artifact))


def test_other_names_line_lists_names_with_counts():
    assert _line(lines(microsoft()), "- Other names in the search, not read:") == (
        "- Other names in the search, not read: Nuance Communications, Inc. (3)"
    )


def test_floor_puts_a_plus_on_the_counts():
    artifact = microsoft()
    artifact["counts"]["floor"] = True
    out = lines(artifact)
    filings = _line(out, "- Filings:")
    assert "0+ applications published" in filings and "1+ patents granted" in filings
    assert "(212+ applications, 140+ grants)" in _line(out, "- Name-matched:")
    assert _line(out, "- First US publications:").startswith("- First US publications: 3+")


def test_person_topic_names_the_company_the_person_leads():
    artifact = copy.deepcopy(NVIDIA)
    artifact["company_via_person"] = True
    filings = _line(lines(artifact), "- Filings:")
    assert "Nvidia, the company the person leads" in filings
    assert "the company's filings, not the person's" in filings


def test_partial_renders_a_note_with_the_number():
    artifact = copy.deepcopy(NVIDIA)
    artifact["partial"] = {"missing_text": 3}
    note = _line(lines(artifact), "- Note:")
    assert "3 of the filings listed by title only" in note
    assert "no abstract or claim text" in note
    assert "stopped answering or time ran out" in note
    out = lines(artifact)
    assert out.index(_line(out, "- First US publications:")) + 1 == out.index(note)


def _full_size(entries, index=40):
    artifact = copy.deepcopy(NVIDIA)
    artifact["entries"] = [
        _entry(
            2000 + n, title="T" * 120, filed_as="NVIDIA Corporation",
            cpc=["G06N3/0455"] * patents.MAX_CPC_CODES, earliest_priority="2024-01-01",
            abstract="a" * patents.ABSTRACT_CHARS, first_claim="c" * patents.FIRST_CLAIM_CHARS,
        )
        for n in range(entries)
    ]
    artifact["index"] = [
        {**NVIDIA["index"][0], "title": f"Index {n} " + "T" * 120} for n in range(index)
    ]
    return artifact


def _size(out):
    return sum(len(line) + 1 for line in out)


def test_a_block_over_the_cap_stops_early_and_says_how_many_were_left_out():
    out = lines(_full_size(entries=30, index=10))
    assert _size(out) <= patent_digest.DIGEST_MAX_CHARS == 24_000
    shown = sum(1 for line in out if line.startswith("- [T"))
    assert 0 < shown < 30
    note = _line(out, "- Note:")
    assert f"{30 - shown} entries and 10 titles left out" in note
    assert "24,000 characters" in note
    assert not any(line.startswith("- More first US publications") for line in out)
    assert not any(line.startswith("  - [") for line in out)


def test_the_lane_s_largest_artifact_stays_under_the_cap():
    # Every slot filled with text at its own cap, and the whole index.
    out = lines(_full_size(entries=patents.DETAIL_SLOTS, index=patents.MAX_INDEX))
    assert _size(out) <= patent_digest.DIGEST_MAX_CHARS
    assert sum(1 for line in out if line.startswith("- [T")) == patents.DETAIL_SLOTS


def test_titles_cut_by_the_cap_are_counted_in_the_note():
    artifact = _full_size(entries=patents.DETAIL_SLOTS, index=patents.MAX_INDEX)
    for item in artifact["index"]:
        item["title"] = "T" * 400
    out = lines(artifact)
    assert _size(out) <= patent_digest.DIGEST_MAX_CHARS
    shown = sum(1 for line in out if line.startswith("  - ["))
    assert 0 < shown < patents.MAX_INDEX
    assert f"{patents.MAX_INDEX - shown} titles left out" in _line(out, "- Note:")


def test_counts_only_or_malformed_artifact_has_no_block():
    counts_only = copy.deepcopy(NVIDIA)
    counts_only.update(status="counts-only", entries=[], index=[])
    assert lines(counts_only) == []
    assert lines(None) == []
    assert lines({"status": "digest"}) == []

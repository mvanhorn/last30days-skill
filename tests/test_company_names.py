"""Tests for the shared company-name helper."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "last30days" / "scripts"))

from lib.company_names import (  # noqa: E402
    CONFIRMED,
    NAME_MATCHED,
    NEITHER,
    classify_applicant,
    name_key,
)

FIXTURE = ROOT / "tests" / "fixtures" / "ppubs_lists.json"


def test_three_values():
    assert (CONFIRMED, NAME_MATCHED, NEITHER) == ("confirmed", "name-matched", "neither")


@pytest.mark.parametrize(
    "raw, key",
    [
        ("Apple Inc.", "apple"),
        ("APPLE INC.", "apple"),
        ("Apple, Inc.", "apple"),
        ("Samsung Electronics Co., Ltd.", "samsung electronics"),
        ("SEOUL NATIONAL UNIVERSITY R&amp;DB FOUNDATION", "seoul national university r db foundation"),
        ("Inc.", "inc"),
        ("LLC", "llc"),
        ("Inc. LLC", "inc"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_name_key(raw, key):
    assert name_key(raw) == key


@pytest.mark.parametrize("applicant", ["Apple Inc.", "APPLE INC.", "Apple, Inc."])
@pytest.mark.parametrize("company", ["Apple Inc.", "Apple"])
def test_apple_confirmed(applicant, company):
    assert classify_applicant(applicant, company) == CONFIRMED
    assert classify_applicant(company, applicant) == CONFIRMED


# (applicant, company, known names, expected)
CASES = [
    ("Deep Apple Therapeutics, Inc.", "Apple", [], NEITHER),
    ("Google LLC", "Google", [], CONFIRMED),
    ("Anthropic, PBC", "Anthropic", [], CONFIRMED),
    ("Meta Platforms Technologies, LLC", "Meta Platforms", [], NAME_MATCHED),
    ("Metabase, Inc.", "Meta", [], NEITHER),
    ("Google LLC", "Alphabet", ["Google"], CONFIRMED),
    ("Google LLC", "Alphabet", [], NEITHER),
    ("Waymo LLC", "Alphabet", ["Google", "Waymo"], CONFIRMED),
    ("Waymo LLC", "Alphabet", ["Google"], NEITHER),
    (
        "International Business Machines Corporation",
        "IBM",
        ["International Business Machines Corporation"],
        CONFIRMED,
    ),
    ("International Business Machines Corporation", "IBM", [], NEITHER),
    ("Microsoft Technology Licensing, LLC", "Microsoft", [], NAME_MATCHED),
    ("Samsung Electronics Co., Ltd.", "Samsung", [], NAME_MATCHED),
    ("Microsoft Corporation", "Microsoft", [], CONFIRMED),
    ("Block Imaging, Inc.", "Block", [], NAME_MATCHED),
    ("Square Enix Co., Ltd.", "Square", [], NAME_MATCHED),
    ("QUICK BLOCK LIMITED", "Block", [], NEITHER),
    ("SEOUL NATIONAL UNIVERSITY R&amp;DB FOUNDATION", "Samsung", [], NEITHER),
    ("Amazon Technologies, Inc.", "Amazon", [], NAME_MATCHED),
    ("OpenAI OpCo, LLC", "OpenAI", [], NAME_MATCHED),
    ("Notion Labs, Inc.", "Notion", [], NAME_MATCHED),
    ("Block Imaging, Inc.", "Alphabet", ["Block"], NAME_MATCHED),
]


@pytest.mark.parametrize("applicant, company, known, expected", CASES)
def test_classification(applicant, company, known, expected):
    assert classify_applicant(applicant, company, known) == expected


def test_known_names_default_none_and_tuple():
    assert classify_applicant("Google LLC", "Alphabet") == NEITHER
    assert classify_applicant("Google LLC", "Alphabet", None) == NEITHER
    assert classify_applicant("Google LLC", "Alphabet", ("Google",)) == CONFIRMED


def test_known_name_prefix_is_name_matched():
    assert classify_applicant("Google Technology Holdings", "Alphabet", ["Google"]) == NAME_MATCHED


@pytest.mark.parametrize("junk", ["", "   ", "Inc.", "LLC", "Co., Ltd.", None])
def test_junk_never_matches(junk):
    assert classify_applicant(junk, "Apple") == NEITHER
    assert classify_applicant("Apple Inc.", junk) == NEITHER
    assert classify_applicant(junk, junk) == NEITHER
    assert classify_applicant("Apple Inc.", "Apple", [junk, ""]) == CONFIRMED
    assert classify_applicant("Inc.", "Apple", ["LLC", ""]) == NEITHER


def _names(patents):
    for patent in patents:
        for field in ("applicantName", "assigneeName"):
            value = patent.get(field)
            if isinstance(value, str):
                value = [value]
            for name in value or []:
                yield name


def _lists():
    return json.loads(FIXTURE.read_text())["lists"]


def _names_for(company):
    out = []
    for key, entry in _lists().items():
        if key.split("|")[0] == company:
            out.extend(_names(entry.get("patents") or []))
    return out


@pytest.mark.parametrize("company", ["Apple", "Nvidia", "Figure AI", "Google", "Block"])
def test_real_data_confirmed_means_same_key(company):
    names = _names_for(company)
    assert names
    for name in names:
        if classify_applicant(name, company) == CONFIRMED:
            assert name_key(name) == name_key(company), name


@pytest.mark.parametrize("company", ["Microsoft", "Amazon", "Samsung", "Sony", "OpenAI", "Notion"])
def test_real_data_name_matched_never_confirmed(company):
    names = _names_for(company)
    assert names
    kinds = [classify_applicant(name, company) for name in names]
    assert NAME_MATCHED in kinds
    for name, kind in zip(names, kinds):
        if kind == CONFIRMED:
            assert name_key(name) == name_key(company), name

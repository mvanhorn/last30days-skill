"""The patent lane against USPTO Patent Public Search, replayed offline.

The fixtures hold real responses recorded on 2026-11-13 for the window
2026-10-14 to 2026-11-13. The lists are trimmed to their first rows, so the
fake answers ``numFound`` with the number of rows it holds unless a test says
otherwise. Rows and documents the live data did not have on that day are
built here from a recorded one.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from lib import health, http, patent_digest, patents
from lib.company_names import name_key

FIXTURES = Path(__file__).parent / "fixtures"
SESSION = json.loads((FIXTURES / "ppubs_session.json").read_text(encoding="utf-8"))
LISTS = json.loads((FIXTURES / "ppubs_lists.json").read_text(encoding="utf-8"))["lists"]
DETAILS = json.loads((FIXTURES / "ppubs_details.json").read_text(encoding="utf-8"))["details"]

DATE_FROM = "2026-10-14"
DATE_TO = "2026-11-13"
PDF = "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/"
APPLE = {"company": "Apple Inc."}


def recorded(name: str, database: str) -> list[dict]:
    return copy.deepcopy(LISTS[f"{name}|{database}"]["patents"])


def make_row(
    guid: str,
    published: str,
    *,
    name: str | list[str] | None = "Apple Inc.",
    kind: str = "A1",
    cpc: str | None = "G06F3/01",
    filed: str = "2026-01-15",
    related: list[str] | None = None,
) -> dict:
    """A list row in the recorded shape."""
    names = name if isinstance(name, list) or name is None else [name]
    grant = not kind.startswith("A")
    return {
        "guid": guid,
        "type": "USPAT" if grant else "US-PGPUB",
        "kindCode": [kind],
        "datePublished": f"{published}T00:00:00Z",
        "inventionTitle": f"Title of {guid}",
        "applicantName": names,
        "assigneeName": names if grant else None,
        "applicationNumber": "19/000001",
        "applicationFilingDate": [f"{filed}T00:00:00Z"],
        "relatedApplFilingDate": [f"{day}T00:00:00Z" for day in related] if related else None,
        "priorityClaimsDate": None,
        "cpcInventiveFlattened": cpc,
        "familyIdentifierCur": 1,
        "publicationReferenceDocumentNumber": guid.split("-")[1],
    }


def original_detail(guid: str) -> dict:
    """A document with no parent application, for guids that were not recorded."""
    return {
        "guid": guid,
        "abstractHtml": f"Abstract of {guid}.",
        "claimsHtml": f"<b>1</b>. A thing of {guid}. <br /> <b>2</b>. The thing of claim 1.",
        "applicantCity": ["Cupertino"],
        "applicantState": ["CA"],
        "applicantCountry": ["US"],
        "cpcInventive": ["G06F3/01 20130101"],
        "continuityData": None,
    }


class FakeUSPTO:
    """Stands in for ``lib.http`` and answers from the recorded fixtures.

    A search is answered with the recorded lists of every quoted name in the
    query (or the rows given in ``lists``) when its dates are the run's
    window, and with ``baseline`` otherwise.
    """

    def __init__(
        self,
        *,
        lists: dict[str, list[dict]] | None = None,
        baseline: list[dict] | None = None,
        details: dict[str, dict] | None = None,
        window: tuple[str, str] = (DATE_FROM, DATE_TO),
        num_found: dict[str, int] | None = None,
        session_headers: dict | None = None,
        session_body: object = None,
        errors: dict[tuple[str, int], Exception] | None = None,
        seconds: dict[str, float] | None = None,
    ):
        self.lists = lists
        self.baseline = baseline or []
        self.details = {**DETAILS, **(details or {})}
        self.window = tuple(day.replace("-", "") for day in window)
        self.num_found = num_found or {}
        self.session_headers = SESSION["headers"] if session_headers is None else session_headers
        self.session_body = SESSION["body"] if session_body is None else session_body
        # ("session" | "search" | "baseline" | "detail", nth call of that kind) -> error
        self.errors = errors or {}
        # kind -> seconds each call of that kind takes. When given, the lane
        # reads a clock only these calls move, and a call that ends at or past
        # its deadline fails the way lib.http fails it.
        self.seconds = seconds
        self.now = 1000.0
        self.calls: list[dict] = []

    def install(self, monkeypatch) -> FakeUSPTO:
        monkeypatch.setattr(
            patents, "http", SimpleNamespace(post=self.post, get=self.get, HTTPError=http.HTTPError),
        )
        if self.seconds is not None:
            monkeypatch.setattr(patents, "time", SimpleNamespace(monotonic=lambda: self.now))
        return self

    def of(self, kind: str) -> list[dict]:
        return [call for call in self.calls if call["kind"] == kind]

    def _note(self, kind: str, **call) -> None:
        self.calls.append({"kind": kind, **call})
        if self.seconds is not None:
            self.now += self.seconds.get(kind, 0.0)
            if self.now >= call["kwargs"]["deadline_monotonic"]:
                raise http.DeadlineExceeded()
        error = self.errors.get((kind, len(self.of(kind))))
        if error is not None:
            raise error

    def post(self, url, json_data, headers=None, **kwargs):
        if url.endswith("/api/users/me/session"):
            self._note("session", url=url, body=json_data, headers=headers, kwargs=kwargs)
            return self.session_body, self.session_headers
        assert url == "https://ppubs.uspto.gov/api/searches/searchWithBeFamily", url
        query = json_data["query"]["q"]
        database = json_data["query"]["databaseFilters"][0]["databaseName"]
        dates = tuple(re.search(r'@pd>="(\d{8})"<="(\d{8})"', query).groups())
        kind = "search" if dates == self.window else "baseline"
        self._note(
            kind, url=url, body=json_data, headers=headers, kwargs=kwargs,
            query=query, database=database, start=json_data["start"],
        )
        if kind == "baseline":
            rows = self.baseline
        elif self.lists is not None:
            rows = self.lists.get(database, [])
        else:
            asked = {name_key(phrase) for phrase in re.findall(r'"([^"]+)"', query.split(" AND ")[0])}
            rows = [
                row
                for key, value in LISTS.items()
                if key.endswith(f"|{database}") and name_key(key.split("|")[0]) in asked
                for row in value["patents"]
            ]
        start, count = json_data["start"], json_data["pageCount"]
        body = {
            "numFound": self.num_found.get(database, len(rows)),
            "patents": copy.deepcopy(rows[start:start + count]),
        }
        return (body, {}) if kwargs.get("return_headers") else body

    def get(self, url, headers=None, **kwargs):
        match = re.fullmatch(
            r"https://ppubs\.uspto\.gov/api/patents/highlight/([^?]+)"
            r"\?queryId=1&source=(US-PGPUB|USPAT)&includeSections=true&uniqueId=",
            url,
        )
        assert match, url
        guid = match.group(1)
        self._note("detail", url=url, headers=headers, kwargs=kwargs, guid=guid, database=match.group(2))
        body = copy.deepcopy(self.details.get(guid) or original_detail(guid))
        return (body, {}) if kwargs.get("return_headers") else body

    def detail_guids(self) -> list[str]:
        return [call["guid"] for call in self.of("detail")]


def run(company: dict = APPLE, **kwargs) -> dict:
    return patents.fetch_patent_data(company, DATE_FROM, DATE_TO, **kwargs)


def guid_of(url: str) -> str:
    return url.removeprefix(PDF)


# --- requests ---------------------------------------------------------------


def test_requests_follow_the_recorded_shapes(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)

    result = run()

    assert "artifact" in result
    [session] = fake.of("session")
    assert session["url"] == "https://ppubs.uspto.gov/api/users/me/session"
    assert session["body"] == -1
    assert session["kwargs"]["return_headers"] is True
    applications, grants = fake.of("search")
    assert applications["query"] == '("apple").aanm. AND @pd>="20261014"<="20261113"'
    assert grants["query"] == '("apple").asnm. AND @pd>="20261014"<="20261113"'
    assert (applications["database"], grants["database"]) == ("US-PGPUB", "USPAT")
    body = applications["body"]
    assert body["docFamilyFiltering"] == "noFiltering"
    assert (body["start"], body["pageCount"], body["sort"]) == (0, 500, "date_publ desc")
    assert body["query"]["caseId"] == SESSION["body"]["userCase"]["caseId"]
    for call in fake.calls[1:]:
        assert call["headers"]["X-Access-Token"] == "REDACTED-TOKEN"
    assert fake.of("detail")[0]["database"] == "US-PGPUB"
    assert len(fake.of("detail")) == 12


def test_every_request_is_bounded_by_the_budget(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)

    run(budget_seconds=5.0)

    assert fake.calls
    lane = [call for call in fake.calls if call["kind"] != "baseline"]
    deadlines = {call["kwargs"]["deadline_monotonic"] for call in lane}
    assert len(deadlines) == 1
    # The baseline search has its own deadline, earlier than the lane's.
    [baseline] = fake.of("baseline")
    assert baseline["kwargs"]["deadline_monotonic"] < min(deadlines)
    for call in fake.calls:
        assert 0 < call["kwargs"]["timeout"] <= 5.0
        assert call["kwargs"]["retries"] <= 2
        # lib.http gives up on the 429 that brings the count to this number,
        # so 2 means one honoured wait.
        assert call["kwargs"]["max_429_retries"] == 2


def test_request_timeout_is_at_most_eight_seconds(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)

    run()

    assert max(call["kwargs"]["timeout"] for call in fake.calls) <= 8.0


def test_a_spent_budget_is_an_error_and_sends_nothing(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)

    result = run(budget_seconds=0)

    assert result["state"] == "error"
    assert result["detail"]
    assert fake.calls == []


def test_a_past_window_sends_its_own_dates_and_a_baseline_that_ends_before_it(monkeypatch):
    fake = FakeUSPTO(window=("2025-03-01", "2025-03-31")).install(monkeypatch)

    result = patents.fetch_patent_data(APPLE, "2025-03-01", "2025-03-31")

    assert result["artifact"]["window"] == {"from": "2025-03-01", "to": "2025-03-31"}
    assert [call["query"] for call in fake.of("search")] == [
        '("apple").aanm. AND @pd>="20250301"<="20250331"',
        '("apple").asnm. AND @pd>="20250301"<="20250331"',
    ]
    [baseline] = fake.of("baseline")
    assert baseline["query"] == '("apple").aanm. AND @pd>="20240301"<="20250228"'
    assert baseline["database"] == "US-PGPUB"
    assert (baseline["start"], baseline["body"]["pageCount"]) == (0, 500)


def test_a_search_larger_than_two_pages_marks_the_counts_as_floors(monkeypatch):
    fake = FakeUSPTO(num_found={"US-PGPUB": 1200}).install(monkeypatch)

    counts = run()["artifact"]["counts"]

    assert counts["floor"] is True
    assert [(call["database"], call["start"]) for call in fake.of("search")] == [
        ("US-PGPUB", 0), ("US-PGPUB", 500), ("USPAT", 0),
    ]


def test_a_search_that_fits_two_pages_reads_both_and_is_not_a_floor(monkeypatch):
    rows = [make_row(f"US-2026030{n:04d}-A1", "2026-11-05") for n in range(600)]
    fake = FakeUSPTO(lists={"US-PGPUB": rows}).install(monkeypatch)

    artifact = run()["artifact"]

    assert artifact["counts"]["floor"] is False
    assert artifact["counts"]["applications"] == 600
    assert [call["start"] for call in fake.of("search")] == [0, 500, 0]


# --- sign-in and failures ---------------------------------------------------


def test_a_session_without_a_token_header_is_sign_in(monkeypatch):
    fake = FakeUSPTO(session_headers={}).install(monkeypatch)

    result = run()

    assert result["state"] == "signin"
    assert result["detail"]
    assert [call["kind"] for call in fake.calls] == ["session"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_session_is_sign_in(monkeypatch, status):
    error = http.HTTPError(f"HTTP {status}: refused", status)
    fake = FakeUSPTO(errors={("session", 1): error}).install(monkeypatch)

    result = run()

    assert set(result) == {"state", "detail"}
    assert result["state"] == "signin"
    assert [call["kind"] for call in fake.calls] == ["session"]


def test_a_session_without_a_case_id_is_sign_in(monkeypatch):
    FakeUSPTO(session_body={"userCase": None, "sessionTimeOutTime": 1800}).install(monkeypatch)

    assert run()["state"] == "signin"


def test_a_sign_in_banner_beside_a_valid_token_proceeds(monkeypatch):
    body = {
        **SESSION["body"],
        "message": "Patent Public Search will soon require sign in with a USPTO.gov account.",
    }
    FakeUSPTO(session_body=body).install(monkeypatch)

    assert run()["artifact"]["status"] == "digest"


def test_a_session_body_that_is_not_json_is_an_error(monkeypatch):
    error = http.HTTPError(
        "Invalid JSON response: Expecting value: line 1 column 1 (char 0)",
        outcome_state=health.SCHEMA_DRIFT,
    )
    FakeUSPTO(errors={("session", 1): error}).install(monkeypatch)

    result = run()

    assert result["state"] == "error"
    assert "Invalid JSON" in result["detail"]


def test_a_session_body_that_is_not_an_object_is_an_error(monkeypatch):
    FakeUSPTO(session_body="<html>Sign in</html>").install(monkeypatch)

    assert run()["state"] == "error"


def test_a_refused_search_is_sign_in(monkeypatch):
    error = http.HTTPError("HTTP 403: Forbidden", 403)
    FakeUSPTO(errors={("search", 2): error}).install(monkeypatch)

    assert run()["state"] == "signin"


def test_a_failed_search_is_an_error(monkeypatch):
    error = http.HTTPError("HTTP 500: Internal Server Error", 500)
    fake = FakeUSPTO(errors={("search", 1): error}).install(monkeypatch)

    result = run()

    assert result == {"state": "error", "detail": "HTTP 500: Internal Server Error"}
    assert fake.of("detail") == []


def test_a_search_answer_in_another_shape_is_an_error(monkeypatch):
    fake = FakeUSPTO().install(monkeypatch)
    monkeypatch.setattr(
        patents.http, "post",
        lambda url, json_data, headers=None, **kwargs: (
            fake.post(url, json_data, headers, **kwargs)
            if url.endswith("/session") else ({"patents": "none"}, {})
        ),
    )

    assert run()["state"] == "error"


def test_a_deadline_that_passes_during_the_searches_is_an_error(monkeypatch):
    FakeUSPTO(errors={("search", 2): http.DeadlineExceeded()}).install(monkeypatch)

    result = run()

    assert result["state"] == "error"
    assert "deadline" in result["detail"].lower()


def test_a_detail_refused_before_any_entry_is_sign_in(monkeypatch):
    error = http.HTTPError("HTTP 401: Unauthorized", 401)
    FakeUSPTO(errors={("detail", 1): error}).install(monkeypatch)

    assert run()["state"] == "signin"


def test_a_detail_refused_after_entries_exist_is_partial(monkeypatch):
    error = http.HTTPError("HTTP 401: Unauthorized", 401)
    fake = FakeUSPTO(errors={("detail", 3): error}).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(artifact["entries"]) == 2
    assert artifact["partial"] == {"missing_text": 10}
    assert len(fake.of("detail")) == 3


def test_a_429_on_the_fifth_detail_stops_fetching_and_keeps_the_counts(monkeypatch):
    clean = FakeUSPTO().install(monkeypatch)
    whole = run()["artifact"]
    error = http.HTTPError("HTTP 429: Too Many Requests", 429)
    fake = FakeUSPTO(errors={("detail", 5): error}).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(fake.of("detail")) == 5
    assert [entry["guid"] for entry in artifact["entries"]] == clean.detail_guids()[:4]
    assert artifact["partial"] == {"missing_text": 8}
    # The candidates that lost their text lead the index, in selection order.
    assert [guid_of(item["url"]) for item in artifact["index"][:8]] == [
        guid.split("-")[1] for guid in clean.detail_guids()[4:12]
    ]
    assert len(artifact["index"]) == patents.MAX_INDEX
    assert artifact["counts"] == whole["counts"]
    assert artifact["groups"] == whole["groups"]
    assert artifact["status"] == "digest"
    assert whole["partial"] is None


def test_a_deadline_that_passes_during_detail_fetches_is_partial(monkeypatch):
    fake = FakeUSPTO(errors={("detail", 3): http.DeadlineExceeded()}).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(artifact["entries"]) == 2
    assert artifact["partial"] == {"missing_text": 10}
    assert len(fake.of("detail")) == 3


def twelve_candidates() -> dict[str, list[dict]]:
    return {"US-PGPUB": [make_row(f"US-202603500{n:02d}-A1", f"2026-11-{12 - n:02d}") for n in range(12)]}


def note_line(artifact: dict) -> str:
    lines = patent_digest.digest_lines(
        artifact, link=lambda _url, title: title, untrusted=lambda text, _limit: text,
    )
    return next(line for line in lines if line.startswith("- Note:"))


def test_the_lane_and_the_fetch_default_have_a_ninety_second_budget(monkeypatch):
    fake = FakeUSPTO(seconds={}).install(monkeypatch)

    run()

    assert patents.LANE_BUDGET_SECONDS == 90.0
    assert fake.of("session")[0]["kwargs"]["deadline_monotonic"] == fake.now + 90.0


def test_a_read_that_times_out_mid_list_does_not_end_the_reading(monkeypatch):
    timeout = http.HTTPError("Request timed out", outcome_state=health.TIMEOUT)
    fake = FakeUSPTO(lists=twelve_candidates(), errors={("detail", 3): timeout}).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(fake.of("detail")) == 12
    third = fake.detail_guids()[2]
    assert len(artifact["entries"]) == 11
    assert third not in [entry["guid"] for entry in artifact["entries"]]
    assert all(entry["abstract"] and entry["first_claim"] for entry in artifact["entries"])
    assert [guid_of(item["url"]) for item in artifact["index"]] == [third.split("-")[1]]
    assert artifact["partial"] == {"missing_text": 1}
    assert note_line(artifact).startswith(
        "- Note: 1 of the filings listed by title only has no abstract or claim text",
    )


def test_a_budget_that_runs_out_after_five_reads_keeps_their_text(monkeypatch):
    fake = FakeUSPTO(lists=twelve_candidates(), seconds={"detail": 17.0}).install(monkeypatch)

    artifact = run()["artifact"]

    # The sixth read ends past the lane's ninety seconds.
    assert len(fake.of("detail")) == 6
    assert len(artifact["entries"]) == 5
    assert all(entry["abstract"] and entry["first_claim"] for entry in artifact["entries"])
    assert len(artifact["index"]) == 7
    assert artifact["partial"] == {"missing_text": 7}
    assert note_line(artifact).startswith(
        "- Note: 7 of the filings listed by title only have no abstract or claim text",
    )


def test_every_read_failing_leaves_a_titles_only_digest_that_says_so(monkeypatch):
    error = http.HTTPError("HTTP 500: Internal Server Error", 500)
    errors = {("detail", n): error for n in range(1, 13)}
    fake = FakeUSPTO(lists=twelve_candidates(), errors=errors).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(fake.of("detail")) == 12
    assert artifact["entries"] == []
    assert len(artifact["index"]) == 12
    assert artifact["status"] == "digest"
    assert artifact["partial"] == {"missing_text": 12}
    assert note_line(artifact).startswith(
        "- Note: 12 of the filings listed by title only have no abstract or claim text",
    )


def test_a_failed_baseline_search_still_builds_the_digest(monkeypatch):
    error = http.HTTPError("HTTP 500: Internal Server Error", 500)
    FakeUSPTO(errors={("baseline", 1): error}).install(monkeypatch)

    artifact = run()["artifact"]

    assert len(artifact["entries"]) == 12
    published = [entry["published"] for entry in artifact["entries"]]
    assert published == sorted(published, reverse=True)


@pytest.mark.parametrize("company", [None, "Apple", {}, {"company": ""}])
def test_a_company_that_cannot_be_searched_is_an_error(monkeypatch, company):
    fake = FakeUSPTO().install(monkeypatch)

    result = patents.fetch_patent_data(company, DATE_FROM, DATE_TO)

    assert result["state"] == "error"
    assert fake.of("search") == []


def test_an_unexpected_exception_becomes_the_error_state(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        patents, "http", SimpleNamespace(post=boom, get=boom, HTTPError=http.HTTPError),
    )

    assert run() == {"state": "error", "detail": "RuntimeError: boom"}


def test_bad_dates_are_an_error(monkeypatch):
    FakeUSPTO().install(monkeypatch)

    assert patents.fetch_patent_data(APPLE, "last month", DATE_TO)["state"] == "error"


def test_without_a_test_transport_the_lane_sends_nothing_and_finds_nothing():
    """The suite-wide default: an engine run in a test must not reach USPTO."""
    assert patents.http is not http

    assert run() == {"state": "none", "other_names": []}


def test_source_name():
    assert patents.SOURCE == "patents"

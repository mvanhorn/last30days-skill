"""`doctor --postmortem` reads the run the engine just saved.

On 2026-11-10 a Disney run saved its raw file and the next `doctor
--postmortem` printed "No saved run found - run `/last30days <topic>` first".
The engine stamped last-report.json "last30days-report-cache/v2"; doctor still
compared it with "/v1" and treated every saved run as unreadable.
"""

import last30days as cli
from lib import doctor, health, schema
from tests.test_last_run_state import _report


def _run_with_outcomes():
    report = _report("Disney")
    report.source_status = {
        "youtube": schema.SourceOutcome(source="youtube", state=health.OK, items_returned=13),
        "reddit": schema.SourceOutcome(
            source="reddit", state=health.TIMEOUT, items_returned=0,
            detail="reddit timed out after 180s", fix_hint="doctor",
        ),
        "digg": schema.SourceOutcome(
            source="digg", state=health.PARTIAL, items_returned=12,
            detail="12 items returned: no recent local sync",
        ),
    }
    return report


def test_doctor_reads_the_stamp_the_engine_writes():
    assert doctor.REPORT_CACHE_SCHEMA_VERSION == cli.REPORT_CACHE_VERSION
    assert doctor.REPORT_CACHE_FILENAME == cli._last_report_cache_path().name


def test_postmortem_after_a_run_reports_that_runs_outcomes():
    assert cli._write_last_run("Disney", _run_with_outcomes())

    postmortem = doctor.build_postmortem({})
    text = doctor.render_postmortem_text(postmortem)

    assert postmortem["present"] is True
    assert "No saved run found" not in text
    assert "last run: Disney" in text
    assert postmortem["outcomes"]["youtube"]["items_returned"] == 13
    # A source timeout is named with its detail.
    assert "✕ reddit — timeout: reddit timed out after 180s" in text
    assert "⚠ digg (12 items) — 12 items returned: no recent local sync" in text


def test_postmortem_with_no_run_still_says_so():
    text = doctor.render_postmortem_text(doctor.build_postmortem({}))

    assert "No saved run found" in text

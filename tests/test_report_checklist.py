"""The checklist's line for a person run's Person Profile block."""

from lib import render, report_checklist

from tests.test_render_person_profile import _profile, _report


def _checklist(rendered):
    return report_checklist.build(rendered, environ={})


def test_person_profile_block_adds_the_quiet_and_identity_rule():
    rendered = render.render_compact(_report(_profile()))
    block = _checklist(rendered)
    assert "`## Person Profile`" in block
    assert "`LinkedIn: checked`" in block
    assert "quiet" in block
    assert "identity is uncertain" in block


def test_person_profile_rule_shares_the_digest_line_and_keeps_the_checklist_short():
    with_profile = _checklist(render.render_compact(_report(_profile())))
    without = _checklist(render.render_compact(_report()))
    count = lambda text: len([line for line in text.splitlines() if line.strip()])
    assert count(with_profile) == count(without)
    assert "Person Profile" not in without

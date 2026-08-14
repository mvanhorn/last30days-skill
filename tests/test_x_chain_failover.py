"""grok must never be a dead end: every failure falls through to the next backend.

Grok is opt-in only (not in the auto chain), but when pinned via
LAST30DAYS_X_BACKEND=grok, any way grok can come back empty or error must
surface properly so the run reports the failure honestly rather than silently
leaving X uncovered.
"""

from unittest import mock

from lib import grok_x, pipeline


def _stub_grok(monkeypatch, stdout="", returncode=0, raises=None):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        if raises:
            raise raises
        import subprocess
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)


def test_grok_cli_missing_yields_an_error_for_failover(monkeypatch):
    monkeypatch.setattr(grok_x, "binary_path", lambda: None)
    result = grok_x.search_x("topic", "2026-07-14", "2026-08-13")
    assert result["items"] == []
    assert result["error"], "a missing CLI must surface an error so the chain logs it"


def test_grok_timeout_yields_an_error_for_failover(monkeypatch):
    import subprocess
    _stub_grok(monkeypatch, raises=subprocess.TimeoutExpired("grok", 1))
    result = grok_x.search_x("topic", "2026-07-14", "2026-08-13")
    assert result["items"] == []
    assert "timed out" in result["error"]


def test_grok_nonzero_exit_yields_an_error_for_failover(monkeypatch):
    _stub_grok(monkeypatch, stdout="boom", returncode=1)
    result = grok_x.search_x("topic", "2026-07-14", "2026-08-13")
    assert result["items"] == []
    assert result["error"]


def test_grok_fabricated_results_end_up_empty_not_fabricated(monkeypatch):
    """Provenance rejection must yield no items, so the chain moves to bird
    rather than the run reporting invented posts."""
    block = (
        "id: 1956158892141441450\nhandle: steipete\n"
        "created_at: Fri, 15 Aug 2025 00:59:58 GMT\nlikes: 100\n"
        "text: recalled from training data\n"
    )
    _stub_grok(monkeypatch, stdout=block)
    result = grok_x.search_x("steipete", "2026-07-14", "2026-08-13")
    assert result["items"] == []


def test_clean_empty_result_carries_no_error(monkeypatch):
    """A quiet window is not a broken backend; the chain still tries the next."""
    _stub_grok(monkeypatch, stdout="The search returned no posts.")
    result = grok_x.search_x("topic", "2026-07-14", "2026-08-13")
    assert result["items"] == []
    assert "error" not in result


# --- lane budget and selective retry ---------------------------------------

def test_lane_budget_stops_issuing_queries(monkeypatch):
    """Three lanes over three handles is up to 14 sequential LLM subprocess
    calls, bounded only by per-call timeouts without a shared budget."""
    import time as _time
    calls = {"n": 0}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        import subprocess
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, "no posts", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    past = _time.monotonic() - 1
    items, revoked = grok_x.search_handles(
        ["a", "b", "c"], "topic", "2026-07-14", "2026-08-13", deadline=past
    )
    assert items == []
    assert revoked is False
    assert calls["n"] == 0, "an expired budget must stop the lane before any call"


def test_lane_budget_constant_is_bounded():
    assert 0 < grok_x.LANE_BUDGET_SECONDS <= 300


def test_clean_empty_is_not_retried(monkeypatch):
    """Retrying a byte-identical prompt cannot turn an empty window into posts;
    it only doubles latency and Grok-plan spend on the common case."""
    calls = {"n": 0}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        import subprocess
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, "The search returned no posts.", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    items, error, auth_revoked = grok_x._run_query("topic", "2026-07-14", "2026-08-13")
    assert items == [] and error == ""
    assert calls["n"] == 1, "a clean empty result must not be retried"
    assert not auth_revoked


def test_fabricated_response_is_still_retried(monkeypatch):
    """The retry must survive for the case it exists to fix."""
    calls = {"n": 0}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    fabricated = (
        "id: 1956158892141441450\nhandle: steipete\n"
        "created_at: Fri, 15 Aug 2025 00:59:58 GMT\nlikes: 10\ntext: recalled\n"
    )

    def fake_run(cmd, **kwargs):
        import subprocess
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, fabricated, "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x._run_query("steipete", "2026-07-14", "2026-08-13")
    assert calls["n"] == 2, "a provenance rejection is exactly what retrying can fix"


def test_lanes_use_a_single_attempt(monkeypatch):
    """Lane queries pass attempts=1: an empty lane is a legitimate outcome and
    the shared budget is better spent on breadth than on repeats."""
    import inspect
    for fn in (grok_x.search_handles, grok_x.search_mentions, grok_x.search_name):
        assert "attempts=1" in inspect.getsource(fn), (
            f"{fn.__name__} should not retry lane queries"
        )


def test_every_empty_path_leaves_the_chain_free_to_continue(monkeypatch):
    """The chain advances on an empty item list, so each way grok can come back
    empty must actually return an empty list rather than raising or hanging.

    Behavioral on purpose: an earlier version of this test asserted on the
    loop's source text and broke when the loop moved, while proving nothing
    about what grok returns.
    """
    import subprocess as sp
    cases = {
        "missing binary": (lambda: None, None),
        "fabricated": (lambda: "/usr/bin/grok",
                       "id: 1956158892141441450\nhandle: steipete\n"
                       "created_at: Fri, 15 Aug 2025 00:59:58 GMT\nlikes: 1\ntext: x\n"),
        "clean empty": (lambda: "/usr/bin/grok", "no posts found"),
        "nonzero exit": (lambda: "/usr/bin/grok", None),
    }
    for label, (binary, stdout) in cases.items():
        monkeypatch.setattr(grok_x, "binary_path", binary)
        if stdout is not None:
            monkeypatch.setattr(
                grok_x.subprocess, "run",
                lambda cmd, **kw: sp.CompletedProcess(cmd, 0, stdout, ""),
            )
        elif binary() is not None:
            monkeypatch.setattr(
                grok_x.subprocess, "run",
                lambda cmd, **kw: sp.CompletedProcess(cmd, 1, "", "boom"),
            )
        result = grok_x.search_x("topic", "2026-07-14", "2026-08-13")
        assert result["items"] == [], f"{label} must yield no items so the chain advances"


def test_a_call_is_not_started_when_it_cannot_finish_in_budget(monkeypatch):
    """The lanes run synchronously with no outer timeout, so a per-call floor
    would let the last call overrun the documented shared ceiling."""
    import time as _time
    calls = {"n": 0}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        import subprocess
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 0, "no posts", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    # 5s left: below the minimum useful call, so nothing should start.
    near = _time.monotonic() + 5
    items, error, auth_revoked = grok_x._run_query("topic", "2026-07-14", "2026-08-13", deadline=near)
    assert calls["n"] == 0
    assert "budget exhausted" in error
    assert not auth_revoked


def test_timeout_never_exceeds_the_remaining_budget(monkeypatch):
    import time as _time
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        import subprocess
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, "no posts", "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    remaining = 40
    grok_x._run_query(
        "topic", "2026-07-14", "2026-08-13",
        deadline=_time.monotonic() + remaining,
    )
    assert seen["timeout"] <= remaining

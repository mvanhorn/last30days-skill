"""grok_x: X retrieval via the Grok CLI.

The failure modes pinned here were all measured against grok CLI 0.2.118 on
2026-08-13, not hypothesized. Retrieval is performed by a language model
rather than an API client, so the module's job is as much rejecting confident
fabrication as it is parsing.
"""

import re
import subprocess

import pytest

from lib import grok_x


@pytest.fixture(autouse=True)
def _reset():
    grok_x.clear_availability_cache()
    yield
    grok_x.clear_availability_cache()


def _block(post_id, handle="steipete", created="Wed, 12 Aug 2026 15:55:18 GMT",
           likes=1462, text="cli was a year ago."):
    return (
        f"id: {post_id}\n"
        f"handle: {handle}\n"
        f"created_at: {created}\n"
        f"likes: {likes}\n"
        f"reposts: 48\nreplies: 95\nquotes: 20\n"
        f"text: {text}\n"
    )


WINDOW = ("2026-07-14", "2026-08-13")


# --- provenance: the primary validity test --------------------------------

def test_in_window_post_is_parsed():
    items = grok_x.parse_x_response(
        {"text": _block("2087568620465607078")}, "steipete", *WINDOW
    )
    assert len(items) == 1
    item = items[0]
    assert item["author_handle"] == "steipete"
    assert item["url"] == "https://x.com/steipete/status/2087568620465607078"
    assert item["date"] == "2026-08-12"
    assert item["engagement"]["likes"] == 1462


def test_out_of_window_ids_are_rejected():
    """The measured fabrication: a since:2026-07-14 request answered with 2025
    posts recalled from training data. Handle, id format, text and engagement
    all looked correct; only the decoded timestamp exposed it."""
    text = "".join(
        _block(pid, created="Fri, 15 Aug 2025 00:59:58 GMT")
        for pid in ("1956158892141441450", "1955681419900834272")
    )
    assert grok_x.parse_x_response({"text": text}, "steipete", *WINDOW) == []


def test_uniform_id_sequence_is_rejected():
    """A generated series: real ranked results are not evenly spaced."""
    base = 2080000000000000000
    step = 715000000000000
    text = "".join(_block(str(base + i * step)) for i in range(6))
    assert grok_x.parse_x_response({"text": text}, "steipete", *WINDOW) == []


def test_placeholder_handle_is_rejected():
    text = _block("2087568620465607078", handle="unknown", likes=0)
    assert grok_x.parse_x_response({"text": text}, "steipete", *WINDOW) == []


def test_self_reported_non_execution_is_rejected():
    text = _block(
        "2087568620465607078",
        text="Requested x_keyword_search was not executed in this turn",
    )
    assert grok_x.parse_x_response({"text": text}, "steipete", *WINDOW) == []


def test_item_without_usable_id_is_dropped():
    text = "handle: steipete\nlikes: 10\ntext: no id here\n"
    assert grok_x.parse_x_response({"text": text}, "steipete", *WINDOW) == []


def test_mixed_response_keeps_only_in_window_posts():
    text = _block("2087568620465607078") + _block(
        "1956158892141441450", created="Fri, 15 Aug 2025 00:59:58 GMT"
    )
    items = grok_x.parse_x_response({"text": text}, "steipete", *WINDOW)
    assert [i["url"].rsplit("/", 1)[-1] for i in items] == ["2087568620465607078"]


def test_duplicate_post_ids_are_deduped():
    text = _block("2087568620465607078") * 2
    assert len(grok_x.parse_x_response({"text": text}, "steipete", *WINDOW)) == 1


# --- parsing resilience ----------------------------------------------------

def test_narration_around_blocks_is_tolerated():
    text = (
        "I'll call x_keyword_search now.\n\n"
        "Here are the posts I found:\n\n"
        + _block("2087568620465607078")
        + "\nThat's everything the tool returned.\n"
    )
    assert len(grok_x.parse_x_response({"text": text}, "steipete", *WINDOW)) == 1


def test_markdown_decorated_fields_are_parsed():
    text = (
        "- **id:** 2087568620465607078\n"
        "- **handle:** @steipete\n"
        "- **created_at:** Wed, 12 Aug 2026 15:55:18 GMT\n"
        "- **likes:** 1,462\n"
        "- **text:** cli was a year ago.\n"
    )
    items = grok_x.parse_x_response({"text": text}, "steipete", *WINDOW)
    assert len(items) == 1
    assert items[0]["engagement"]["likes"] == 1462


def test_error_response_returns_empty_not_raises():
    assert grok_x.parse_x_response({"error": "boom"}, "t", *WINDOW) == []


def test_non_dict_response_returns_empty():
    assert grok_x.parse_x_response(None, "t", *WINDOW) == []


# --- invocation contract ---------------------------------------------------

def test_invocation_omits_json_schema_and_tools(monkeypatch):
    """Both flags degrade or suppress the tool call; neither may be passed."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    grok_x.search_x("steipete", *WINDOW)
    assert "--json-schema" not in seen["cmd"]
    assert "--tools" not in seen["cmd"]
    assert "--permission-mode" in seen["cmd"]


def test_subprocess_runs_in_an_isolated_empty_directory(monkeypatch):
    """The child has tool permissions bypassed and its context carries
    untrusted post text, so it must not act in the user's repository."""
    import os
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        seen["existed"] = os.path.isdir(kwargs.get("cwd") or "")
        seen["entries"] = os.listdir(kwargs.get("cwd")) if seen["existed"] else None
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    grok_x.search_x("steipete", *WINDOW)
    assert seen["cwd"] and seen["cwd"] != os.getcwd()
    # Isolated and near-empty: the only entry is the throwaway HOME staged for
    # the child, never the user's checkout.
    assert seen["existed"] and seen["entries"] == ["home"]


def test_subprocess_environment_is_minimal(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "dummy-key")
    monkeypatch.setenv("AUTH_TOKEN", "dummy-token")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    grok_x.search_x("steipete", *WINDOW)
    assert "XAI_API_KEY" not in seen["env"]
    assert "AUTH_TOKEN" not in seen["env"]
    assert "PATH" in seen["env"]


def test_resolved_binary_path_is_used_not_bare_name(monkeypatch):
    """shutil.which and subprocess.run resolve a bare name differently on
    Windows, so the resolved path must be passed."""
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/opt/custom/grok")

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x.search_x("steipete", *WINDOW)
    assert seen["cmd"][0] == "/opt/custom/grok"


def test_missing_binary_returns_error_not_raises(monkeypatch):
    monkeypatch.setattr(grok_x, "binary_path", lambda: None)
    result = grok_x.search_x("steipete", *WINDOW)
    assert result["items"] == [] and result["error"]


def test_timeout_returns_error_not_raises(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    result = grok_x.search_x("steipete", *WINDOW)
    assert result["items"] == [] and "timed out" in result["error"]


def test_non_execution_is_retried(monkeypatch):
    """A response that fails provenance is a retryable non-execution, not a
    thin result -- the measured rate made single-shot unreliable.

    Asserted against _run_query directly: search_x additionally fans out across
    query variants to reach the depth target, which would confound a call count.
    """
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        body = (
            _block("1956158892141441450", created="Fri, 15 Aug 2025 00:59:58 GMT")
            if calls["n"] == 1 else _block("2087568620465607078")
        )
        return subprocess.CompletedProcess(cmd, 0, body, "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    items, error, auth_revoked = grok_x._run_query("steipete", *WINDOW)
    assert calls["n"] == 2, "a provenance rejection must be retried once"
    assert not auth_revoked
    assert len(items) == 1 and not error


# --- auth surfaces ---------------------------------------------------------

def test_stored_auth_status_makes_no_subprocess_or_network(monkeypatch):
    """This is the doctor path; the whole-doctor test patches these to raise."""
    def boom(*a, **k):
        raise AssertionError("doctor path must not spawn a process or hit the network")

    monkeypatch.setattr(grok_x.subprocess, "run", boom)
    monkeypatch.setattr(grok_x.subprocess, "Popen", boom)
    grok_x.stored_auth_status()


def test_stored_auth_status_reports_missing_store(monkeypatch, tmp_path):
    monkeypatch.setattr(grok_x, "token_store_path", lambda: tmp_path / "nope.json")
    status, detail, expires_at = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_MISSING and "nope.json" in detail
    assert expires_at is None


def test_stored_auth_status_detects_credentials(monkeypatch, tmp_path):
    store = tmp_path / "auth.json"
    store.write_text('{"iss": {"auth_mode": "oidc", "refresh_token": "dummy-token"}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    assert grok_x.stored_auth_status()[0] == grok_x.AUTH_OK


def test_stored_auth_status_never_echoes_store_contents(monkeypatch, tmp_path):
    """doctor output gets pasted into issue reports."""
    store = tmp_path / "auth.json"
    store.write_text('{"key": "SUPER-SECRET-VALUE", "refresh_token": "ALSO-SECRET"}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    _, detail, _ = grok_x.stored_auth_status()
    assert "SUPER-SECRET-VALUE" not in detail and "ALSO-SECRET" not in detail


def test_unreadable_store_is_an_error(monkeypatch, tmp_path):
    store = tmp_path / "auth.json"
    store.write_text("{}")

    def boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    monkeypatch.setattr(type(store), "read_text", boom, raising=False)
    assert grok_x.stored_auth_status()[0] == grok_x.AUTH_ERROR


# --- expires_at parsing -----------------------------------------------------

def test_stored_auth_status_future_expires_at_is_ok(monkeypatch, tmp_path):
    """Credentials with expires_at in the future report AUTH_OK."""
    from datetime import datetime, timezone, timedelta
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    store = tmp_path / "auth.json"
    store.write_text(f'{{"iss": {{"refresh_token": "tok", "expires_at": "{future}"}}}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    status, detail, expires_at = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_OK
    assert expires_at is not None


def test_stored_auth_status_past_expires_at_is_expired(monkeypatch, tmp_path):
    """Credentials with expires_at in the past report AUTH_EXPIRED, not AUTH_OK."""
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store = tmp_path / "auth.json"
    store.write_text(f'{{"iss": {{"refresh_token": "tok", "expires_at": "{past}"}}}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    status, detail, expires_at = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_EXPIRED
    assert "expired" in detail.lower()
    assert expires_at is not None


def test_stored_auth_status_no_expires_at_is_ok(monkeypatch, tmp_path):
    """Credentials without expires_at default to AUTH_OK (legacy stores)."""
    store = tmp_path / "auth.json"
    store.write_text('{"iss": {"refresh_token": "tok"}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    status, _, expires_at = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_OK
    assert expires_at is None


def test_stored_auth_status_unparseable_json_is_ok_with_markers(monkeypatch, tmp_path):
    """Malformed JSON with credential markers reports AUTH_OK (graceful degradation)."""
    store = tmp_path / "auth.json"
    store.write_text('{"refresh_token": "tok" this is not valid json')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    status, _, _ = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_OK


def test_stored_auth_status_unparseable_expires_at_is_ok(monkeypatch, tmp_path):
    """Malformed expires_at is ignored, status is AUTH_OK."""
    store = tmp_path / "auth.json"
    store.write_text('{"iss": {"refresh_token": "tok", "expires_at": "not-a-date"}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    status, _, expires_at = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_OK
    assert expires_at is None


def test_stored_auth_status_z_suffix_parses_correctly(monkeypatch, tmp_path):
    """ISO 8601 timestamps with Z suffix parse correctly."""
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = tmp_path / "auth.json"
    store.write_text(f'{{"iss": {{"refresh_token": "tok", "expires_at": "{past}"}}}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    status, _, _ = grok_x.stored_auth_status()
    assert status == grok_x.AUTH_EXPIRED


def test_has_stored_auth_true_when_expired(monkeypatch, tmp_path):
    """has_stored_auth returns True even when expired (refresh may work)."""
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store = tmp_path / "auth.json"
    store.write_text(f'{{"iss": {{"refresh_token": "tok", "expires_at": "{past}"}}}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    assert grok_x.has_stored_auth() is True


def test_is_available_true_when_expired(monkeypatch, tmp_path):
    """is_available returns True when expired (CLI will try refresh at runtime)."""
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store = tmp_path / "auth.json"
    store.write_text(f'{{"iss": {{"refresh_token": "tok", "expires_at": "{past}"}}}}')
    monkeypatch.setattr(grok_x, "token_store_path", lambda: store)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    grok_x.clear_availability_cache()
    assert grok_x.is_available() is True


# --- auth revocation detection ----------------------------------------------

def test_is_auth_revoked_error_detects_markers():
    """Auth revocation markers are detected."""
    assert grok_x.is_auth_revoked_error("Not signed in")
    assert grok_x.is_auth_revoked_error("invalid_grant: Refresh token has been revoked")
    assert grok_x.is_auth_revoked_error("Authentication failed")
    assert grok_x.is_auth_revoked_error("grok CLI exited 1: not logged in")
    assert not grok_x.is_auth_revoked_error("timed out after 30s")
    assert not grok_x.is_auth_revoked_error("")


def test_classify_run_failure_returns_auth_failed_for_revocation():
    """classify_run_failure maps revocation errors to AUTH_FAILED."""
    from lib import health
    assert grok_x.classify_run_failure("Not signed in") == health.AUTH_FAILED
    assert grok_x.classify_run_failure("invalid_grant") == health.AUTH_FAILED
    assert grok_x.classify_run_failure("timed out") == health.TIMEOUT
    assert grok_x.classify_run_failure("some other error") == health.ERROR


def test_search_x_returns_auth_revoked_on_session_failure(monkeypatch):
    """search_x returns auth_revoked when the session is revoked mid-run."""
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(
        grok_x.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "Not signed in"),
    )
    result = grok_x.search_x("test topic", *WINDOW)
    assert result.get("auth_revoked") is True
    assert "error" in result


def test_availability_cache_is_resettable(monkeypatch):
    monkeypatch.setattr(grok_x, "_is_available_uncached", lambda: True)
    assert grok_x.is_available() is True
    monkeypatch.setattr(grok_x, "_is_available_uncached", lambda: False)
    assert grok_x.is_available() is True, "memoized within a process"
    grok_x.clear_availability_cache()
    assert grok_x.is_available() is False


# --- lanes -----------------------------------------------------------------

def _stub_response(monkeypatch, body):
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    monkeypatch.setattr(
        grok_x.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, body, ""),
    )


def test_from_lane_filters_by_actual_author(monkeypatch):
    """Operator fidelity is not guaranteed: a measured from: query returned a
    post by a different account."""
    _stub_response(monkeypatch, _block("2087568620465607078", handle="leojr94_"))
    items, revoked = grok_x.search_handles(["steipete"], "topic", *WINDOW)
    assert items == []
    assert revoked is False


def test_from_lane_keeps_matching_author(monkeypatch):
    _stub_response(monkeypatch, _block("2087568620465607078", handle="steipete"))
    items, revoked = grok_x.search_handles(["steipete"], "topic", *WINDOW)
    assert len(items) == 1
    assert revoked is False


def test_from_lane_does_not_and_the_topic_into_the_query(monkeypatch):
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[2]
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x.search_handles(["steipete"], "quantum widgets", *WINDOW)
    assert "quantum widgets" not in seen["prompt"]


def test_mention_lane_excludes_the_subject_client_side(monkeypatch):
    """A measured run carrying -from:X still returned a post authored by X."""
    _stub_response(monkeypatch, _block("2087568620465607078", handle="GetEnergy_"))
    items, revoked = grok_x.search_mentions(["GetEnergy_"], *WINDOW)
    assert items == []
    assert revoked is False


def test_name_lane_needs_no_handle(monkeypatch):
    _stub_response(monkeypatch, _block("2087568620465607078", handle="iamcaroren"))
    items, revoked = grok_x.search_name("Bentgo", *WINDOW)
    assert len(items) == 1
    assert revoked is False


def test_name_lane_quotes_multi_word_names(monkeypatch):
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[2]
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x.search_name("Peter Steinberger", *WINDOW)
    assert '"Peter Steinberger"' in seen["prompt"]


def test_name_lane_excludes_subject_authored_posts(monkeypatch):
    _stub_response(monkeypatch, _block("2087568620465607078", handle="Bentgo"))
    items, revoked = grok_x.search_name("Bentgo", *WINDOW, exclude_handles=["Bentgo"])
    assert items == []
    assert revoked is False


def test_name_lane_applies_an_engagement_floor(monkeypatch):
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[2]
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x.search_name("Bentgo", *WINDOW)
    assert "min_faves:" in seen["prompt"], (
        "the bare-name lane is the widest of the three and needs a floor the "
        "other two do not"
    )


# --- lane revocation propagation -------------------------------------------

def test_from_lane_returns_revoked_on_auth_failure(monkeypatch):
    """search_handles should return (items, True) when auth is revoked mid-lane."""
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Error: Not signed in")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    items, revoked = grok_x.search_handles(["steipete"], "topic", *WINDOW)
    assert revoked is True
    assert items == []


def test_mention_lane_returns_revoked_on_auth_failure(monkeypatch):
    """search_mentions should return (items, True) when auth is revoked."""
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Error: Not signed in")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    items, revoked = grok_x.search_mentions(["steipete"], *WINDOW)
    assert revoked is True
    assert items == []


def test_name_lane_returns_revoked_on_auth_failure(monkeypatch):
    """search_name should return (items, True) when auth is revoked."""
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Error: Not signed in")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    items, revoked = grok_x.search_name("Bentgo", *WINDOW)
    assert revoked is True
    assert items == []


def test_from_lane_preserves_items_collected_before_revocation(monkeypatch):
    """Items collected before auth revocation should be returned with revoked=True."""
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First handle succeeds
            return subprocess.CompletedProcess(
                cmd, 0, _block("2087568620465607078", handle="steipete"), ""
            )
        else:
            # Second handle hits auth revocation
            return subprocess.CompletedProcess(cmd, 1, "", "Error: Not signed in")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    items, revoked = grok_x.search_handles(["steipete", "other"], "topic", *WINDOW)
    # Should return items from first successful call AND signal revocation
    assert len(items) == 1
    assert items[0]["author_handle"] == "steipete"
    assert revoked is True


# --- fixes applied after review --------------------------------------------

def test_child_home_is_not_the_users_home(monkeypatch, tmp_path):
    """Stripping credential env vars is not enough: the engine writes those same
    credentials to $HOME/.config/last30days/.env, and an empty cwd is no
    boundary for a filesystem-capable child (cwd bounds relative paths, not
    $HOME/... reads)."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    grok_x.search_x("steipete", *WINDOW)
    import os as _os
    assert seen["env"]["HOME"] != _os.path.expanduser("~")
    assert seen["env"]["HOME"].startswith(seen["cwd"])


def test_abbreviated_engagement_does_not_invert_ranking():
    """'1.2M' parsed as 1 ranked a viral post below one with 500 likes."""
    assert grok_x._as_int("39K") == 39_000
    assert grok_x._as_int("1.2M") == 1_200_000
    assert grok_x._as_int("1,462") == 1462
    assert grok_x._as_int("N/A") is None


def test_unparsable_window_rejects_rather_than_bypasses():
    """Fail closed: a bad window must not silently disable provenance."""
    assert grok_x.parse_x_response(
        {"text": _block("2087568620465607078")}, "steipete", "not-a-date", "also-bad"
    ) == []


def test_handle_outside_x_grammar_is_rejected():
    """Model-reported handles reach post URLs and the next child's prompt."""
    text = _block("2087568620465607078", handle="Peter Steinberger (@steipete)")
    items = grok_x.parse_x_response({"text": text}, "steipete", *WINDOW)
    # Falls back to the @-pattern inside the value, or drops the item entirely.
    assert all(
        re.fullmatch(r"[A-Za-z0-9_]{1,15}", i["author_handle"]) for i in items
    )


def test_clean_handle_rejects_non_grammar_values():
    assert grok_x._clean_handle("@steipete") == "steipete"
    assert grok_x._clean_handle("Peter Steinberger") == ""
    assert grok_x._clean_handle("a" * 16) == ""
    assert grok_x._clean_handle("bad'; drop") == ""


def test_empty_result_is_not_reported_as_an_error():
    """A quiet window is not a broken backend."""
    import subprocess as sp
    import unittest.mock as m
    with m.patch.object(grok_x, "binary_path", lambda: "/usr/bin/grok"), \
         m.patch.object(grok_x.subprocess, "run",
                        lambda cmd, **kw: sp.CompletedProcess(cmd, 0, "no posts found", "")):
        result = grok_x.search_x("nothing-matches-this", *WINDOW)
    assert result["items"] == []
    assert "error" not in result


def test_depth_drives_the_fanout_call_count():
    """DEPTH_CONFIG was dead: grok returned 10 posts at every depth while
    sitting ahead of bird, silently downgrading a deep run."""
    quick = grok_x._fanout_queries("t", "2026-07-14", "2026-08-13", 1)
    deep = grok_x._fanout_queries("t", "2026-07-14", "2026-08-13", 4)
    assert len(quick) == 1 and len(deep) == 4
    assert len(set(deep)) == 4, "fan-out variants must differ or they repeat one result set"


def test_fanout_queries_no_phrase_quote_for_place_names():
    """_fanout_queries("Rome Italy") must not emit '"Rome Italy"' variant.

    This was the 2026-08-14 Rome failure: phrase-quoting "Rome Italy" returned
    thin hits that promoted off-topic accounts (PrettyCitiesX, visegrad24).
    """
    variants = grok_x._fanout_queries("Rome Italy", "2026-07-14", "2026-08-13", 4)
    for v in variants:
        assert '"Rome Italy"' not in v, (
            "Place/disambiguation strings must not be phrase-quoted; "
            f"got {v!r}"
        )
    # First variant should use unquoted AND
    assert "Rome Italy" in variants[0]


def test_fanout_queries_proper_name_gets_phrase_quote():
    """Proper names like 'Peter Steinberger' should get phrase-quoted variant."""
    variants = grok_x._fanout_queries("Peter Steinberger", "2026-07-14", "2026-08-13", 4)
    # One of the variants should phrase-quote the proper name
    has_phrase = any('"Peter Steinberger"' in v for v in variants)
    assert has_phrase, (
        "Proper person names should have a phrase-quoted variant for exact match"
    )


def test_search_handles_and_topic_false_does_not_add_topic(monkeypatch):
    """search_handles without and_topic should not AND the topic into query."""
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[2]
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x.search_handles(["steipete"], "quantum widgets", *WINDOW, and_topic=False)
    assert "quantum widgets" not in seen["prompt"]


def test_search_handles_and_topic_true_adds_topic_to_query(monkeypatch):
    """search_handles with and_topic=True should AND the topic into query."""
    seen = {}
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")

    def fake_run(cmd, **kwargs):
        seen["prompt"] = cmd[2]
        return subprocess.CompletedProcess(cmd, 0, _block("2087568620465607078"), "")

    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    grok_x.search_handles(["visegrad24"], "Rome", *WINDOW, and_topic=True)
    assert "Rome" in seen["prompt"], (
        "Extracted handles should AND the topic to ensure on-topic results"
    )

"""Protocol and bounded recovery regressions retained from PRs 1065 and 1064."""

from unittest.mock import patch

import pytest

from lib import bluesky, http


@pytest.fixture(autouse=True)
def reset_session():
    bluesky._reset_session_cache()
    yield
    bluesky._reset_session_cache()


def unauthorized():
    return http.HTTPError("Unauthorized", 401, "")


def search():
    return bluesky.search_bluesky(
        "test", "2026-08-01", "2026-09-01",
        config={"BSKY_HANDLE": "fake.bsky.social", "BSKY_APP_PASSWORD": "fake-password"},
    )


@pytest.mark.parametrize("second_unauthorized", [False, True])
def test_missing_refresh_token_uses_one_fresh_login(second_unauthorized):
    with patch.object(bluesky.http, "request", side_effect=[
        {"accessJwt": "old"}, unauthorized(), {"accessJwt": "new"},
        unauthorized() if second_unauthorized else {"posts": []},
    ]) as request:
        result = search()
    assert request.call_count == 4
    assert request.call_args_list[2].args[1] == bluesky.BSKY_SESSION_URL
    assert request.call_args_list[3].kwargs["headers"] == {"Authorization": "Bearer new"}
    if second_unauthorized:
        assert "remained unauthorized" in result["error"]
        assert result["error"] != "refresh"
    else:
        assert "error" not in result


@pytest.mark.parametrize("status", [400, 401])
def test_rejected_refresh_uses_one_fresh_login(status):
    with patch.object(bluesky.http, "request", side_effect=[
        {"accessJwt": "old", "refreshJwt": "refresh-old"}, unauthorized(),
        http.HTTPError("Rejected", status, ""),
        {"accessJwt": "new", "refreshJwt": "refresh-new"}, {"posts": []},
    ]) as request:
        result = search()
    assert "error" not in result
    assert request.call_count == 5
    assert request.call_args_list[3].args[1] == bluesky.BSKY_SESSION_URL


@pytest.mark.parametrize("status", [429, 500])
def test_transient_refresh_never_falls_back_to_login_or_leaks_tokens(status, capsys):
    with patch.object(bluesky.http, "request", side_effect=[
        {"accessJwt": "old", "refreshJwt": "SENTINEL-REFRESH"}, unauthorized(),
        http.HTTPError("Echoed SENTINEL-REFRESH", status, "SENTINEL-REFRESH"),
    ]) as request:
        result = search()
    assert request.call_count == 3
    assert "refresh failed" in result["error"]
    assert "SENTINEL-REFRESH" not in result["error"] + capsys.readouterr().err


def test_missing_access_token_does_not_replace_cached_tokens():
    with patch.object(bluesky.http, "request", side_effect=[
        {"accessJwt": "old", "refreshJwt": "refresh-old"}, unauthorized(),
        {"refreshJwt": "malformed-rotation"},
    ]) as request:
        result = search()
    assert request.call_count == 3
    assert "No accessJwt" in result["error"]
    assert bluesky._cached_token == "old"
    assert bluesky._cached_refresh_token == "refresh-old"


def test_refreshed_search_second_401_is_actionable_not_sentinel():
    with patch.object(bluesky.http, "request", side_effect=[
        {"accessJwt": "old", "refreshJwt": "refresh-old"}, unauthorized(),
        {"accessJwt": "new", "refreshJwt": "refresh-new"}, unauthorized(),
    ]) as request:
        result = search()
    assert request.call_count == 4
    assert "remained unauthorized" in result["error"]
    assert result["error"] != "refresh"

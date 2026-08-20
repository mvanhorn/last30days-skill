"""Tests for unauthenticated GitHub availability + error surfacing (U5)."""

from unittest import mock

from lib import github, pipeline


class TestUnauthAvailability:
    def test_github_available_without_token_or_gh(self):
        # GitHub is reachable via the anon REST tier, so it is available even
        # with no token and no gh CLI.
        with mock.patch.dict("os.environ", {}, clear=True):
            sources = pipeline.available_sources({})
        assert "github" in sources


class TestUnauthCap:
    def test_unauth_caps_result_count(self):
        with mock.patch.object(github, "_resolve_token", return_value=None), \
             mock.patch.object(github, "_fetch_json", return_value={"items": []}) as fetch:
            github.search_github("kubernetes", "2026-03-01", "2026-03-31", depth="deep")
        # deep would be 60 with a token; unauth caps to UNAUTH_COUNT_CAP.
        called_url = fetch.call_args.args[0]
        assert f"per_page={github.UNAUTH_COUNT_CAP}" in called_url

    def test_authed_keeps_full_depth(self):
        with mock.patch.object(github, "_resolve_token", return_value="tok"), \
             mock.patch.object(github, "_fetch_json", return_value={"items": []}) as fetch:
            github.search_github("kubernetes", "2026-03-01", "2026-03-31", depth="deep")
        called_url = fetch.call_args.args[0]
        assert "per_page=60" in called_url

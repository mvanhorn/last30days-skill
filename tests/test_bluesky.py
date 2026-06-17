"""Tests for bluesky module."""

import os
import unittest
from unittest.mock import patch, MagicMock

from lib import bluesky


class TestExtractCoreSubject(unittest.TestCase):
    def test_strips_prefix(self):
        result = bluesky._extract_core_subject("what are people saying about claude code")
        self.assertEqual(result, "claude code")

    def test_strips_noise(self):
        result = bluesky._extract_core_subject("latest trending news claude code")
        self.assertNotIn("latest", result)
        self.assertNotIn("trending", result)
        self.assertIn("claude", result)

    def test_preserves_core(self):
        result = bluesky._extract_core_subject("react native")
        self.assertEqual(result, "react native")


class TestParseDate(unittest.TestCase):
    def test_indexed_at_iso(self):
        item = {"indexedAt": "2024-06-15T12:00:00Z"}
        self.assertEqual(bluesky._parse_date(item), "2024-06-15")

    def test_created_at_iso(self):
        item = {"createdAt": "2024-03-01T08:30:00.000Z"}
        self.assertEqual(bluesky._parse_date(item), "2024-03-01")

    def test_indexed_at_preferred_over_created_at(self):
        item = {"indexedAt": "2024-06-15T12:00:00Z", "createdAt": "2024-06-14T12:00:00Z"}
        self.assertEqual(bluesky._parse_date(item), "2024-06-15")

    def test_none_returns_none(self):
        self.assertIsNone(bluesky._parse_date({}))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(bluesky._parse_date({"indexedAt": "not-a-date"}))


class TestParseBlueskyResponse(unittest.TestCase):
    def test_basic_post(self):
        response = {
            "posts": [{
                "uri": "at://did:plc:abc123/app.bsky.feed.post/xyz789",
                "author": {"handle": "alice.bsky.social", "displayName": "Alice"},
                "record": {"text": "Hello world", "createdAt": "2024-06-15T12:00:00Z"},
                "indexedAt": "2024-06-15T12:01:00Z",
                "likeCount": 10,
                "repostCount": 5,
                "replyCount": 3,
                "quoteCount": 1,
            }]
        }
        items = bluesky.parse_bluesky_response(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["handle"], "alice.bsky.social")
        self.assertEqual(items[0]["display_name"], "Alice")
        self.assertEqual(items[0]["text"], "Hello world")
        self.assertEqual(items[0]["url"], "https://bsky.app/profile/alice.bsky.social/post/xyz789")
        self.assertEqual(items[0]["engagement"]["likes"], 10)
        self.assertEqual(items[0]["engagement"]["reposts"], 5)
        self.assertEqual(items[0]["date"], "2024-06-15")

    def test_empty_response(self):
        items = bluesky.parse_bluesky_response({})
        self.assertEqual(items, [])

    def test_missing_fields(self):
        response = {"posts": [{"uri": "", "author": {}, "record": {}}]}
        items = bluesky.parse_bluesky_response(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["handle"], "")
        self.assertEqual(items[0]["text"], "")

    def test_relevance_decreases_with_position(self):
        response = {"posts": [
            {"uri": f"at://did/app.bsky.feed.post/{i}", "author": {"handle": f"u{i}"}, "record": {"text": f"post {i}"}}
            for i in range(5)
        ]}
        items = bluesky.parse_bluesky_response(response)
        self.assertGreater(items[0]["relevance"], items[4]["relevance"])


class TestDepthConfig(unittest.TestCase):
    def test_all_depths_exist(self):
        for depth in ("quick", "default", "deep"):
            self.assertIn(depth, bluesky.DEPTH_CONFIG)

    def test_deep_has_more_results(self):
        quick = bluesky.DEPTH_CONFIG["quick"]
        deep = bluesky.DEPTH_CONFIG["deep"]
        self.assertGreater(deep, quick)


class TestCreateSession(unittest.TestCase):
    def setUp(self):
        bluesky._cached_token = None

    def tearDown(self):
        bluesky._cached_token = None

    @patch("lib.bluesky.http.request")
    def test_returns_token(self, mock_request):
        mock_request.return_value = {"accessJwt": "tok123", "refreshJwt": "ref456"}
        token = bluesky._create_session("user.bsky.social", "app-pw")
        self.assertEqual(token, "tok123")
        mock_request.assert_called_once()

    @patch("lib.bluesky.http.request")
    def test_caches_token(self, mock_request):
        mock_request.return_value = {"accessJwt": "tok123", "refreshJwt": "ref456"}
        bluesky._create_session("user.bsky.social", "app-pw")
        bluesky._create_session("user.bsky.social", "app-pw")
        mock_request.assert_called_once()  # Only one HTTP call

    @patch("lib.bluesky.http.request")
    def test_returns_none_on_failure(self, mock_request):
        mock_request.side_effect = Exception("connection refused")
        token = bluesky._create_session("user.bsky.social", "app-pw")
        self.assertIsNone(token)
        self.assertIn("connection refused", bluesky._session_error)

    @patch("lib.bluesky.http.request")
    def test_returns_none_on_missing_jwt(self, mock_request):
        mock_request.return_value = {"did": "did:plc:abc"}
        token = bluesky._create_session("user.bsky.social", "app-pw")
        self.assertIsNone(token)


class TestSearchBlueskyAuth(unittest.TestCase):
    def setUp(self):
        bluesky._cached_token = None

    def tearDown(self):
        bluesky._cached_token = None

    def test_no_config_returns_error(self):
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09")
        self.assertEqual(result["posts"], [])
        self.assertIn("not configured", result["error"])

    def test_empty_config_returns_error(self):
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09", config={})
        self.assertEqual(result["posts"], [])
        self.assertIn("not configured", result["error"])

    @patch("lib.bluesky.http.request")
    def test_auth_failure_returns_specific_error(self, mock_request):
        mock_request.side_effect = Exception("connection refused")
        config = {"BSKY_HANDLE": "user.bsky.social", "BSKY_APP_PASSWORD": "pw"}
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09", config=config)
        self.assertEqual(result["posts"], [])
        self.assertIn("connection refused", result["error"])
        self.assertNotIn("auth failed", result["error"])

    @patch("lib.bluesky.http.request")
    def test_cloudflare_403_returns_network_error(self, mock_request):
        from lib.http import HTTPError
        mock_request.side_effect = HTTPError("HTTP 403: Forbidden", 403, "<html>Cloudflare</html>")
        config = {"BSKY_HANDLE": "user.bsky.social", "BSKY_APP_PASSWORD": "pw"}
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09", config=config)
        self.assertEqual(result["posts"], [])
        self.assertIn("Cloudflare", result["error"])
        self.assertIn("network", result["error"].lower())

    @patch("lib.bluesky.http.request")
    def test_401_returns_credentials_error(self, mock_request):
        from lib.http import HTTPError
        mock_request.side_effect = HTTPError("HTTP 401: Unauthorized", 401, "")
        config = {"BSKY_HANDLE": "user.bsky.social", "BSKY_APP_PASSWORD": "pw"}
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09", config=config)
        self.assertEqual(result["posts"], [])
        self.assertIn("Invalid credentials", result["error"])

    @patch("lib.bluesky.http.request")
    def test_successful_search_passes_bearer(self, mock_request):
        # First call: createSession, second call: searchPosts
        mock_request.side_effect = [
            {"accessJwt": "tok123", "refreshJwt": "ref456"},
            {"posts": [{"uri": "at://did/app.bsky.feed.post/abc", "author": {"handle": "u1"}, "record": {"text": "hi"}}]},
        ]
        config = {"BSKY_HANDLE": "user.bsky.social", "BSKY_APP_PASSWORD": "pw"}
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09", config=config)
        self.assertEqual(len(result["posts"]), 1)
        # Verify the search call included the Bearer token
        search_call = mock_request.call_args_list[1]
        self.assertEqual(search_call.kwargs.get("headers", {}), {"Authorization": "Bearer tok123"})

    @patch("lib.bluesky.http.request")
    def test_401_search_refreshes_session_once(self, mock_request):
        from lib.http import HTTPError

        mock_request.side_effect = [
            {"accessJwt": "tok-old", "refreshJwt": "ref-old"},
            HTTPError("HTTP 401: Unauthorized", 401, ""),
            {"accessJwt": "tok-new", "refreshJwt": "ref-new"},
            {"posts": [{"uri": "at://did/app.bsky.feed.post/abc", "author": {"handle": "u1"}, "record": {"text": "hi"}}]},
        ]
        config = {"BSKY_HANDLE": "user.bsky.social", "BSKY_APP_PASSWORD": "pw"}
        result = bluesky.search_bluesky("test", "2026-01-01", "2026-03-09", config=config)
        self.assertEqual(len(result["posts"]), 1)
        self.assertEqual(mock_request.call_count, 4)
        self.assertEqual(mock_request.call_args_list[3].kwargs.get("headers", {}), {"Authorization": "Bearer tok-new"})


class TestSearchEndpointHostResolution(unittest.TestCase):
    """The default search host moved from `public.api.bsky.app` (the
    unauthenticated public mirror, now BunnyCDN-blocked for searchPosts) to
    `api.bsky.app` (the canonical authenticated AppView). BSKY_SEARCH_HOST
    env var or config value can override the default if Bluesky migrates
    infrastructure again. Same os.environ-or-config hybrid pattern as
    LAST30DAYS_STORE.
    """

    def setUp(self):
        # Snapshot env so per-test overrides don't leak
        self._saved_env = os.environ.pop("BSKY_SEARCH_HOST", None)

    def tearDown(self):
        if self._saved_env is not None:
            os.environ["BSKY_SEARCH_HOST"] = self._saved_env
        else:
            os.environ.pop("BSKY_SEARCH_HOST", None)

    def test_resolver_default_uses_canonical_appview(self):
        # Regression guard against the public mirror reappearing as the default.
        # Anchored at the resolver because that is the code path search_bluesky
        # actually calls; a module-level constant would not catch a resolver
        # regression.
        self.assertIn("api.bsky.app", bluesky._resolve_search_url())

    def test_resolver_default_does_not_use_public_mirror(self):
        # Hard regression guard — the exact host that BunnyCDN was blocking.
        # Asserted at the resolver level (the runtime path) so a default-host
        # regression in _resolve_search_url is actually caught.
        self.assertNotIn("public.api.bsky.app", bluesky._resolve_search_url())

    def test_resolver_default_when_no_override(self):
        self.assertEqual(
            bluesky._resolve_search_url(),
            "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        )

    def test_resolver_env_var_override(self):
        os.environ["BSKY_SEARCH_HOST"] = "staging.bsky.app"
        self.assertEqual(
            bluesky._resolve_search_url(),
            "https://staging.bsky.app/xrpc/app.bsky.feed.searchPosts",
        )

    def test_resolver_config_dict_override(self):
        # User has BSKY_SEARCH_HOST only in .env file (project loads .env into
        # config, not os.environ). Resolver must read both.
        url = bluesky._resolve_search_url({"BSKY_SEARCH_HOST": "pds.example.com"})
        self.assertEqual(url, "https://pds.example.com/xrpc/app.bsky.feed.searchPosts")

    def test_resolver_env_var_wins_over_config(self):
        # When both are set, os.environ takes precedence (matches LAST30DAYS_STORE)
        os.environ["BSKY_SEARCH_HOST"] = "shell-host.example"
        url = bluesky._resolve_search_url({"BSKY_SEARCH_HOST": "config-host.example"})
        self.assertIn("shell-host.example", url)
        self.assertNotIn("config-host.example", url)

    def test_resolver_output_does_not_use_public_mirror(self):
        # Regression guard at the resolver level (not just the constant) —
        # this is what runtime actually calls. The constant-level guard
        # above doesn't catch a regression where the resolver reverts.
        self.assertNotIn("public.api.bsky.app", bluesky._resolve_search_url())

    def test_resolver_strips_surrounding_whitespace(self):
        # Pre-fix: " api.bsky.app " produced "https:// api.bsky.app /xrpc/..."
        # which urllib raises ValueError on with no hint the env var caused it.
        os.environ["BSKY_SEARCH_HOST"] = "  api.bsky.app  "
        self.assertEqual(
            bluesky._resolve_search_url(),
            "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        )

    def test_resolver_rejects_embedded_path(self):
        # "my-proxy.com/xrpc/prefix" would have doubled the /xrpc/ segment.
        # We fall back to the default to avoid a guaranteed 404.
        os.environ["BSKY_SEARCH_HOST"] = "my-proxy.example.com/xrpc/prefix"
        self.assertEqual(
            bluesky._resolve_search_url(),
            "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        )

    def test_resolver_strips_embedded_scheme(self):
        # Users who paste a full URL get a sane outcome, not a malformed URL.
        os.environ["BSKY_SEARCH_HOST"] = "https://api.bsky.app"
        self.assertEqual(
            bluesky._resolve_search_url(),
            "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        )

    def test_resolver_empty_string_falls_back_to_default(self):
        os.environ["BSKY_SEARCH_HOST"] = ""
        self.assertEqual(
            bluesky._resolve_search_url(),
            "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        )


class TestAppPasswordFormat(unittest.TestCase):
    """Bluesky app passwords are 19-char xxxx-xxxx-xxxx-xxxx (lowercase
    alphanumeric, three hyphens at fixed positions). Main-account passwords
    are accepted by createSession but are bad hygiene. The validator detects
    the format mismatch without gating any caller.
    """

    def test_accepts_valid_app_password_form(self):
        # Use a fake example — never a real password
        self.assertTrue(bluesky._validate_app_password_format("wfwp-cq7o-5six-7wy5"))

    def test_rejects_length_15_string(self):
        # The exact failure mode that triggered the 2026-05-04 investigation:
        # user stored their main login password (15 chars) in BSKY_APP_PASSWORD
        self.assertFalse(bluesky._validate_app_password_format("mainpassword123"))

    def test_rejects_16_char_no_hyphen_string(self):
        # Hex-style API key shape — common confusion with other services
        self.assertFalse(bluesky._validate_app_password_format("abcdef0123456789"))

    def test_rejects_uppercase_letters(self):
        # Bluesky app passwords are all-lowercase by spec
        self.assertFalse(bluesky._validate_app_password_format("WFWP-cq7o-5six-7wy5"))

    def test_rejects_underscore_separator(self):
        # Wrong separator
        self.assertFalse(bluesky._validate_app_password_format("wfwp_cq7o_5six_7wy5"))

    def test_rejects_special_chars_in_groups(self):
        # Special characters are not part of the alphanumeric class
        self.assertFalse(bluesky._validate_app_password_format("wfwp-cq7o-5six-7wy@"))

    def test_rejects_empty_string(self):
        self.assertFalse(bluesky._validate_app_password_format(""))

    def test_rejects_none(self):
        # Callers may pass config.get('BSKY_APP_PASSWORD') which is None when unset
        self.assertFalse(bluesky._validate_app_password_format(None))

    def test_rejects_integer(self):
        # Defensive: don't crash if a numeric value sneaks in
        self.assertFalse(bluesky._validate_app_password_format(123456789012345))

    def test_rejects_list(self):
        # Defensive: don't crash on iterables
        self.assertFalse(bluesky._validate_app_password_format(["wfwp", "cq7o", "5six", "7wy5"]))

if __name__ == "__main__":
    unittest.main()

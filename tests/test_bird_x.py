import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from lib import bird_x
from lib.bird_x import parse_bird_response

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDORED_BIRD = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib" / "vendor" / "bird-search" / "bird-search.mjs"


class TestSubprocessEnv(unittest.TestCase):
    """_subprocess_env() passes only the vendored client's env surface (issue #1063)."""

    def _ambient(self, **overrides):
        base = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/test",
            "NODE_ENV": "production",
            "AUTH_TOKEN": "ambient-token",
            "CT0": "ambient-ct0",
            "TWITTER_AUTH_TOKEN": "ambient-tw-token",
            "TWITTER_CT0": "ambient-tw-ct0",
            "LAST30DAYS_DISABLE_BROWSER_COOKIES": "0",
            "BIRD_QUERY_IDS_CACHE": "/tmp/ids.json",
            "SCRAPECREATORS_API_KEY": "sc-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
        }
        base.update(overrides)
        return base

    def _call(self, ambient, credentials=None):
        from lib import bird_x
        old = bird_x._credentials
        try:
            bird_x._credentials = dict(credentials or {})
            # None means "absent" - os.environ cannot hold None values.
            patch_env = {k: v for k, v in ambient.items() if v is not None}
            with mock.patch.dict(os.environ, patch_env, clear=True):
                return bird_x._subprocess_env()
        finally:
            bird_x._credentials = old

    def test_unrelated_ambient_secrets_are_excluded(self):
        out = self._call(self._ambient())
        self.assertNotIn("SCRAPECREATORS_API_KEY", out)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", out)

    def test_runtime_and_client_vars_pass_through(self):
        out = self._call(self._ambient())
        self.assertEqual("/usr/bin:/bin", out.get("PATH"))
        self.assertEqual("/home/test", out.get("HOME"))
        self.assertEqual("production", out.get("NODE_ENV"))
        self.assertEqual("ambient-token", out.get("AUTH_TOKEN"))
        self.assertEqual("ambient-ct0", out.get("CT0"))
        self.assertEqual("ambient-tw-token", out.get("TWITTER_AUTH_TOKEN"))
        self.assertEqual("ambient-tw-ct0", out.get("TWITTER_CT0"))
        self.assertEqual("0", out.get("LAST30DAYS_DISABLE_BROWSER_COOKIES"))
        self.assertEqual("/tmp/ids.json", out.get("BIRD_QUERY_IDS_CACHE"))

    def test_absent_vars_are_omitted(self):
        out = self._call(self._ambient(NODE_ENV=None))
        self.assertNotIn("NODE_ENV", out)
        out = self._call(self._ambient(LAST30DAYS_DISABLE_BROWSER_COOKIES=None))
        self.assertNotIn("LAST30DAYS_DISABLE_BROWSER_COOKIES", out)

    def test_injected_credentials_override_ambient(self):
        out = self._call(self._ambient(), credentials={"AUTH_TOKEN": "injected", "CT0": "injected-ct0"})
        self.assertEqual("injected", out.get("AUTH_TOKEN"))
        self.assertEqual("injected-ct0", out.get("CT0"))

    def test_disable_flag_always_hard_set(self):
        out = self._call(self._ambient(), credentials={"AUTH_TOKEN": "t", "CT0": "c"})
        self.assertEqual("1", out.get("BIRD_DISABLE_BROWSER_COOKIES"))
        # ambient 0 is overridden to 1
        out = self._call(self._ambient(BIRD_DISABLE_BROWSER_COOKIES="0"))
        self.assertEqual("1", out.get("BIRD_DISABLE_BROWSER_COOKIES"))

    def test_ambient_bird_vars_pass_through(self):
        out = self._call(self._ambient(BIRD_FEATURES_PATH="/tmp/features.json"))
        self.assertEqual("/tmp/features.json", out.get("BIRD_FEATURES_PATH"))

    def test_allowlist_covers_vendored_client_env_reads(self):
        """Every process.env name the vendored client reads is reachable.

        Guards the allowlist against vendor drift: a future bird-search bump
        that reads a new non-BIRD_ env var must either be added to the
        allowlist or fail here, keeping the child env surface explicit
        (issue #1063).
        """
        import re

        from lib import bird_x

        vendor_dir = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib" / "vendor" / "bird-search"
        reads = set()
        for path in list(vendor_dir.rglob("*.js")) + list(vendor_dir.rglob("*.mjs")):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(
                r"process\.env\[\s*['\"]([A-Z0-9_]+)['\"]\s*\]|process\.env\.([A-Z0-9_]+)",
                text,
            ):
                reads.add(m.group(1) or m.group(2))
            # cookies.js reads via helpers with the keys passed as arguments:
            # envFlagEnabled('NAME') and readEnvCookie(cookies, ['A', 'B'], ...).
            for m in re.finditer(r"envFlagEnabled\(\s*['\"]([A-Z0-9_]+)['\"]\s*\)", text):
                reads.add(m.group(1))
            for m in re.finditer(r"readEnvCookie\(\s*\w+\s*,\s*\[([^\]]*)\]", text):
                reads.update(re.findall(r"['\"]([A-Z0-9_]+)['\"]", m.group(1)))
        self.assertTrue(reads, "vendored client env reads not found")
        allowlist = set(bird_x._SUBPROCESS_ENV_ALLOWLIST)
        uncovered = {
            name for name in reads
            if not name.startswith("BIRD_") and name not in allowlist
        }
        self.assertEqual(set(), uncovered)


class TestBirdXEngagementZero(unittest.TestCase):
    def test_zero_likes_preserved(self):
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "likeCount": 0,
                "retweetCount": 5,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertEqual(0, items[0]["engagement"]["likes"])
        self.assertEqual(5, items[0]["engagement"]["reposts"])

@unittest.skipUnless(shutil.which("node"), "node is required for vendored Bird tests")
class TestVendoredBirdRuntime(unittest.TestCase):
    def test_check_uses_env_credentials_without_browser_cookie_dependency(self):
        env = os.environ.copy()
        env["AUTH_TOKEN"] = "dummy-auth"
        env["CT0"] = "dummy-ct0"

        result = subprocess.run(
            ["node", str(VENDORED_BIRD), "--check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["authenticated"])
        self.assertEqual("env AUTH_TOKEN", payload["source"])

    def test_check_with_browser_lookup_disabled_returns_json_warnings(self):
        env = os.environ.copy()
        env.pop("AUTH_TOKEN", None)
        env.pop("CT0", None)
        env["BIRD_DISABLE_BROWSER_COOKIES"] = "1"

        result = subprocess.run(
            ["node", str(VENDORED_BIRD), "--check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(1, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["authenticated"])
        self.assertTrue(payload["warnings"])
        self.assertIn("Missing auth_token", " ".join(payload["warnings"]))

    def test_browser_cookie_helpers_lazy_load_sweet_cookie(self):
        sweet_cookie_dir = (
            REPO_ROOT
            / "skills"
            / "last30days"
            / "scripts"
            / "lib"
            / "vendor"
            / "bird-search"
            / "lib"
            / "node_modules"
            / "@steipete"
            / "sweet-cookie"
        )
        if sweet_cookie_dir.exists():
            self.skipTest("vendored sweet-cookie test stub already exists")

        sweet_cookie_dir.mkdir(parents=True)
        (sweet_cookie_dir / "package.json").write_text(
            json.dumps(
                {
                    "name": "@steipete/sweet-cookie",
                    "type": "module",
                    "exports": "./index.js",
                }
            ),
            encoding="utf-8",
        )
        (sweet_cookie_dir / "index.js").write_text(
            textwrap.dedent(
                """
                export async function getCookies(options) {
                  const browser = options.browsers?.[0] ?? "unknown";
                  return {
                    cookies: [
                      { name: "auth_token", value: `${browser}-auth`, domain: "x.com" },
                      { name: "ct0", value: `${browser}-ct0`, domain: "x.com" },
                    ],
                    warnings: [],
                  };
                }
                """
            ),
            encoding="utf-8",
        )

        try:
            result = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    textwrap.dedent(
                        """
                        import {
                          extractCookiesFromSafari,
                          extractCookiesFromChrome,
                          extractCookiesFromFirefox,
                        } from "./skills/last30days/scripts/lib/vendor/bird-search/lib/cookies.js";

                        const payload = await Promise.all([
                          extractCookiesFromSafari(),
                          extractCookiesFromChrome("Profile 1"),
                          extractCookiesFromFirefox("default-release"),
                        ]);
                        process.stdout.write(JSON.stringify(payload));
                        """
                    ),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual("Safari", payload[0]["cookies"]["source"])
            self.assertEqual('Chrome profile "Profile 1"', payload[1]["cookies"]["source"])
            self.assertEqual(
                'Firefox profile "default-release"', payload[2]["cookies"]["source"]
            )
            self.assertEqual("safari-auth", payload[0]["cookies"]["authToken"])
            self.assertEqual("chrome-auth", payload[1]["cookies"]["authToken"])
            self.assertEqual("firefox-auth", payload[2]["cookies"]["authToken"])
        finally:
            shutil.rmtree(sweet_cookie_dir, ignore_errors=True)
            for path in [sweet_cookie_dir.parent, sweet_cookie_dir.parent.parent]:
                try:
                    path.rmdir()
                except OSError:
                    pass

    def test_none_likes_when_missing(self):
        tweets = [
            {
                "id": "1",
                "text": "test tweet with no engagement fields",
                "permanent_url": "https://x.com/u/status/1",
                # no likeCount, like_count, or favorite_count
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertIsNone(items[0]["engagement"])

    def test_fallback_to_second_key(self):
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "like_count": 7,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertEqual(7, items[0]["engagement"]["likes"])

    def test_zero_does_not_fall_through(self):
        """likeCount=0 should not fall through to like_count=10."""
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "likeCount": 0,
                "like_count": 10,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertEqual(0, items[0]["engagement"]["likes"])

    def test_engagement_none_when_all_fields_missing(self):
        """All-None engagement dict should become None, not propagate."""
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertIsNone(items[0]["engagement"])

    def test_engagement_preserved_when_any_field_present(self):
        """Engagement dict kept when at least one metric exists."""
        tweets = [
            {
                "id": "1",
                "text": "test",
                "permanent_url": "https://x.com/u/status/1",
                "likeCount": 5,
            }
        ]
        items = parse_bird_response(tweets, "test query")
        self.assertIsNotNone(items[0]["engagement"])
        self.assertEqual(5, items[0]["engagement"]["likes"])


class TestRunBirdSearchJsonDecodeRetry(unittest.TestCase):
    """When bird-search returns non-JSON stdout, retry the subprocess.

    Twitter's edge sometimes serves an HTML anti-bot interstitial in place of
    JSON. Before this fix, that response made json.loads raise JSONDecodeError
    and the function returned {"items": []} with no diagnostic — silent-empty
    against an orchestrator that can't distinguish "Twitter blocked us" from
    "no tweets matched the query."
    """

    def _make_result(self, stdout: str, stderr: str = "", returncode: int = 0):
        from lib.subproc import SubprocResult
        return SubprocResult(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_retries_subprocess_on_html_interstitial_then_succeeds(self):
        """First subprocess attempt returns HTML; second returns JSON → success."""
        from unittest import mock
        from lib import bird_x

        html_interstitial = "<!DOCTYPE html><html><body>Rate limited</body></html>"
        json_success = '[{"id": "1", "text": "tweet"}]'

        results = [
            (self._make_result(stdout=html_interstitial), None),
            (self._make_result(stdout=json_success), None),
        ]

        with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=results), \
             mock.patch.object(bird_x.time, "sleep") as mock_sleep:
            response = bird_x._run_bird_search("test", count=10, timeout=30)

        self.assertNotIn("error", response)
        self.assertEqual(response["items"], [{"id": "1", "text": "tweet"}])
        # Should have slept between the failed first attempt and the retry.
        mock_sleep.assert_called_once_with(bird_x.JSON_DECODE_RETRY_DELAY)

    def test_returns_error_after_all_retries_exhausted(self):
        """All attempts return HTML → error dict with diagnostic + items=[]."""
        from unittest import mock
        from lib import bird_x

        html_interstitial = "<!DOCTYPE html><html>blocked</html>"
        results = [
            (self._make_result(stdout=html_interstitial), None),
            (self._make_result(stdout=html_interstitial), None),
        ]

        with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=results), \
             mock.patch.object(bird_x.time, "sleep"):
            response = bird_x._run_bird_search("test", count=10, timeout=30)

        self.assertIn("error", response)
        self.assertIn("Invalid JSON response", response["error"])
        # Diagnostic message names the anti-bot interstitial so it's
        # distinguishable from a genuine no-results case in logs.
        self.assertIn("anti-bot interstitial", response["error"].lower())
        self.assertEqual(response["items"], [])

    def test_terminal_subprocess_error_is_not_retried(self):
        """Subprocess timeout / spawn failure → terminal error, no retry."""
        from unittest import mock
        from lib import bird_x

        timeout_error = {"error": "Search timed out after 30s", "items": []}
        results = [(None, timeout_error)]

        with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=results), \
             mock.patch.object(bird_x.time, "sleep") as mock_sleep:
            response = bird_x._run_bird_search("test", count=10, timeout=30)

        self.assertEqual(response, timeout_error)
        mock_sleep.assert_not_called()

if __name__ == "__main__":
    unittest.main()


class TestXFromAndAboutLanes(unittest.TestCase):
    """U7/U8: FROM lane drops the topic-AND; ABOUT lane queries @handle and
    excludes the handle's own tweets."""

    def _result(self, body_items):
        import json as _j
        class _R:
            returncode = 0
            stderr = ""
        r = _R()
        r.stdout = _j.dumps({"items": body_items})
        return r

    def test_from_lane_drops_topic_and(self):
        from unittest import mock
        from lib import bird_x
        captured = []

        def fake_run(cmd, timeout=None, env=None):
            captured.append(cmd[2])  # the query string arg
            return self._result([])

        with mock.patch.object(bird_x.subproc, "run_with_timeout", side_effect=fake_run):
            bird_x.search_handles(["xuezhao"], "lan xuezhao", "2026-05-19", count_per=1)
        self.assertEqual(captured[0], "from:xuezhao since:2026-05-19")
        self.assertNotIn("lan xuezhao", captured[0])

    def test_mention_lane_queries_at_handle(self):
        from unittest import mock
        from lib import bird_x
        captured = []

        def fake_run(cmd, timeout=None, env=None):
            captured.append(cmd[2])
            return self._result([])

        with mock.patch.object(bird_x.subproc, "run_with_timeout", side_effect=fake_run):
            bird_x.search_mentions(["xuezhao"], "2026-05-19", count_per=1)
        self.assertEqual(captured[0], "@xuezhao since:2026-05-19")

    def test_mention_lane_excludes_own_tweets(self):
        from unittest import mock
        from lib import bird_x
        parsed = [
            {"url": "https://x.com/xuezhao/status/1", "title": "own tweet"},
            {"url": "https://twitter.com/xuezhao/status/3", "title": "own legacy-domain tweet"},
            {"url": "https://x.com/fan99/status/2", "title": "mention of them"},
        ]
        with mock.patch.object(bird_x.subproc, "run_with_timeout",
                               return_value=self._result([{"id": "x"}])), \
             mock.patch.object(bird_x, "parse_bird_response", return_value=parsed):
            out = bird_x.search_mentions(["xuezhao"], "2026-05-19", count_per=5)
        urls = [it["url"] for it in out]
        self.assertNotIn("https://x.com/xuezhao/status/1", urls)          # own (x.com) excluded
        self.assertNotIn("https://twitter.com/xuezhao/status/3", urls)    # own (twitter.com) excluded
        self.assertIn("https://x.com/fan99/status/2", urls)              # mention kept

    def test_mention_lane_empty_when_no_mentions(self):
        from unittest import mock
        from lib import bird_x
        with mock.patch.object(bird_x.subproc, "run_with_timeout",
                               return_value=self._result([])), \
             mock.patch.object(bird_x, "parse_bird_response", return_value=[]):
            out = bird_x.search_mentions(["xuezhao"], "2026-05-19")
        self.assertEqual(out, [])
class TestProbeAndDiagnoseHonesty(unittest.TestCase):
    """U5: --diagnose probe + true auth lane; X is not reported green when dead."""

    def setUp(self):
        from lib import bird_x
        bird_x._probe_cache = "unset"
        bird_x._credentials = {"AUTH_TOKEN": "t", "CT0": "c"}  # injected creds present

    def tearDown(self):
        from lib import bird_x
        bird_x._probe_cache = "unset"
        bird_x._credentials = {}

    def test_probe_true_when_response_ok(self):
        from unittest import mock
        from lib import bird_x
        with mock.patch.object(bird_x, "_run_bird_search", return_value={"items": [{"id": "1"}]}):
            self.assertTrue(bird_x.probe_works())

    def test_probe_false_on_auth_error(self):
        from unittest import mock
        from lib import bird_x
        with mock.patch.object(bird_x, "_run_bird_search",
                               return_value={"error": "Missing auth_token", "items": []}):
            self.assertIs(bird_x.probe_works(), False)

    def test_probe_none_on_timeout_inconclusive(self):
        from unittest import mock
        from lib import bird_x
        with mock.patch.object(bird_x, "_run_bird_search",
                               return_value={"error": "Search timed out after 8s", "items": []}):
            self.assertIsNone(bird_x.probe_works())

    def test_probe_false_when_no_credentials(self):
        from unittest import mock
        from lib import bird_x
        bird_x._credentials = {}
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIs(bird_x.probe_works(), False)

    def test_probe_cached_per_process(self):
        from unittest import mock
        from lib import bird_x
        with mock.patch.object(bird_x, "_run_bird_search",
                               return_value={"items": [{"id": "1"}]}) as m:
            bird_x.probe_works()
            bird_x.probe_works()
            self.assertEqual(m.call_count, 1)  # cached, not re-run

    def test_get_x_source_status_reports_true_lane(self):
        from unittest import mock
        from lib import env, bird_x
        cfg = {"AUTH_TOKEN": "t", "CT0": "c", "_AUTH_TOKEN_SOURCE": "browser"}
        with mock.patch.object(bird_x, "get_bird_status",
                               return_value={"installed": True, "authenticated": True,
                                             "username": "env AUTH_TOKEN", "can_install": True}):
            status = env.get_x_source_status(cfg, probe=False)
        self.assertEqual(status["bird_username"], "browser AUTH_TOKEN")

    def test_diagnose_probe_downgrades_when_dead(self):
        from unittest import mock
        from lib import env, bird_x
        cfg = {"AUTH_TOKEN": "t", "CT0": "c", "_AUTH_TOKEN_SOURCE": "browser"}
        with mock.patch.object(bird_x, "get_bird_status",
                               return_value={"installed": True, "authenticated": True,
                                             "username": "env AUTH_TOKEN", "can_install": True}), \
             mock.patch.object(bird_x, "probe_works", return_value=False):
            status = env.get_x_source_status(cfg, probe=True)
        self.assertFalse(status["bird_authenticated"])
        self.assertIn("no working X auth", status["bird_username"])


class TestHandleSearchLogsOnSuccess(unittest.TestCase):
    """U6: handle searches log query + count on success, not only on failure."""

    def test_search_handles_logs_on_success(self):
        from unittest import mock
        from lib import bird_x

        class _R:
            returncode = 0
            stdout = '{"items": [{"id": "1"}]}'
            stderr = ""

        logged = []
        with mock.patch.object(bird_x.subproc, "run_with_timeout", return_value=_R()), \
             mock.patch.object(bird_x, "_log", side_effect=lambda m: logged.append(m)):
            bird_x.search_handles(["mvanhorn"], "matt van horn", "2026-05-19", count_per=1)
        self.assertTrue(any("Searching:" in m for m in logged),
                        f"expected a Searching: log on success, got {logged}")


class TestStrongestTokenRetryAnchored(unittest.TestCase):
    """The last-chance retry must keep an entity anchor, not collapse to a bare
    generic token (e.g. 'compound') that floods the X pool with off-topic noise.
    """

    def test_last_chance_retry_keeps_entity_anchor(self):
        from unittest import mock
        from lib import bird_x

        queries = []

        def fake_run(query, count, timeout):
            queries.append(query)
            return {"items": []}  # always 0 → forces every retry tier

        # extract_compound_terms may run; let it. Force all bird calls empty.
        with mock.patch.object(bird_x, "_run_bird_search", side_effect=fake_run):
            bird_x.search_x("trevin chow ai agents compound", "2026-05-19", "2026-06-18")

        self.assertTrue(queries, "expected at least one bird query")
        last = queries[-1]
        # The final (last-chance) query keeps the entity anchor ...
        self.assertIn("trevin", last)
        # ... and is NOT a bare generic token query.
        self.assertFalse(last.startswith("compound "), f"bare generic retry: {last!r}")
        self.assertNotEqual(last, "compound since:2026-05-19")

    def test_retry_with_single_distinctive_token_no_crash(self):
        from unittest import mock
        from lib import bird_x

        queries = []

        def fake_run(query, count, timeout):
            queries.append(query)
            return {"items": []}

        with mock.patch.object(bird_x, "_run_bird_search", side_effect=fake_run):
            # 'trending tools' is all low-signal except nothing distinctive ->
            # whatever survives, the retry must not crash and stays anchored.
            bird_x.search_x("agentcookie", "2026-05-19", "2026-06-18")

        self.assertTrue(queries)
        self.assertIn("agentcookie", queries[-1])


class TestBirdRetryQueryCorrectness(unittest.TestCase):
    def test_quoted_topic_only_generates_balanced_retry_queries(self):
        from lib import bird_x

        queries = []

        def fake_run(query, count, timeout):
            queries.append(query)
            if len(queries) == 1:
                return {"items": []}
            return {"error": "Bird search failed", "items": []}

        with mock.patch.object(
            bird_x,
            "_extract_core_subject",
            return_value='immobilienmakler(berlin "mixed-use',
        ), mock.patch(
            "lib.query.extract_compound_terms",
            return_value=['"immobilienmakler berlin"'],
        ), mock.patch.object(bird_x, "_run_bird_search", side_effect=fake_run):
            response = bird_x.search_x(
                '"Immobilienmakler Berlin" competitors',
                "2026-07-12",
                "2026-07-19",
            )

        self.assertGreaterEqual(len(queries), 2)
        self.assertEqual(
            "immobilienmakler berlin mixed-use since:2026-07-12",
            queries[0],
        )
        for query in queries:
            self.assertEqual(0, query.count('"') % 2, query)
            self.assertEqual(query.count("("), query.count(")"), query)
        self.assertNotIn("error", response)
        self.assertEqual([], response["items"])

    def test_every_failed_attempt_still_reports_backend_failure(self):
        from lib import bird_x

        with mock.patch.object(
            bird_x, "_extract_core_subject", return_value="immobilienmakler berlin market"
        ), mock.patch.object(
            bird_x,
            "_run_bird_search",
            return_value={"error": "Bird search failed", "items": []},
        ):
            response = bird_x.search_x(
                "Immobilienmakler Berlin market", "2026-07-12", "2026-07-19"
            )

        self.assertEqual("Bird search failed", response["error"])


class LeadingMentionsTests(unittest.TestCase):
    """U5: leading @mentions parsed from post text identify reply targets."""

    def test_single_leading_mention(self):
        from lib import bird_x
        self.assertEqual(["alpha"], bird_x._leading_mentions("@alpha thanks so much!"))

    def test_multiple_leading_mentions(self):
        from lib import bird_x
        self.assertEqual(["alpha", "beta"], bird_x._leading_mentions("@alpha @beta hi"))

    def test_in_body_mention_not_collected(self):
        from lib import bird_x
        self.assertEqual([], bird_x._leading_mentions("hello @gamma nice work"))

    def test_punctuation_stripped(self):
        from lib import bird_x
        self.assertEqual(["alpha"], bird_x._leading_mentions("@alpha, nice"))

    def test_empty_text(self):
        from lib import bird_x
        self.assertEqual([], bird_x._leading_mentions(""))
        self.assertEqual([], bird_x._leading_mentions(None))


if __name__ == "__main__":
    unittest.main()


class CredentialScrubInFailureDetailTests(unittest.TestCase):
    """A handle-lane failure reason is recorded in the run's source_status and
    rendered in the report, so subprocess stderr must not carry the X session
    cookies into user-facing output."""

    def tearDown(self):
        bird_x._credentials.clear()

    def test_injected_cookie_values_are_redacted_from_notes(self):
        bird_x.set_credentials("auth-token-sentinel-value", "ct0-sentinel-value")
        failures: list[str] = []
        proc = mock.Mock(
            returncode=1,
            stdout="",
            stderr="rejected cookie auth_token=auth-token-sentinel-value ct0=ct0-sentinel-value",
        )
        with mock.patch.object(bird_x.subproc, "run_with_timeout", return_value=proc):
            bird_x.search_handles(
                ["someone"], "topic", "2026-05-19", count_per=1,
                failure_out=failures,
            )
        joined = " ".join(failures)
        assert failures, "a non-zero exit must be reported"
        self.assertNotIn("auth-token-sentinel-value", joined)
        self.assertNotIn("ct0-sentinel-value", joined)
        self.assertIn("<redacted>", joined)
        # The actionable part of the reason survives.
        self.assertIn("exited 1", joined)

    def test_scrub_leaves_ordinary_stderr_intact(self):
        bird_x.set_credentials("auth-token-sentinel-value", "ct0-sentinel-value")
        self.assertEqual(
            "connect ETIMEDOUT 104.244.42.1:443",
            bird_x._scrub_credentials("connect ETIMEDOUT 104.244.42.1:443"),
        )

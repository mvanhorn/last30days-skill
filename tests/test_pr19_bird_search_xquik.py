"""Regression tests for PR 19: vendored bird-search, bird_x & xquik security and lifecycle.

Covers:
- HIGH-150: xquik handle validation, topic query sanitization, date-window filtering, schema guards, and None/ID coercion.
- HIGH-156: runtime-query-ids timeouts, byte/bundle caps, allowlisted manual redirects, and SearchTimeline-only refresh.
- HIGH-157: fetch body timeout, byte cap, signal composition, 404 body cancel, immediate abort on network/timeout errors, and --timeout-ms.
- HIGH-158: atomic private (0o700/0o600) cache writes, symlink refusal, concurrent write integrity, and read-only home refresh.
- HIGH-159: cursor cycle detection, empty-page stop, maxPages: 0/non-finite guards, default page cap, and partial-page preservation.
- HIGH-160: cookies.js FROM_BROWSER=off/BROWSER_CONSENT refusal and bird_x.set_credentials clearing stale failover/keys.
- HIGH-161: cancel, deadline_monotonic, and capture_limit_bytes forwarding to subproc.run_with_timeout in bird_x.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import bird_x, env, xquik
from lib.subproc import SubprocResult, SubprocTimeout

REPO_ROOT = Path(__file__).resolve().parents[1]
BIRD_DIR = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib" / "vendor" / "bird-search"
BIRD_MJS = BIRD_DIR / "bird-search.mjs"
HAS_NODE = shutil.which("node") is not None


def _node_eval(script: str, *, env_overrides: dict[str, str | None] | None = None, timeout: float = 20) -> subprocess.CompletedProcess:
    run_env = os.environ.copy()
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                run_env.pop(key, None)
            else:
                run_env[key] = value
    return subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _register_sweet_cookie_stub(tmp_path: Path) -> Path:
    loader = tmp_path / "sweet-cookie-loader.mjs"
    loader.write_text(
        textwrap.dedent(
            """
            export async function resolve(specifier, context, nextResolve) {
              if (specifier === '@steipete/sweet-cookie') {
                return { url: 'sweet-cookie-stub://mock', shortCircuit: true };
              }
              return nextResolve(specifier, context);
            }
            export async function load(url, context, nextLoad) {
              if (url === 'sweet-cookie-stub://mock') {
                return {
                  format: 'module',
                  shortCircuit: true,
                  source: `
                    export async function getCookies(options) {
                      process.stderr.write('SWEET_COOKIE_CALLED=' + (options.browsers?.[0] ?? 'none') + '\\n');
                      return {
                        cookies: [
                          { name: 'auth_token', value: 'stub-browser-auth-1111', domain: 'x.com' },
                          { name: 'ct0', value: 'stub-browser-ct0-2222', domain: 'x.com' },
                        ],
                        warnings: [],
                      };
                    }
                  `,
                };
              }
              return nextLoad(url, context);
            }
            """
        ),
        encoding="utf-8",
    )
    register = tmp_path / "register-sweet-cookie.mjs"
    register.write_text(
        "import { register } from 'node:module';\n"
        f"register({json.dumps(loader.as_uri())});\n",
        encoding="utf-8",
    )
    return register


# ---------------------------------------------------------------------------
# HIGH-150: xquik.py handle validation, query sanitization, window & parsing
# ---------------------------------------------------------------------------


class TestHigh150XquikSecurityAndParsing:
    def test_handle_lanes_validate_handles_strip_whitespace_before_at_and_exclude_self_mentions(self):
        seen_queries: list[str] = []

        def fake_get(url, **kwargs):
            seen_queries.append(parse_qs(urlsplit(url).query)["q"][0])
            return {"tweets": []}

        bad_and_good = [None, "", "   ", " @alice ", "alice OR from:bob", "bad;handle", "a" * 16, 12345]
        with mock.patch("lib.xquik.http.get", side_effect=fake_get):
            xquik.search_handles(bad_and_good, "topic", "2026-05-01", "2026-05-31", token="dummy-key")
            xquik.search_mentions(bad_and_good, "2026-05-01", "2026-05-31", token="dummy-key")

        assert seen_queries == [
            "from:alice since:2026-05-01 until:2026-06-01",
            "@alice -from:alice since:2026-05-01 until:2026-06-01",
        ]

    @pytest.mark.parametrize(
        "raw_query,expected_prefix",
        [
            ("alpha OR beta", "(alpha OR beta)"),
            ("(alpha) OR (beta)", "((alpha) OR (beta))"),
            ('("alpha" OR "beta")', '("alpha" OR "beta")'),
            ('"unbalanced quote OR beta', "(unbalanced quote OR beta)"),
            ('"good phrase" and "dangling', '"good phrase" and dangling'),
            ('foo "a OR b"', 'foo "a OR b"'),
            ("foo) OR (bar", "(foo OR bar)"),
            ("foo since:2020-01-01 until:2020-12-31", "foo"),
            ('"since:2020" foo', '"since:2020" foo'),
            ("OR foo OR", "foo"),
        ],
    )
    def test_topic_query_sanitization_and_or_parenthesization(self, raw_query, expected_prefix):
        seen_queries: list[str] = []

        def fake_get(url, **kwargs):
            seen_queries.append(parse_qs(urlsplit(url).query)["q"][0])
            return {"tweets": []}

        with mock.patch("lib.xquik.expand_xquik_queries", return_value=[raw_query]), \
             mock.patch("lib.xquik.http.get", side_effect=fake_get):
            xquik.search_xquik(raw_query, "2026-05-01", "2026-05-31", depth="quick", token="dummy-key")

        assert seen_queries == [f"{expected_prefix} since:2026-05-01 until:2026-06-01"]

    def test_empty_sanitized_topic_query_is_skipped(self):
        with mock.patch("lib.xquik.expand_xquik_queries", return_value=['""', "()", "OR", "since:2020"]), \
             mock.patch("lib.xquik.http.get") as mock_get:
            res = xquik.search_xquik("()", "2026-05-01", "2026-05-31", depth="quick", token="dummy-key")
        assert res == {"items": []}
        mock_get.assert_not_called()

    def test_date_window_filtering_applies_to_topic_handle_and_mention_lanes(self):
        tweets = [
            {"id": "1001", "text": "too old", "createdAt": "2026-04-30T23:59:59Z", "author": {"username": "fan"}},
            {"id": "1002", "text": "start edge", "createdAt": "2026-05-01T00:00:00Z", "author": {"username": "fan"}},
            {"id": "1003", "text": "end edge", "createdAt": "2026-05-31T23:59:59Z", "author": {"username": "fan"}},
            {"id": "1004", "text": "too new", "createdAt": "2026-06-01T00:00:00Z", "author": {"username": "fan"}},
            # +05:00 converts to 2026-04-30T22:00:00Z -> outside window!
            {"id": "1005", "text": "tz before", "createdAt": "2026-05-01T03:00:00+05:00", "author": {"username": "fan"}},
            {"id": "1006", "text": "undated kept", "author": {"username": "fan"}},
        ]
        with mock.patch("lib.xquik.http.get", return_value={"tweets": tweets}):
            topic_items = xquik.search_xquik("agents", "2026-05-01", "2026-05-31", depth="quick", token="k")["items"]
            handle_items = xquik.search_handles(["fan"], "agents", "2026-05-01", "2026-05-31", token="k")
            mention_items = xquik.search_mentions(["other"], "2026-05-01", "2026-05-31", token="k")

        expected_urls = [
            "https://x.com/fan/status/1002",
            "https://x.com/fan/status/1003",
            "https://x.com/fan/status/1006",
        ]
        assert [it["url"] for it in topic_items] == expected_urls
        assert [it["url"] for it in handle_items] == expected_urls
        assert [it["url"] for it in mention_items] == expected_urls

    def test_schema_drift_and_none_fields_never_crash_or_fabricate_urls(self):
        failures: list[str] = []
        with mock.patch("lib.xquik.http.get", side_effect=[["not", "a", "dict"], "plain-string", None]):
            assert xquik.search_handles(["alice"], "t", "2026-05-01", "2026-05-31", token="k", failure_out=failures) == []
            assert xquik.search_handles(["alice"], "t", "2026-05-01", "2026-05-31", token="k", failure_out=failures) == []
            assert xquik.search_handles(["alice"], "t", "2026-05-01", "2026-05-31", token="k", failure_out=failures) == []
        assert len(failures) == 3

        malformed_tweets = [
            {"id": None, "text": "null id", "author": {"username": "alice"}},
            {"id": "None", "text": "str none id", "author": {"username": "alice"}},
            {"id": "123abc", "text": "alpha id", "author": {"username": "alice"}},
            {"id": "١٢٣٤٥", "text": "non-ascii digits", "author": {"username": "alice"}},
            {"id": True, "text": "bool id", "author": {"username": "alice"}},
            {"id": "1001", "text": "null username", "author": {"username": None}},
            {"id": "1002", "text": "bad username", "author": {"username": "alice/../../evil"}},
            {"id": "1003", "text": "string author", "author": "alice"},
            {"id": "1004", "text": "list author", "author": ["alice"]},
            {"id": "1005", "text": None, "createdAt": 12345, "author": {"username": " @valid_user "}},
        ]
        with mock.patch("lib.xquik.http.get", return_value={"tweets": malformed_tweets}):
            res = xquik.search_xquik("test", "2026-05-01", "2026-05-31", depth="quick", token="k")

        assert len(res["items"]) == 1
        item = res["items"][0]
        assert item["url"] == "https://x.com/valid_user/status/1005"
        assert item["author_handle"] == "valid_user"
        assert item["text"] == ""
        assert item["date"] is None
        assert xquik.parse_xquik_response("not-a-dict") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HIGH-156: runtime-query-ids redirect validation, timeouts, caps & SearchTimeline-only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh156QueryIdDiscoverySecurity:
    def test_discovery_blocks_cross_origin_downgrade_userinfo_port_and_redirect_loops(self, tmp_path):
        cache_file = tmp_path / "ids.json"
        script = textwrap.dedent(
            f"""
            import {{ createRuntimeQueryIdStore, validateDiscoveryUrl }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-query-ids.js';

            const rejected = [];
            for (const bad of [
              'http://x.com/?lang=en',
              'https://evil.example/bundle.js',
              'https://evil.x.com/bundle.js',
              'https://x.com.evil.example/bundle.js',
              'https://x.com:8443/?lang=en',
              'https://user:pass@x.com/?lang=en',
              'http://169.254.169.254/latest/meta-data/',
            ]) {{
              try {{
                validateDiscoveryUrl(bad);
                rejected.push({{ bad, ok: true }});
              }} catch (err) {{
                rejected.push({{ bad, ok: false }});
              }}
            }}

            const fetchedUrls = [];
            const fetchImpl = async (url, init) => {{
              fetchedUrls.push({{ url: String(url), redirect: init?.redirect }});
              const u = String(url);
              if (u === 'https://x.com/?lang=en') {{
                return new Response('', {{ status: 302, headers: {{ location: 'http://169.254.169.254/latest/meta-data/' }} }});
              }}
              if (u === 'https://x.com/explore') {{
                return new Response('', {{ status: 302, headers: {{ location: 'https://evil.example/steal' }} }});
              }}
              if (u === 'https://x.com/notifications') {{
                // Safe same-host relative redirect -> succeeds
                return new Response('', {{ status: 302, headers: {{ location: '/home' }} }});
              }}
              if (u === 'https://x.com/home') {{
                return new Response(
                  '<script src="https://abs.twimg.com/responsive-web/client-web/main.abc123.js"></script>' +
                  '<script src="https://abs.twimg.com/responsive-web/client-web/bad.redirect.js"></script>',
                  {{ status: 200 }},
                );
              }}
              if (u.endsWith('bad.redirect.js')) {{
                return new Response('', {{ status: 301, headers: {{ location: 'http://abs.twimg.com/downgrade.js' }} }});
              }}
              if (u.endsWith('main.abc123.js')) {{
                return new Response('e.exports={{queryId:"FreshSearchId_1",operationName:"SearchTimeline"}}', {{ status: 200 }});
              }}
              return new Response('', {{ status: 404 }});
            }};

            const store = createRuntimeQueryIdStore({{
              fetchImpl,
              cachePath: {json.dumps(str(cache_file))},
            }});
            const info = await store.refresh(['SearchTimeline'], {{ force: true }});

            // Redirect loop exceeding maxRedirects must stop cleanly
            let loopCount = 0;
            const loopStore = createRuntimeQueryIdStore({{
              cachePath: {json.dumps(str(tmp_path / "loop.json"))},
              maxRedirects: 2,
              fetchImpl: async (url) => {{
                loopCount += 1;
                return new Response('', {{ status: 302, headers: {{ location: `${{url}}&hop=${{loopCount}}` }} }});
              }},
            }});
            let loopErr = null;
            try {{
              await loopStore.refresh(['SearchTimeline'], {{ force: true }});
            }} catch (e) {{
              loopErr = e.message;
            }}

            process.stdout.write(JSON.stringify({{
              rejected,
              fetchedUrls,
              searchId: info?.snapshot?.ids?.SearchTimeline ?? null,
              loopCount,
              loopErr,
            }}));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert all(item["ok"] is False for item in data["rejected"]), data["rejected"]
        urls = [entry["url"] for entry in data["fetchedUrls"]]
        assert all(entry["redirect"] == "manual" for entry in data["fetchedUrls"])
        assert not any("169.254.169.254" in u or "evil.example" in u or u.startswith("http://") for u in urls), urls
        assert "https://x.com/home" in urls
        assert data["searchId"] == "FreshSearchId_1"
        assert data["loopErr"] is not None
        # 4 discovery pages * (1 initial + 2 allowed redirects = 3 fetches each) = 12 max
        assert data["loopCount"] == 12

    def test_discovery_enforces_body_timeout_byte_cap_bundle_cap_and_search_only_refresh(self, tmp_path):
        cache_file = tmp_path / "ids-caps.json"
        script = textwrap.dedent(
            f"""
            import {{ createRuntimeQueryIdStore }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-query-ids.js';
            import {{ TwitterClientBase }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';
            import {{ SEARCH_QUERY_ID_OPERATIONS }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-constants.js';

            // 1) Stalled body stream must abort within fetchTimeoutMs
            let stalledCancelled = false;
            const stalledStore = createRuntimeQueryIdStore({{
              cachePath: {json.dumps(str(tmp_path / "stalled.json"))},
              fetchTimeoutMs: 80,
              refreshTimeoutMs: 250,
              fetchImpl: async () => new Response(new ReadableStream({{
                start(controller) {{
                  controller.enqueue(new TextEncoder().encode('<html>partial'));
                }},
                cancel() {{
                  stalledCancelled = true;
                }},
              }}), {{ status: 200 }}),
            }});
            const t0 = Date.now();
            let stalledErr = null;
            try {{
              await stalledStore.refresh(['SearchTimeline'], {{ force: true }});
            }} catch (e) {{
              stalledErr = e.message;
            }}
            const stalledElapsed = Date.now() - t0;

            // 2) Oversized bundle stream must abort & cancel without buffering unbounded data;
            //    and bundle count must be capped at maxBundles.
            let oversizedCancelled = false;
            const bundleFetches = [];
            const htmlBundles = Array.from({{ length: 50 }}, (_, i) =>
              `https://abs.twimg.com/responsive-web/client-web/chunk.${{i}}.js`
            ).join(' ');
            const cappedStore = createRuntimeQueryIdStore({{
              cachePath: {json.dumps(str(cache_file))},
              maxBundles: 5,
              maxBytes: 4096,
              fetchImpl: async (url) => {{
                const u = String(url);
                if (u.startsWith('https://x.com/')) {{
                  return new Response(htmlBundles, {{ status: 200 }});
                }}
                bundleFetches.push(u);
                if (u.endsWith('chunk.0.js')) {{
                  return new Response(new ReadableStream({{
                    pull(controller) {{
                      controller.enqueue(new Uint8Array(3000));
                    }},
                    cancel() {{
                      oversizedCancelled = true;
                    }},
                  }}), {{ status: 200 }});
                }}
                return new Response('noop', {{ status: 200 }});
              }},
            }});
            await cappedStore.refresh(['SearchTimeline'], {{ force: true }});

            // 3) TwitterClientBase.refreshQueryIds only refreshes SearchTimeline (exits early)
            const origNodeEnv = process.env.NODE_ENV;
            delete process.env.NODE_ENV;
            const clientBundlesFetched = [];
            const origFetch = globalThis.fetch;
            globalThis.fetch = async (url) => {{
              const u = String(url);
              if (u.startsWith('https://x.com/')) {{
                const body = Array.from({{ length: 18 }}, (_, i) =>
                  `https://abs.twimg.com/responsive-web/client-web/b${{i}}.js`
                ).join(' ');
                return new Response(body, {{ status: 200 }});
              }}
              clientBundlesFetched.push(u);
              if (u.endsWith('b0.js')) {{
                return new Response('e.exports={{queryId:"SearchOnlyId99",operationName:"SearchTimeline"}}', {{ status: 200 }});
              }}
              return new Response('noop', {{ status: 200 }});
            }};
            process.env.BIRD_QUERY_IDS_CACHE = {json.dumps(str(tmp_path / "client-cache.json"))};
            const client = new TwitterClientBase({{ cookies: {{ authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' }}, timeoutMs: 5000 }});
            await client.refreshQueryIds();
            globalThis.fetch = origFetch;
            if (origNodeEnv !== undefined) process.env.NODE_ENV = origNodeEnv;

            process.stdout.write(JSON.stringify({{
              searchOps: SEARCH_QUERY_ID_OPERATIONS,
              stalledErr,
              stalledElapsed,
              stalledCancelled,
              oversizedCancelled,
              bundleFetchCount: bundleFetches.length,
              clientBundlesFetchedCount: clientBundlesFetched.length,
            }}));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["searchOps"] == ["SearchTimeline"]
        assert data["stalledErr"] is not None
        assert data["stalledElapsed"] < 2000
        assert data["stalledCancelled"] is True
        assert data["oversizedCancelled"] is True
        assert data["bundleFetchCount"] == 5
        # First chunk of concurrency 6 finds SearchTimeline in b0.js and stops before chunks 2-3 (18 total).
        assert data["clientBundlesFetchedCount"] <= 6


# ---------------------------------------------------------------------------
# HIGH-157: fetch body timeout, byte cap, signal composition, 404 cancel, abort loop, --timeout-ms
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh157FetchLifecycleAndTimeoutCli:
    def test_body_timeout_signal_composition_404_cancel_byte_cap_and_no_4x_retry(self):
        script = textwrap.dedent(
            """
            import { TwitterClientBase } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';
            import { withSearch } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-search.js';

            const Client = withSearch(TwitterClientBase);

            // 1) Stalled body after 200 headers must time out AND not multiply 4x across fallback queryIds
            let stallFetches = 0;
            let stallCancelled = false;
            let refreshCalls = 0;
            globalThis.fetch = async () => {
              stallFetches += 1;
              return new Response(new ReadableStream({
                start(controller) {
                  controller.enqueue(new TextEncoder().encode('{"partial":'));
                },
                cancel() {
                  stallCancelled = true;
                },
              }), { status: 200, headers: { 'content-type': 'application/json' } });
            };
            const client1 = new Client({ cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' }, timeoutMs: 120 });
            client1.refreshQueryIds = async () => { refreshCalls += 1; };
            const t0 = Date.now();
            const r1 = await client1.search('hello', 5);
            const elapsed1 = Date.now() - t0;

            // 2) 404 cancels response.body before continuing to next queryId
            let cancelled404Count = 0;
            let fetchCount404 = 0;
            globalThis.fetch = async () => {
              fetchCount404 += 1;
              if (fetchCount404 === 1) {
                return new Response(new ReadableStream({
                  start(controller) {
                    controller.enqueue(new TextEncoder().encode('not found'));
                  },
                  cancel() {
                    cancelled404Count += 1;
                  },
                }), { status: 404 });
              }
              return new Response(JSON.stringify({
                data: { search_by_raw_query: { search_timeline: { timeline: { instructions: [] } } } },
              }), { status: 200, headers: { 'content-type': 'application/json' } });
            };
            const client2 = new Client({ cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' }, timeoutMs: 2000 });
            const r2 = await client2.search('hello', 5);

            // 3) Network error stops immediately on queryId #1 without retrying remaining 3 IDs
            let netFetches = 0;
            globalThis.fetch = async () => {
              netFetches += 1;
              throw new TypeError('fetch failed');
            };
            const client3 = new Client({ cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' }, timeoutMs: 2000 });
            const r3 = await client3.search('hello', 5);

            // 4) Caller init.signal is composed in both fetchWithTimeout and fetchTextWithTimeout
            const caller = new AbortController();
            globalThis.fetch = async (_url, init) => {
              setTimeout(() => caller.abort(new Error('caller cancelled')), 30);
              return new Promise((_, reject) => {
                init?.signal?.addEventListener('abort', () => reject(init.signal.reason), { once: true });
              });
            };
            const client4 = new Client({ cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' }, timeoutMs: 10000 });
            let callerAbortMsg = null;
            try {
              await client4.fetchTextWithTimeout('https://x.com/test', { signal: caller.signal });
            } catch (e) {
              callerAbortMsg = e.message;
            }
            const caller2 = new AbortController();
            globalThis.fetch = async (_url, init) => {
              setTimeout(() => caller2.abort(new Error('caller2 cancelled')), 30);
              return new Promise((_, reject) => {
                init?.signal?.addEventListener('abort', () => reject(init.signal.reason), { once: true });
              });
            };
            let caller2AbortMsg = null;
            try {
              await client4.fetchWithTimeout('https://x.com/test', { signal: caller2.signal });
            } catch (e) {
              caller2AbortMsg = e.message;
            }

            // 5) Oversized 200 response hits maxResponseBytes cap and cancels stream
            let bigCancelled = false;
            globalThis.fetch = async () => new Response(new ReadableStream({
              pull(controller) {
                controller.enqueue(new Uint8Array(400));
              },
              cancel() {
                bigCancelled = true;
              },
            }), { status: 200 });
            const client5 = new Client({
              cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' },
              timeoutMs: 2000,
              maxResponseBytes: 512,
            });
            const r5 = await client5.search('hello', 5);

            process.stdout.write(JSON.stringify({
              r1, elapsed1, stallFetches, stallCancelled, refreshCalls,
              r2Ok: r2.success, fetchCount404, cancelled404Count,
              r3, netFetches,
              callerAbortMsg, caller2AbortMsg,
              r5, bigCancelled,
            }));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)

        assert data["r1"]["success"] is False
        assert "timed out" in data["r1"]["error"].lower()
        assert data["elapsed1"] < 1500
        assert data["stallFetches"] == 1
        assert data["stallCancelled"] is True
        assert data["refreshCalls"] == 0

        assert data["r2Ok"] is True
        assert data["fetchCount404"] == 2
        assert data["cancelled404Count"] == 1

        assert data["r3"]["success"] is False
        assert data["netFetches"] == 1

        assert data["callerAbortMsg"] == "caller cancelled"
        assert data["caller2AbortMsg"] == "caller2 cancelled"

        assert data["r5"]["success"] is False
        assert "byte limit" in data["r5"]["error"].lower()
        assert data["bigCancelled"] is True

    def test_bird_search_mjs_timeout_ms_cli_flag_and_python_forwarding(self, tmp_path):
        stub = tmp_path / "stub-stall.mjs"
        stub.write_text(
            textwrap.dedent(
                """
                globalThis.fetch = async (_url, init) => new Promise((_resolve, reject) => {
                  init?.signal?.addEventListener('abort', () => reject(init.signal.reason), { once: true });
                });
                """
            ),
            encoding="utf-8",
        )
        run_env = os.environ.copy()
        run_env.update(
            AUTH_TOKEN="dummy-auth-token-1234",
            CT0="dummy-ct0-token-5678",
            BIRD_DISABLE_BROWSER_COOKIES="1",
        )
        started = time.monotonic()
        res = subprocess.run(
            [
                "node", "--import", stub.as_uri(), str(BIRD_MJS),
                "--count", "1", "--timeout-ms", "150", "--json", "--", "hello",
            ],
            cwd=REPO_ROOT,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed = time.monotonic() - started
        assert res.returncode == 1
        assert elapsed < 3.0
        payload = json.loads(res.stdout)
        assert "150ms" in payload["error"]

        for bad in (["--timeout-ms", "0"], ["--timeout-ms", "-5"], ["--timeout-ms", "abc"], ["--timeout-ms"]):
            bad_res = subprocess.run(
                ["node", str(BIRD_MJS), *bad, "--json", "--", "hello"],
                cwd=REPO_ROOT,
                env=run_env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert bad_res.returncode == 1
            assert bad_res.stdout == ""

        # Verify bird_x.py passes --timeout-ms matching clamped subprocess budget on all 3 lanes
        captured_cmds: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            captured_cmds.append(cmd)
            return SubprocResult(returncode=0, stdout="[]", stderr="")

        with mock.patch.object(bird_x.subproc, "run_with_timeout", side_effect=fake_run):
            bird_x._invoke_bird_subprocess("hello", count=5, timeout=8)
            bird_x.search_handles(["alice"], "topic", "2026-05-01", count_per=2)
            bird_x.search_mentions(["alice"], "2026-05-01", count_per=2)

        assert len(captured_cmds) == 3
        expected_ms = ["8000", "15000", "15000"]
        for cmd, want in zip(captured_cmds, expected_ms):
            sep = cmd.index("--")
            idx = cmd.index("--timeout-ms")
            assert idx < sep
            assert cmd[idx + 1] == want


# ---------------------------------------------------------------------------
# HIGH-158: atomic private cache writes, symlink refusal & read-only HOME
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh158AtomicPrivateCacheWrites:
    def test_private_permissions_and_symlink_refusal_for_query_ids_and_features(self, tmp_path):
        nested_dir = tmp_path / "private_cache_dir"
        qid_cache = nested_dir / "query-ids-cache.json"
        feat_cache = nested_dir / "features.json"

        victim = tmp_path / "victim.txt"
        victim.write_text("DO_NOT_OVERWRITE", encoding="utf-8")
        symlink_target = tmp_path / "symlink-cache.json"
        symlink_target.symlink_to(victim)

        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        symlink_dir = tmp_path / "symlink_dir"
        symlink_dir.symlink_to(real_dir)

        script = textwrap.dedent(
            f"""
            import {{ createRuntimeQueryIdStore, writeFileAtomicPrivate }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-query-ids.js';
            import {{ refreshFeatureOverridesCache }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-features.js';

            const fetchImpl = async (url) => {{
              if (String(url).startsWith('https://x.com/')) {{
                return new Response('https://abs.twimg.com/responsive-web/client-web/main.1.js', {{ status: 200 }});
              }}
              return new Response('e.exports={{queryId:"AtomicId_1",operationName:"SearchTimeline"}}', {{ status: 200 }});
            }};

            const store = createRuntimeQueryIdStore({{
              fetchImpl,
              cachePath: {json.dumps(str(qid_cache))},
            }});
            await store.refresh(['SearchTimeline'], {{ force: true }});

            process.env.BIRD_FEATURES_CACHE = {json.dumps(str(feat_cache))};
            await refreshFeatureOverridesCache();

            let symlinkTargetErr = null;
            try {{
              await writeFileAtomicPrivate({json.dumps(str(symlink_target))}, '{{}}\\n');
            }} catch (e) {{
              symlinkTargetErr = e.message;
            }}

            let symlinkDirErr = null;
            try {{
              await writeFileAtomicPrivate({json.dumps(str(symlink_dir / "out.json"))}, '{{}}\\n');
            }} catch (e) {{
              symlinkDirErr = e.message;
            }}

            process.stdout.write(JSON.stringify({{ symlinkTargetErr, symlinkDirErr }}));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["symlinkTargetErr"] and "symlink" in data["symlinkTargetErr"].lower()
        assert data["symlinkDirErr"] and "symlink" in data["symlinkDirErr"].lower()
        assert victim.read_text(encoding="utf-8") == "DO_NOT_OVERWRITE"
        assert not (real_dir / "out.json").exists()

        if os.name != "nt":
            assert stat.S_IMODE(nested_dir.stat().st_mode) == 0o700
            assert stat.S_IMODE(qid_cache.stat().st_mode) == 0o600
            assert stat.S_IMODE(feat_cache.stat().st_mode) == 0o600
        # No leftover .tmp sibling files
        assert not [p for p in nested_dir.iterdir() if p.name.endswith(".tmp")]

    def test_concurrent_node_processes_never_corrupt_query_ids_cache(self, tmp_path):
        cache_file = tmp_path / "concurrent" / "query-ids-cache.json"

        def worker(idx: int) -> subprocess.CompletedProcess:
            script = textwrap.dedent(
                f"""
                import {{ createRuntimeQueryIdStore }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-query-ids.js';
                const fetchImpl = async (url) => {{
                  if (String(url).startsWith('https://x.com/')) {{
                    return new Response('https://abs.twimg.com/responsive-web/client-web/main.{idx}.js', {{ status: 200 }});
                  }}
                  return new Response('e.exports={{queryId:"ConcurrentId_{idx}",operationName:"SearchTimeline"}}', {{ status: 200 }});
                }};
                const store = createRuntimeQueryIdStore({{ fetchImpl, cachePath: {json.dumps(str(cache_file))} }});
                for (let round = 0; round < 3; round++) {{
                  await store.refresh(['SearchTimeline'], {{ force: true }});
                }}
                """
            )
            return _node_eval(script)

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(worker, range(6)))

        for res in results:
            assert res.returncode == 0, res.stderr
        parsed = json.loads(cache_file.read_text(encoding="utf-8"))
        assert parsed["ids"]["SearchTimeline"].startswith("ConcurrentId_")
        assert not [p for p in cache_file.parent.iterdir() if p.name.endswith(".tmp")]

    @pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX non-root chmod test")
    def test_readonly_cache_dir_still_refreshes_memory_snapshot(self, tmp_path):
        ro_dir = tmp_path / "readonly_home"
        ro_dir.mkdir(mode=0o700)
        ro_dir.chmod(0o500)
        cache_file = ro_dir / "query-ids-cache.json"
        try:
            script = textwrap.dedent(
                f"""
                import {{ createRuntimeQueryIdStore }} from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-query-ids.js';
                const fetchImpl = async (url) => {{
                  if (String(url).startsWith('https://x.com/')) {{
                    return new Response('https://abs.twimg.com/responsive-web/client-web/main.ro.js', {{ status: 200 }});
                  }}
                  return new Response('e.exports={{queryId:"ReadOnlyMemId_42",operationName:"SearchTimeline"}}', {{ status: 200 }});
                }};
                const store = createRuntimeQueryIdStore({{ fetchImpl, cachePath: {json.dumps(str(cache_file))} }});
                const info = await store.refresh(['SearchTimeline'], {{ force: true }});
                const id = await store.getQueryId('SearchTimeline');
                process.stdout.write(JSON.stringify({{ id, infoId: info?.snapshot?.ids?.SearchTimeline ?? null }}));
                """
            )
            res = _node_eval(script)
            assert res.returncode == 0, res.stderr
            data = json.loads(res.stdout)
            assert data["id"] == "ReadOnlyMemId_42"
            assert data["infoId"] == "ReadOnlyMemId_42"
            assert not cache_file.exists()
        finally:
            ro_dir.chmod(0o700)


# ---------------------------------------------------------------------------
# HIGH-159: pagination cycle detection, empty-page stop, maxPages:0, caps & partials
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh159PaginationGuards:
    def test_paginate_cursor_stops_on_cycles_empty_pages_and_invalid_max_pages(self):
        script = textwrap.dedent(
            """
            import { paginateCursor } from './skills/last30days/scripts/lib/vendor/bird-search/lib/paginate-cursor.js';

            // 1) 2-step cursor cycle A -> B -> A
            let cycleCalls = 0;
            const cycleRes = await paginateCursor({
              pageDelayMs: 0,
              getKey: (x) => x.id,
              fetchPage: async (cursor) => {
                cycleCalls += 1;
                if (!cursor) return { success: true, items: [{ id: '1' }], cursor: 'A' };
                if (cursor === 'A') return { success: true, items: [{ id: '2' }], cursor: 'B' };
                if (cursor === 'B') return { success: true, items: [{ id: '3' }], cursor: 'A' };
                throw new Error('should never loop');
              },
            });

            // 2) Rotating unique cursors with empty items stops immediately
            let emptyCalls = 0;
            const emptyRes = await paginateCursor({
              pageDelayMs: 0,
              getKey: (x) => x.id,
              fetchPage: async () => {
                emptyCalls += 1;
                return { success: true, items: [], cursor: `empty-${emptyCalls}` };
              },
            });

            // 3) Rotating unique cursors with only duplicate items (added === 0) stops
            let dupCalls = 0;
            const dupRes = await paginateCursor({
              pageDelayMs: 0,
              getKey: (x) => x.id,
              fetchPage: async () => {
                dupCalls += 1;
                return { success: true, items: [{ id: 'same' }], cursor: `dup-${dupCalls}` };
              },
            });

            // 4) maxPages: 0, -1, NaN, Infinity never call fetchPage
            let guardCalls = 0;
            const guardFetch = async () => { guardCalls += 1; return { success: true, items: [{ id: 'x' }], cursor: 'C' }; };
            const zeroRes = await paginateCursor({ maxPages: 0, cursor: 'init', getKey: (x) => x.id, fetchPage: guardFetch });
            const negRes = await paginateCursor({ maxPages: -3, cursor: 'init', getKey: (x) => x.id, fetchPage: guardFetch });
            const nanRes = await paginateCursor({ maxPages: Number.NaN, getKey: (x) => x.id, fetchPage: guardFetch });
            const infRes = await paginateCursor({ maxPages: Number.POSITIVE_INFINITY, getKey: (x) => x.id, fetchPage: guardFetch });

            process.stdout.write(JSON.stringify({
              cycleCalls, cycleLen: cycleRes.items.length, cycleNext: cycleRes.nextCursor ?? null,
              emptyCalls, emptyLen: emptyRes.items.length,
              dupCalls, dupLen: dupRes.items.length,
              guardCalls, zeroRes, negRes, nanRes, infRes,
            }));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["cycleCalls"] == 3
        assert data["cycleLen"] == 3
        assert data["cycleNext"] is None
        assert data["emptyCalls"] == 1
        assert data["emptyLen"] == 0
        assert data["dupCalls"] == 2
        assert data["dupLen"] == 1
        assert data["guardCalls"] == 0
        assert data["zeroRes"] == {"success": True, "items": [], "nextCursor": "init"}
        assert data["negRes"] == {"success": True, "items": [], "nextCursor": "init"}
        assert data["nanRes"]["success"] is False
        assert data["infRes"]["success"] is False

    def test_search_paged_handles_cycles_max_pages_zero_default_cap_and_partial_page_failure(self):
        script = textwrap.dedent(
            """
            import { TwitterClientBase } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';
            import { withSearch } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-search.js';

            const Client = withSearch(TwitterClientBase);

            function makePageBody(tweets, nextCursor) {
              const entries = tweets.map((t) => ({
                entryId: `tweet-${t.id}`,
                content: {
                  itemContent: {
                    tweet_results: {
                      result: {
                        __typename: 'Tweet',
                        rest_id: t.id,
                        legacy: {
                          id_str: t.id,
                          full_text: t.text,
                          created_at: 'Wed May 15 12:00:00 +0000 2026',
                        },
                        core: {
                          user_results: {
                            result: {
                              rest_id: '42',
                              legacy: { screen_name: 'tester', name: 'Tester' },
                            },
                          },
                        },
                      },
                    },
                  },
                },
              }));
              if (nextCursor) {
                entries.push({
                  entryId: 'cursor-bottom-0',
                  content: { cursorType: 'Bottom', value: nextCursor },
                });
              }
              return JSON.stringify({
                data: {
                  search_by_raw_query: {
                    search_timeline: {
                      timeline: {
                        instructions: [{ type: 'TimelineAddEntries', entries }],
                      },
                    },
                  },
                },
              });
            }

            const client = new Client({ cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' }, timeoutMs: 2000 });

            // 1) maxPages: 0 & NaN perform 0 fetches
            let zeroCalls = 0;
            globalThis.fetch = async () => { zeroCalls += 1; return new Response(makePageBody([], null), { status: 200 }); };
            const zeroRes = await client.searchPaged('q', 20, { maxPages: 0, cursor: 'c0' });
            const nanRes = await client.searchPaged('q', 20, { maxPages: Number.NaN });

            // 2) Multi-step cycle A -> B -> A terminates cleanly
            let cycleCalls = 0;
            globalThis.fetch = async (url) => {
              cycleCalls += 1;
              const vars = JSON.parse(new URL(String(url)).searchParams.get('variables'));
              if (!vars.cursor) return new Response(makePageBody([{ id: '1', text: 'p1' }], 'A'), { status: 200 });
              if (vars.cursor === 'A') return new Response(makePageBody([{ id: '2', text: 'p2' }], 'B'), { status: 200 });
              return new Response(makePageBody([{ id: '3', text: 'p3' }], 'A'), { status: 200 });
            };
            const cycleRes = await client.searchPaged('q', 60);

            // 3) Default page cap prevents 60 sequential requests when each page yields 1 tweet
            let capCalls = 0;
            globalThis.fetch = async () => {
              capCalls += 1;
              return new Response(makePageBody([{ id: String(100 + capCalls), text: 'one' }], `cur-${capCalls}`), { status: 200 });
            };
            const capRes = await client.searchPaged('q', 60);

            // 4) Unlimited getAllSearchResults also respects a hard default cap
            let unlimCalls = 0;
            globalThis.fetch = async () => {
              unlimCalls += 1;
              return new Response(makePageBody([{ id: String(1000 + unlimCalls), text: 'u' }], `ucur-${unlimCalls}`), { status: 200 });
            };
            const unlimRes = await client.getAllSearchResults('q');

            // 5) Page 2 failure preserves already-fetched page 1 tweets
            let partialCalls = 0;
            globalThis.fetch = async () => {
              partialCalls += 1;
              if (partialCalls === 1) {
                return new Response(makePageBody([{ id: '501', text: 'kept' }], 'page2'), { status: 200 });
              }
              return new Response('upstream boom', { status: 500 });
            };
            const partialRes = await client.searchPaged('q', 20);

            process.stdout.write(JSON.stringify({
              zeroCalls, zeroRes, nanRes,
              cycleCalls, cycleIds: cycleRes.tweets.map((t) => t.id),
              capCalls, capLen: capRes.tweets.length,
              unlimCalls, unlimLen: unlimRes.tweets.length,
              partialSuccess: partialRes.success,
              partialIds: (partialRes.tweets || []).map((t) => t.id),
              partialNextCursor: partialRes.nextCursor ?? null,
            }));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["zeroCalls"] == 0
        assert data["zeroRes"] == {"success": True, "tweets": [], "nextCursor": "c0"}
        assert data["nanRes"]["success"] is False
        assert data["cycleCalls"] == 3
        assert data["cycleIds"] == ["1", "2", "3"]
        # For limit=60, pageSize=20 -> Math.min(10, ceil(60/20)+2) = 5 pages max
        assert data["capCalls"] == 5
        assert data["capLen"] == 5
        assert data["unlimCalls"] == 20
        assert data["unlimLen"] == 20
        assert data["partialSuccess"] is False
        assert data["partialIds"] == ["501"]
        assert data["partialNextCursor"] == "page2"


# ---------------------------------------------------------------------------
# HIGH-160: cookies.js consent flags & bird_x.py failover/credential reset
# ---------------------------------------------------------------------------


class TestHigh160CookieConsentAndFailoverReset:
    def setup_method(self):
        self._saved_creds = dict(bird_x._credentials)
        self._saved_seen = set(bird_x._seen_secrets)
        self._saved_browser = bird_x._session_browser
        self._saved_next = bird_x._next_session
        bird_x._credentials.clear()
        bird_x.set_session_failover(None, None)

    def teardown_method(self):
        bird_x._credentials.clear()
        bird_x._credentials.update(self._saved_creds)
        bird_x._seen_secrets.clear()
        bird_x._seen_secrets.update(self._saved_seen)
        bird_x.set_session_failover(self._saved_browser, self._saved_next)

    @pytest.mark.skipif(not HAS_NODE, reason="node is required")
    @pytest.mark.parametrize(
        "consent_env",
        [
            {"FROM_BROWSER": "off"},
            {"FROM_BROWSER": "  OFF  "},
            {"BROWSER_CONSENT": "false"},
            {"BROWSER_CONSENT": "0"},
            {"BROWSER_CONSENT": "no"},
            {"BROWSER_CONSENT": "off"},
            {"BROWSER_CONSENT": ""},
            {"BROWSER_CONSENT": "   "},
            {"BROWSER_CONSENT": "maybe"},
        ],
    )
    def test_cookies_js_blocks_browser_extraction_on_from_browser_off_or_non_truthy_consent(
        self, tmp_path, consent_env
    ):
        register = _register_sweet_cookie_stub(tmp_path)
        run_env = os.environ.copy()
        for key in (
            "AUTH_TOKEN",
            "CT0",
            "TWITTER_AUTH_TOKEN",
            "TWITTER_CT0",
            "BIRD_DISABLE_BROWSER_COOKIES",
            "LAST30DAYS_DISABLE_BROWSER_COOKIES",
            "FROM_BROWSER",
            "BROWSER_CONSENT",
        ):
            run_env.pop(key, None)
        run_env.update(consent_env)

        script = textwrap.dedent(
            """
            import { resolveCredentials } from './skills/last30days/scripts/lib/vendor/bird-search/lib/cookies.js';
            const res = await resolveCredentials({ cookieSource: ['safari', 'chrome'] });
            process.stdout.write(JSON.stringify(res));
            """
        )
        res = subprocess.run(
            ["node", "--import", register.as_uri(), "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        assert "SWEET_COOKIE_CALLED" not in res.stderr
        payload = json.loads(res.stdout)
        assert payload["cookies"]["authToken"] is None
        assert payload["cookies"]["ct0"] is None
        assert payload["warnings"]

    def test_set_credentials_clears_stale_failover_and_removed_keys(self):
        failover_calls = []

        def fake_next(deadline=None, cancel=None):
            failover_calls.append(True)
            return ("stale-browser-auth-9999", "stale-browser-ct0-9999", "chrome")

        # Simulate prior browser-sourced run registering failover
        bird_x.set_credentials("browser-auth-token-1111", "browser-ct0-token-1111")
        bird_x.set_session_failover("safari", fake_next)
        assert bird_x._session_browser == "safari"
        assert bird_x._next_session is fake_next

        # Subsequent explicit non-browser run calls env._inject_bird_credentials -> set_credentials only
        explicit_cfg = {
            "AUTH_TOKEN": "explicit-auth-token-2222",
            "CT0": "explicit-ct0-token-2222",
            "FROM_BROWSER": "off",
            "BROWSER_CONSENT": "false",
        }
        env._inject_bird_credentials(explicit_cfg)
        assert bird_x._session_browser is None
        assert bird_x._next_session is None

        # A 401 or 500 must NOT invoke the stale browser failover callback
        search_401 = (
            SubprocResult(
                returncode=0,
                stdout='{"error": "HTTP 401: Could not authenticate you", "items": []}',
                stderr="",
            ),
            None,
        )
        with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=[search_401]):
            resp = bird_x._run_bird_search("topic", count=5, timeout=15)
        assert failover_calls == []
        assert "cookies from safari" not in resp["error"]

        # Passing None/empty clears stale credentials while preserving retired secret scrubbing
        bird_x.set_credentials(None, None)
        assert bird_x._credentials == {}
        scrubbed = bird_x._scrub_credentials("leaked explicit-auth-token-2222 and browser-auth-token-1111")
        assert "explicit-auth-token-2222" not in scrubbed
        assert "browser-auth-token-1111" not in scrubbed

    def test_internal_failover_preserves_multi_browser_rotation(self):
        rotations = [
            ("chrome-auth-token-3333", "chrome-ct0-token-3333", "chrome"),
            ("edge-auth-token-4444", "edge-ct0-token-4444", "edge"),
        ]

        def next_browser(deadline=None, cancel=None):
            return rotations.pop(0) if rotations else None

        bird_x.set_credentials("safari-auth-token-1111", "safari-ct0-token-1111")
        bird_x.set_session_failover("safari", next_browser)

        err_401 = (SubprocResult(returncode=0, stdout='{"error": "HTTP 401: Could not authenticate you"}', stderr=""), None)
        ok_resp = (SubprocResult(returncode=0, stdout='[{"id": "1", "text": "ok"}]', stderr=""), None)
        with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=[err_401, err_401, ok_resp]):
            resp = bird_x._run_bird_search("topic", count=5, timeout=15)

        assert resp["items"] == [{"id": "1", "text": "ok"}]
        assert bird_x._credentials["AUTH_TOKEN"] == "edge-auth-token-4444"
        assert bird_x._session_browser == "edge"
        assert bird_x._next_session is next_browser


# ---------------------------------------------------------------------------
# HIGH-161: cancel, deadline_monotonic & capture_limit_bytes forwarded to subproc
# ---------------------------------------------------------------------------


class TestHigh161SubprocessLifecycleForwarding:
    def test_all_three_bird_call_sites_forward_cancel_deadline_and_capture_limit(self):
        cancel = threading.Event()
        deadline = time.monotonic() + 30.0
        seen_kwargs: list[dict] = []

        def fake_run(cmd, **kwargs):
            seen_kwargs.append(kwargs)
            return SubprocResult(returncode=0, stdout="[]", stderr="")

        with mock.patch.object(bird_x.subproc, "run_with_timeout", side_effect=fake_run):
            bird_x._invoke_bird_subprocess("hello", count=5, timeout=10, deadline=deadline, cancel=cancel)
            bird_x.search_handles(["alice"], "topic", "2026-05-01", count_per=2, deadline=deadline, cancel=cancel)
            bird_x.search_mentions(["alice"], "2026-05-01", count_per=2, deadline=deadline, cancel=cancel)

        assert len(seen_kwargs) == 3
        for kw in seen_kwargs:
            assert kw["cancel"] is cancel
            assert kw["deadline_monotonic"] == deadline
            assert kw["capture_limit_bytes"] == bird_x._BIRD_CAPTURE_LIMIT_BYTES
            assert kw["capture_limit_bytes"] == 8 * 1024 * 1024

    @pytest.mark.skipif(not HAS_NODE, reason="node is required")
    def test_setting_cancel_terminates_inflight_node_subprocess_promptly(self, tmp_path):
        sleeper = tmp_path / "sleeper.mjs"
        sleeper.write_text("setTimeout(() => {}, 30000);\n", encoding="utf-8")

        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with mock.patch.object(bird_x, "_BIRD_SEARCH_MJS", sleeper):
                result, err = bird_x._invoke_bird_subprocess(
                    "hello",
                    count=1,
                    timeout=15,
                    deadline=time.monotonic() + 15.0,
                    cancel=cancel,
                )
        finally:
            timer.cancel()
        elapsed = time.monotonic() - started
        assert result is None
        assert err is not None
        assert "cancelled" in err["error"].lower()
        assert elapsed < 3.0

    @pytest.mark.skipif(not HAS_NODE, reason="node is required")
    def test_setting_cancel_terminates_inflight_handle_and_mention_workers_promptly(self, tmp_path):
        sleeper = tmp_path / "sleeper-lane.mjs"
        sleeper.write_text("setTimeout(() => {}, 30000);\n", encoding="utf-8")

        for lane in ("handles", "mentions"):
            cancel = threading.Event()
            failures: list[str] = []
            timer = threading.Timer(0.2, cancel.set)
            timer.start()
            started = time.monotonic()
            try:
                with mock.patch.object(bird_x, "_BIRD_SEARCH_MJS", sleeper):
                    if lane == "handles":
                        items = bird_x.search_handles(
                            ["alice", "bob"],
                            "topic",
                            "2026-05-01",
                            count_per=1,
                            failure_out=failures,
                            deadline=time.monotonic() + 15.0,
                            cancel=cancel,
                        )
                    else:
                        items = bird_x.search_mentions(
                            ["alice", "bob"],
                            "2026-05-01",
                            count_per=1,
                            failure_out=failures,
                            deadline=time.monotonic() + 15.0,
                            cancel=cancel,
                        )
            finally:
                timer.cancel()
            elapsed = time.monotonic() - started
            assert items == []
            assert elapsed < 3.0
            assert failures and any("cancelled" in msg.lower() for msg in failures)

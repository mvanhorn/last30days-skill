"""Regression tests for PR08: X envelope sanitization & vendored bird-search JS security.

Covers:
- HIGH-140: Downstream html.unescape bypass of x_envelope._clean_text defenses.
- HIGH-152: Unvalidated cached queryId path traversal in authenticated POST URLs.
- HIGH-153: Session cookie leakage across Bird error paths, non-JSON logs, and JS errors.
- HIGH-154: Unvalidated auth_token / ct0 cookie delimiter & CRLF/control-char injection.
- HIGH-155: Leading `-` search queries & CLI option confusion across Python -> Node.
"""

from __future__ import annotations

import html
import io
import json
import os
import shutil
import subprocess
import textwrap
from contextlib import redirect_stderr
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from lib import bird_x, dates, html_render, normalize, pipeline, render, x_api, x_envelope
from lib.subproc import SubprocResult

REPO_ROOT = Path(__file__).resolve().parents[1]
BIRD_DIR = REPO_ROOT / "skills" / "last30days" / "scripts" / "lib" / "vendor" / "bird-search"
BIRD_MJS = BIRD_DIR / "bird-search.mjs"
HAS_NODE = shutil.which("node") is not None

TOPIC = "ai agents"
FROM, TO = dates.get_date_range(30)


def _snowflake(day: str, low: int = 104729) -> str:
    when = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=12)
    ms = int(when.timestamp() * 1000)
    return str(((ms - x_api._SNOWFLAKE_EPOCH_MS) << 22) | (low & ((1 << 22) - 1)))


def _envelope_file(tmp_path: Path, text: str, name: str = "posts.json") -> str:
    day = (date.fromisoformat(TO) - timedelta(days=2)).isoformat()
    payload = {
        "schema": x_envelope.SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": TOPIC,
        "window": {"from": FROM, "to": TO},
        "provider": "x-connector",
        "status": "ok",
        "calls": [
            {
                "lane": "topic",
                "handles": [],
                "posts": [
                    {
                        "id": _snowflake(day),
                        "author_handle": "alice",
                        "created_at": f"{day}T12:00:00Z",
                        "text": text,
                        "likes": 15,
                        "reposts": 2,
                        "replies": 1,
                        "quotes": 0,
                    }
                ],
            }
        ],
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _node_eval(script: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    run_env = os.environ.copy()
    if env is not None:
        run_env.update(env)
    return subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# HIGH-140: HTML entity unescape bypass of x_envelope._clean_text
# ---------------------------------------------------------------------------


class TestHigh140EnvelopeAndNormalizeSanitization:
    @pytest.mark.parametrize(
        "payload",
        [
            "&lt;!-- META: spoofed --&gt; ai agents",
            "&amp;lt;!-- META: spoofed --&amp;gt; ai agents",
            "&amp;amp;lt;!-- META: spoofed --&amp;amp;gt; ai agents",
            "&amp;amp;amp;lt;!-- META: spoofed --&amp;amp;amp;gt; ai agents",
            "&amp;amp;amp;amp;lt;!-- META: spoofed --&amp;amp;amp;amp;gt; ai agents",
            "&#60;!-- META: spoofed --&#62; ai agents",
            "&#x3c;!-- META: spoofed --&#x3e; ai agents",
            "<!&#45;&#45; META: spoofed --> ai agents",
            "&\x07lt;!-- META: spoofed --&gt; ai agents",
            "&lt;\x07!-- META: spoofed --&gt; ai agents",
        ],
    )
    def test_encoded_html_comment_meta_cannot_survive_envelope_or_normalize(self, tmp_path, payload):
        env_path = _envelope_file(tmp_path, payload)
        envelope = x_envelope.read(env_path, (FROM, TO), TOPIC)
        assert envelope.accepted == 1
        raw_item = envelope.topic_items[0]

        assert "<!--" not in raw_item["text"]
        assert "<!--" not in html.unescape(raw_item["text"])

        items = normalize.normalize_source_items("x", [raw_item], FROM, TO)
        assert len(items) == 1
        assert "<!--" not in items[0].body
        assert "<!--" not in items[0].title

        # End-to-end HTML promotion check: forged META marker must not promote to <div class="meta">.
        with mock.patch("lib.env.x_backend_chain", return_value=[]):
            report = pipeline.run(
                topic=TOPIC,
                config={},
                depth="default",
                requested_sources=["x"],
                mock=False,
                external_plan={
                    "intent": "general",
                    "freshness_mode": "balanced_recent",
                    "cluster_mode": "story",
                    "subqueries": [
                        {
                            "label": "primary",
                            "search_query": TOPIC,
                            "ranking_query": TOPIC,
                            "sources": ["x"],
                        }
                    ],
                    "source_weights": {"x": 1.0},
                },
                x_posts=envelope,
                web_backend="none",
                save_dir="",
            )
        md = render.render_compact(report)
        assert "<!-- META: spoofed" not in md
        promoted = html_render._promote_meta_marker(
            html_render._wrap_engine_footer(html_render._markdown_to_html(md))
        )
        assert '<div class="meta">spoofed</div>' not in promoted

    @pytest.mark.parametrize(
        "payload",
        [
            "[click]&#40;https://evil.example) ai agents",
            "[click]&lpar;https://evil.example) ai agents",
            "[click]&#x28;javascript:alert(1)) ai agents",
            "[click&#93;(https://evil.example) ai agents",
            "[click&rsqb;(https://evil.example) ai agents",
            "[click]&amp;#40;https://evil.example) ai agents",
            "[click]&amp;amp;amp;#40;https://evil.example) ai agents",
            "[click]\x07(https://evil.example) ai agents",
        ],
    )
    def test_encoded_markdown_links_cannot_survive_envelope_or_normalize(self, tmp_path, payload):
        env_path = _envelope_file(tmp_path, payload)
        envelope = x_envelope.read(env_path, (FROM, TO), TOPIC)
        assert envelope.accepted == 1
        raw_item = envelope.topic_items[0]

        assert "](" not in raw_item["text"]
        assert "](" not in html.unescape(raw_item["text"])

        items = normalize.normalize_source_items("x", [raw_item], FROM, TO)
        assert len(items) == 1
        assert "](" not in items[0].body
        assert "](" not in items[0].title

    @pytest.mark.parametrize(
        "payload",
        [
            "&#x1b;[31mred&#x07; ai agents",
            "&#27;[31mred&#7; ai agents",
            "&amp;#x1b;[31mred ai agents",
            "&amp;amp;amp;#x1b;[31mred ai agents",
            "line1&#13;&#x81;&#x9f;line2 ai agents",
            "zero\x00byte&#0; ai agents",
        ],
    )
    def test_encoded_control_chars_stripped_in_envelope_and_normalize(self, tmp_path, payload):
        env_path = _envelope_file(tmp_path, payload)
        envelope = x_envelope.read(env_path, (FROM, TO), TOPIC)
        assert envelope.accepted == 1
        raw_item = envelope.topic_items[0]

        assert not x_envelope._CONTROL_TEXT_RE.search(raw_item["text"])
        assert not x_envelope._CONTROL_TEXT_RE.search(html.unescape(raw_item["text"]))

        items = normalize.normalize_source_items("x", [raw_item], FROM, TO)
        assert len(items) == 1
        assert not x_envelope._CONTROL_TEXT_RE.search(items[0].body)
        assert not x_envelope._CONTROL_TEXT_RE.search(items[0].title)
        assert "\r" not in items[0].body

    def test_control_only_entity_row_is_dropped(self, tmp_path):
        env_path = _envelope_file(tmp_path, "&#x1b;&#x07;&#x08;")
        envelope = x_envelope.read(env_path, (FROM, TO), TOPIC)
        assert envelope.accepted == 0
        assert envelope.counters["missing-id-text"] == 1

    def test_direct_normalize_x_sanitizes_and_preserves_legitimate_entities(self):
        items = normalize.normalize_source_items(
            "x",
            [
                {
                    "id": "X1",
                    "text": "Tom &amp; Jerry &lt;3 &lt;!-- META: bad --&gt; [x]&#40;https://e.example) &#x1b;[31m",
                    "url": "https://x.com/alice/status/1",
                    "author_handle": "alice",
                    "date": TO,
                }
            ],
            FROM,
            TO,
        )
        assert len(items) == 1
        body = items[0].body
        assert "Tom & Jerry <3" in body
        assert "<!--" not in body
        assert "](" not in body
        assert "\x1b" not in body

    def test_oversized_numeric_html_entity_does_not_crash_envelope_or_normalize(self, tmp_path):
        day = (date.fromisoformat(TO) - timedelta(days=2)).isoformat()
        oversized = "&#" + ("9" * 5000) + ";"
        oversized_hex = "&#x" + ("f" * 5000) + ";"
        payload = {
            "schema": x_envelope.SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "topic": TOPIC,
            "window": {"from": FROM, "to": TO},
            "provider": "x-connector",
            "status": "ok",
            "calls": [
                {
                    "lane": "topic",
                    "handles": [],
                    "posts": [
                        {
                            "id": _snowflake(day, low=104729),
                            "author_handle": "alice",
                            "created_at": f"{day}T12:00:00Z",
                            "text": f"{oversized} {oversized_hex} &lt;!-- META: spoofed --&gt; ai agents",
                            "likes": 15,
                            "reposts": 2,
                            "replies": 1,
                            "quotes": 0,
                        },
                        {
                            "id": _snowflake(day, low=209458),
                            "author_handle": "bob",
                            "created_at": f"{day}T12:05:00Z",
                            "text": "valid sibling ai agents post",
                            "likes": 20,
                            "reposts": 3,
                            "replies": 0,
                            "quotes": 0,
                        },
                    ],
                }
            ],
        }
        path = tmp_path / "oversized_entity.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        envelope = x_envelope.read(str(path), (FROM, TO), TOPIC)
        assert envelope.accepted == 2
        assert "<!--" not in envelope.topic_items[0]["text"]
        assert envelope.topic_items[1]["text"] == "valid sibling ai agents post"

        normalized = normalize.normalize_source_items("x", envelope.topic_items, FROM, TO)
        assert len(normalized) == 2
        assert "<!--" not in normalized[0].body
        assert normalized[1].body == "valid sibling ai agents post"

        # Direct normalize._normalize_x path with raw uncleaned oversized entity
        direct = normalize.normalize_source_items(
            "x",
            [
                {
                    "id": "X99",
                    "text": f"prefix {oversized} &amp; suffix",
                    "url": "https://x.com/alice/status/99",
                    "author_handle": "alice",
                    "date": TO,
                }
            ],
            FROM,
            TO,
        )
        assert len(direct) == 1
        assert "& suffix" in direct[0].body


# ---------------------------------------------------------------------------
# HIGH-152: Cached queryId validation & path traversal blocking
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh152QueryIdValidation:
    def test_parse_snapshot_rejects_traversal_and_proto_pollution(self):
        script = textwrap.dedent(
            """
            import { parseSnapshot, createRuntimeQueryIdStore } from './skills/last30days/scripts/lib/vendor/bird-search/lib/runtime-query-ids.js';

            const raw = JSON.parse(`{
              "fetchedAt": "2026-11-23T00:00:00.000Z",
              "ttlMs": 86400000,
              "ids": {
                "SearchTimeline": "../../1.1/statuses/update.json?status=pwned",
                "CreateTweet": "abc123/CreateTweet#",
                "UserTweets": "valid_Query-Id123",
                "BadSpace": "abc def",
                "BadQuery": "abc?x=1",
                "__proto__": "polluted_id",
                "constructor": "ctor_id",
                "prototype": "proto_id"
              },
              "discovery": { "pages": ["https://x.com/?lang=en"], "bundles": ["main.js"] }
            }`);

            const snap = parseSnapshot(raw);
            process.stdout.write(JSON.stringify({
              nullProto: Object.getPrototypeOf(snap.ids) === null,
              keys: Object.keys(snap.ids),
              userTweets: snap.ids.UserTweets ?? null,
              searchTimeline: snap.ids.SearchTimeline ?? null,
              toStringLookup: snap.ids.toString ?? null,
            }));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["nullProto"] is True
        assert data["keys"] == ["UserTweets"]
        assert data["userTweets"] == "valid_Query-Id123"
        assert data["searchTimeline"] is None
        assert data["toStringLookup"] is None

    def test_poisoned_cache_file_cannot_hijack_authenticated_post_url(self, tmp_path):
        cache_file = tmp_path / "query-ids-cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "fetchedAt": datetime.now(timezone.utc).isoformat(),
                    "ttlMs": 86_400_000,
                    "ids": {
                        "SearchTimeline": "../../1.1/statuses/update.json?status=pwned#",
                    },
                    "discovery": {"pages": ["https://x.com/?lang=en"], "bundles": ["main.js"]},
                }
            ),
            encoding="utf-8",
        )
        stub = tmp_path / "stub.mjs"
        stub.write_text(
            textwrap.dedent(
                """
                globalThis.fetch = async (url) => {
                  process.stderr.write(`FETCH_URL=${String(url)}\\n`);
                  const body = {data: {search_by_raw_query: {search_timeline: {timeline: {instructions: []}}}}};
                  return new Response(JSON.stringify(body), {status: 200, headers: {'content-type': 'application/json'}});
                };
                """
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            AUTH_TOKEN="dummy-auth-token-1234",
            CT0="dummy-ct0-token-5678",
            BIRD_DISABLE_BROWSER_COOKIES="1",
            BIRD_QUERY_IDS_CACHE=str(cache_file),
        )
        res = subprocess.run(
            ["node", "--import", stub.as_uri(), str(BIRD_MJS), "--count", "1", "--json", "--", "hello"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        urls = [line.split("=", 1)[1] for line in res.stderr.splitlines() if line.startswith("FETCH_URL=")]
        assert urls
        for url in urls:
            assert "statuses/update" not in url
            assert ".." not in url
            assert url.startswith("https://x.com/i/api/graphql/")
            assert "/SearchTimeline?" in url

    def test_search_interpolation_site_skips_invalid_query_ids(self):
        script = textwrap.dedent(
            """
            import { TwitterClientBase } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';
            import { withSearch } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-search.js';

            const Client = withSearch(TwitterClientBase);
            const seenUrls = [];
            globalThis.fetch = async (url) => {
              seenUrls.push(String(url));
              const body = {data: {search_by_raw_query: {search_timeline: {timeline: {instructions: []}}}}};
              return new Response(JSON.stringify(body), {status: 200, headers: {'content-type': 'application/json'}});
            };
            const client = new Client({ cookies: { authToken: 'dummy-auth-1234', ct0: 'dummy-ct0-5678' } });
            client.getSearchTimelineQueryIds = async () => [
              '../../1.1/statuses/update.json?status=hi#',
              'mutId/CreateTweet#',
              'M1jEez78PEfVfbQLvlWMvQ',
            ];
            const res = await client.search('hello', 5);
            process.stdout.write(JSON.stringify({ ok: res.success, seenUrls }));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["ok"] is True
        assert len(data["seenUrls"]) == 1
        assert "/M1jEez78PEfVfbQLvlWMvQ/SearchTimeline?" in data["seenUrls"][0]


# ---------------------------------------------------------------------------
# HIGH-153: Credential scrubbing in bird_x.py and vendored JS
# ---------------------------------------------------------------------------


class TestHigh153CredentialScrubbingPython:
    def setup_method(self):
        self._saved_creds = dict(bird_x._credentials)
        self._saved_seen = set(bird_x._seen_secrets)
        bird_x._credentials.clear()
        bird_x._seen_secrets.clear()

    def teardown_method(self):
        bird_x._credentials.clear()
        bird_x._credentials.update(self._saved_creds)
        bird_x._seen_secrets.clear()
        bird_x._seen_secrets.update(self._saved_seen)

    def test_scrubs_both_injected_and_ambient_env_and_trimmed_variants(self, monkeypatch):
        injected_auth = "fake-injected-auth-1111"
        ambient_auth = "  fake-ambient-auth-2222  "
        ambient_tw_ct0 = "  fake-twitter-ct0-3333  "

        bird_x.set_credentials(injected_auth, "fake-injected-ct0-4444")
        monkeypatch.setenv("AUTH_TOKEN", ambient_auth)
        monkeypatch.setenv("TWITTER_CT0", ambient_tw_ct0)

        msg = (
            f"failed: {injected_auth} / {ambient_auth.strip()} / "
            f"{ambient_tw_ct0.strip()} / {ambient_auth}"
        )
        scrubbed = bird_x._scrub_credentials(msg)
        assert injected_auth not in scrubbed
        assert ambient_auth.strip() not in scrubbed
        assert ambient_tw_ct0.strip() not in scrubbed
        assert "<redacted>" in scrubbed

    def test_scrubs_retired_failover_credentials(self):
        retired_auth = "fake-safari-retired-auth-0001"
        retired_ct0 = "fake-safari-retired-ct0-0002"
        live_auth = "fake-chrome-live-auth-0003"
        live_ct0 = "fake-chrome-live-ct0-0004"

        bird_x.set_credentials(retired_auth, retired_ct0)
        bird_x.set_credentials(live_auth, live_ct0)

        scrubbed = bird_x._scrub_credentials(
            f"first attempt rejected auth={retired_auth} ct0={retired_ct0} then {live_auth}"
        )
        assert retired_auth not in scrubbed
        assert retired_ct0 not in scrubbed
        assert live_auth not in scrubbed

    def test_primary_search_paths_scrub_stderr_json_error_and_non_json_logs(self):
        secret_auth = "fake-primary-auth-secret-9999"
        secret_ct0 = "fake-primary-ct0-secret-8888"
        bird_x.set_credentials(secret_auth, secret_ct0)

        # 1) Non-zero exit with empty stdout -> stderr scrubbed
        proc_stderr = SubprocResult(
            returncode=1,
            stdout="",
            stderr=f"Headers.append: '{secret_ct0}' auth={secret_auth}",
        )
        with mock.patch.object(bird_x, "_invoke_bird_subprocess", return_value=(proc_stderr, None)):
            res = bird_x._run_bird_search_once("test", count=5, timeout=10)
        assert secret_auth not in res["error"]
        assert secret_ct0 not in res["error"]
        assert "<redacted>" in res["error"]

        # 2) JSON stdout with error key -> parsed["error"] scrubbed
        proc_json_err = SubprocResult(
            returncode=1,
            stdout=json.dumps({"error": f"invalid header {secret_auth} {secret_ct0}", "items": []}),
            stderr="",
        )
        with mock.patch.object(bird_x, "_invoke_bird_subprocess", return_value=(proc_json_err, None)):
            res = bird_x._run_bird_search_once("test", count=5, timeout=10)
        assert secret_auth not in res["error"]
        assert secret_ct0 not in res["error"]

        # 3) Non-JSON stdout with secret straddling char 80 -> log output scrubbed before slicing
        prefix = "x" * 65
        non_json = f"{prefix}{secret_auth} trailing html"
        proc_non_json = SubprocResult(returncode=0, stdout=non_json, stderr="")
        logged = []
        with mock.patch.object(
            bird_x, "_invoke_bird_subprocess", side_effect=[(proc_non_json, None), (proc_non_json, None)]
        ), mock.patch.object(bird_x.time, "sleep"), mock.patch.object(
            bird_x.log, "source_log", side_effect=lambda src, msg, **kw: logged.append(msg)
        ):
            res = bird_x._run_bird_search_once("test", count=5, timeout=10)
        combined_logs = "\n".join(logged)
        assert secret_auth not in combined_logs
        assert secret_auth[:12] not in combined_logs
        assert secret_auth not in res["error"]

        # 4) _invoke_bird_subprocess unexpected exception -> str(e) scrubbed
        with mock.patch.object(
            bird_x.subproc, "run_with_timeout", side_effect=RuntimeError(f"boom {secret_auth}")
        ):
            _, err_dict = bird_x._invoke_bird_subprocess("test", count=5, timeout=10)
        assert secret_auth not in err_dict["error"]
        assert "<redacted>" in err_dict["error"]

        # 5) parse_bird_response -> logged response["error"] scrubbed
        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            bird_x.parse_bird_response({"error": f"bad token {secret_auth} {secret_ct0}"})
        assert secret_auth not in err_buf.getvalue()
        assert secret_ct0 not in err_buf.getvalue()


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh153CredentialScrubbingJS:
    def test_js_fetch_error_never_echoes_secrets_or_header_values(self, tmp_path):
        fake_auth = "fake-js-auth-secret-77777"
        fake_ct0 = "fake-js-ct0-secret-66666"
        stub = tmp_path / "stub-err.mjs"
        stub.write_text(
            textwrap.dedent(
                f"""
                globalThis.fetch = async () => {{
                  throw new TypeError('Headers.append: "auth_token={fake_auth}; ct0={fake_ct0}" is an invalid header value.');
                }};
                """
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            AUTH_TOKEN=fake_auth,
            CT0=fake_ct0,
            BIRD_DISABLE_BROWSER_COOKIES="1",
        )
        res = subprocess.run(
            ["node", "--import", stub.as_uri(), str(BIRD_MJS), "--count", "1", "--json", "--", "hello"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 1
        combined = res.stdout + res.stderr
        assert fake_auth not in combined
        assert fake_ct0 not in combined
        payload = json.loads(res.stdout)
        assert "<redacted>" in payload["error"]

    def test_custom_cookie_header_extra_cookies_are_fully_redacted(self):
        script = textwrap.dedent(
            """
            import { redactSecrets } from './skills/last30days/scripts/lib/vendor/bird-search/lib/cookies.js';
            import { TwitterClientBase } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';

            const authToken = 'fake-auth-token-1111';
            const ct0 = 'fake-ct0-token-2222';
            const cookieHeader = `auth_token=${authToken}; ct0=${ct0}; twid=u%3D12345; kdt=supersecretkdt`;
            const client = new TwitterClientBase({ cookies: { authToken, ct0, cookieHeader } });
            const rawErr = `Upstream rejected request with Cookie: ${cookieHeader} (HTTP 403)`;

            const viaClient = client.redactError(rawErr);
            const viaHelper = redactSecrets(rawErr, [authToken, ct0, cookieHeader]);
            process.stdout.write(JSON.stringify({ viaClient, viaHelper }));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        for out in (data["viaClient"], data["viaHelper"]):
            assert "<redacted>" in out
            for secret in ("fake-auth-token-1111", "fake-ct0-token-2222", "twid", "u%3D12345", "kdt", "supersecretkdt"):
                assert secret not in out, f"leaked {secret!r} in {out!r}"


# ---------------------------------------------------------------------------
# HIGH-154: Cookie delimiter & CRLF/control-char validation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_NODE, reason="node is required")
class TestHigh154CookieValidation:
    @pytest.mark.parametrize(
        "bad_auth,bad_ct0",
        [
            ("fake-auth-1111; ct0=evil", "fake-ct0-2222"),
            ("fake-auth-1111,extra", "fake-ct0-2222"),
            ("fake-auth-1111", "fake-ct0-2222\r\nX-Injected: 1"),
            ("fake-auth-1111\n", "fake-ct0-2222"),
            ("fake-auth-1111", "fake-ct0-2222\r"),
            ("fake-auth-1111", "fake-ct0-2222\x00tail"),
            ("fake auth 1111", "fake-ct0-2222"),
            ("fake-auth-1111", "fake-ct0-2222\u00e9"),
            ('fake"auth1111', "fake-ct0-2222"),
            ("fake\\auth1111", "fake-ct0-2222"),
        ],
    )
    def test_cookies_and_client_reject_invalid_tokens_without_echoing_secrets(self, bad_auth, bad_ct0):
        script = textwrap.dedent(
            """
            import { resolveCredentials } from './skills/last30days/scripts/lib/vendor/bird-search/lib/cookies.js';
            import { TwitterClientBase } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';

            const badAuth = JSON.parse(process.env.TEST_BAD_AUTH);
            const badCt0 = JSON.parse(process.env.TEST_BAD_CT0);

            let resolveErr = null;
            try {
              await resolveCredentials({ authToken: badAuth, ct0: badCt0 });
            } catch (e) {
              resolveErr = e.message;
            }

            let ctorErr = null;
            try {
              new TwitterClientBase({ cookies: { authToken: badAuth, ct0: badCt0 } });
            } catch (e) {
              ctorErr = e.message;
            }

            process.stdout.write(JSON.stringify({ resolveErr, ctorErr }));
            """
        )
        res = _node_eval(
            script,
            env={
                "TEST_BAD_AUTH": json.dumps(bad_auth),
                "TEST_BAD_CT0": json.dumps(bad_ct0),
                "BIRD_DISABLE_BROWSER_COOKIES": "1",
            },
        )
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["resolveErr"] == "Invalid auth_token/ct0 format"
        assert data["ctorErr"] == "Invalid auth_token/ct0 format"

    def test_client_rejects_invalid_cookie_header_with_crlf_or_nul(self):
        script = textwrap.dedent(
            """
            import { TwitterClientBase } from './skills/last30days/scripts/lib/vendor/bird-search/lib/twitter-client-base.js';
            const results = [];
            for (const header of [
              'auth_token=good; ct0=good\\r\\nX-Evil: 1',
              'auth_token=good; ct0=good\\n',
              'auth_token=good; ct0=good\\0',
              'auth_token=good; ct0=good\\u00e9',
            ]) {
              try {
                new TwitterClientBase({ cookies: { authToken: 'good-auth', ct0: 'good-ct0', cookieHeader: header } });
                results.push('accepted');
              } catch (e) {
                results.push(e.message);
              }
            }
            process.stdout.write(JSON.stringify(results));
            """
        )
        res = _node_eval(script)
        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout) == ["Invalid auth_token/ct0 format"] * 4

    def test_cli_fails_fast_with_generic_error_on_malformed_env_token(self):
        bad_secret = "fake-secret-value; ct0=injected-9999"
        env = os.environ.copy()
        env.update(
            AUTH_TOKEN=bad_secret,
            CT0="fake-valid-ct0-1234",
            BIRD_DISABLE_BROWSER_COOKIES="1",
        )
        for cli_args in (["--check"], ["--whoami"], ["--count", "1", "--json", "--", "hello"]):
            res = subprocess.run(
                ["node", str(BIRD_MJS), *cli_args],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert res.returncode == 1
            combined = res.stdout + res.stderr
            assert "Invalid auth_token/ct0 format" in combined
            assert bad_secret not in combined
            assert "fake-secret-value" not in combined

    def test_malformed_first_browser_cookie_warns_and_fails_over_to_next_browser(self, tmp_path):
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
                          const browser = options.browsers?.[0];
                          if (browser === 'safari') {
                            return {
                              cookies: [
                                { name: 'auth_token', value: 'bad-safari-secret; evil=1', domain: 'x.com' },
                                { name: 'ct0', value: 'safari-ct0-1234', domain: 'x.com' },
                              ],
                              warnings: [],
                            };
                          }
                          if (browser === 'chrome') {
                            return {
                              cookies: [
                                { name: 'auth_token', value: 'good-chrome-auth-5678', domain: 'x.com' },
                                { name: 'ct0', value: 'good-chrome-ct0-9012', domain: 'x.com' },
                              ],
                              warnings: [],
                            };
                          }
                          return { cookies: [], warnings: [] };
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
        register_file = tmp_path / "register-loader.mjs"
        register_file.write_text(
            "import { register } from 'node:module';\n"
            f"register({json.dumps(loader.as_uri())});\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        for key in (
            "AUTH_TOKEN",
            "CT0",
            "TWITTER_AUTH_TOKEN",
            "TWITTER_CT0",
            "BIRD_DISABLE_BROWSER_COOKIES",
            "LAST30DAYS_DISABLE_BROWSER_COOKIES",
        ):
            env.pop(key, None)

        script = textwrap.dedent(
            """
            import { resolveCredentials } from './skills/last30days/scripts/lib/vendor/bird-search/lib/cookies.js';
            const res = await resolveCredentials({ cookieSource: ['safari', 'chrome'] });
            process.stdout.write(JSON.stringify(res));
            """
        )
        res = subprocess.run(
            ["node", "--import", register_file.as_uri(), "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        data = json.loads(res.stdout)
        assert data["cookies"]["authToken"] == "good-chrome-auth-5678"
        assert data["cookies"]["ct0"] == "good-chrome-ct0-9012"
        assert data["cookies"]["source"] == "Chrome default profile"
        assert "Invalid auth_token/ct0 format" in data["warnings"]
        joined_warnings = "\n".join(data["warnings"])
        assert "bad-safari-secret" not in joined_warnings
        assert "evil=1" not in joined_warnings
        assert "bad-safari-secret" not in res.stderr


# ---------------------------------------------------------------------------
# HIGH-155: Leading `-` queries & CLI option parsing across Python -> Node
# ---------------------------------------------------------------------------


class TestHigh155CliOptionBoundary:
    def test_python_invocations_place_flags_before_double_dash_and_query_after(self):
        cmds = []

        def fake_run(cmd, timeout=None, env=None, **_kwargs):
            cmds.append(cmd)
            return SubprocResult(returncode=0, stdout="[]", stderr="")

        with mock.patch.object(bird_x.subproc, "run_with_timeout", side_effect=fake_run):
            bird_x._invoke_bird_subprocess("--check", count=10, timeout=15, top=True)
            bird_x._invoke_bird_subprocess("-is:retweet claude", count=5, timeout=15, top=False)
            bird_x.search_handles(["alice"], "topic", "2026-05-01", count_per=3)
            bird_x.search_mentions(["alice"], "2026-05-01", count_per=3)

        assert len(cmds) == 4
        for cmd in cmds:
            assert "--" in cmd
            sep = cmd.index("--")
            assert sep == len(cmd) - 2
            assert "--json" in cmd[:sep]
            assert "--count" in cmd[:sep]
        assert cmds[0][-1] == "--check"
        assert "--top" in cmds[0][: cmds[0].index("--")]
        assert cmds[1][-1] == "-is:retweet claude"
        assert "--top" not in cmds[1][: cmds[1].index("--")]

    @pytest.mark.skipif(not HAS_NODE, reason="node is required")
    @pytest.mark.parametrize(
        "query",
        [
            "-is:retweet claude",
            "-crypto news",
            "--verbose curl",
            "-10% inflation",
            "--check",
            "--whoami",
            "--count",
            "-n",
        ],
    )
    def test_bird_search_mjs_accepts_leading_dash_and_flag_named_queries_after_double_dash(
        self, tmp_path, query
    ):
        stub = tmp_path / "stub-q.mjs"
        stub.write_text(
            textwrap.dedent(
                """
                globalThis.fetch = async (url) => {
                  const u = new URL(String(url));
                  const v = JSON.parse(u.searchParams.get('variables'));
                  process.stderr.write(`RAW_QUERY=${JSON.stringify(v.rawQuery)}\\n`);
                  const body = {data: {search_by_raw_query: {search_timeline: {timeline: {instructions: []}}}}};
                  return new Response(JSON.stringify(body), {status: 200, headers: {'content-type': 'application/json'}});
                };
                """
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            AUTH_TOKEN="dummy-auth-token-1234",
            CT0="dummy-ct0-token-5678",
            BIRD_DISABLE_BROWSER_COOKIES="1",
        )
        res = subprocess.run(
            ["node", "--import", stub.as_uri(), str(BIRD_MJS), "--count", "3", "--json", "--", query],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
        assert json.loads(res.stdout) == []
        seen = [
            json.loads(line.split("=", 1)[1])
            for line in res.stderr.splitlines()
            if line.startswith("RAW_QUERY=")
        ]
        assert seen == [query]

    @pytest.mark.skipif(not HAS_NODE, reason="node is required")
    @pytest.mark.parametrize(
        "bad_args",
        [
            ["--count", "abc", "--json", "--", "hello"],
            ["--count", "0", "--json", "--", "hello"],
            ["--count", "-5", "--json", "--", "hello"],
            ["--count", "10abc", "--json", "--", "hello"],
            ["-n", "NaN", "--json", "--", "hello"],
            ["--count"],
            ["--unknown-flag", "hello"],
        ],
    )
    def test_bird_search_mjs_rejects_invalid_counts_and_unknown_flags(self, bad_args):
        env = os.environ.copy()
        env.update(
            AUTH_TOKEN="dummy-auth-token-1234",
            CT0="dummy-ct0-token-5678",
            BIRD_DISABLE_BROWSER_COOKIES="1",
        )
        res = subprocess.run(
            ["node", str(BIRD_MJS), *bad_args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 1
        assert res.stdout == ""
        assert res.stderr.strip() != ""

    @pytest.mark.skipif(not HAS_NODE, reason="node is required")
    def test_unknown_option_does_not_echo_inline_token_to_stderr(self):
        env = os.environ.copy()
        env.update(
            AUTH_TOKEN="dummy-auth-token-1234",
            CT0="dummy-ct0-token-5678",
            BIRD_DISABLE_BROWSER_COOKIES="1",
        )
        inline_secret = "secret_inline_token_12345"
        res = subprocess.run(
            ["node", str(BIRD_MJS), f"--auth-token={inline_secret}", "hello"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 1
        assert res.stdout == ""
        assert "Unknown option" in res.stderr
        assert inline_secret not in res.stderr
        assert "--auth-token" not in res.stderr

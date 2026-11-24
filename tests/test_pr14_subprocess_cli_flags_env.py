"""Regression tests for PR 14: CLI flag injection & env isolation in subprocess sources.

Covers:
- HIGH-182: Bright Data ambient BRIGHTDATA_API_KEY consistency + credential scrubbing
- HIGH-187: Bright Data Windows PATHEXT binary resolution (.cmd/.bat/.exe) without shell=True
- HIGH-191: Digg CLI `--` separator, upstream cluster ID validation, outdated flag matching, and child env isolation
- HIGH-192: Techmeme CLI `--` separator, exact `--pages` unknown-flag matching, and child env isolation
- HIGH-199: Trustpilot CLI option-shaped topic/domain/hit rejection + `--` separator
- HIGH-200: Trustpilot child env allowlist stripping unrelated provider secrets and NODE_OPTIONS
- HIGH-216: evaluate_search_quality.py `--research-only` / `--` topic isolation and git rev validation
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import evaluate_search_quality as evaluator
from lib import brightdata, digg, subproc, techmeme, trustpilot


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setattr(digg, "_all_topic_search", None)
    trustpilot._reset_state_for_tests()
    yield
    trustpilot._reset_state_for_tests()


def _proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> subproc.SubprocResult:
    return subproc.SubprocResult(returncode=returncode, stdout=stdout, stderr=stderr)


# =====================================================================
# HIGH-182: Bright Data ambient API key consistency & secret scrubbing
# =====================================================================


def test_high182_ambient_key_recognized_by_has_credentials_and_scrubbed(monkeypatch, tmp_path):
    secret = "bd-ambient-secret-token-98765"
    monkeypatch.setenv("BRIGHTDATA_API_KEY", f"  {secret}  ")
    monkeypatch.setattr(brightdata, "_config_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")

    assert brightdata._api_key(None) == secret
    assert brightdata._api_key({}) == secret
    assert brightdata.has_credentials(None) is True
    assert brightdata.has_credentials({}) is True

    logged: list[str] = []
    seen_env: dict[str, str] = {}
    monkeypatch.setattr(brightdata.log, "source_log", lambda _n, msg, **k: logged.append(msg))

    def fake_run(cmd, *, timeout, env=None):
        seen_env.update(env or {})
        return _proc(
            returncode=1,
            stderr=(
                f"Warning: 401 unauthorized for key {secret}\n"
                f"Error: 401 rejected api_key={secret}"
            ),
        )

    monkeypatch.setattr(subproc, "run_with_timeout", fake_run)
    for cfg in (None, {}):
        logged.clear()
        seen_env.clear()
        out = brightdata.run_pipeline("gd_x", {"url": "u"}, timeout=30, config=cfg)
        assert seen_env["BRIGHTDATA_API_KEY"] == secret
        assert out["records"] == []
        assert secret not in out["error"]
        assert not any(secret in m for m in logged)


def test_high182_credentials_json_auth_scrubs_bearer_kv_and_userinfo(monkeypatch, tmp_path):
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")
    logged: list[str] = []
    monkeypatch.setattr(brightdata.log, "source_log", lambda _n, msg, **k: logged.append(msg))

    stderr = (
        "Warning: 401 Bearer bd_live_bearer_secret_xyz at https://user:pass123@proxy.brightdata.test/api\n"
        "Warning: 401 Authorization: Basic dXNlcjpwYXNzd29yZA== socks5://puser:ppass@proxy.local:1080\n"
        "Warning: token expired - run brightdata login\n"
        "Error: 401 BRIGHTDATA_API_KEY=creds_json_secret_12345 "
        'api_key=bd_kv_secret_111 token="bd_quoted_222" '
        '"access_token": "bd_escaped_\\"333\\"_secret" '
        "token 'bd_single_quoted_555' token bd_spaced_444444"
    )
    monkeypatch.setattr(subproc, "run_with_timeout", lambda *a, **k: _proc(returncode=1, stderr=stderr))

    out = brightdata.run_pipeline("gd_x", {"url": "u"}, timeout=30, config={})
    combined = "\n".join(logged + [out["error"]])
    for leaked in (
        "bd_live_bearer_secret_xyz",
        "user:pass123",
        "dXNlcjpwYXNzd29yZA==",
        "puser:ppass",
        "creds_json_secret_12345",
        "bd_kv_secret_111",
        "bd_quoted_222",
        "bd_escaped_",
        "bd_single_quoted_555",
        "bd_spaced_444444",
    ):
        assert leaked not in combined
    # Legitimate auth warning prose must survive redaction intact.
    assert any("token expired" in msg for msg in logged)


def test_high182_url_encoded_secret_and_whitespace_child_env_fallback(monkeypatch):
    raw_secret = "bd+key/123=secret"
    encoded_secret = "bd%2Bkey%2F123%3Dsecret"
    monkeypatch.setenv("BRIGHTDATA_API_KEY", f"  {raw_secret}  ")

    child = brightdata._child_env("   ")
    assert child["BRIGHTDATA_API_KEY"] == raw_secret

    scrubbed = brightdata._scrub(
        f"Error: request failed for https://api.test/?k={encoded_secret} raw={raw_secret}",
        raw_secret,
    )
    assert raw_secret not in scrubbed
    assert encoded_secret not in scrubbed


def test_high182_exception_messages_also_scrub_secrets(monkeypatch):
    secret = "bd-exc-secret-777"
    monkeypatch.setenv("BRIGHTDATA_API_KEY", secret)
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")
    logged: list[str] = []
    monkeypatch.setattr(brightdata.log, "source_log", lambda _n, msg, **k: logged.append(msg))

    def boom(*a, **k):
        raise OSError(f"spawn failed with Bearer tok_999 and {secret}")

    monkeypatch.setattr(subproc, "run_with_timeout", boom)
    out = brightdata.run_pipeline("gd_x", {"url": "u"}, timeout=30)
    assert secret not in out["error"]
    assert "tok_999" not in out["error"]
    assert not any(secret in m or "tok_999" in m for m in logged)


# =====================================================================
# HIGH-187: Bright Data Windows PATHEXT resolution & BatBadBut defense
# =====================================================================


@pytest.mark.parametrize(
    "resolved_binary",
    [
        r"C:\Tools\brightdata.exe",
        "/home/dev/.local/bin/brightdata",
    ],
)
def test_high187_native_binary_resolved_once_and_passed_directly(monkeypatch, resolved_binary):
    which_calls: list[str] = []

    def fake_which(name):
        which_calls.append(name)
        return resolved_binary if name == "brightdata" else None

    monkeypatch.setattr(brightdata.shutil, "which", fake_which)
    seen: dict = {}

    def fake_run(cmd, *, timeout, env=None):
        seen["cmd"] = list(cmd)
        return _proc(stdout="[]")

    monkeypatch.setattr(subproc, "run_with_timeout", fake_run)
    out = brightdata.run_pipeline("gd_test", {"url": "https://example.com"}, timeout=30)
    assert "error" not in out
    assert which_calls == ["brightdata"]
    assert seen["cmd"][0] == resolved_binary
    assert seen["cmd"][1:5] == ["pipelines", "run", "gd_test", "collect_by_url"]


@pytest.mark.parametrize(
    "shim_name,bin_manifest,entry_rel",
    [
        ("brightdata.CMD", '{"bin": "dist/index.js"}', "dist/index.js"),
        ("brightdata.cmd", '{"bin": {"brightdata": "dist/cli.js"}}', "dist/cli.js"),
        ("brightdata.bat", "{}", "dist/index.js"),
    ],
)
def test_high187_windows_batch_shim_unwraps_to_node_and_preserves_metacharacters(
    monkeypatch, tmp_path, shim_name, bin_manifest, entry_rel
):
    shim = tmp_path / shim_name
    shim.write_text("@echo off\r\n", encoding="utf-8")
    node_exe = tmp_path / "node.exe"
    node_exe.write_bytes(b"MZ")
    pkg_dir = tmp_path / "node_modules" / "@brightdata" / "cli"
    entry = pkg_dir / entry_rel
    entry.parent.mkdir(parents=True)
    entry.write_text("// entrypoint\n", encoding="utf-8")
    (pkg_dir / "package.json").write_text(bin_manifest, encoding="utf-8")

    monkeypatch.setattr(
        brightdata.shutil, "which", lambda name: str(shim) if name == "brightdata" else None
    )
    seen: dict = {}

    def fake_run(cmd, *, timeout, env=None):
        seen["cmd"] = list(cmd)
        return _proc(stdout="[]")

    monkeypatch.setattr(subproc, "run_with_timeout", fake_run)
    payload_record = {"keyword": "USB-C & Lightning | 100% ^ calc.exe"}
    out = brightdata.run_pipeline("gd_test", payload_record, timeout=30)
    assert "error" not in out
    assert seen["cmd"][0] == str(node_exe)
    assert seen["cmd"][1] == str(entry.resolve())
    assert seen["cmd"][2:6] == ["pipelines", "run", "gd_test", "collect_by_url"]
    assert json.loads(seen["cmd"][seen["cmd"].index("--input") + 1]) == [payload_record]


@pytest.mark.parametrize(
    "shim_name",
    [
        r"C:\Users\dev\AppData\Roaming\npm\brightdata.CMD",
        r"C:\Tools\brightdata.bat",
    ],
)
def test_high187_unwrappable_batch_shim_fails_closed_without_invoking_cmd_exe(
    monkeypatch, shim_name
):
    monkeypatch.setattr(
        brightdata.shutil, "which", lambda name: shim_name if name == "brightdata" else None
    )
    called = []
    monkeypatch.setattr(subproc, "run_with_timeout", lambda *a, **k: called.append(a))
    out = brightdata.run_pipeline(
        "gd_test", {"keyword": "router & calc.exe"}, timeout=30
    )
    assert out["records"] == []
    assert "batch shim" in out["error"]
    assert called == []


def test_high187_batch_shim_rejects_package_json_path_traversal(monkeypatch, tmp_path):
    shim = tmp_path / "brightdata.cmd"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    (tmp_path / "node.exe").write_bytes(b"MZ")
    outside = tmp_path / "evil.js"
    outside.write_text("// outside pkg\n", encoding="utf-8")
    pkg_dir = tmp_path / "node_modules" / "@brightdata" / "cli"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text('{"bin": "../../../evil.js"}', encoding="utf-8")

    monkeypatch.setattr(
        brightdata.shutil, "which", lambda name: str(shim) if name == "brightdata" else None
    )
    called = []
    monkeypatch.setattr(subproc, "run_with_timeout", lambda *a, **k: called.append(a))
    out = brightdata.run_pipeline("gd_test", {"url": "https://example.com"}, timeout=30)
    assert out["records"] == []
    assert "batch shim" in out["error"]
    assert called == []


def test_high187_batch_shim_rejects_node_cmd_fallback_and_fails_closed(monkeypatch, tmp_path):
    shim = tmp_path / "brightdata.cmd"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    pkg_dir = tmp_path / "node_modules" / "@brightdata" / "cli"
    entry = pkg_dir / "dist" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// ok\n", encoding="utf-8")
    (pkg_dir / "package.json").write_text('{"bin": "dist/index.js"}', encoding="utf-8")

    def fake_which(name):
        if name == "brightdata":
            return str(shim)
        if name == "node":
            return r"C:\Tools\node.cmd"
        return None

    monkeypatch.setattr(brightdata.shutil, "which", fake_which)
    called = []
    monkeypatch.setattr(subproc, "run_with_timeout", lambda *a, **k: called.append(a))
    out = brightdata.run_pipeline("gd_test", {"keyword": "router & calc.exe"}, timeout=30)
    assert out["records"] == []
    assert "batch shim" in out["error"]
    assert called == []


def test_high187_build_args_defaults_to_cli_bin_for_direct_callers():
    args = brightdata._build_args("gd_test", {"url": "u"}, cli_timeout=20)
    assert args[0] == brightdata.CLI_BIN


# =====================================================================
# HIGH-191: Digg CLI `--` separator, upstream ID validation & env isolation
# =====================================================================


@pytest.mark.parametrize(
    "topic",
    [
        "--config=/tmp/evil.yaml",
        "--deliver=file:/tmp/pwn",
        "--dangerously-skip-permissions",
        "--help",
        "-O3",
        "-x",
    ],
)
@pytest.mark.parametrize("all_topics", [True, False])
def test_high191_digg_search_args_put_flags_before_double_dash(topic, all_topics):
    args = digg._build_search_args(topic, 20, all_topics=all_topics, by_score=all_topics)
    sep = args.index("--")
    assert sep == len(args) - 2
    assert args[0:2] == [digg.CLI_BIN, "search"]
    assert "--since" in args[:sep]
    assert "--agent" in args[:sep]
    assert "--limit" in args[:sep]
    if all_topics:
        assert "--sort" in args[:sep]
        assert args[-1] == digg._fit_query(topic)
    else:
        assert "--topic" in args[:sep]
        assert args[-1] == topic


def test_high191_outdated_cli_error_only_matches_known_digg_search_flags():
    for flag in ("--topic", "--since", "--agent", "--limit", "--sort"):
        assert digg.outdated_cli_error(f"Error: unknown flag: {flag}") is not None
    for bogus in ("--dangerously-skip-permissions", "--config", "--deliver", "--help"):
        assert digg.outdated_cli_error(f"Error: unknown flag: {bogus}") is None


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "-x",
        "--deliver=file:/tmp/pwn",
        "--config=/etc/passwd",
        "../admin",
        "abc/def",
        "abc?x=1",
        "abc#frag",
        "abc def",
        "abc\n--evil",
        "_leading_underscore",
    ],
)
def test_high191_digg_rejects_invalid_cluster_ids_across_all_sinks(monkeypatch, bad_id):
    with pytest.raises(ValueError):
        digg._build_posts_args(bad_id, 5)
    with pytest.raises(ValueError):
        digg._build_sources_args(bad_id)
    with pytest.raises(ValueError):
        digg._build_url(bad_id)

    called = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/usr/bin/digg-pp-cli")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", lambda *a, **k: called.append(a))

    assert digg.fetch_top_posts(bad_id, posts_per=5) == []

    class _Item:
        source = "digg"
        engagement = {"digg_score": 500}
        metadata = {digg.STORY_ID_KEY: bad_id}

    assert digg.fetch_story_sources([_Item()], depth="default") == {}
    assert called == []


def test_high191_digg_parse_drops_invalid_url_id_and_strips_invalid_cluster_id():
    payload = {
        "results": [
            {
                "clusterUrlId": "--deliver=file:/tmp/pwn",
                "clusterId": "11111111-2222-3333-4444-000000000001",
                "url": "https://digg.com/tech/bad",
                "title": "Bad url id",
                "publishedAt": "2026-11-08T10:00:00Z",
            },
            {
                "clusterUrlId": "../traverse",
                "title": "Bad legacy url id",
                "firstPostAge": "2d",
            },
            {
                "clusterUrlId": "good1234",
                "clusterId": "--deliver=webhook:https://evil.example",
                "url": "https://digg.com/tech/good1234",
                "title": "Good story with poisoned clusterId",
                "publishedAt": "2026-11-08T10:00:00Z",
            },
        ]
    }
    items = digg.parse_digg_response(payload, from_date="2026-10-12")
    assert [it["id"] for it in items] == ["good1234"]
    assert items[0]["cluster_id"] == ""


def test_high191_digg_posts_and_sources_args_use_double_dash():
    posts = digg._build_posts_args("abc123xy", 5)
    assert posts == [digg.CLI_BIN, "posts", "--agent", "--by", "rank", "--limit", "5", "--", "abc123xy"]
    sources = digg._build_sources_args("6043f14d-1717-4b75-8fb2-3a596fe44df5")
    assert sources == [
        digg.CLI_BIN, "posts", "--agent", "--limit", str(digg.SOURCES_PER_STORY), "--",
        "6043f14d-1717-4b75-8fb2-3a596fe44df5",
    ]


# =====================================================================
# HIGH-192: Techmeme CLI `--` separator & exact `--pages` flag match
# =====================================================================


@pytest.mark.parametrize(
    "topic",
    [
        "-O3 compiler",
        "--pages=99",
        "--pages-router",
        "--config=/tmp/x",
        "--deliver=file:/tmp/pwn",
    ],
)
def test_high192_techmeme_search_args_put_flags_before_double_dash(topic):
    args = techmeme._build_search_args(topic, "2026-10-01", "2026-10-31", pages=2)
    assert args == [
        techmeme.CLI_BIN,
        "search",
        "--json",
        "--pages",
        "2",
        "--",
        f"{topic} date:[10/1/26 TO 10/31/26]",
    ]
    single = techmeme._build_search_args(topic, pages=1)
    assert single == [techmeme.CLI_BIN, "search", "--json", "--", topic]


def test_high192_rejects_pages_flag_requires_complete_token_and_avoids_bogus_retry(monkeypatch):
    assert techmeme._rejects_pages_flag({"error": "Error: unknown flag: --pages"}) is True
    assert techmeme._rejects_pages_flag({"error": "unknown flag: --pages\nUsage: ..."}) is True
    assert techmeme._rejects_pages_flag({"error": "Error: unknown flag: --pages-router"}) is False
    assert techmeme._rejects_pages_flag({"error": "Error: unknown flag: --config (see --pages)"}) is False

    calls: list[list[str]] = []

    def fake_run(cmd, *, timeout, env=None):
        calls.append(list(cmd))
        return _proc(returncode=1, stderr="Error: unknown flag: --pages-router\n")

    monkeypatch.setattr(techmeme, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme.subproc, "run_with_timeout", fake_run)
    out = techmeme.search_techmeme("--pages-router", "2026-10-01", "2026-10-31")
    assert len(calls) == 1
    assert out["error"] == "Error: unknown flag: --pages-router"


# =====================================================================
# HIGH-199: Trustpilot option-shaped input rejection & `--` separator
# =====================================================================


@pytest.mark.parametrize(
    "bad_topic",
    [
        "-x Brand",
        "--deliver=file:/tmp/pwn Brand",
        "--help",
        "-x chownow.com",
        "Brand\n--deliver=file:/tmp/pwn",
        "Brand\x00",
    ],
)
def test_high199_trustpilot_is_brand_shaped_rejects_option_and_control_topics(bad_topic):
    assert trustpilot.is_brand_shaped(bad_topic) is False


@pytest.mark.parametrize(
    "bad_domain",
    [
        "--deliver=file:/tmp/pwn",
        "-bad.com",
        "bad-.com",
        "foo bar.com",
        "example.com\n--flag",
        "example.com/path",
        "localhost",
        "127.0.0.1",
        "example.xn---",
        "example.xn--a-",
        "example.xn--",
    ],
)
@pytest.mark.parametrize("domain_is_hint", [False, True])
def test_high199_trustpilot_rejects_invalid_explicit_domain_before_cli(
    monkeypatch, bad_domain, domain_is_hint
):
    called = []
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", lambda *a, **k: called.append(a) or {})
    out = trustpilot.search_trustpilot(
        "ChowNow", "2026-10-01", "2026-10-31",
        explicit_domain=bad_domain, domain_is_hint=domain_is_hint,
    )
    assert out == {"results": []}
    assert called == []


@pytest.mark.parametrize("valid_domain", ["example.xn--p1ai", "example.XN--P1AI", "example.xn--a"])
def test_high199_trustpilot_accepts_valid_punycode_tlds(valid_domain):
    assert trustpilot._is_valid_domain(valid_domain) is True


def test_high199_trustpilot_rejects_malicious_domains_from_search_hits():
    hits = [
        {"displayName": "ChowNow", "domain": "--deliver=file:/tmp/pwn", "numberOfReviews": 999999},
        {"displayName": "ChowNow", "identifyingName": "-evil.com", "numberOfReviews": 999999},
        {"displayName": "ChowNow", "domain": "chownow.com\n--flag", "numberOfReviews": 999999},
        {"displayName": "ChowNow", "domain": "chownow.com/review", "numberOfReviews": 999999},
    ]
    assert trustpilot._select_search_hit("ChowNow", hits) is None


def test_high199_trustpilot_builders_use_double_dash_and_reject_leading_dash(monkeypatch):
    assert trustpilot._build_info_args("chownow.com") == [
        trustpilot.CLI_BIN, "info", "--agent", "--", "chownow.com"
    ]
    with pytest.raises(ValueError):
        trustpilot._build_info_args("--deliver=file:/tmp/pwn")

    called = []
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(trustpilot, "_run_cli", lambda *a, **k: called.append(a) or {})

    assert trustpilot._search_domain("-x Brand") is None
    assert trustpilot._cached_info("--deliver=file:/tmp/pwn") is None
    assert trustpilot._cached_info("not a domain") is None
    assert called == []


# =====================================================================
# HIGH-200 (+ digg/techmeme): Subprocess environment allowlist isolation
# =====================================================================


_SECRET_VARS = {
    "OPENAI_API_KEY": "fake-openai",
    "PERPLEXITY_API_KEY": "fake-perplexity",
    "SCRAPECREATORS_API_KEY": "fake-sc",
    "XAI_API_KEY": "fake-xai",
    "GITHUB_TOKEN": "fake-gh",
    "OPENROUTER_API_KEY": "fake-or",
    "AUTH_TOKEN": "fake-auth-token",
    "CT0": "fake-ct0",
    "BRIGHTDATA_API_KEY": "fake-bd",
    "GOOGLE_API_KEY": "fake-google",
    "NODE_OPTIONS": "--require /tmp/untrusted.js",
    "PYTHONPATH": "/tmp/untrusted",
    "LD_PRELOAD": "/tmp/untrusted.so",
}

_ESSENTIAL_VARS = {
    "PATH": "/usr/local/bin:/usr/bin",
    "HOME": "/home/tester",
    "USERPROFILE": r"C:\Users\tester",
    "HOMEDRIVE": "C:",
    "HOMEPATH": r"\Users\tester",
    "APPDATA": r"C:\Users\tester\AppData\Roaming",
    "LOCALAPPDATA": r"C:\Users\tester\AppData\Local",
    "XDG_CONFIG_HOME": "/home/tester/.config",
    "XDG_DATA_HOME": "/home/tester/.local/share",
    "XDG_CACHE_HOME": "/home/tester/.cache",
    "SystemRoot": r"C:\Windows",
    "WINDIR": r"C:\Windows",
    "SystemDrive": "C:",
    "COMSPEC": r"C:\Windows\System32\cmd.exe",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "TMPDIR": "/tmp",
    "TEMP": r"C:\Temp",
    "TMP": r"C:\Temp",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "LC_MESSAGES": "C",
    "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
    "SSL_CERT_DIR": "/etc/ssl/certs",
    "HTTP_PROXY": "http://proxy.internal:8080",
    "https_proxy": "http://proxy.internal:8080",
    "NO_PROXY": "localhost,127.0.0.1",
}


@pytest.mark.parametrize("module", [trustpilot, digg, techmeme])
def test_high200_child_env_strips_secrets_and_preserves_cross_platform_vars(module):
    combined = {**_ESSENTIAL_VARS, **_SECRET_VARS}
    with mock.patch.object(module.os, "environ", combined):
        child = module._child_env()
    for secret_key in _SECRET_VARS:
        assert secret_key not in child
    for keep_key, keep_val in _ESSENTIAL_VARS.items():
        assert child[keep_key] == keep_val


def test_high200_real_child_subprocesses_do_not_receive_parent_secrets(monkeypatch):
    probe = (
        "import json, os; "
        "leaked = [k for k in ('OPENAI_API_KEY','XAI_API_KEY','SCRAPECREATORS_API_KEY',"
        "'AUTH_TOKEN','CT0','GITHUB_TOKEN','NODE_OPTIONS','PERPLEXITY_API_KEY') if k in os.environ]; "
        "print(json.dumps({'results': [{'leaked': leaked, 'has_path': 'PATH' in os.environ}], 'leaked': leaked}))"
    )
    monkeypatch.setattr(trustpilot, "_is_available", lambda: True)
    monkeypatch.setattr(digg, "_is_available", lambda: True)
    monkeypatch.setattr(techmeme, "_is_available", lambda: True)

    with mock.patch.dict(os.environ, _SECRET_VARS, clear=False):
        tp_out = trustpilot._run_cli([sys.executable, "-I", "-c", probe], timeout=10)
        digg_out = digg._run_cli([sys.executable, "-I", "-c", probe], timeout=10)
        tm_out = techmeme._run_cli([sys.executable, "-I", "-c", probe], timeout=10)

    assert tp_out["leaked"] == []
    assert digg_out["results"] == [{"leaked": [], "has_path": True}]
    assert tm_out["results"] == [{"leaked": [], "has_path": True}]


@pytest.mark.parametrize("module", [digg, techmeme, trustpilot])
def test_high200_proxy_userinfo_is_scrubbed_from_cli_stderr_and_logs(monkeypatch, module):
    logged: list[str] = []
    monkeypatch.setattr(module, "_is_available", lambda: True)
    monkeypatch.setattr(module, "_log", lambda msg: logged.append(msg))
    stderr = "proxyconnect tcp: http://proxyuser:supersecret@proxy.corp:8080: 407 Proxy Auth Required\n"
    monkeypatch.setattr(
        module.subproc, "run_with_timeout",
        lambda *a, **k: _proc(returncode=1, stderr=stderr),
    )

    out = module._run_cli([module.CLI_BIN, "search"], timeout=10)
    combined = "\n".join(logged + [out.get("error", "")])
    assert "proxyuser" not in combined
    assert "supersecret" not in combined
    assert "http://proxy.corp:8080" in out["error"]


# =====================================================================
# HIGH-216: evaluate_search_quality.py topic & git rev isolation
# =====================================================================


@pytest.mark.parametrize(
    "topic",
    [
        "--publish-html",
        "--store",
        "--corpus=/etc",
        "setup",
        "library feed --publish",
    ],
)
def test_high216_run_last30days_uses_research_only_and_double_dash(tmp_path, topic):
    engine = tmp_path / "skills" / "last30days" / "scripts" / "last30days.py"
    engine.parent.mkdir(parents=True)
    engine.write_text('parser.add_argument("--research-only")\nparser.add_argument("--json-profile")\n')

    completed = mock.Mock(returncode=0, stdout='{"ranked_candidates": []}', stderr="")
    with mock.patch.object(evaluator.subprocess, "run", return_value=completed) as run:
        evaluator.run_last30days(
            tmp_path, topic, search="reddit", timeout_seconds=30, quick=True, mock=True, env={"PATH": "/bin"},
        )

    cmd = run.call_args.args[0]
    sep = cmd.index("--")
    assert "--research-only" in cmd[:sep]
    assert "--json-profile=raw" in cmd[:sep]
    assert "--emit=json" in cmd[:sep]
    assert cmd[sep:] == ["--", topic]


def test_high216_run_last30days_omits_research_only_for_legacy_engine(tmp_path):
    engine = tmp_path / "skills" / "last30days" / "scripts" / "last30days.py"
    engine.parent.mkdir(parents=True)
    engine.write_text('parser.add_argument("--emit")\n')

    completed = mock.Mock(returncode=0, stdout='{"reddit": []}', stderr="")
    with mock.patch.object(evaluator.subprocess, "run", return_value=completed) as run:
        evaluator.run_last30days(
            tmp_path, "--store", search="", timeout_seconds=30, quick=False, mock=False, env={"PATH": "/bin"},
        )

    cmd = run.call_args.args[0]
    assert "--research-only" not in cmd
    assert "--json-profile=raw" not in cmd
    assert cmd[-2:] == ["--", "--store"]


@pytest.mark.parametrize("bad_rev", ["--orphan", "-b", "--checkout", "", "  HEAD  ", "HEAD\n--evil", "HEAD\x00"])
def test_high216_create_worktree_rejects_option_and_malformed_revs(bad_rev):
    with mock.patch.object(evaluator.subprocess, "run") as run, \
         mock.patch.object(evaluator.tempfile, "mkdtemp") as mkdtemp:
        with pytest.raises(ValueError):
            evaluator.create_worktree(bad_rev)
    run.assert_not_called()
    mkdtemp.assert_not_called()


def test_high216_create_worktree_resolves_commit_with_end_of_options_and_cleans_up_on_error(tmp_path):
    sha = "a" * 40
    wt_path = tmp_path / "wt-ok"
    wt_path.mkdir()

    with mock.patch.object(evaluator.tempfile, "mkdtemp", return_value=str(wt_path)), \
         mock.patch.object(
             evaluator.subprocess, "run",
             side_effect=[
                 mock.Mock(returncode=0, stdout=f"{sha}\n", stderr=""),
                 mock.Mock(returncode=0, stdout="", stderr=""),
             ],
         ) as run:
        result = evaluator.create_worktree("HEAD~1")

    assert result == wt_path
    assert run.call_args_list[0].args[0] == [
        "git", "rev-parse", "--verify", "--end-of-options", "HEAD~1^{commit}"
    ]
    assert run.call_args_list[1].args[0] == [
        "git", "worktree", "add", "--detach", str(wt_path), sha
    ]

    # Verify failed worktree creation removes partially populated dir & git worktree metadata.
    wt_fail = tmp_path / "wt-fail"
    nested = wt_fail / "sub"
    nested.mkdir(parents=True)
    (nested / "partial.txt").write_text("partial", encoding="utf-8")
    with mock.patch.object(evaluator.tempfile, "mkdtemp", return_value=str(wt_fail)), \
         mock.patch.object(evaluator, "remove_worktree", wraps=evaluator.remove_worktree) as rm_wt, \
         mock.patch.object(
             evaluator.subprocess, "run",
             side_effect=[
                 mock.Mock(returncode=0, stdout=f"{sha}\n", stderr=""),
                 subprocess.CalledProcessError(128, ["git", "worktree", "add"]),
                 mock.Mock(returncode=0, stdout="", stderr=""),
             ],
         ):
        with pytest.raises(subprocess.CalledProcessError):
            evaluator.create_worktree("HEAD")
    rm_wt.assert_called_once_with(wt_fail)
    assert not wt_fail.exists()

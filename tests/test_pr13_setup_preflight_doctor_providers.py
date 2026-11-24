"""Regression tests for PR 13: setup, preflight, doctor, and provider hardening.

Covers:
  - HIGH-064: permission_preflight hosted mode, endpoint overrides, credentials,
              local corpus & secret store disclosures, CDP scheme case, reads_values
  - HIGH-066: per-user private GitHub device handle path, atomic 0o600 writes,
              fail-closed persistence before user-code/browser emission, safe poll read
  - HIGH-067: atomic private .env rewrite and hardened _open_secret_append
  - HIGH-069: clean/no-config mode (env_path=None) handling across writers and CLI
  - HIGH-071: atomic 0o600 doctor-cache.json, complete secret leak guard,
              KEY_PRESENCE_VARS parity, and full _config_fingerprint coverage
  - HIGH-073: scoped @xdevplatform/xurl package, app-only bearer prescription,
              and OAuth-only token-store mismatch detection
  - HIGH-075: Gemini API key in x-goog-api-key header with same-origin redirect guard
              and anchored model validation/percent-encoding
"""

from __future__ import annotations

import concurrent.futures
import http.server
import io
import json
import os
import socketserver
import stat
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from urllib.request import Request

import pytest

import last30days as cli
from lib import (
    backends,
    chrome_cdp,
    doctor,
    env,
    health,
    http as http_mod,
    permission_preflight,
    pipeline,
    private_files,
    providers,
    proxy,
    setup_wizard,
    xurl_x,
)


def _diag(**overrides):
    base = {
        "providers": {
            "google": False,
            "openai": False,
            "xai": False,
            "openrouter": False,
            "perplexity": False,
        },
        "has_scrapecreators": False,
        "has_github": False,
        "available_sources": ["reddit", "hackernews", "polymarket"],
        "safe": True,
        "config_source": "env_only",
        "ignored_project_config": None,
        "ignored_project_config_keys": [],
        "ignored_endpoint_overrides": [],
        "browser_cookies": {"mode": "plan_only", "browsers": [], "reads_values": False},
        "external_commands": {"yt-dlp": False, "digg-pp-cli": False, "gh": False},
        "credential_destinations": {"global_env": None},
        "local_writes": [],
        "native_search": False,
    }
    base.update(overrides)
    return base


class _NtOs:
    """Delegates to os while reporting os.name == 'nt'."""

    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


# ---------------------------------------------------------------------------
# HIGH-064: permission_preflight completeness and accuracy
# ---------------------------------------------------------------------------


class TestHigh064PermissionPreflight:
    def test_hosted_mode_disclosed_and_hides_local_sources(self, monkeypatch):
        secret = "dummy-hosted-key-064"
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", secret)

        preflight = permission_preflight.build({}, _diag())

        assert preflight["status"] == "ready"
        assert preflight["network"]["hosted_mode"] is True
        assert preflight["network"]["available_sources"] == ["hosted"]
        assert "LAST30DAYS_API_BASE" in preflight["network"]["endpoint_overrides"]
        assert preflight["credentials"]["hosted_api"]["present"] is True

        rendered = permission_preflight.render_text(preflight)
        assert "Hosted mode: active (LAST30DAYS_API_BASE)" in rendered
        assert "last30days hosted/proxy API key" in rendered
        assert secret not in json.dumps(preflight)
        assert secret not in rendered
        assert "hosted.example" not in rendered

    @pytest.mark.parametrize(
        "bad_base",
        [
            "http://remote-cleartext.example/api/v1",
            "https://secret-user:secret-pass@hosted.example/api/v1",
            "https://hosted.example/api/v1?token=secret-param",
            "https://hosted.example/api/v1#frag",
            "https://hosted.example/api/v1\nX-Injected: 1",
            "https://hosted.example\\evil.example/api/v1",
        ],
    )
    def test_invalid_hosted_api_base_rejected_without_leaking_url(self, monkeypatch, bad_base):
        monkeypatch.setenv("LAST30DAYS_API_BASE", bad_base)
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key-064")

        preflight = permission_preflight.build({}, _diag())

        assert preflight["status"] == "action_needed"
        assert preflight["network"]["hosted_mode"] is False
        assert "LAST30DAYS_API_BASE" not in preflight["network"]["endpoint_overrides"]
        assert "LAST30DAYS_API_BASE" in preflight["network"]["ignored_endpoint_overrides"]
        assert any("LAST30DAYS_API_BASE" in item for item in preflight["action_items"])
        serialized = json.dumps(preflight) + permission_preflight.render_text(preflight)
        assert "secret-user" not in serialized
        assert "secret-pass" not in serialized
        assert "secret-param" not in serialized

    def test_proxy_mode_keeps_local_sources_and_reports_api_base(self, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_API_MODE", "proxy")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://proxy.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-proxy-key-064")

        preflight = permission_preflight.build({}, _diag())

        assert preflight["status"] == "ready"
        assert preflight["network"]["hosted_mode"] is False
        assert preflight["network"]["available_sources"] == ["reddit", "hackernews", "polymarket"]
        assert "LAST30DAYS_API_BASE" in preflight["network"]["endpoint_overrides"]
        assert preflight["credentials"]["hosted_api"]["present"] is True

    def test_new_endpoint_override_keys_and_amazon_default_handling(self, tmp_path, monkeypatch):
        for key in (
            "LAST30DAYS_API_BASE",
            "BROWSER_CDP_URL",
            "YOUTUBE_PROXY",
            "YT_PROXY",
            "LAST30DAYS_AMAZON_DOMAIN",
        ):
            assert key in permission_preflight.ENDPOINT_OVERRIDE_KEYS

        default_only = permission_preflight.build(
            {"LAST30DAYS_AMAZON_DOMAIN": "https://www.amazon.com/"},
            _diag(),
        )
        assert "LAST30DAYS_AMAZON_DOMAIN" not in default_only["network"]["endpoint_overrides"]

        active = permission_preflight.build(
            {
                "BROWSER_CDP_URL": "http://127.0.0.1:18800",
                "YOUTUBE_PROXY": "http://127.0.0.1:8080",
                "YT_PROXY": "socks5://127.0.0.1:1080",
                "LAST30DAYS_AMAZON_DOMAIN": "https://www.amazon.co.uk",
            },
            _diag(),
        )
        assert {
            "BROWSER_CDP_URL",
            "YOUTUBE_PROXY",
            "YT_PROXY",
            "LAST30DAYS_AMAZON_DOMAIN",
        } <= set(active["network"]["endpoint_overrides"])

        project_env = tmp_path / ".claude" / "last30days.env"
        project_env.parent.mkdir()
        project_env.write_text(
            "BROWSER_CDP_URL=http://127.0.0.1:19999\n"
            "YOUTUBE_PROXY=http://attacker.example:8080\n"
            "YT_PROXY=http://attacker.example:8081\n"
            "LAST30DAYS_AMAZON_DOMAIN=https://evil.example\n"
            "LAST30DAYS_API_BASE=https://evil.example/api/v1\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(env, "CONFIG_FILE", None)
        with mock.patch.object(env, "_load_keychain", return_value={}), \
             mock.patch.object(env, "_load_pass", return_value={}):
            config = env.get_config(
                policy=env.ConfigLoadPolicy(
                    browser_cookies="plan_only",
                    inspect_ignored_project_config=True,
                )
            )
        diag = pipeline.diagnose(config, safe=True)
        ignored = set(diag["permission_preflight"]["network"]["ignored_endpoint_overrides"])
        assert {
            "BROWSER_CDP_URL",
            "YOUTUBE_PROXY",
            "YT_PROXY",
            "LAST30DAYS_AMAZON_DOMAIN",
            "LAST30DAYS_API_BASE",
        } <= ignored

    def test_all_credential_families_disclosed_without_values(self):
        secrets = {
            "AUTH_TOKEN": "dummy-auth-token-064",
            "CT0": "dummy-ct0-064",
            "BRAVE_API_KEY": "dummy-brave-064",
            "EXA_API_KEY": "dummy-exa-064",
            "SERPER_API_KEY": "dummy-serper-064",
            "PARALLEL_API_KEY": "dummy-parallel-064",
            "BSKY_HANDLE": "tester.bsky.social",
            "BSKY_APP_PASSWORD": "dummy-bsky-pass-064",
            "TRUTHSOCIAL_TOKEN": "dummy-truth-064",
            "APIFY_API_TOKEN": "dummy-apify-064",
            "XQUIK_API_KEY": "dummy-xquik-064",
            "BRIGHTDATA_API_KEY": "dummy-brightdata-064",
            "GROQ_API_KEY": "dummy-groq-064",
            "DEEPLINE_API_KEY": "dummy-deepline-064",
            "LAST30DAYS_API_KEY": "dummy-hosted-064",
            "LAST30DAYS_PUBLISH_PASSWORD": "dummy-publish-064",
        }
        preflight = permission_preflight.build(secrets, _diag())
        creds = preflight["credentials"]
        for family in (
            "x_cookies",
            "brave",
            "exa",
            "serper",
            "parallel",
            "bluesky",
            "truthsocial",
            "apify",
            "xquik",
            "brightdata",
            "groq",
            "deepline",
            "hosted_api",
            "publish_password",
        ):
            assert creds[family]["present"] is True, family

        text = permission_preflight.render_text(preflight)
        assert "Present: none" not in text
        combined = json.dumps(preflight) + text
        for key, value in secrets.items():
            if key == "BSKY_HANDLE":
                continue
            assert value not in combined, key

        # Proxy sentinel placeholders must not masquerade as user-held keys.
        proxied = permission_preflight.build({"SERPER_API_KEY": proxy.SENTINEL}, _diag())
        assert proxied["credentials"]["serper"]["present"] is False

    def test_corpus_dirs_and_secret_stores_disclosed_with_env_file(self, tmp_path, monkeypatch):
        notes = tmp_path / "notes"
        notes.mkdir()
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key-064")
        monkeypatch.delenv(env.KEYCHAIN_DISABLE_ENV, raising=False)
        with mock.patch.object(permission_preflight.platform, "system", return_value="Darwin"), \
             mock.patch.object(
                 permission_preflight.shutil,
                 "which",
                 side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd in {"security", "pass"} else None,
             ):
            preflight = permission_preflight.build(
                {"LAST30DAYS_CORPUS_DIRS": str(notes)},
                _diag(config_source="global:/Users/tester/.config/last30days/.env"),
            )

        assert preflight["local_reads"]["corpus_dirs"] == [str(notes.resolve())]
        # Local corpus bypasses hosted mode, so secret stores and local sources remain active.
        assert preflight["network"]["hosted_mode"] is False
        assert preflight["local_reads"]["secret_stores"] == ["keychain", "pass"]
        rendered = permission_preflight.render_text(preflight)
        assert f"Corpus directories: {notes.resolve()}" in rendered
        assert "Secret stores: keychain, pass" in rendered

        excluded = permission_preflight.build(
            {"LAST30DAYS_CORPUS_DIRS": str(notes), "EXCLUDE_SOURCES": "corpus"},
            _diag(),
        )
        assert excluded["local_reads"]["corpus_dirs"] == []

    def test_uppercase_https_wss_cdp_scheme_excluded_and_reads_values_preserved(self, monkeypatch):
        monkeypatch.setattr(env, "x_extras_enabled", lambda _cfg: True)
        monkeypatch.setattr(chrome_cdp, "cookie_access_allowed", lambda _cfg: True)
        monkeypatch.setattr(
            chrome_cdp,
            "candidate_endpoints",
            lambda _cfg: ["HTTPS://127.0.0.1:9222", "WSS://127.0.0.1:9222/devtools/page/1"],
        )
        assert permission_preflight._conditional_x_cookie_routes({"BROWSER_CONSENT": "true"}) == []

        diag_unsafe = _diag(
            safe=False,
            browser_cookies={"mode": "read", "browsers": ["firefox"], "reads_values": True},
        )
        preflight = permission_preflight.build({"FROM_BROWSER": "firefox"}, diag_unsafe)
        assert preflight["local_reads"]["browser_cookies"]["reads_values"] is True
        rendered = permission_preflight.render_text(preflight)
        assert "cookie values were read" in rendered
        assert "preflight did not read cookie values" not in rendered


# ---------------------------------------------------------------------------
# HIGH-066: GitHub device-handle path, atomic write, fail-closed start, safe poll
# ---------------------------------------------------------------------------


class TestHigh066DeviceHandleSecurity:
    def test_temp_fallback_uses_per_user_private_dir_on_posix(self, monkeypatch):
        if os.name != "posix":
            pytest.skip("POSIX per-uid temp dir check")
        monkeypatch.setattr(env, "CONFIG_FILE", None)
        path = setup_wizard._device_handle_path()
        expected_dir = Path(tempfile.gettempdir()) / f"last30days-{os.geteuid()}"
        assert path.parent == expected_dir
        assert path.name == "github-device-handle.json"
        assert path != Path(tempfile.gettempdir()) / "last30days-github-device-handle.json"

    def test_temp_fallback_uses_localappdata_on_windows(self, monkeypatch, tmp_path):
        monkeypatch.setattr(env, "CONFIG_FILE", None)
        monkeypatch.setattr(setup_wizard, "os", _NtOs())
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
        path = setup_wizard._device_handle_path()
        assert path == tmp_path / "LocalAppData" / "last30days" / "github-device-handle.json"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and symlinks")
    def test_fallback_dir_created_0700_and_rejects_symlink_or_foreign_owner(self, tmp_path, monkeypatch):
        fallback = tmp_path / f"last30days-{os.geteuid()}"
        monkeypatch.setattr(setup_wizard, "_device_handle_fallback_dir", lambda: fallback)
        setup_wizard._ensure_private_handle_dir(fallback)
        assert stat.S_IMODE(fallback.lstat().st_mode) == 0o700

        fallback.chmod(0o755)
        setup_wizard._ensure_private_handle_dir(fallback)
        assert stat.S_IMODE(fallback.lstat().st_mode) == 0o700

        sym_dir = tmp_path / "sym-dir"
        sym_dir.symlink_to(fallback)
        with pytest.raises(OSError, match="symlink"):
            setup_wizard._ensure_private_handle_dir(sym_dir)

        real_lstat = os.lstat

        def foreign_lstat(p):
            st = real_lstat(p)
            fake = list(st)
            fake[4] = os.geteuid() + 1  # st_uid
            return os.stat_result(fake)

        with mock.patch.object(setup_wizard.os, "lstat", side_effect=foreign_lstat):
            with pytest.raises(OSError, match="another user"):
                setup_wizard._ensure_private_handle_dir(fallback)

    def test_ensure_private_handle_dir_rejects_windows_junction(self, tmp_path, monkeypatch):
        junction = tmp_path / "junction-dir"
        junction.mkdir()
        monkeypatch.setattr(setup_wizard.os.path, "isjunction", lambda p: Path(p) == junction, raising=False)
        with pytest.raises(OSError, match="junction"):
            setup_wizard._ensure_private_handle_dir(junction)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and permissions")
    def test_github_start_fails_closed_before_printing_or_opening_browser(self, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("precious-data", encoding="utf-8")
        victim.chmod(0o644)
        handle_link = tmp_path / ".github-device-handle.json"
        handle_link.symlink_to(victim)

        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(setup_wizard, "_existing_scrapecreators_key", return_value=None), \
             mock.patch.object(
                 setup_wizard,
                 "run_device_auth",
                 return_value=("dev-code-066", "ABCD-1234", "https://github.com/login/device", 5),
             ), \
             mock.patch.object(setup_wizard, "_device_handle_path", return_value=handle_link), \
             mock.patch("webbrowser.open") as open_browser, \
             redirect_stdout(out), redirect_stderr(err):
            result = setup_wizard.run_github_start()

        assert result["status"] == "error"
        assert "device_code_ready" not in out.getvalue()
        assert "ABCD-1234" not in out.getvalue()
        open_browser.assert_not_called()
        assert victim.read_text(encoding="utf-8") == "precious-data"
        assert stat.S_IMODE(victim.stat().st_mode) == 0o644

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and permissions")
    def test_github_poll_rejects_symlink_and_world_writable_handles(self, tmp_path):
        real_handle = tmp_path / "real.json"
        real_handle.write_text(json.dumps({"device_code": "attacker-code", "interval": 5}), encoding="utf-8")

        link_handle = tmp_path / "link.json"
        link_handle.symlink_to(real_handle)
        with mock.patch.object(setup_wizard, "_device_handle_path", return_value=link_handle), \
             mock.patch.object(setup_wizard, "poll_device_auth") as poll_mock:
            res = setup_wizard.run_github_poll(timeout=1)
        assert res["status"] == "error"
        poll_mock.assert_not_called()

        real_handle.chmod(0o666)
        with mock.patch.object(setup_wizard, "_device_handle_path", return_value=real_handle), \
             mock.patch.object(setup_wizard, "poll_device_auth") as poll_mock:
            res = setup_wizard.run_github_poll(timeout=1)
        assert res["status"] == "error"
        poll_mock.assert_not_called()


# ---------------------------------------------------------------------------
# HIGH-067: atomic .env updates and hardened _open_secret_append
# ---------------------------------------------------------------------------


class TestHigh067EnvSecretFileHardening:
    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and symlinks")
    def test_open_secret_append_uses_fchmod_rejects_symlink_and_closes_fd_on_error(self, tmp_path):
        victim = tmp_path / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        victim.chmod(0o644)
        link = tmp_path / ".env"
        link.symlink_to(victim)

        with pytest.raises(OSError):
            setup_wizard._open_secret_append(link)
        assert victim.read_text(encoding="utf-8") == "keep"
        assert stat.S_IMODE(victim.stat().st_mode) == 0o644

        target = tmp_path / "loose.env"
        target.write_text("A=1\n", encoding="utf-8")
        target.chmod(0o644)
        with mock.patch.object(setup_wizard.os, "chmod", side_effect=AssertionError("must use fchmod")):
            with setup_wizard._open_secret_append(target) as stream:
                stream.write("B=2\n")
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

        closed_fds: list[int] = []
        real_close = os.close

        def spy_close(fd: int):
            closed_fds.append(fd)
            return real_close(fd)

        target2 = tmp_path / "fdopen_fail.env"
        with mock.patch.object(setup_wizard.os, "fdopen", side_effect=OSError("fdopen failed")), \
             mock.patch.object(setup_wizard.os, "close", side_effect=spy_close):
            with pytest.raises(OSError, match="fdopen failed"):
                setup_wizard._open_secret_append(target2)
        assert len(closed_fds) == 1

    def test_open_secret_append_rejects_windows_junction(self, tmp_path, monkeypatch):
        junction = tmp_path / "junction.env"
        junction.mkdir()
        monkeypatch.setattr(setup_wizard, "os", _NtOs())
        monkeypatch.setattr(private_files, "_IS_JUNCTION", lambda p: Path(p) == junction)
        with pytest.raises(OSError, match="junction"):
            setup_wizard._open_secret_append(junction)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and symlinks")
    def test_replace_env_line_ignores_preplanted_loose_tmp_and_cleans_up_on_replace_error(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("XAI_API_KEY=old-secret\n", encoding="utf-8")
        env_path.chmod(0o600)

        planted = tmp_path / ".env.tmp"
        planted.write_text("attacker-file", encoding="utf-8")
        planted.chmod(0o644)

        assert setup_wizard._replace_env_line(env_path, env_path.read_text(), "XAI_API_KEY", "new-secret")
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
        assert "new-secret" in env_path.read_text(encoding="utf-8")
        assert planted.read_text(encoding="utf-8") == "attacker-file"

        # Failed replace must unlink the temporary staging file with no stray secret file left behind.
        with mock.patch.object(private_files.os, "replace", side_effect=OSError("replace denied")):
            with pytest.raises(OSError, match="replace denied"):
                setup_wizard._replace_env_line(env_path, env_path.read_text(), "XAI_API_KEY", "leaked-secret")
        stray = [p for p in tmp_path.iterdir() if p.name not in {".env", ".env.tmp"}]
        assert stray == []
        assert "leaked-secret" not in env_path.read_text(encoding="utf-8")

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and concurrency")
    def test_concurrent_replace_env_line_stays_valid_and_0600(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("XAI_API_KEY=init\nKEEP_ME=ok\n", encoding="utf-8")
        env_path.chmod(0o600)

        def worker(idx: int) -> bool:
            return setup_wizard.write_api_key(
                env_path, f"secret-value-{idx}", key_name="XAI_API_KEY", replace=True
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(worker, range(12)))

        assert all(results)
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
        loaded = env.load_env_file(env_path)
        assert loaded["KEEP_ME"] == "ok"
        assert loaded["XAI_API_KEY"].startswith("secret-value-")
        leftover = [p for p in tmp_path.iterdir() if p.name != ".env"]
        assert leftover == []

    @pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
    def test_writers_reject_symlink_env_path(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("untouched\n", encoding="utf-8")
        link = tmp_path / ".env"
        link.symlink_to(target)

        assert setup_wizard.write_api_key(link, "dummy-secret") is False
        assert setup_wizard.write_setup_config(link, from_browser="chrome") is False
        assert target.read_text(encoding="utf-8") == "untouched\n"


# ---------------------------------------------------------------------------
# HIGH-069: Clean/no-config mode (env_path is None)
# ---------------------------------------------------------------------------


class TestHigh069CleanModeNoConfig:
    def test_writers_and_reddit_session_handle_none_env_path_without_typeerror(self):
        assert setup_wizard.write_api_key(None, "dummy-secret") is False
        assert setup_wizard.write_api_key("", "dummy-secret") is False
        assert setup_wizard.write_api_key(123, "dummy-secret") is False  # type: ignore[arg-type]
        assert setup_wizard.write_setup_config(None, from_browser="chrome", browser_consent=True) is False
        assert setup_wizard.write_setup_config("", browser_consent=False) is False

        declined = setup_wizard.run_reddit_session_setup({}, None, consent=False)
        assert declined == {"status": "declined", "consent": False, "env_written": False}

        found = setup_wizard.run_reddit_session_setup(
            {},
            None,
            consent=True,
            reader=lambda _cfg, browser: ("dummy-cookie", "found") if browser == "chrome" else (None, "empty"),
        )
        assert found["status"] == "found"
        assert found["browser"] == "chrome"
        assert found["env_written"] is False

    def test_cli_setup_paths_survive_none_config_file_and_mask_secrets(self):
        dummy_key = "sc_live_cleanmode_secret_069"

        # 1. setup --store-key
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.env, "CONFIG_FILE", None), \
             mock.patch.object(sys, "stdin", io.StringIO(dummy_key + "\n")), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup", "--store-key", "X_BEARER_TOKEN"]), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.main()
        assert rc == 1
        assert dummy_key not in out.getvalue() and dummy_key not in err.getvalue()
        assert json.loads(out.getvalue().strip().splitlines()[-1]) == {
            "persisted": False,
            "key": "X_BEARER_TOKEN",
        }

        # 2. setup --github-poll (must mask api_key even when persistence fails)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.env, "CONFIG_FILE", None), \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(
                 setup_wizard,
                 "run_github_poll",
                 return_value={"status": "success", "method": "device", "api_key": dummy_key},
             ), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup", "--github-poll"]), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.main()
        assert rc == 0
        payload = json.loads(out.getvalue())
        assert payload["persisted"] is False
        assert dummy_key not in out.getvalue()
        assert payload["api_key"] == setup_wizard.mask_api_key(dummy_key)

        # 3. setup --no-reddit-session
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.env, "CONFIG_FILE", None), \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup", "--no-reddit-session"]), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.main()
        assert rc == 0
        assert json.loads(out.getvalue())["env_written"] is False

        # 4. auto-setup
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli.env, "CONFIG_FILE", None), \
             mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(setup_wizard, "run_auto_setup", return_value={"cookies_found": {}}), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup"]), \
             redirect_stdout(out), redirect_stderr(err):
            rc = cli.main()
        assert rc == 1
        assert "could not be fully saved" in err.getvalue().lower()


# ---------------------------------------------------------------------------
# HIGH-071: doctor-cache.json atomic 0o600 write, secret guard, fingerprint
# ---------------------------------------------------------------------------


class TestHigh071DoctorCacheHardening:
    @staticmethod
    def _sample_report(note: str = "ok") -> dict:
        return {
            "engine_version": "4.6.0",
            "config": {"global_env": None, "config_source": None},
            "setup": {"setup_complete": False, "keys_present": {}},
            "permissions": {"status": "ready"},
            "sources": {
                "hackernews": {
                    "tier": "ok",
                    "status": "ok",
                    "mode": "single",
                    "backends": None,
                    "active_backend": None,
                    "fix": "",
                    "requires": "none",
                    "note": note,
                    "detail": "",
                    "pin_var": None,
                    "pin_flag": None,
                    "pinned": False,
                }
            },
        }

    @pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions and symlinks")
    def test_write_cache_creates_0600_atomically_and_refuses_symlinks(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "cfg"
        monkeypatch.setattr(env, "CONFIG_DIR", config_dir)

        old_umask = os.umask(0o022)
        try:
            assert doctor._write_cache(self._sample_report(), {}) is True
        finally:
            os.umask(old_umask)

        cache_file = config_dir / doctor.CACHE_FILENAME
        assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
        assert doctor.read_cached_report({}) is not None

        # Symlink at doctor-cache.json must be refused on both read and write.
        victim = tmp_path / "victim.json"
        victim.write_text('{"keep": true}', encoding="utf-8")
        cache_file.unlink()
        cache_file.symlink_to(victim)

        assert doctor.read_cached_report({}) is None
        err = io.StringIO()
        with redirect_stderr(err):
            assert doctor._write_cache(self._sample_report("overwrite"), {}) is False
        assert victim.read_text(encoding="utf-8") == '{"keep": true}'
        assert "WARNING" in err.getvalue()

    def test_key_presence_and_secret_leak_guard_cover_new_secrets(self, tmp_path, monkeypatch):
        assert "GOOGLE_GENAI_API_KEY" in doctor.KEY_PRESENCE_VARS
        assert "BRIGHTDATA_API_KEY" in doctor.KEY_PRESENCE_VARS

        setup_block = doctor._setup_block(
            {
                "GOOGLE_GENAI_API_KEY": "dummy-genai-071",
                "BRIGHTDATA_API_KEY": "dummy-brightdata-071",
            }
        )
        assert setup_block["keys_present"]["GOOGLE_GENAI_API_KEY"] is True
        assert setup_block["keys_present"]["BRIGHTDATA_API_KEY"] is True

        config_dir = tmp_path / "cfg"
        monkeypatch.setattr(env, "CONFIG_DIR", config_dir)

        for key, secret in (
            ("BRIGHTDATA_API_KEY", "dummy-brightdata-leak-071"),
            ("LAST30DAYS_PUBLISH_PASSWORD", "dummy-publish-leak-071"),
            ("LAST30DAYS_API_KEY", "dummy-hosted-leak-071"),
            ("CUSTOM_EXTRA_TOKEN", "dummy-extra-token-leak-071"),
        ):
            report = self._sample_report(note=f"accidental {secret}")
            assert doctor._write_cache(report, {key: secret}) is False, key
            assert not (config_dir / doctor.CACHE_FILENAME).exists()

        # Also block LAST30DAYS_API_KEY set only in os.environ.
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-env-hosted-leak-071")
        assert doctor._write_cache(self._sample_report(note="dummy-env-hosted-leak-071"), {}) is False

        # Proxy sentinel must not cause a false positive refusal.
        monkeypatch.delenv("LAST30DAYS_API_KEY", raising=False)
        assert doctor._write_cache(self._sample_report(note="ok"), {"XAI_API_KEY": proxy.SENTINEL}) is True

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("EXCLUDE_SOURCES", "reddit"),
            ("BRIGHTDATA_API_KEY", "dummy-bd-071"),
            ("GOOGLE_GENAI_API_KEY", "dummy-genai-071"),
            ("TELEGRAM_SOURCES", "chan1"),
            ("XIAOHONGSHU_API_BASE", "http://127.0.0.1:18061"),
            ("LAST30DAYS_NATIVE_SEARCH", "1"),
            ("CLAUDECODE", "1"),
            ("SETUP_COMPLETE", "true"),
            ("LAST30DAYS_REDDIT_SC_MIN_ITEMS", "9"),
            ("LAST30DAYS_MEMORY_DIR", "/tmp/custom-mem"),
            ("LAST30DAYS_LIBRARY_CONTEXT", "off"),
        ],
    )
    def test_config_fingerprint_changes_for_every_tracked_knob(self, key, value):
        base = doctor._config_fingerprint({})
        changed = doctor._config_fingerprint({key: value})
        assert changed != base, f"{key} did not invalidate doctor cache fingerprint"


# ---------------------------------------------------------------------------
# HIGH-073: xurl scoped package, app-only bearer prescription, store check
# ---------------------------------------------------------------------------


class TestHigh073XurlBackendPrescriptionAndAuth:
    def test_missing_binary_prescribes_scoped_package_and_app_only_auth(self):
        with mock.patch("lib.backends.which", return_value=None):
            finding = backends._probe_xurl({})
        assert finding.status == health.MISSING
        assert "npm install -g @xdevplatform/xurl" in finding.prescription
        assert "npm install -g xurl " not in finding.prescription
        assert "xurl auth app-only <bearer-token>" in finding.prescription
        assert "oauth2" not in finding.prescription.lower()
        assert "app-only bearer" in finding.requires.lower()
        assert "app-only bearer" in backends._X_REQUIRES["xurl"].lower()

    @pytest.mark.parametrize(
        "store_content",
        [
            "apps:\n  app:\n    oauth2_tokens:\n      me:\n        oauth2:\n          access_token: dummy-oauth2-only\n",
            "apps:\n  app:\n    bearer_token: null\n    oauth2_tokens:\n      me:\n        access_token: dummy-oauth2-only\n",
            'apps:\n  app:\n    bearer_token: ""\n    oauth2_tokens:\n      me:\n        access_token: dummy-oauth2-only\n',
            "# bearer_token: none\naccess_token: dummy-oauth2-only\n",
            json.dumps({"bearer_token": None, "oauth2_tokens": {"me": {"access_token": "dummy"}}}),
        ],
    )
    def test_oauth2_only_token_store_does_not_report_will_use_xurl(self, tmp_path, store_content):
        store = tmp_path / ".xurl" / "auth.yml"
        store.parent.mkdir(exist_ok=True)
        store.write_text(store_content, encoding="utf-8")
        with mock.patch("lib.backends.which", side_effect=lambda n: "/usr/bin/xurl" if n == "xurl" else None), \
             mock.patch("lib.xurl_x.token_store_path", return_value=store):
            finding = backends._probe_xurl({})
        assert finding.status == health.MISSING
        assert not finding.usable
        assert "app-only bearer" in finding.detail.lower()
        assert "xurl auth app-only <bearer-token>" in finding.prescription

    def test_bearer_token_store_reports_ok_with_app_only_detail(self, tmp_path):
        store = tmp_path / ".xurl" / "auth.yml"
        store.parent.mkdir()
        store.write_text(
            "apps:\n  app:\n    bearer_token:\n      bearer: dummy-bearer-073\n",
            encoding="utf-8",
        )
        with mock.patch("lib.backends.which", side_effect=lambda n: "/usr/bin/xurl" if n == "xurl" else None), \
             mock.patch("lib.xurl_x.token_store_path", return_value=store):
            finding = backends._probe_xurl({})
        assert finding.status == health.OK
        assert "app-only bearer" in finding.detail.lower()
        assert "oauth2" not in finding.detail.lower()

    def test_doctor_x_auth_path_describes_xurl_as_app_only_bearer(self):
        with mock.patch.object(env, "get_x_source", return_value="xurl"):
            info = doctor._x_auth_path({"LAST30DAYS_HOST": "grok-bot"})
        assert "app-only bearer" in info["note"].lower()
        assert "oauth2" not in info["note"].lower()


# ---------------------------------------------------------------------------
# HIGH-075: Gemini header auth, redirect guard, and anchored model validation
# ---------------------------------------------------------------------------


class TestHigh075GeminiProviderHardening:
    def test_gemini_sends_api_key_in_header_not_query_string(self):
        assert "?key=" not in providers.GEMINI_URL
        secret = "dummy-gemini-key&extra=1#frag"
        client = providers.GeminiClient(secret)
        captured: dict = {}

        def fake_post(url, body, *, headers=None, timeout=None, redirect_guard=None, **kwargs):
            captured["url"] = url
            captured["headers"] = headers
            captured["redirect_guard"] = redirect_guard
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        with mock.patch.object(providers.http, "post", side_effect=fake_post):
            assert client.generate_text("gemini-3.1-flash-lite", "hello") == "ok"

        assert secret not in captured["url"]
        assert "?key=" not in captured["url"]
        assert captured["url"] == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.1-flash-lite:generateContent"
        )
        assert captured["headers"]["x-goog-api-key"] == secret
        assert callable(captured["redirect_guard"])
        assert captured["redirect_guard"](
            "https://generativelanguage.googleapis.com/v1beta/models/other:generateContent"
        ) is True
        assert captured["redirect_guard"]("https://attacker.example/steal") is False
        assert captured["redirect_guard"]("http://generativelanguage.googleapis.com/downgrade") is False

    def test_gemini_cross_origin_redirect_refused_before_leaking_header(self):
        leaked: dict = {}

        class Attacker(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                leaked.update({k.lower(): v for k, v in self.headers.items()})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"candidates":[]}')

            def do_GET(self):
                self.do_POST()

            def log_message(self, *_args):
                pass

        attacker = socketserver.TCPServer(("127.0.0.1", 0), Attacker)
        threading.Thread(target=attacker.serve_forever, daemon=True).start()
        attacker_port = attacker.server_address[1]

        class Victim(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header("Location", f"http://127.0.0.1:{attacker_port}/steal")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                pass

        victim = socketserver.TCPServer(("127.0.0.1", 0), Victim)
        threading.Thread(target=victim.serve_forever, daemon=True).start()
        victim_port = victim.server_address[1]

        try:
            client = providers.GeminiClient("dummy-gemini-redirect-secret-075")
            redirect_url = f"http://127.0.0.1:{victim_port}/v1beta/models/{{model}}:generateContent"
            with mock.patch.object(providers, "GEMINI_URL", redirect_url):
                with pytest.raises(providers.http.HTTPError):
                    client.generate_text("gemini-3.1-flash-lite", "hello")
            assert "x-goog-api-key" not in leaked
        finally:
            victim.shutdown()
            victim.server_close()
            attacker.shutdown()
            attacker.server_close()

    @pytest.mark.parametrize(
        "malicious_model",
        [
            "gemini-3.1-flash/../../evil?foo=bar",
            "gemini-3.1-flash?alt=json",
            "gemini-3.1-flash#fragment",
            "gemini-3.1-flash\n",
            "gemini-3.1-flash evil",
            "gemini-3.1-..",
            "gemini-3.1\\evil",
        ],
    )
    def test_gemini_model_pin_rejects_path_query_and_control_injection(self, malicious_model):
        assert providers._is_gemini_31_or_newer(malicious_model) is False
        runtime = providers.mock_runtime(
            {"LAST30DAYS_PLANNER_MODEL": malicious_model, "LAST30DAYS_RERANK_MODEL": malicious_model},
            depth="default",
        )
        assert runtime.planner_model == providers.GEMINI_FLASH_LITE
        assert runtime.rerank_model == providers.GEMINI_FLASH_LITE

        client = providers.GeminiClient("dummy-key")
        with pytest.raises(ValueError, match="Invalid Gemini model name"):
            client.generate_text(malicious_model, "hello")

    @pytest.mark.parametrize(
        "valid_model",
        [
            "gemini-3.1",
            "gemini-3.1-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-3.5-flash",
            "gemini-4-hydrogen",
            "gemini-10-pro",
        ],
    )
    def test_gemini_valid_31_plus_models_accepted(self, valid_model):
        assert providers._is_gemini_31_or_newer(valid_model) is True

    def test_http_cross_origin_auth_headers_strips_x_goog_api_key(self):
        assert "x-goog-api-key" in http_mod._CROSS_ORIGIN_AUTH_HEADERS
        handler = http_mod._StripAuthOnCrossOriginRedirect()
        req = Request(
            "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
            headers={"X-Goog-Api-Key": "dummy-secret-075"},
        )
        cross = handler.redirect_request(
            req, None, 302, "Found", {}, "https://attacker.example/steal"
        )
        cross_headers = {
            k.lower(): v
            for store in (cross.headers, getattr(cross, "unredirected_hdrs", {}))
            for k, v in store.items()
        }
        assert "x-goog-api-key" not in cross_headers

        same = handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {},
            "https://generativelanguage.googleapis.com/v1beta/models/other:generateContent",
        )
        same_headers = {
            k.lower(): v
            for store in (same.headers, getattr(same, "unredirected_hdrs", {}))
            for k, v in store.items()
        }
        assert same_headers.get("x-goog-api-key") == "dummy-secret-075"

    @pytest.mark.parametrize(
        "bad_model",
        [
            "gemini-٣.١",
            "gemini-٣.١-flash",
            ".",
            "..",
            "%2e%2e",
            "gemini-3.1-flash\u0085",
            "gemini-3.1-flash\u00a0",
            "gemini-3.1-flash\u2028",
        ],
    )
    def test_gemini_model_rejects_unicode_digits_dots_and_control_chars(self, bad_model):
        assert providers._is_gemini_31_or_newer(bad_model) is False
        client = providers.GeminiClient("dummy-key")
        with pytest.raises(ValueError, match="Invalid Gemini model name"):
            client.generate_text(bad_model, "hello")

    def test_gemini_redirect_guard_handles_malformed_port_without_value_error(self):
        client = providers.GeminiClient("dummy-key")
        captured: dict = {}

        def fake_post(url, body, *, headers=None, timeout=None, redirect_guard=None, **kwargs):
            captured["redirect_guard"] = redirect_guard
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        with mock.patch.object(providers.http, "post", side_effect=fake_post):
            client.generate_text("gemini-3.1-flash-lite", "hello")

        assert captured["redirect_guard"]("https://generativelanguage.googleapis.com:99999/x") is False


class TestReviewRound2Regressions:
    """Regressions for PR 13 adversarial review findings."""

    def test_cli_corpus_flag_disclosed_in_preflight_and_disables_hosted_mode(
        self, tmp_path, monkeypatch, capsys
    ):
        notes = tmp_path / "cli-notes"
        notes.mkdir()
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key-r2")
        monkeypatch.setattr(
            sys,
            "argv",
            ["last30days.py", "--preflight", "--emit=json", "--corpus", str(notes)],
        )
        assert cli.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["local_reads"]["corpus_dirs"] == [str(notes.resolve())]
        assert payload["network"]["hosted_mode"] is False
        assert "hosted" not in payload["network"]["available_sources"]

    @pytest.mark.parametrize(
        "store_content",
        [
            "apps:\n  app:\n    bearer_token: []\n    oauth2_tokens:\n      me:\n        access_token: tok\n",
            "apps:\n  app:\n    bearer_token:\n      bearer: null\n      type: app\n    oauth2_tokens:\n      me:\n        access_token: tok\n",
            json.dumps({"bearer_token": {"bearer": None, "type": "app"}, "oauth2_tokens": {"me": {"access_token": "tok"}}}),
        ],
    )
    def test_xurl_oauth2_only_rejected_consistently_across_all_surfaces(self, tmp_path, store_content):
        store = tmp_path / ".xurl" / "auth.yml"
        store.parent.mkdir(exist_ok=True)
        store.write_text(store_content, encoding="utf-8")

        assert env._X_METHOD_LABELS["xurl"] == "bearer"
        with mock.patch("lib.xurl_x.token_store_path", return_value=store), \
             mock.patch("lib.xurl_x.shutil.which", side_effect=lambda n: "/usr/bin/xurl" if n == "xurl" else None), \
             mock.patch("lib.backends.which", side_effect=lambda n: "/usr/bin/xurl" if n == "xurl" else None):
            assert xurl_x.stored_auth_status()[0] == xurl_x.AUTH_MISSING
            assert xurl_x.has_stored_auth() is False
            assert env.get_x_source({"LAST30DAYS_HOST": "grok-bot"}, local_only=True) is None
            assert env.get_x_source_status({"LAST30DAYS_HOST": "grok-bot"}, probe=False)["xurl_available"] is False
            assert doctor._x_auth_path({"LAST30DAYS_HOST": "grok-bot"})["armed"] is False
            finding = backends._probe_xurl({})
            assert finding.status == health.MISSING
            assert "app-only bearer" in finding.detail.lower()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO check")
    def test_xurl_store_and_doctor_cache_do_not_block_on_fifo(self, tmp_path, monkeypatch):
        fifo_dir = tmp_path / ".xurl"
        fifo_dir.mkdir()
        fifo_store = fifo_dir / "auth.yml"
        os.mkfifo(fifo_store)
        with mock.patch("lib.xurl_x.token_store_path", return_value=fifo_store), \
             mock.patch("lib.backends.which", side_effect=lambda n: "/usr/bin/xurl" if n == "xurl" else None):
            status, _ = xurl_x.stored_auth_status()
            assert status == xurl_x.AUTH_MISSING
            with pytest.raises(OSError, match="regular"):
                xurl_x._read_token_store_text(fifo_store)
            finding = backends._probe_xurl({})
            assert finding.status == health.MISSING

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        os.mkfifo(cfg_dir / doctor.CACHE_FILENAME)
        monkeypatch.setattr(env, "CONFIG_DIR", cfg_dir)
        assert doctor.read_cached_report({}) is None

    def test_doctor_cache_short_secret_values_do_not_disable_caching_and_size_cap_enforced(
        self, tmp_path, monkeypatch
    ):
        cfg_dir = tmp_path / "cfg"
        monkeypatch.setattr(env, "CONFIG_DIR", cfg_dir)
        report = TestHigh071DoctorCacheHardening._sample_report("ok")
        assert doctor._write_cache(
            report,
            {
                "BRIGHTDATA_API_KEY": "ok",
                "LAST30DAYS_PUBLISH_PASSWORD": "1",
                "LAST30DAYS_API_KEY": "yes",
            },
        ) is True
        assert doctor.read_cached_report(
            {
                "BRIGHTDATA_API_KEY": "ok",
                "LAST30DAYS_PUBLISH_PASSWORD": "1",
                "LAST30DAYS_API_KEY": "yes",
            }
        ) is not None

        # Oversized cache file (>4 MiB) is ignored safely.
        cache_file = cfg_dir / doctor.CACHE_FILENAME
        cache_file.write_bytes(b" " * (doctor._MAX_DOCTOR_CACHE_BYTES + 16))
        assert doctor.read_cached_report({}) is None

    def test_in_memory_poll_does_not_unlink_disk_handle_and_windows_temp_has_user_suffix(
        self, tmp_path, monkeypatch
    ):
        sentinel = tmp_path / "existing-handle.json"
        sentinel.write_text('{"device_code": "keep"}', encoding="utf-8")
        with mock.patch.object(setup_wizard, "_device_handle_path", return_value=sentinel), \
             mock.patch.object(setup_wizard, "poll_device_auth", return_value=None):
            res = setup_wizard.run_github_poll(
                timeout=1,
                _handle={"device_code": "mem-only", "interval": 1, "user_code": "ABCD-1234"},
            )
        assert res["status"] == "timeout"
        assert sentinel.exists()

        monkeypatch.setattr(env, "CONFIG_FILE", None)
        monkeypatch.setattr(setup_wizard, "os", _NtOs())
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setenv("USERNAME", "Alice/Bob")
        path = setup_wizard._device_handle_path()
        assert path.parent == Path(tempfile.gettempdir()) / "last30days-Alice_Bob"
        assert path.name == "github-device-handle.json"


class TestGreptileRound3Regressions:
    """Regressions for PR 13 Greptile findings."""

    def test_preflight_deep_research_and_default_search_with_corpus_disable_hosted_mode(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key-r3")

        # --deep-research runs locally in CLI, so preflight must report hosted_mode=False.
        monkeypatch.setattr(
            sys,
            "argv",
            ["last30days.py", "--preflight", "--emit=json", "--deep-research"],
        )
        assert cli.main() == 0
        deep_payload = json.loads(capsys.readouterr().out)
        assert deep_payload["network"]["hosted_mode"] is False
        assert "hosted" not in deep_payload["network"]["available_sources"]

        # Configured corpus keeps CLI local even when LAST30DAYS_DEFAULT_SEARCH=reddit
        # omits corpus from planned local_reads.corpus_dirs.
        notes = tmp_path / "notes"
        notes.mkdir()
        monkeypatch.setenv("LAST30DAYS_CORPUS_DIRS", str(notes))
        monkeypatch.setenv("LAST30DAYS_DEFAULT_SEARCH", "reddit")
        monkeypatch.setattr(
            sys,
            "argv",
            ["last30days.py", "--preflight", "--emit=json"],
        )
        assert cli.main() == 0
        reddit_payload = json.loads(capsys.readouterr().out)
        assert reddit_payload["local_reads"]["corpus_dirs"] == []
        assert reddit_payload["network"]["hosted_mode"] is False
        assert "hosted" not in reddit_payload["network"]["available_sources"]

        # EXCLUDE_SOURCES=corpus disables corpus completely and restores hosted mode.
        monkeypatch.setenv("EXCLUDE_SOURCES", "corpus")
        monkeypatch.setattr(
            sys,
            "argv",
            ["last30days.py", "--preflight", "--emit=json"],
        )
        assert cli.main() == 0
        excluded_payload = json.loads(capsys.readouterr().out)
        assert excluded_payload["local_reads"]["corpus_dirs"] == []
        assert excluded_payload["network"]["hosted_mode"] is True
        assert excluded_payload["network"]["available_sources"] == ["hosted"]

    def test_doctor_cache_fingerprint_tracks_hosted_key_endpoint_and_corpus_without_leaking(
        self, tmp_path, monkeypatch
    ):
        cfg_dir = tmp_path / "cfg"
        notes = tmp_path / "notes"
        notes.mkdir()
        monkeypatch.setattr(env, "CONFIG_DIR", cfg_dir)

        base_fp = doctor._config_fingerprint({})

        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-fp-secret-r3")
        key_fp = doctor._config_fingerprint({})
        assert key_fp != base_fp

        monkeypatch.setenv("LAST30DAYS_API_BASE", "http://cleartext.example/api/v1")
        invalid_ep_fp = doctor._config_fingerprint({})
        assert invalid_ep_fp != key_fp

        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
        valid_ep_fp = doctor._config_fingerprint({})
        assert valid_ep_fp != invalid_ep_fp

        monkeypatch.setenv("LAST30DAYS_API_MODE", "proxy")
        proxy_fp = doctor._config_fingerprint({})
        assert proxy_fp != valid_ep_fp

        monkeypatch.delenv("LAST30DAYS_API_MODE", raising=False)
        corpus_fp = doctor._config_fingerprint({"LAST30DAYS_CORPUS_DIRS": str(notes)})
        assert corpus_fp != valid_ep_fp

        report = TestHigh071DoctorCacheHardening._sample_report("ok")
        assert doctor._write_cache(report, {}) is True
        raw_cache = (cfg_dir / doctor.CACHE_FILENAME).read_text(encoding="utf-8")
        assert "dummy-hosted-fp-secret-r3" not in raw_cache
        assert "hosted.example" not in raw_cache

    def test_preflight_discloses_secret_stores_even_in_hosted_mode(self, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key-r3")
        monkeypatch.delenv(env.KEYCHAIN_DISABLE_ENV, raising=False)
        with mock.patch.object(permission_preflight.platform, "system", return_value="Darwin"), \
             mock.patch.object(
                 permission_preflight.shutil,
                 "which",
                 side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd in {"security", "pass"} else None,
             ):
            preflight = permission_preflight.build(
                {},
                _diag(config_source="global:/Users/tester/.config/last30days/.env"),
            )
        assert preflight["network"]["hosted_mode"] is True
        assert preflight["local_reads"]["secret_stores"] == ["keychain", "pass"]
        assert "Secret stores: keychain, pass" in permission_preflight.render_text(preflight)

    def test_probe_xurl_reads_token_store_once_without_redundant_reread(self, tmp_path):
        store = tmp_path / ".xurl" / "auth.yml"
        store.parent.mkdir()
        store.write_text("apps:\n  app:\n    bearer_token:\n      bearer: dummy-bearer-r3\n", encoding="utf-8")

        assert not hasattr(backends, "_xurl_store_lacks_bearer")
        with mock.patch("lib.backends.which", side_effect=lambda n: "/usr/bin/xurl" if n == "xurl" else None), \
             mock.patch("lib.xurl_x.token_store_path", return_value=store), \
             mock.patch.object(
                 xurl_x, "_read_token_store_text", wraps=xurl_x._read_token_store_text
             ) as read_spy:
            finding = backends._probe_xurl({})
        assert finding.status == health.OK
        assert read_spy.call_count == 1

    def test_preflight_does_not_report_proxy_sentinels_as_direct_xai_or_scrapecreators_keys(
        self, monkeypatch
    ):
        monkeypatch.setenv("LAST30DAYS_API_MODE", "proxy")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://proxy.example/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-proxy-key-r3")
        config = {
            "XAI_API_KEY": proxy.SENTINEL,
            "SCRAPECREATORS_API_KEY": proxy.SENTINEL,
            "SERPER_API_KEY": proxy.SENTINEL,
            "PARALLEL_API_KEY": proxy.SENTINEL,
        }
        diag = _diag(
            providers={
                "google": False,
                "openai": False,
                "xai": True,
                "openrouter": False,
                "perplexity": False,
            },
            has_scrapecreators=True,
        )
        preflight = permission_preflight.build(config, diag)
        assert preflight["credentials"]["xai"]["present"] is False
        assert preflight["credentials"]["scrapecreators"]["present"] is False
        assert preflight["credentials"]["serper"]["present"] is False
        assert preflight["credentials"]["parallel"]["present"] is False
        assert preflight["credentials"]["hosted_api"]["present"] is True
        text = permission_preflight.render_text(preflight)
        assert "xAI API key" not in text
        assert "ScrapeCreators API key" not in text
        assert "last30days hosted/proxy API key" in text

"""Characterization tests freezing the legacy --diagnose / --preflight JSON shapes.

U4 of the doctor plan (R2): `--diagnose` and `--preflight` become frozen-shape
aliases once `doctor` lands. These snapshots freeze the CURRENT shapes so the
doctor work can prove it changed neither. Additions to the legacy shapes are
prohibited — new data appears only in `doctor --json` — so the key-set
assertions below use exact equality, not superset checks.

The two real consumers, each with an explicit test:

1. The Go MCP `preflight` tool (mcp/internal/tools/preflight.go) invokes
   `--preflight --preflight-report-on-save-dir <dir> --emit=json` and passes
   engine stdout through VERBATIM (`mcplib.NewToolResultText(res.Stdout)`).
   The engine side of that contract is asserted here: the exact invocation,
   the frozen top-level key set, the nested shapes, the conditional-writes
   entry for the report-on-save dir, and the byte format
   (json.dumps(..., indent=2, sort_keys=True) + newline).

2. SKILL.md reads `--diagnose`'s `available_sources` array (the engine's
   authoritative source list). Asserted: the key exists, is a list of
   source-name strings.

hooks/scripts/check-config.sh is NOT a JSON consumer (it reads env files and
last-run.json, never engine JSON) — deliberately no compat test for it.

NOTE: snapshots re-recorded against the committed v3.10.0 baseline
(origin/main a5b3ca1, post-v3.9.x source wave: arxiv/techmeme/stocktwits/
trustpilot + the x_pending_browser_auth diag flag). The SHAPES here must
otherwise stay frozen; re-record only when a committed baseline legitimately
changes them.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import last30days as cli

# Source names the engine can emit in available_sources today (v3.10.0).
KNOWN_SOURCE_NAMES = {
    "reddit", "x", "youtube", "tiktok", "instagram", "hackernews", "bluesky",
    "truthsocial", "polymarket", "grounding", "xiaohongshu", "github",
    "perplexity", "threads", "pinterest", "digg", "jobs", "linkedin",
    "arxiv", "techmeme", "stocktwits", "trustpilot", "dripstack",
}

# ---------------------------------------------------------------------------
# Frozen shapes (exact key sets — additions to legacy JSON are prohibited).
# ---------------------------------------------------------------------------

DIAGNOSE_TOP_KEYS = {
    "providers",
    "local_mode",
    "reasoning_provider",
    "x_backend",
    "bird_installed",
    "bird_authenticated",
    "bird_username",
    "xquik_available",
    "xquik_working",
    "xquik_status",
    "native_web_backend",
    "native_search",
    "has_scrapecreators",
    "has_github",
    "brightdata_installed",
    "brightdata_authenticated",
    "x_pending_browser_auth",
    "available_sources",
    "safe",
    "config_source",
    "ignored_project_config",
    "ignored_project_config_keys",
    "ignored_endpoint_overrides",
    "browser_cookies",
    "external_commands",
    "credential_destinations",
    "local_writes",
    "permission_preflight",
}

DIAGNOSE_PROVIDERS_KEYS = {"google", "openai", "xai", "openrouter", "perplexity"}
DIAGNOSE_BROWSER_COOKIES_KEYS = {"mode", "browsers", "reads_values"}
DIAGNOSE_EXTERNAL_COMMANDS_KEYS = {
    "yt-dlp", "digg-pp-cli", "arxiv-pp-cli", "techmeme-pp-cli", "trustpilot-pp-cli",
    "brightdata", "gh",
}
DIAGNOSE_CREDENTIAL_DESTINATIONS_KEYS = {"global_env"}

PREFLIGHT_TOP_KEYS = {
    "status",
    "safe",
    "local_reads",
    "local_writes",
    "conditional_writes",
    "external_commands",
    "credentials",
    "network",
    "action_items",
}
PREFLIGHT_LOCAL_READS_KEYS = {"config_source", "project_config", "browser_cookies"}
PREFLIGHT_PROJECT_CONFIG_KEYS = {"status", "trusted", "ignored_path", "ignored_keys"}
PREFLIGHT_BROWSER_COOKIES_KEYS = {"status", "mode", "browsers", "reads_values"}
PREFLIGHT_CREDENTIALS_KEYS = {
    "google", "openai", "xai", "openrouter", "perplexity", "scrapecreators", "github",
}
PREFLIGHT_NETWORK_KEYS = {
    "available_sources", "native_search", "endpoint_overrides", "ignored_endpoint_overrides",
}


FAKE_KEYLESS_CONFIG: dict = {}

# Obvious dummies only (repo security hygiene): used to prove key-presence
# booleans stay booleans and no credential value ever reaches legacy JSON.
FAKE_KEYED_CONFIG = {
    "SCRAPECREATORS_API_KEY": "dummy-sc-key-not-real-000",
    "XAI_API_KEY": "dummy-xai-key-not-real-000",
    "BRAVE_API_KEY": "dummy-brave-key-not-real-000",
}


_BIRD_STATUS_KEYLESS = {
    "installed": False,
    "authenticated": False,
    "username": None,
    "can_install": True,
}


def _run_cli(
    argv: list[str],
    config: dict,
    *,
    extra_patches: tuple = (),
    patch_has_stored_auth: bool = True,
) -> tuple[int, str]:
    """Run cli.main() in-process with a controlled config; return (rc, stdout).

    `extra_patches` are additional `mock.patch(...)` context managers layered
    on top of the common safe-path mock stack (e.g. to fake xurl's on-disk
    token store instead of stubbing `has_stored_auth` directly).
    `patch_has_stored_auth=False` omits the default `has_stored_auth` stub so
    a caller-supplied patch (or the real function) can take its place.
    """
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(cli.env, "get_config", return_value=dict(config)))
        stack.enter_context(mock.patch("lib.bird_x.get_bird_status", return_value=_BIRD_STATUS_KEYLESS))
        stack.enter_context(mock.patch("lib.bird_x.is_bird_installed", return_value=False))
        stack.enter_context(mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None))
        stack.enter_context(mock.patch(
            "lib.xurl_x.is_available",
            side_effect=AssertionError(
                "--diagnose/--preflight are safe paths and must not run the "
                "live `xurl whoami` network check"
            ),
        ))
        if patch_has_stored_auth:
            stack.enter_context(mock.patch("lib.xurl_x.has_stored_auth", return_value=False))
        for patch in extra_patches:
            stack.enter_context(patch)
        stack.enter_context(mock.patch.object(sys, "argv", ["last30days.py"] + argv))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main()
    return rc, stdout.getvalue()


class DiagnoseShapeCompat(unittest.TestCase):
    """Freeze the --diagnose JSON shape (snapshot: pre-v3.9.0 baseline)."""

    def _diagnose(self, config: dict) -> dict:
        rc, out = _run_cli(["--diagnose"], config)
        self.assertEqual(0, rc)
        return json.loads(out)

    def test_top_level_key_set_is_frozen(self):
        payload = self._diagnose(FAKE_KEYLESS_CONFIG)
        self.assertEqual(DIAGNOSE_TOP_KEYS, set(payload.keys()))

    def test_top_level_key_set_is_frozen_with_keys_configured(self):
        payload = self._diagnose(FAKE_KEYED_CONFIG)
        self.assertEqual(DIAGNOSE_TOP_KEYS, set(payload.keys()))

    def test_nested_shapes_are_frozen(self):
        payload = self._diagnose(FAKE_KEYLESS_CONFIG)
        self.assertEqual(DIAGNOSE_PROVIDERS_KEYS, set(payload["providers"].keys()))
        self.assertEqual(
            DIAGNOSE_BROWSER_COOKIES_KEYS, set(payload["browser_cookies"].keys())
        )
        self.assertEqual(
            DIAGNOSE_EXTERNAL_COMMANDS_KEYS, set(payload["external_commands"].keys())
        )
        self.assertEqual(
            DIAGNOSE_CREDENTIAL_DESTINATIONS_KEYS,
            set(payload["credential_destinations"].keys()),
        )
        # The embedded permission preflight carries the same frozen shape the
        # --preflight alias emits.
        self.assertEqual(
            PREFLIGHT_TOP_KEYS, set(payload["permission_preflight"].keys())
        )

    def test_key_presence_fields_are_booleans_never_values(self):
        payload = self._diagnose(FAKE_KEYED_CONFIG)
        self.assertIs(True, payload["has_scrapecreators"])
        for value in payload["providers"].values():
            self.assertIsInstance(value, bool)
        raw = json.dumps(payload)
        for secret in FAKE_KEYED_CONFIG.values():
            self.assertNotIn(secret, raw)

    def test_diagnose_runs_safe_mode(self):
        payload = self._diagnose(FAKE_KEYLESS_CONFIG)
        self.assertIs(True, payload["safe"])

    def test_skill_md_consumer_available_sources_array(self):
        """Consumer (b): SKILL.md reads `available_sources` as the engine's
        authoritative list of source names."""
        payload = self._diagnose(FAKE_KEYLESS_CONFIG)
        self.assertIn("available_sources", payload)
        sources = payload["available_sources"]
        self.assertIsInstance(sources, list)
        self.assertTrue(sources, "available_sources must never be empty (reddit/hn are free)")
        for name in sources:
            self.assertIsInstance(name, str)
            self.assertIn(name, KNOWN_SOURCE_NAMES)
        # Free sources are always present even in a keyless environment.
        for free in ("reddit", "hackernews", "polymarket", "github"):
            self.assertIn(free, sources)


class DiagnoseXurlAuthWiring(unittest.TestCase):
    """Regression for #978 / PR #1027: prove `--diagnose`'s `x_backend` and
    `available_sources` reflect xurl_x.stored_auth_status()'s corrected
    directory-layout detection end-to-end through the real CLI entrypoint,
    not just at the `has_stored_auth()` unit level (test_xurl_x.py) or a
    mocked wiring level (test_env_v3.py). Neither of those proves the two
    compose correctly through `pipeline.diagnose()` into the exact
    `available_sources` array SKILL.md reads.

    Scope: this covers the AUTH_OK / AUTH_MISSING distinction only.
    `has_stored_auth()` collapses AUTH_ERROR (permission-denied store) to
    the same `False` as AUTH_MISSING, so a permission-denied store is not
    separately observable through `--diagnose`/`available_sources` -- only
    `doctor`'s `backends._probe_xurl` surfaces the typed AUTH_ERROR. Not
    covered here; that distinction is doctor-only by the current design."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fake_home = Path(self._tmp.name)
        self.store = self.fake_home / ".xurl"
        boom = mock.patch(
            "subprocess.run",
            side_effect=AssertionError(
                "--diagnose is a safe path and must not spawn any subprocess"
            ),
        )
        boom.start()
        self.addCleanup(boom.stop)

    def _diagnose_with_store(self, auth_yml_content: str | None) -> dict:
        """Run `--diagnose` with `Path.home()` pointed at a fake home dir
        holding a real `~/.xurl/auth.yml` (current directory layout);
        `auth_yml_content=None` leaves no store at all. `Path.home()` -- not
        `token_store_path()` -- is what's faked, so this exercises
        `token_store_path()`'s own directory-layout logic instead of
        bypassing it; a pre-fix `token_store_path()` returning the bare
        `~/.xurl` directory would make this fail exactly as it did for #978.

        `Path.home` is a shared class attribute, so this patch also redirects
        any other lib module's `Path.home()` call for the duration of the
        CLI invocation (e.g. `brightdata.gate_status`). That's inert today:
        the paired `shutil.which` patch below makes `brightdata.is_installed()`
        return False, short-circuiting `has_credentials()` before it would
        reach `Path.home()`. If that short-circuit is ever removed, re-check
        whether another module's home-relative lookup needs isolating too."""
        if auth_yml_content is not None:
            self.store.mkdir(exist_ok=True)
            (self.store / "auth.yml").write_text(auth_yml_content, encoding="utf-8")
        rc, out = _run_cli(
            ["--diagnose"],
            FAKE_KEYLESS_CONFIG,
            patch_has_stored_auth=False,
            extra_patches=(
                mock.patch("lib.xurl_x.Path.home", return_value=self.fake_home),
                mock.patch(
                    "lib.xurl_x.shutil.which",
                    side_effect=lambda name: "/usr/local/bin/xurl" if name == "xurl" else None,
                ),
            ),
        )
        self.assertEqual(0, rc)
        return json.loads(out)

    def test_new_layout_auth_yml_makes_xurl_the_reported_backend(self):
        payload = self._diagnose_with_store("access_token: dummy-not-real\n")
        self.assertEqual("xurl", payload["x_backend"])
        self.assertIn("x", payload["available_sources"])

    def test_no_store_leaves_xurl_unreported(self):
        payload = self._diagnose_with_store(None)
        self.assertNotEqual("xurl", payload["x_backend"])
        self.assertFalse(
            payload["x_pending_browser_auth"],
            "no browser-cookie config in this test, so pending-auth must be False",
        )
        self.assertNotIn("x", payload["available_sources"])


class PreflightShapeCompat(unittest.TestCase):
    """Freeze the --preflight JSON shape (snapshot: pre-v3.9.0 baseline)."""

    MCP_SAVE_DIR = "/tmp/last30days-mcp-save-dir"

    def _preflight_mcp_invocation(self, config: dict) -> tuple[str, dict]:
        # Consumer (a): mcp/internal/tools/preflight.go builds exactly
        #   ["--preflight", "--preflight-report-on-save-dir", mcpSaveDir()]
        # plus "--emit=json" for format=json, and passes stdout through
        # verbatim. Mirror that invocation exactly.
        rc, out = _run_cli(
            [
                "--preflight",
                "--preflight-report-on-save-dir",
                self.MCP_SAVE_DIR,
                "--emit=json",
            ],
            config,
        )
        self.assertEqual(0, rc)
        return out, json.loads(out)

    def test_mcp_passthrough_top_level_key_set_is_frozen(self):
        _, payload = self._preflight_mcp_invocation(FAKE_KEYLESS_CONFIG)
        self.assertEqual(PREFLIGHT_TOP_KEYS, set(payload.keys()))

    def test_mcp_passthrough_top_level_key_set_is_frozen_with_keys(self):
        _, payload = self._preflight_mcp_invocation(FAKE_KEYED_CONFIG)
        self.assertEqual(PREFLIGHT_TOP_KEYS, set(payload.keys()))

    def test_mcp_passthrough_nested_shapes_are_frozen(self):
        _, payload = self._preflight_mcp_invocation(FAKE_KEYLESS_CONFIG)
        self.assertEqual(PREFLIGHT_LOCAL_READS_KEYS, set(payload["local_reads"].keys()))
        self.assertEqual(
            PREFLIGHT_PROJECT_CONFIG_KEYS,
            set(payload["local_reads"]["project_config"].keys()),
        )
        self.assertEqual(
            PREFLIGHT_BROWSER_COOKIES_KEYS,
            set(payload["local_reads"]["browser_cookies"].keys()),
        )
        self.assertEqual(PREFLIGHT_CREDENTIALS_KEYS, set(payload["credentials"].keys()))
        for entry in payload["credentials"].values():
            self.assertEqual({"present", "label"}, set(entry.keys()))
            self.assertIsInstance(entry["present"], bool)
        self.assertEqual(PREFLIGHT_NETWORK_KEYS, set(payload["network"].keys()))
        self.assertIsInstance(payload["network"]["available_sources"], list)

    def test_mcp_passthrough_reports_conditional_write_for_save_dir(self):
        # With --preflight-report-on-save-dir and no --save-dir, the report
        # dir appears as a conditional write — the MCP tool relies on this to
        # explain what a save WOULD touch.
        _, payload = self._preflight_mcp_invocation(FAKE_KEYLESS_CONFIG)
        self.assertIn(
            {"kind": "report_on_save", "path": self.MCP_SAVE_DIR},
            payload["conditional_writes"],
        )

    def test_mcp_passthrough_byte_format_is_stable(self):
        # The Go tool surfaces stdout verbatim, so the serialization format
        # (indent=2, sort_keys=True, trailing newline) is part of the contract.
        out, payload = self._preflight_mcp_invocation(FAKE_KEYLESS_CONFIG)
        self.assertEqual(json.dumps(payload, indent=2, sort_keys=True) + "\n", out)

    def test_no_secret_values_in_preflight_output(self):
        out, _ = self._preflight_mcp_invocation(FAKE_KEYED_CONFIG)
        for secret in FAKE_KEYED_CONFIG.values():
            self.assertNotIn(secret, out)


if __name__ == "__main__":
    unittest.main()

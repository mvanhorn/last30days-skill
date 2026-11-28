"""Tests for the keyless (Playwright) Threads source."""

import sys
import io
import json
import os
import unittest
from unittest import mock

from lib import threads_browser
from lib import env, subproc, threads


def _post(code, taken_at, likes=0, user="alice", text="claude code tips", replies=0, reposts=0):
    return {
        "pk": f"pk_{code}",
        "id": f"pk_{code}_1",
        "code": code,
        "taken_at": taken_at,
        "caption": {"text": text},
        "like_count": likes,
        "user": {"username": user, "full_name": user.title(), "is_verified": False},
        "text_post_app_info": {
            "direct_reply_count": replies,
            "repost_count": reposts,
            "quote_count": 0,
            "reshare_count": None,
        },
        "permalink": f"https://www.threads.com/@{user}/post/{code}",
        "image_versions2": {"candidates": [{"url": "https://cdn.example/x.jpg"}]},
    }


def _ssr_html(posts):
    """Mimic the logged-out search page: results inside a data-sjs script."""
    payload = {
        "require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"require": [
            ["RelayPrefetchedStreamCache", "next", [], ["adp_q", {"__bbox": {"result": {"data": {
                "searchResults": {"edges": [
                    {"node": {"thread": {"thread_items": [{"post": p}]}}} for p in posts
                ]}
            }}}}]],
        ]}}]]]
    }
    noise = '<script type="application/json" data-sjs>{"require":[["Bootloader"]]}</script>'
    blob = f'<script type="application/json" data-content-len="1" data-sjs>{json.dumps(payload)}</script>'
    return f"<html><body>{noise}{blob}</body></html>"


class TestExtractCoreSubject(unittest.TestCase):
    def test_caps_verbose_planner_query_to_three_words(self):
        self.assertEqual(
            threads._extract_core_subject("AI video tools OR tutorials"),
            "ai video tools",
        )

    def test_strips_boolean_operators(self):
        self.assertNotIn("or", threads._extract_core_subject("cursor OR windsurf").split())


class TestQueryLadder(unittest.TestCase):
    def test_three_words_fall_back_to_two(self):
        self.assertEqual(threads._query_ladder("ai video tools"), ["ai video tools", "ai video"])

    def test_single_word_is_kept(self):
        self.assertEqual(threads._query_ladder("claude"), ["claude"])

    def test_empty(self):
        self.assertEqual(threads._query_ladder(""), [])


class TestHelperExtraction(unittest.TestCase):
    def test_posts_from_server_rendered_html(self):
        html = _ssr_html([_post("A1", 1795851801), _post("B2", 1795851900)])
        posts = threads_browser.posts_from_html(html)
        self.assertEqual({p["code"] for p in posts}, {"A1", "B2"})

    def test_ignores_blobs_without_search_results(self):
        html = '<script type="application/json" data-sjs>{"x": {"taken_at": 1, "user": {}, "code": "Z"}}</script>'
        self.assertEqual(threads_browser.posts_from_html(html), [])

    def test_posts_from_graphql_body_with_prefix(self):
        body = "for (;;);" + json.dumps({"data": {"searchResults": {"edges": [
            {"node": {"thread": {"thread_items": [{"post": _post("C3", 1795851801)}]}}}
        ]}}})
        self.assertEqual([p["code"] for p in threads_browser.posts_from_json_text(body)], ["C3"])

    def test_slim_drops_media_and_keeps_counts(self):
        slim = threads_browser._slim(_post("D4", 1795851801, likes=7, replies=3))
        self.assertNotIn("image_versions2", slim)
        self.assertEqual(slim["like_count"], 7)
        self.assertEqual(slim["text_post_app_info"]["direct_reply_count"], 3)

    def test_main_reports_missing_playwright_as_json(self):
        with mock.patch.object(threads_browser, "search", side_effect=ImportError("no playwright")), \
                mock.patch.object(threads_browser.sys, "stdout") as out:
            self.assertEqual(threads_browser.main(["claude"]), 0)
        written = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn("playwright_missing", json.loads(written)["error"])

    def test_main_passes_query_ladder_after_separator(self):
        with mock.patch.object(threads_browser, "search", return_value={"posts": []}) as search, \
                mock.patch.object(threads_browser.sys, "stdout"):
            threads_browser.main(["--modes", "recent", "--", "ai video tools", "ai video"])
        self.assertEqual(search.call_args.args[0], ["ai video tools", "ai video"])
        self.assertEqual(search.call_args.args[1], ["recent"])


class TestParseItems(unittest.TestCase):
    def test_web_shape_fields(self):
        items = threads._parse_items([_post("E5", 1795851801, likes=12, replies=4, reposts=2)], "claude code")
        item = items[0]
        self.assertEqual(item["handle"], "alice")
        self.assertEqual(item["url"], "https://www.threads.com/@alice/post/E5")
        self.assertEqual(item["date"], "2026-11-28")
        self.assertEqual(item["engagement"], {"likes": 12, "replies": 4, "reposts": 2, "quotes": 0})

    def test_foreign_permalink_is_rebuilt(self):
        raw = _post("F6", 1795851801)
        raw["permalink"] = "javascript:alert(1)"
        self.assertEqual(threads._parse_items([raw], "x")[0]["url"], "https://www.threads.com/@alice/post/F6")

    def test_bad_counts_become_zero(self):
        raw = _post("G7", 1795851801)
        raw["like_count"] = "n/a"
        self.assertEqual(threads._parse_items([raw], "x")[0]["engagement"]["likes"], 0)


class TestBrowserCommand(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(threads.PYTHON_ENV, None)
        os.environ.pop(threads.BROWSER_ENV, None)

    def test_off_switch(self):
        self.assertIsNone(threads.browser_command({threads.BROWSER_ENV: "off"}))
        self.assertFalse(env.is_threads_available({threads.BROWSER_ENV: "off"}))

    def test_off_switch_from_process_env(self):
        os.environ[threads.BROWSER_ENV] = "0"
        self.assertIsNone(threads.browser_command({}))

    def test_python_override_wins(self):
        cmd = threads.browser_command({threads.PYTHON_ENV: sys.executable})
        self.assertEqual(cmd, [sys.executable, str(threads.HELPER)])

    def test_missing_python_override_is_unavailable(self):
        cfg = {threads.PYTHON_ENV: "/nonexistent/bin/python-l30d"}
        self.assertIsNone(threads.browser_command(cfg))
        self.assertFalse(env.is_threads_available(cfg))

    def test_missing_helper_is_unavailable(self):
        from pathlib import Path
        with mock.patch.object(threads, "HELPER", Path("/nonexistent/threads_browser.py")):
            self.assertIsNone(threads.browser_command({threads.PYTHON_ENV: sys.executable}))

    def test_helper_ships_inside_lib(self):
        # The MCP bundle vendors lib/ only; the helper must live there.
        self.assertEqual(threads.HELPER.parent.name, "lib")
        self.assertTrue(threads.HELPER.is_file())

    def test_helper_runs_by_path_without_lib_shadowing_stdlib(self):
        import subprocess
        out = subprocess.run(
            [sys.executable, str(threads.HELPER), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        lib_dir = str(threads.HELPER.parent)
        code = (
            "import runpy, sys; "
            f"sys.path.insert(0, {lib_dir!r}); "
            f"sys.argv = [{str(threads.HELPER)!r}, '--help']; "
            "sys.stdout = open('/dev/null', 'w') if sys.platform != 'win32' else sys.stdout\n"
            "try:\n"
            f"    runpy.run_path({str(threads.HELPER)!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "import http\n"
            "sys.stderr.write(http.__file__)"
        )
        probe = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertNotIn(lib_dir, probe.stderr)
        self.assertTrue(probe.stderr.endswith("__init__.py"), probe.stderr)

    def test_uv_fallback_when_playwright_missing(self):
        with mock.patch.object(threads.importlib.util, "find_spec", return_value=None), \
                mock.patch.object(threads, "_uv_path", return_value="/usr/bin/uv"):
            cmd = threads.browser_command({})
        self.assertEqual(cmd[:2], ["/usr/bin/uv", "run"])
        self.assertIn(threads.PLAYWRIGHT_REQ, cmd)
        self.assertEqual(cmd[-1], str(threads.HELPER))

    def test_unavailable_without_playwright_or_uv(self):
        with mock.patch.object(threads.importlib.util, "find_spec", return_value=None), \
                mock.patch.object(threads, "_uv_path", return_value=None):
            self.assertIsNone(threads.browser_command({}))
            self.assertFalse(env.is_threads_available({}))

    def test_helper_script_exists(self):
        self.assertTrue(threads.HELPER.is_file())

    def test_child_env_carries_no_api_keys(self):
        os.environ.update({
            "SCRAPECREATORS_API_KEY": "secret", "OPENAI_API_KEY": "secret",
            "PATH": "/bin", "PLAYWRIGHT_BROWSERS_PATH": "/pw", "LC_ALL": "C",
        })
        child = threads._child_env()
        self.assertNotIn("SCRAPECREATORS_API_KEY", child)
        self.assertNotIn("OPENAI_API_KEY", child)
        self.assertEqual(child["PLAYWRIGHT_BROWSERS_PATH"], "/pw")
        self.assertEqual(child["LC_ALL"], "C")
        self.assertEqual(child["PYTHONIOENCODING"], "utf-8")

    def test_no_browser_on_disk_is_unavailable(self):
        with mock.patch.object(threads, "browser_installed", return_value=False):
            cfg = {threads.PYTHON_ENV: sys.executable}
            self.assertIsNone(threads.browser_command(cfg))
            self.assertFalse(env.is_threads_available(cfg))
            self.assertIn("playwright install chromium", threads.describe_backend(cfg))

    def test_find_browser_reads_playwright_cache(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = tmp
            with mock.patch.object(threads.Path, "is_file", return_value=False), \
                    mock.patch.object(threads.shutil, "which", return_value=None):
                self.assertFalse(threads._find_browser())
                (Path(tmp) / "chromium_headless_shell-1200").mkdir()
                self.assertTrue(threads._find_browser())


class TestHelperOutput(unittest.TestCase):
    def test_line_separators_in_posts_survive_the_pipe(self):
        post = {"pk": "1", "code": "U1", "taken_at": 1795851801,
                "caption": {"text": "a\u2028b\x85c \U0001F600 claude code"},
                "user": {"username": "bob"}}
        buf = io.StringIO()
        with mock.patch.object(threads_browser, "search", return_value={"posts": [post]}), \
                mock.patch.object(sys, "stdout", buf):
            threads_browser.main(["--", "claude code"])
        line = buf.getvalue()
        self.assertTrue(line.isascii())
        result = subproc.SubprocResult(returncode=0, stdout=line, stderr="")
        with mock.patch.object(subproc, "run_with_timeout", return_value=result):
            data = threads._run_helper([sys.executable, "h"], dict(threads.DEPTH_CONFIG["quick"], queries=["q"]), {})
        self.assertEqual(data["posts"][0]["caption"]["text"], post["caption"]["text"])

    def test_stop_epoch_caps_the_search_budget(self):
        import time as _time
        seen = {}

        def fake_search(queries, modes, timeout_s, scrolls, headless):
            seen["budget"] = timeout_s
            return {"posts": []}

        with mock.patch.object(threads_browser, "search", side_effect=fake_search), \
                mock.patch.object(sys, "stdout", io.StringIO()):
            threads_browser.main(["--timeout", "60", "--stop-epoch", str(_time.time() + 12), "--", "q"])
        self.assertLess(seen["budget"], 11)

    def test_run_helper_passes_stop_epoch_before_research_deadline(self):
        import time as _time
        result = subproc.SubprocResult(returncode=0, stdout='{"posts": []}', stderr="")
        with mock.patch.object(subproc, "run_with_timeout", return_value=result) as run:
            threads._run_helper([sys.executable, "h"], dict(threads.DEPTH_CONFIG["default"], queries=["q"]),
                                {"_research_deadline": _time.monotonic() + 30})
        argv = run.call_args.args[0]
        stop = float(argv[argv.index("--stop-epoch") + 1])
        self.assertLess(stop, _time.time() + 26)


class TestSearchThreads(unittest.TestCase):
    CFG = {threads.PYTHON_ENV: sys.executable}

    def _run(self, helper_json, depth="default", returncode=0):
        result = subproc.SubprocResult(returncode=returncode, stdout=json.dumps(helper_json), stderr="")
        with mock.patch.object(subproc, "run_with_timeout", return_value=result) as run:
            out = threads.search_threads("claude code tips", "2026-10-29", "2026-11-28", depth=depth, config=self.CFG)
        return out, run

    def test_needs_no_key_and_filters_window(self):
        posts = [
            _post("IN1", 1795851801, likes=5),
            _post("IN2", 1795851801, likes=50, user="bob"),
            _post("OLD", 1769000000, likes=999),
        ]
        out, run = self._run({"posts": posts})
        self.assertNotIn("error", out)
        self.assertEqual([i["id"] for i in out["items"]], ["pk_IN2", "pk_IN1"])
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:2], [sys.executable, str(threads.HELPER)])
        self.assertEqual(cmd[cmd.index("--") + 1:], ["claude code tips", "claude code"])
        self.assertNotIn("SCRAPECREATORS_API_KEY", run.call_args.kwargs["env"])

    def test_quick_depth_runs_recent_only(self):
        _, run = self._run({"posts": []}, depth="quick")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--modes") + 1], "recent")

    def test_helper_error_is_surfaced(self):
        out, _ = self._run({"posts": [], "error": "login_wall: x"})
        self.assertEqual(out, {"items": [], "error": "login_wall: x"})

    def test_empty_without_error_is_not_an_error(self):
        out, _ = self._run({"posts": []})
        self.assertEqual(out, {"items": []})

    def test_nonzero_exit(self):
        out, _ = self._run({}, returncode=1)
        self.assertIn("browser helper failed", out["error"])

    def test_timeout(self):
        with mock.patch.object(subproc, "run_with_timeout", side_effect=subproc.SubprocTimeout("t")):
            out = threads.search_threads("claude", "2026-10-29", "2026-11-28", config=self.CFG)
        self.assertIn("timed out", out["error"])

    def test_unavailable(self):
        out = threads.search_threads("claude", "2026-10-29", "2026-11-28", config={threads.BROWSER_ENV: "off"})
        self.assertIn("unavailable", out["error"])

    def test_results_capped_by_depth(self):
        posts = [_post(f"P{i}", 1795851801, likes=i) for i in range(30)]
        out, _ = self._run({"posts": posts}, depth="quick")
        self.assertEqual(len(out["items"]), threads.DEPTH_CONFIG["quick"]["results"])

    def test_research_deadline_shrinks_helper_budget(self):
        import time
        cfg = {**self.CFG, "_research_deadline": time.monotonic() + 20}
        result = subproc.SubprocResult(returncode=0, stdout='{"posts": []}', stderr="")
        with mock.patch.object(subproc, "run_with_timeout", return_value=result) as run:
            threads.search_threads("claude", "2026-10-29", "2026-11-28", config=cfg)
        cmd = run.call_args.args[0]
        self.assertLessEqual(int(cmd[cmd.index("--timeout") + 1]), 15)
        self.assertEqual(run.call_args.kwargs["deadline_monotonic"], cfg["_research_deadline"])


if __name__ == "__main__":
    unittest.main()

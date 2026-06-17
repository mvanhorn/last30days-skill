import io
import unittest
from contextlib import redirect_stderr
from unittest import mock

from lib import ui


class PromoMessageTests(unittest.TestCase):
    def test_x_promo_mentions_browser_support_and_fallbacks(self):
        msg = ui.PROMO_SINGLE_KEY["x"]
        self.assertIn("Firefox", msg, "Firefox should be listed as supported")
        self.assertIn("Windows", msg, "Windows limitation should be mentioned")
        self.assertIn("FROM_BROWSER=auto", msg, "Chrome opt-in should be mentioned")
        self.assertIn("AUTH_TOKEN", msg, "AUTH_TOKEN/CT0 fallback should be listed")
        self.assertIn("XAI_API_KEY", msg, "XAI_API_KEY fallback should be listed")

    def test_x_promo_does_not_say_firefox_or_safari_without_qualification(self):
        msg = ui.PROMO_SINGLE_KEY["x"]
        self.assertNotIn(
            "Firefox or Safari",
            msg,
            "Promo should not say 'Firefox or Safari' without qualification",
        )
        self.assertNotIn(
            "Chrome/Safari",
            msg,
            "Promo should not list Chrome alongside Safari as if both are default",
        )


class UiV3Tests(unittest.TestCase):
    def test_show_diagnostic_banner_uses_v3_source_model(self):
        diag = {
            "available_sources": ["grounding", "youtube"],
            "providers": {"google": True, "openai": False, "xai": False},
            "x_backend": None,
            "bird_installed": True,
            "bird_authenticated": False,
            "bird_username": None,
            "native_web_backend": "brave",
        }
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                ui.show_diagnostic_banner(diag)
        output = stderr.getvalue()
        self.assertIn("Reddit", output)
        self.assertIn("unavailable", output)
        self.assertIn("Add AUTH_TOKEN/CT0 or XAI_API_KEY", output)
        self.assertIn("brave API available", output)

    def test_build_nux_message_mentions_v3_unlock_paths(self):
        text = ui._build_nux_message(
            {"available_sources": ["reddit", "youtube", "grounding"]}
        )
        self.assertIn("Reddit ✓, X ✗, YouTube ✓, Web ✓", text)
        self.assertIn("works fine as-is", text)
        self.assertIn("all free", text)

    def test_show_complete_uses_actual_sources_for_source_restricted_runs(self):
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                progress = ui.ProgressDisplay("test topic", show_banner=False)
                progress.show_complete(
                    source_counts={"grounding": 2},
                    display_sources=["grounding"],
                )
        output = stderr.getvalue()
        self.assertIn("Web: 2 results", output)
        self.assertNotIn("Reddit:", output)
        self.assertNotIn("X:", output)

    def test_show_complete_supports_newer_sources(self):
        with mock.patch.object(ui, "IS_TTY", False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                progress = ui.ProgressDisplay("test topic", show_banner=False)
                progress.show_complete(
                    source_counts={
                        "bluesky": 3,
                        "truthsocial": 1,
                        "xiaohongshu": 4,
                    },
                    display_sources=["bluesky", "truthsocial", "xiaohongshu"],
                )
        output = stderr.getvalue()
        self.assertIn("Bluesky: 3 posts", output)
        self.assertIn("Truth Social: 1 post", output)
        self.assertIn("Xiaohongshu: 4 posts", output)

if __name__ == "__main__":
    unittest.main()

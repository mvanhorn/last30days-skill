"""Tests for --search flag source selection."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

# Add scripts to path
SCRIPT_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

# Set clean config mode for tests
os.environ['LAST30DAYS_CONFIG_DIR'] = ''

# Import after path setup
from last30days import parse_search_flag, VALID_SEARCH_SOURCES


class TestParseSearchFlag(unittest.TestCase):
    """Test --search flag parsing."""

    def test_single_source(self):
        result = parse_search_flag("reddit")
        self.assertEqual(result, {"reddit"})

    def test_multiple_sources(self):
        result = parse_search_flag("reddit,hn,yt")
        self.assertEqual(result, {"reddit", "hn", "yt"})

    def test_all_sources(self):
        result = parse_search_flag("reddit,x,web,hn,yt,ph")
        self.assertEqual(result, VALID_SEARCH_SOURCES)

    def test_strips_whitespace(self):
        result = parse_search_flag("reddit, hn, yt")
        self.assertEqual(result, {"reddit", "hn", "yt"})

    def test_case_insensitive(self):
        result = parse_search_flag("Reddit,HN,YT")
        self.assertEqual(result, {"reddit", "hn", "yt"})

    def test_deduplicates(self):
        result = parse_search_flag("reddit,reddit,hn")
        self.assertEqual(result, {"reddit", "hn"})

    def test_invalid_source_exits(self):
        with self.assertRaises(SystemExit):
            parse_search_flag("reddit,invalid")

    def test_empty_string_exits(self):
        with self.assertRaises(SystemExit):
            parse_search_flag("")

    def test_web_only(self):
        result = parse_search_flag("web")
        self.assertEqual(result, {"web"})

    def test_just_hn(self):
        result = parse_search_flag("hn")
        self.assertEqual(result, {"hn"})

    def test_just_ph(self):
        result = parse_search_flag("ph")
        self.assertEqual(result, {"ph"})


class TestValidSources(unittest.TestCase):
    """Test valid source constants."""

    def test_all_expected_sources(self):
        expected = {"reddit", "x", "web", "hn", "yt", "ph"}
        self.assertEqual(VALID_SEARCH_SOURCES, expected)


class TestSearchSourceOverride(unittest.TestCase):
    """Test that search_sources properly overrides source selection in run_research."""

    def test_search_sources_none_uses_default(self):
        """When search_sources is None, original logic applies."""
        from last30days import run_research
        import lib.ui as ui_module

        progress = ui_module.ProgressDisplay("test", show_banner=False)
        # Web-only mode via sources parameter
        result = run_research(
            topic="test",
            sources="web",
            config={},
            selected_models={},
            from_date="2026-01-01",
            to_date="2026-02-01",
            mock=True,
            progress=progress,
            search_sources=None,
        )
        # web_needed should be True (index 5)
        self.assertTrue(result[5])

    def test_search_sources_web_only(self):
        """--search=web should result in web_needed=True and no API items."""
        from last30days import run_research
        import lib.ui as ui_module

        progress = ui_module.ProgressDisplay("test", show_banner=False)
        result = run_research(
            topic="test",
            sources="auto",
            config={},
            selected_models={},
            from_date="2026-01-01",
            to_date="2026-02-01",
            mock=True,
            progress=progress,
            search_sources={"web"},
        )
        reddit_items, x_items, web_needed = result[0], result[1], result[5]
        self.assertEqual(reddit_items, [])
        self.assertEqual(x_items, [])
        self.assertTrue(web_needed)

    def test_search_sources_reddit_only(self):
        """--search=reddit should run only Reddit."""
        from last30days import run_research
        import lib.ui as ui_module

        progress = ui_module.ProgressDisplay("test", show_banner=False)
        result = run_research(
            topic="test",
            sources="auto",
            config={"OPENAI_API_KEY": "mock"},
            selected_models={"openai": "gpt-4o"},
            from_date="2026-01-01",
            to_date="2026-02-01",
            mock=True,
            progress=progress,
            search_sources={"reddit"},
        )
        reddit_items, x_items, web_needed = result[0], result[1], result[5]
        # Reddit should have items (from mock), X should be empty, web not needed
        self.assertGreater(len(reddit_items), 0)
        self.assertEqual(x_items, [])
        self.assertFalse(web_needed)

    def test_search_sources_excludes_reddit(self):
        """--search=x should NOT run Reddit even if keys are available."""
        from last30days import run_research
        import lib.ui as ui_module

        progress = ui_module.ProgressDisplay("test", show_banner=False)
        result = run_research(
            topic="test",
            sources="both",  # Would normally run both
            config={"OPENAI_API_KEY": "mock", "XAI_API_KEY": "mock"},
            selected_models={"openai": "gpt-4o", "xai": "grok-3"},
            from_date="2026-01-01",
            to_date="2026-02-01",
            mock=True,
            progress=progress,
            search_sources={"x"},
        )
        reddit_items, x_items = result[0], result[1]
        self.assertEqual(reddit_items, [])
        self.assertGreater(len(x_items), 0)


if __name__ == "__main__":
    unittest.main()

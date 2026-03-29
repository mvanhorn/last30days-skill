"""Tests for render module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import render, schema


class TestRenderCompact(unittest.TestCase):
    def test_renders_basic_report(self):
        report = schema.Report(
            topic="test topic",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="both",
            openai_model_used="gpt-5.2",
            xai_model_used="grok-4-latest",
        )

        result = render.render_compact(report)

        self.assertIn("test topic", result)
        self.assertIn("2026-01-01", result)
        self.assertIn("both", result)
        self.assertIn("gpt-5.2", result)

    def test_renders_reddit_items(self):
        report = schema.Report(
            topic="test",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="reddit-only",
            reddit=[
                schema.RedditItem(
                    id="R1",
                    title="Test Thread",
                    url="https://reddit.com/r/test/1",
                    subreddit="test",
                    date="2026-01-15",
                    date_confidence="high",
                    score=85,
                    why_relevant="Very relevant",
                )
            ],
        )

        result = render.render_compact(report)

        self.assertIn("R1", result)
        self.assertIn("Test Thread", result)
        self.assertIn("r/test", result)

    def test_shows_coverage_tip_for_reddit_only(self):
        report = schema.Report(
            topic="test",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="reddit-only",
        )

        result = render.render_compact(report)

        self.assertIn("xAI key", result)

    def test_mode_line_includes_auxiliary_sources_present_in_report(self):
        report = schema.Report(
            topic="test",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="reddit-only",
            youtube=[
                schema.YouTubeItem(
                    id="Y1",
                    title="Video",
                    url="https://youtube.com/watch?v=1",
                    channel_name="ch",
                    date="2026-01-15",
                )
            ],
            hackernews=[
                schema.HackerNewsItem(
                    id="HN1",
                    title="Story",
                    url="https://example.com/story",
                    hn_url="https://news.ycombinator.com/item?id=1",
                    author="hn",
                    date="2026-01-16",
                    engagement=schema.Engagement(score=10, num_comments=2),
                )
            ],
        )

        result = render.render_compact(report)

        self.assertIn("**Mode:** reddit-only + youtube + hn", result)


class TestRenderContextSnippet(unittest.TestCase):
    def test_renders_snippet(self):
        report = schema.Report(
            topic="Claude Code Skills",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="both",
        )

        result = render.render_context_snippet(report)

        self.assertIn("Claude Code Skills", result)
        self.assertIn("Last 30 Days", result)


class TestRenderFullReport(unittest.TestCase):
    def test_renders_full_report(self):
        report = schema.Report(
            topic="test topic",
            range_from="2026-01-01",
            range_to="2026-01-31",
            generated_at="2026-01-31T12:00:00Z",
            mode="both",
            openai_model_used="gpt-5.2",
            xai_model_used="grok-4-latest",
        )

        result = render.render_full_report(report)

        self.assertIn("# test topic", result)
        self.assertIn("## Models Used", result)
        self.assertIn("gpt-5.2", result)


class TestGetContextPath(unittest.TestCase):
    def test_returns_path_string(self):
        result = render.get_context_path()
        self.assertIsInstance(result, str)
        self.assertIn("last30days.context.md", result)


class TestEnsureOutputDir(unittest.TestCase):
    """Tests for ensure_output_dir()."""

    def test_creates_directory(self):
        import os
        import tempfile
        test_dir = os.path.join(tempfile.mkdtemp(), "output", "nested")
        os.environ["LAST30DAYS_OUTPUT_DIR"] = test_dir
        try:
            render.ensure_output_dir()
            self.assertTrue(os.path.exists(test_dir))
        finally:
            os.environ.pop("LAST30DAYS_OUTPUT_DIR", None)


class TestXrefTag(unittest.TestCase):
    """Tests for _xref_tag()."""

    def test_no_refs(self):
        item = schema.RedditItem(id="R1", title="T", url="u", subreddit="s")
        self.assertEqual(render._xref_tag(item), "")

    def test_with_refs(self):
        item = schema.RedditItem(
            id="R1", title="T", url="u", subreddit="s",
            cross_refs=["X1", "HN2"],
        )
        tag = render._xref_tag(item)
        self.assertIn("X", tag)
        self.assertIn("HN", tag)


class TestRenderEmptyReport(unittest.TestCase):
    """Test render_compact handles empty reports gracefully."""

    def test_empty_items_graceful(self):
        report = schema.Report(
            topic="test",
            range_from="2026-02-04",
            range_to="2026-03-06",
            generated_at="2026-03-06T00:00:00+00:00",
            mode="both",
        )
        output = render.render_compact(report)
        self.assertIn("test", output)
        self.assertIsInstance(output, str)


if __name__ == "__main__":
    unittest.main()

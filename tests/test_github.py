"""Tests for GitHub source module."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from lib import github


class TestResolveToken(unittest.TestCase):
    def test_explicit_token(self):
        self.assertEqual(github._resolve_token("my-token"), "my-token")

    @patch.dict("os.environ", {"GITHUB_TOKEN": "env-token"})
    def test_env_token(self):
        self.assertEqual(github._resolve_token(), "env-token")

    @patch.dict("os.environ", {}, clear=True)
    @patch("subprocess.run")
    def test_gh_cli_fallback(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="gh-token\n")
        # Clear GITHUB_TOKEN from env for this test
        result = github._resolve_token()
        self.assertEqual(result, "gh-token")

    @patch.dict("os.environ", {}, clear=True)
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_no_token_available(self, mock_run):
        result = github._resolve_token()
        self.assertIsNone(result)


class TestParseRepoFromUrl(unittest.TestCase):
    def test_issue_url(self):
        url = "https://github.com/facebook/react/issues/123"
        self.assertEqual(github._parse_repo_from_url(url), "facebook/react")

    def test_pr_url(self):
        url = "https://github.com/vercel/next.js/pull/456"
        self.assertEqual(github._parse_repo_from_url(url), "vercel/next.js")

    def test_empty(self):
        self.assertEqual(github._parse_repo_from_url(""), "")


class TestParseDate(unittest.TestCase):
    def test_iso_date(self):
        self.assertEqual(github._parse_date("2026-03-15T12:00:00Z"), "2026-03-15")

    def test_none(self):
        self.assertIsNone(github._parse_date(None))

    def test_empty(self):
        self.assertIsNone(github._parse_date(""))


class TestSearchGithub(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_no_token_returns_empty(self, mock_run):
        result = github.search_github("react", "2026-03-01", "2026-03-31", token=None)
        self.assertEqual(result, [])

    @patch.object(github, "_fetch_json")
    @patch.object(github, "_resolve_token", return_value="test-token")
    def test_search_returns_items(self, mock_token, mock_fetch):
        mock_fetch.return_value = {
            "total_count": 1,
            "items": [
                {
                    "html_url": "https://github.com/facebook/react/issues/42",
                    "title": "React Server Components bug",
                    "body": "There is a bug when using RSC with streaming...",
                    "created_at": "2026-03-15T10:00:00Z",
                    "state": "open",
                    "comments": 12,
                    "reactions": {"total_count": 8},
                    "labels": [{"name": "bug"}, {"name": "rsc"}],
                    "user": {"login": "testuser"},
                },
            ],
        }
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item["source"], "github")
        self.assertEqual(item["container"], "facebook/react")
        self.assertEqual(item["title"], "React Server Components bug")
        self.assertEqual(item["date"], "2026-03-15")
        self.assertEqual(item["author"], "testuser")
        self.assertIn("bug", item["metadata"]["labels"])
        self.assertEqual(item["metadata"]["state"], "open")
        self.assertEqual(item["metadata"]["comment_count"], 12)
        self.assertEqual(item["metadata"]["reactions"], 8)
        self.assertEqual(item["engagement"]["reactions"], 8)
        self.assertEqual(item["engagement"]["comments"], 12)
        self.assertFalse(item["metadata"]["is_pr"])

    @patch.object(github, "_fetch_json", return_value=None)
    @patch.object(github, "_resolve_token", return_value="test-token")
    def test_rate_limit_returns_empty(self, mock_token, mock_fetch):
        """403 rate limit returns empty list gracefully."""
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(result, [])

    @patch.object(github, "_fetch_json")
    @patch.object(github, "_resolve_token", return_value="test-token")
    def test_pr_detected(self, mock_token, mock_fetch):
        mock_fetch.return_value = {
            "total_count": 1,
            "items": [
                {
                    "html_url": "https://github.com/vercel/next.js/pull/99",
                    "title": "Add streaming support",
                    "body": "This PR adds...",
                    "created_at": "2026-03-20T10:00:00Z",
                    "state": "open",
                    "comments": 5,
                    "reactions": {"total_count": 3},
                    "labels": [],
                    "user": {"login": "dev"},
                    "pull_request": {"url": "..."},
                },
            ],
        }
        result = github.search_github("next.js", "2026-03-01", "2026-03-31")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["metadata"]["is_pr"])


class TestComputeRelevance(unittest.TestCase):
    def test_basic_relevance(self):
        score = github._compute_relevance("react hooks", "React Hooks Tutorial", 0, 10, 5)
        self.assertGreater(score, 0.5)
        self.assertLessEqual(score, 1.0)

    def test_lower_rank_lower_score(self):
        high = github._compute_relevance("react", "React", 0, 0, 0)
        low = github._compute_relevance("react", "React", 20, 0, 0)
        self.assertGreater(high, low)


if __name__ == "__main__":
    unittest.main()

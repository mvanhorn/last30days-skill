"""Tests for GitHub source module."""

import json
import unittest
from unittest.mock import patch, MagicMock

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

    def test_rejects_garbage(self):
        """The old naive slicing returned 'hello worl' for 'hello world'. Reject it."""
        self.assertIsNone(github._parse_date("hello world"))
        self.assertIsNone(github._parse_date("not-a-date"))
        self.assertIsNone(github._parse_date("abcdefghij"))

    def test_rejects_invalid_date_values(self):
        """An out-of-range date like 2026-99-99 is not a real date."""
        self.assertIsNone(github._parse_date("2026-99-99"))

    def test_iso_with_offset(self):
        self.assertEqual(github._parse_date("2026-03-15T12:00:00+00:00"), "2026-03-15")

    def test_iso_with_no_colon_offset(self):
        self.assertEqual(github._parse_date("2026-03-15T12:00:00+0000"), "2026-03-15")


class TestSearchGithub(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_no_token_returns_empty_envelope(self, mock_run):
        result = github.search_github("react", "2026-03-01", "2026-03-31", token=None)
        self.assertEqual(result.get("items", []), [])
        self.assertIn("error", result)
        # Envelope shape must match the fetch-failure path: context is
        # always present so callers (and parse_github_response) can read
        # diagnostic fields without branching on which failure mode hit.
        self.assertIn("context", result)
        self.assertEqual(result["context"]["from_date"], "2026-03-01")
        self.assertEqual(result["context"]["to_date"], "2026-03-31")

    def test_resolve_token_public_alias(self):
        """resolve_token is the public entry point pipeline uses; _resolve_token stays
        private. Both should return the same value for the same input."""
        self.assertEqual(
            github.resolve_token("explicit-token"),
            github._resolve_token("explicit-token"),
        )
        self.assertEqual(github.resolve_token("explicit-token"), "explicit-token")

    @patch.object(github, "_fetch_json")
    @patch.object(github, "_resolve_token", return_value="test-token")
    def test_search_returns_raw_envelope(self, mock_token, mock_fetch):
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
        # Search returns raw envelope; parse normalizes.
        response = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(len(response["items"]), 1)
        self.assertEqual(response["items"][0]["title"], "React Server Components bug")
        self.assertEqual(response["context"]["from_date"], "2026-03-01")

        items = github.parse_github_response(response)
        self.assertEqual(len(items), 1)
        item = items[0]
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
    def test_rate_limit_returns_empty_envelope(self, mock_token, mock_fetch):
        """403 rate limit returns envelope with empty items list."""
        response = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(response["items"], [])
        self.assertEqual(github.parse_github_response(response), [])

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
        response = github.search_github("next.js", "2026-03-01", "2026-03-31")
        items = github.parse_github_response(response)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["metadata"]["is_pr"])


class TestParseGithubResponse(unittest.TestCase):
    """Fixture-driven parse tests: feed a synthetic search_github envelope to
    parse_github_response and assert normalized output.

    This contract (search returns dict envelope, parse turns it into a list)
    matches every other source adapter. Before this refactor, search_github
    returned a bare list and there was no parse step, blocking fixture tests.
    """

    _RAW_ENVELOPE = {
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
        "context": {
            "core": "react",
            "from_date": "2026-03-01",
            "to_date": "2026-03-31",
            "count": 25,
        },
    }

    def test_normalizes_items(self):
        items = github.parse_github_response(self._RAW_ENVELOPE)
        self.assertEqual(len(items), 2)
        by_url = {i["url"]: i for i in items}
        issue = by_url["https://github.com/facebook/react/issues/42"]
        self.assertEqual(issue["source"], "github")
        self.assertEqual(issue["container"], "facebook/react")
        self.assertEqual(issue["title"], "React Server Components bug")
        self.assertEqual(issue["date"], "2026-03-15")
        self.assertEqual(issue["author"], "testuser")
        self.assertEqual(issue["engagement"]["reactions"], 8)
        self.assertEqual(issue["engagement"]["comments"], 12)
        self.assertFalse(issue["metadata"]["is_pr"])

    def test_detects_pr(self):
        items = github.parse_github_response(self._RAW_ENVELOPE)
        pr = next(i for i in items if "/pull/" in i["url"])
        self.assertTrue(pr["metadata"]["is_pr"])

    def test_date_filter_drops_outside_window(self):
        envelope = {
            "items": [
                {
                    "html_url": "https://github.com/foo/bar/issues/1",
                    "title": "Too old",
                    "created_at": "2026-01-15T10:00:00Z",
                    "comments": 0, "reactions": {"total_count": 0},
                    "labels": [], "user": {"login": "x"},
                },
                {
                    "html_url": "https://github.com/foo/bar/issues/2",
                    "title": "In window",
                    "created_at": "2026-03-15T10:00:00Z",
                    "comments": 0, "reactions": {"total_count": 0},
                    "labels": [], "user": {"login": "x"},
                },
            ],
            "context": {"core": "foo", "from_date": "2026-03-01",
                        "to_date": "2026-03-31", "count": 25},
        }
        items = github.parse_github_response(envelope)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "In window")

    def test_sorts_by_relevance(self):
        items = github.parse_github_response(self._RAW_ENVELOPE)
        scores = [i.get("relevance", 0) for i in items]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_envelope(self):
        self.assertEqual(github.parse_github_response({"items": []}), [])
        self.assertEqual(github.parse_github_response({}), [])


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

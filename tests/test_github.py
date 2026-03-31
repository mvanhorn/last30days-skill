"""Tests for GitHub source module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import github, normalize, schema, score


class TestParseGithubResponse(unittest.TestCase):
    SAMPLE_RESPONSE = {
        "total_count": 2,
        "items": [
            {
                "number": 42,
                "title": "Add support for streaming responses",
                "html_url": "https://github.com/owner/repo/issues/42",
                "repository_url": "https://api.github.com/repos/owner/repo",
                "user": {"login": "alice"},
                "created_at": "2026-03-10T14:30:00Z",
                "body": "We need streaming support for real-time data.",
                "labels": [{"name": "enhancement"}, {"name": "priority-high"}],
                "comments": 15,
                "reactions": {"total_count": 25, "+1": 20, "-1": 0, "laugh": 2, "hooray": 3},
                "pull_request": None,
            },
            {
                "number": 99,
                "title": "Fix memory leak in worker pool",
                "html_url": "https://github.com/other/project/pull/99",
                "repository_url": "https://api.github.com/repos/other/project",
                "user": {"login": "bob"},
                "created_at": "2026-03-12T10:00:00Z",
                "body": "This PR fixes the memory leak described in #98.",
                "labels": [{"name": "bug"}],
                "comments": 8,
                "reactions": {"total_count": 10, "+1": 8, "-1": 0},
                "pull_request": {"url": "https://api.github.com/repos/other/project/pulls/99"},
            },
        ],
    }

    def test_parses_items(self):
        items = github.parse_github_response(self.SAMPLE_RESPONSE)
        self.assertEqual(len(items), 2)

    def test_item_fields(self):
        items = github.parse_github_response(self.SAMPLE_RESPONSE)
        item = items[0]
        self.assertEqual(item["number"], 42)
        self.assertEqual(item["title"], "Add support for streaming responses")
        self.assertEqual(item["url"], "https://github.com/owner/repo/issues/42")
        self.assertEqual(item["repository"], "owner/repo")
        self.assertEqual(item["author"], "alice")
        self.assertEqual(item["item_type"], "issue")
        self.assertEqual(item["engagement"]["reactions"], 25)
        self.assertEqual(item["engagement"]["num_comments"], 15)
        self.assertEqual(item["labels"], ["enhancement", "priority-high"])

    def test_pr_detection(self):
        items = github.parse_github_response(self.SAMPLE_RESPONSE)
        self.assertEqual(items[0]["item_type"], "issue")
        self.assertEqual(items[1]["item_type"], "pull_request")

    def test_date_extraction(self):
        items = github.parse_github_response(self.SAMPLE_RESPONSE)
        self.assertEqual(items[0]["date"], "2026-03-10")
        self.assertEqual(items[1]["date"], "2026-03-12")

    def test_relevance_range(self):
        items = github.parse_github_response(self.SAMPLE_RESPONSE)
        for item in items:
            self.assertGreaterEqual(item["relevance"], 0.0)
            self.assertLessEqual(item["relevance"], 1.0)

    def test_empty_response(self):
        items = github.parse_github_response({"items": []})
        self.assertEqual(items, [])

    def test_missing_items(self):
        items = github.parse_github_response({})
        self.assertEqual(items, [])

    def test_body_truncation(self):
        long_body = "A" * 1000
        response = {
            "items": [{
                "number": 1,
                "title": "Test",
                "html_url": "https://github.com/o/r/issues/1",
                "repository_url": "https://api.github.com/repos/o/r",
                "user": {"login": "u"},
                "created_at": "2026-03-01T00:00:00Z",
                "body": long_body,
                "labels": [],
                "comments": 0,
                "reactions": {"total_count": 0},
            }],
        }
        items = github.parse_github_response(response)
        self.assertLessEqual(len(items[0]["body_snippet"]), 504)  # 500 + "..."


class TestNormalizeGithubItems(unittest.TestCase):
    def test_normalize(self):
        raw_items = [
            {
                "number": 42,
                "title": "Test Issue",
                "url": "https://github.com/o/r/issues/42",
                "repository": "o/r",
                "author": "testuser",
                "item_type": "issue",
                "date": "2026-02-15",
                "body_snippet": "Some description",
                "labels": ["bug"],
                "engagement": {"reactions": 10, "num_comments": 5},
                "relevance": 0.8,
                "why_relevant": "Test",
            }
        ]
        result = normalize.normalize_github_items(raw_items, "2026-01-01", "2026-03-01")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.GitHubItem)
        self.assertEqual(result[0].id, "GH1")
        self.assertEqual(result[0].title, "Test Issue")
        self.assertEqual(result[0].repository, "o/r")
        self.assertEqual(result[0].item_type, "issue")
        self.assertEqual(result[0].date_confidence, "high")
        self.assertEqual(result[0].engagement.score, 10)  # reactions mapped to score
        self.assertEqual(result[0].engagement.num_comments, 5)
        self.assertEqual(result[0].labels, ["bug"])

    def test_normalize_with_comments(self):
        raw_items = [
            {
                "number": 1,
                "title": "Test",
                "url": "",
                "repository": "o/r",
                "author": "user",
                "item_type": "issue",
                "date": "2026-02-15",
                "body_snippet": "",
                "labels": [],
                "engagement": {"reactions": 1, "num_comments": 1},
                "relevance": 0.5,
                "why_relevant": "Test",
                "top_comments": [
                    {"author": "commenter", "text": "LGTM!", "reactions": 3},
                ],
                "comment_insights": ["LGTM!"],
            }
        ]
        result = normalize.normalize_github_items(raw_items, "2026-01-01", "2026-03-01")
        self.assertEqual(len(result[0].top_comments), 1)
        self.assertEqual(result[0].top_comments[0].author, "commenter")
        self.assertEqual(len(result[0].comment_insights), 1)


class TestScoreGithubItems(unittest.TestCase):
    def test_score_items(self):
        items = [
            schema.GitHubItem(
                id="GH1", title="High engagement", url="", repository="o/r",
                author="user1", item_type="issue", date="2026-02-20",
                engagement=schema.Engagement(score=50, num_comments=100),
                relevance=0.9,
            ),
            schema.GitHubItem(
                id="GH2", title="Low engagement", url="", repository="o/r",
                author="user2", item_type="issue", date="2026-02-18",
                engagement=schema.Engagement(score=2, num_comments=1),
                relevance=0.5,
            ),
        ]
        scored = score.score_github_items(items)
        self.assertEqual(len(scored), 2)
        # High engagement + high relevance should score higher
        self.assertGreater(scored[0].score, scored[1].score)

    def test_score_empty(self):
        result = score.score_github_items([])
        self.assertEqual(result, [])

    def test_engagement_formula(self):
        eng = schema.Engagement(score=100, num_comments=50)
        result = score.compute_github_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_engagement_none(self):
        result = score.compute_github_engagement_raw(None)
        self.assertIsNone(result)

    def test_engagement_empty(self):
        eng = schema.Engagement()
        result = score.compute_github_engagement_raw(eng)
        self.assertIsNone(result)


class TestSortItemsWithGithub(unittest.TestCase):
    def test_github_in_sort(self):
        """GitHub items should sort alongside other sources."""
        x_item = schema.XItem(id="X1", text="test", url="", author_handle="user")
        x_item.score = 50

        gh_item = schema.GitHubItem(
            id="GH1", title="test", url="", repository="o/r",
            author="user", item_type="issue",
        )
        gh_item.score = 50

        hn_item = schema.HackerNewsItem(id="HN1", title="test", url="", hn_url="", author="user")
        hn_item.score = 50

        sorted_items = score.sort_items([gh_item, hn_item, x_item])
        # Same score, so sorted by source priority: X(1) > HN(5) > GitHub(6)
        self.assertIsInstance(sorted_items[0], schema.XItem)
        self.assertIsInstance(sorted_items[1], schema.HackerNewsItem)
        self.assertIsInstance(sorted_items[2], schema.GitHubItem)


class TestGitHubItemSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        item = schema.GitHubItem(
            id="GH1",
            title="Test Issue",
            url="https://github.com/o/r/issues/1",
            repository="o/r",
            author="testuser",
            item_type="issue",
            date="2026-03-10",
            engagement=schema.Engagement(score=10, num_comments=5),
            body_snippet="Test body",
            labels=["bug", "enhancement"],
            relevance=0.8,
            why_relevant="Test issue",
        )
        d = item.to_dict()
        self.assertEqual(d["id"], "GH1")
        self.assertEqual(d["repository"], "o/r")
        self.assertEqual(d["item_type"], "issue")
        self.assertEqual(d["labels"], ["bug", "enhancement"])
        self.assertEqual(d["body_snippet"], "Test body")
        self.assertEqual(d["engagement"]["score"], 10)


if __name__ == "__main__":
    unittest.main()

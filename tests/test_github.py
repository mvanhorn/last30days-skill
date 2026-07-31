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
    @patch("lib.github._fetch_json", return_value=None)
    def test_no_token_unauth_rate_limited_sets_error(self, mock_fetch, mock_run):
        # No token -> unauthenticated request; on failure (likely anon rate
        # limit) the envelope carries a clear error instead of being silent.
        result = github.search_github("react", "2026-03-01", "2026-03-31", token=None)
        self.assertEqual(result.get("items", []), [])
        self.assertIn("error", result)
        self.assertIn("unauthenticated", result["error"].lower())
        self.assertIn("context", result)
        self.assertEqual(result["context"]["from_date"], "2026-03-01")
        # Unauth requests are capped to the low-rate tier.
        self.assertLessEqual(result["context"]["count"], github.UNAUTH_COUNT_CAP)
        # Both lanes attempted without a token (no early return).
        self.assertEqual(mock_fetch.call_count, 2)
        for call in mock_fetch.call_args_list:
            self.assertIsNone(call.kwargs.get("token"))

    @patch.dict("os.environ", {}, clear=True)
    @patch("subprocess.run", side_effect=FileNotFoundError)
    @patch("lib.github._fetch_json", return_value={"items": [{"id": 1, "title": "x"}]})
    def test_no_token_unauth_success_returns_items(self, mock_fetch, mock_run):
        result = github.search_github("react", "2026-03-01", "2026-03-31", token=None)
        self.assertEqual(len(result["items"]), 1)  # deduped
        self.assertNotIn("error", result)

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
        pr_item = {
            "id": 99,
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
        }
        mock_fetch.side_effect = [
            {"total_count": 0, "items": []},
            {"total_count": 1, "items": [pr_item]},
        ]
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


class TestDualLaneSearch(unittest.TestCase):
    """Two-lane search (issue #916)."""

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_queries_include_qualifiers(self, mock_fetch, mock_token):
        mock_fetch.return_value = {"items": []}
        github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(mock_fetch.call_count, 2)
        urls = [call.args[0] for call in mock_fetch.call_args_list]
        qualifiers_found = set()
        for url in urls:
            if "is%3Aissue" in url:
                qualifiers_found.add("is:issue")
            if "is%3Apull-request" in url:
                qualifiers_found.add("is:pull-request")
        self.assertEqual(qualifiers_found, {"is:issue", "is:pull-request"})

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_merges_results_from_both_lanes(self, mock_fetch, mock_token):
        issue_item = {"id": 1, "title": "Bug", "reactions": {"total_count": 5}}
        pr_item = {"id": 2, "title": "Fix", "reactions": {"total_count": 3},
                   "pull_request": {"url": "..."}}
        mock_fetch.side_effect = [
            {"items": [issue_item]},
            {"items": [pr_item]},
        ]
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(len(result["items"]), 2)
        ids = {item["id"] for item in result["items"]}
        self.assertEqual(ids, {1, 2})

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_deduplicates_by_id(self, mock_fetch, mock_token):
        item = {"id": 42, "title": "Shared", "reactions": {"total_count": 10}}
        mock_fetch.side_effect = [
            {"items": [item]},
            {"items": [item]},
        ]
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], 42)

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_sorts_by_reactions_descending(self, mock_fetch, mock_token):
        low = {"id": 1, "title": "Low", "reactions": {"total_count": 2}}
        high = {"id": 2, "title": "High", "reactions": {"total_count": 50}}
        mid = {"id": 3, "title": "Mid", "reactions": {"total_count": 10}}
        mock_fetch.side_effect = [
            {"items": [low, mid]},
            {"items": [high]},
        ]
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        reaction_counts = [
            item["reactions"]["total_count"] for item in result["items"]
        ]
        self.assertEqual(reaction_counts, [50, 10, 2])

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_partial_lane_failure_returns_available(self, mock_fetch, mock_token):
        item = {"id": 7, "title": "Survivor", "reactions": {"total_count": 1}}
        mock_fetch.side_effect = [
            None,
            {"items": [item]},
        ]
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(len(result["items"]), 1)
        self.assertNotIn("error", result)

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_both_lanes_fail_returns_error(self, mock_fetch, mock_token):
        def _fail(url, *, token=None, timeout=15, failure_out=None):
            if failure_out is not None:
                failure_out.append("HTTP 422: unprocessable query")
            return None
        mock_fetch.side_effect = _fail
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(result["items"], [])
        self.assertIn("error", result)

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_empty_lane_responses(self, mock_fetch, mock_token):
        mock_fetch.return_value = {"items": []}
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(result["items"], [])
        self.assertNotIn("error", result)

    @patch.object(github, "_resolve_token", return_value="test-token")
    @patch.object(github, "_fetch_json")
    def test_missing_reactions_handled_safely(self, mock_fetch, mock_token):
        a = {"id": 1, "title": "A", "reactions": {"total_count": 5}}
        b = {"id": 2, "title": "B"}
        c = {"id": 3, "title": "C", "reactions": "bad"}
        mock_fetch.side_effect = [
            {"items": [b, a]},
            {"items": [c]},
        ]
        result = github.search_github("react", "2026-03-01", "2026-03-31")
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["items"][0]["id"], 1)


class TestComputeRelevance(unittest.TestCase):
    def test_basic_relevance(self):
        score = github._compute_relevance("react hooks", "React Hooks Tutorial", 0, 10, 5)
        self.assertGreater(score, 0.5)
        self.assertLessEqual(score, 1.0)

    def test_lower_rank_lower_score(self):
        high = github._compute_relevance("react", "React", 0, 0, 0)
        low = github._compute_relevance("react", "React", 20, 0, 0)
        self.assertGreater(high, low)

class TestPersonPushEventsLane(unittest.TestCase):
    """Person mode must not go dark when PR search returns nothing."""

    @staticmethod
    def _event(
        event_id,
        *,
        actor="kurt",
        repo="kurt/power-bi-agentic-development",
        created_at="2026-07-22T20:28:18Z",
        event_type="PushEvent",
    ):
        return {
            "id": str(event_id),
            "type": event_type,
            "actor": {"login": actor},
            "repo": {"name": repo},
            "created_at": created_at,
        }

    def _run(self):
        with patch.object(github, "_resolve_token", return_value="t"), \
                patch.object(github, "_enrich_own_repo", return_value={}), \
                patch.object(github, "_fetch_repo_info", return_value={
                    "stars": 811,
                    "forks": 119,
                    "description": "Claude Code plugin marketplace for Power BI",
                    "language": "Python",
                    "open_issues": 4,
                }):
            return github.search_github_person(
                "kurt", "2026-06-25", "2026-07-25", token="t",
            )

    def test_unsearchable_account_falls_back_to_actor_push_events(self):
        def fetch(url, **kwargs):
            if "search/issues" in url:
                return None
            return [self._event(1)]

        with patch.object(github, "_fetch_json", side_effect=fetch):
            items = self._run()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["container"], "kurt/power-bi-agentic-development")
        self.assertEqual(items[0]["date"], "2026-07-22")
        self.assertIn("@kurt pushed", items[0]["title"])
        self.assertIn("recent-push", items[0]["metadata"]["labels"])
        self.assertEqual(items[0]["metadata"]["event_type"], "PushEvent")

    def test_empty_pr_search_falls_back_to_actor_push_events(self):
        def fetch(url, **kwargs):
            if "search/issues" in url:
                return {"total_count": 0, "items": []}
            return [self._event(1, actor="KURT")]

        with patch.object(github, "_fetch_json", side_effect=fetch):
            items = self._run()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["author"], "KURT")

    def test_other_actor_push_is_rejected(self):
        def fetch(url, **kwargs):
            if "search/issues" in url:
                return {"total_count": 0, "items": []}
            return [self._event(1, actor="collaborator")]

        with patch.object(github, "_fetch_json", side_effect=fetch):
            items = self._run()

        self.assertEqual(items, [])

    def test_discovers_push_on_second_events_page(self):
        first_page = [
            self._event(
                i,
                event_type="WatchEvent",
                created_at=f"2026-07-{24 - (i // 25):02d}T12:00:00Z",
            )
            for i in range(github.PERSON_EVENTS_PER_PAGE)
        ]
        requested_urls = []

        def fetch(url, **kwargs):
            requested_urls.append(url)
            if "search/issues" in url:
                return {"total_count": 0, "items": []}
            if "&page=1" in url:
                return first_page
            if "&page=2" in url:
                return [self._event(101, repo="kurt/page-two")]
            self.fail(f"Unexpected URL: {url}")

        with patch.object(github, "_fetch_json", side_effect=fetch):
            items = self._run()

        self.assertEqual([item["container"] for item in items], ["kurt/page-two"])
        self.assertTrue(any("&page=2" in url for url in requested_urls))

    def test_requests_page_after_three_full_event_pages(self):
        full_page = [
            self._event(
                i,
                event_type="WatchEvent",
                created_at="2026-07-24T12:00:00Z",
            )
            for i in range(github.PERSON_EVENTS_PER_PAGE)
        ]
        requested_pages = []

        def fetch(url, **kwargs):
            if "search/issues" in url:
                return {"total_count": 0, "items": []}
            page = int(url.rsplit("&page=", 1)[1])
            requested_pages.append(page)
            return full_page if page <= 3 else []

        with patch.object(github, "_fetch_json", side_effect=fetch):
            items = self._run()

        self.assertEqual(items, [])
        self.assertEqual(requested_pages, [1, 2, 3, 4])

    def test_stops_paging_at_event_older_than_window(self):
        requested_urls = []

        def fetch(url, **kwargs):
            requested_urls.append(url)
            if "search/issues" in url:
                return {"total_count": 0, "items": []}
            if "&page=1" in url:
                return [
                    self._event(1, event_type="WatchEvent"),
                    self._event(2, created_at="2026-06-24T23:59:59Z"),
                ]
            self.fail("Events paging continued after reaching an old event")

        with patch.object(github, "_fetch_json", side_effect=fetch):
            items = self._run()

        self.assertEqual(items, [])
        event_urls = [url for url in requested_urls if "/events/public" in url]
        self.assertEqual(len(event_urls), 1)

    def test_ranks_all_event_repos_before_applying_depth_cap(self):
        events = [
            self._event(1, repo="kurt/newest", created_at="2026-07-24T12:00:00Z"),
            self._event(2, repo="kurt/recent", created_at="2026-07-23T12:00:00Z"),
            self._event(3, repo="kurt/third", created_at="2026-07-22T12:00:00Z"),
            self._event(4, repo="kurt/high-star", created_at="2026-07-21T12:00:00Z"),
        ]
        stars = {
            "kurt/newest": 3,
            "kurt/recent": 2,
            "kurt/third": 1,
            "kurt/high-star": 10_000,
        }

        def repo_info(repo, token):
            return {
                "stars": stars[repo],
                "forks": 0,
                "description": "",
                "language": "Python",
                "open_issues": 0,
            }

        with patch.object(github, "_fetch_json", return_value=events), \
                patch.object(github, "_fetch_repo_info", side_effect=repo_info), \
                patch.object(github, "_enrich_own_repo", return_value={}) as enrich:
            items = github._person_recent_pushes(
                "kurt",
                "2026-06-25",
                "2026-07-25",
                {"own_repos": 3},
                "t",
            )

        containers = [item["container"] for item in items]
        self.assertIn("kurt/high-star", containers)
        self.assertNotIn("kurt/third", containers)
        self.assertEqual(enrich.call_count, 3)

    def test_aggregates_each_repo_at_its_latest_matching_push(self):
        events = [
            self._event(2, created_at="2026-07-24T12:00:00Z"),
            self._event(1, created_at="2026-07-20T12:00:00Z"),
        ]

        with patch.object(github, "_fetch_json", return_value=events), \
                patch.object(github, "_fetch_repo_info", return_value={}), \
                patch.object(github, "_enrich_own_repo", return_value={}):
            items = github._person_recent_pushes(
                "kurt",
                "2026-06-25",
                "2026-07-25",
                {"own_repos": 5},
                "t",
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["date"], "2026-07-24")
        self.assertEqual(items[0]["metadata"]["event_id"], "2")


if __name__ == "__main__":
    unittest.main()

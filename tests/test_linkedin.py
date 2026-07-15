import unittest
from unittest import mock

from lib.linkedin import (
    _best_author_match,
    _extract_posts,
    _int_field,
    _parse_date,
    enrich_articles,
    parse_linkedin_response,
    parse_profile_articles,
    search_linkedin,
)


class TestParseLinkedinResponse(unittest.TestCase):
    def _make_post(self, **overrides):
        # Mirrors the live ScrapeCreators /v1/linkedin/search/posts post object:
        # body in `description`, timestamp in `datePublished`, author as a nested
        # dict, engagement in `likeCount`/`commentCount`, comments as a list.
        base = {
            "url": "https://www.linkedin.com/posts/example_123",
            "datePublished": "2026-06-01T12:30:00.000Z",
            "description": "Excited to share our latest product update.",
            "author": {
                "name": "Jane Doe",
                "url": "https://www.linkedin.com/in/janedoe",
                "followers": 1234,
            },
            "comments": [{"author": "Bob", "text": "nice", "linkedinUrl": "x"}],
            "likeCount": 42,
            "commentCount": 5,
        }
        base.update(overrides)
        return base

    def test_real_shape_post_parses_all_fields(self):
        items = parse_linkedin_response({"posts": [self._make_post()]})
        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual("https://www.linkedin.com/posts/example_123", item["url"])
        self.assertEqual("Excited to share our latest product update.", item["text"])
        self.assertEqual("Jane Doe", item["author"])
        self.assertEqual("https://www.linkedin.com/in/janedoe", item["author_url"])
        self.assertEqual("2026-06-01", item["date"])
        self.assertEqual(42, item["engagement"]["likes"])
        self.assertEqual(5, item["engagement"]["comments"])
        self.assertFalse(item["is_article"])

    def test_description_and_datepublished_only_parses_nonempty(self):
        # Regression guard for the exact bug that shipped: the live API uses
        # `description` + `datePublished`, and a parser keyed on `text`/`date`
        # dropped every post (10 raw -> 0 items). This must stay non-empty.
        raw = {
            "description": "body via description field",
            "datePublished": "2026-06-10T09:00:00Z",
            "url": "https://www.linkedin.com/posts/abc",
        }
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual(1, len(items))
        self.assertEqual("body via description field", items[0]["text"])
        self.assertEqual("2026-06-10", items[0]["date"])

    def test_empty_posts_returns_empty_list(self):
        self.assertEqual([], parse_linkedin_response({"posts": []}))
        self.assertEqual([], parse_linkedin_response({}))

    def test_post_without_text_is_skipped(self):
        raw = self._make_post()
        del raw["description"]
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual([], items)

    def test_non_dict_post_is_skipped(self):
        items = parse_linkedin_response({"posts": ["not-a-dict", None]})
        self.assertEqual([], items)

    def test_author_as_dict(self):
        raw = self._make_post(author={"name": "Jane Doe", "full_name": "Jane M Doe"})
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual("Jane Doe", items[0]["author"])

    def test_author_dict_falls_back_to_full_name(self):
        raw = self._make_post(author={"full_name": "Jane M Doe"})
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual("Jane M Doe", items[0]["author"])

    def test_author_missing_defaults_to_empty_string(self):
        raw = self._make_post()
        del raw["author"]
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual("", items[0]["author"])

    def test_id_falls_back_to_generated_index(self):
        # The live post object carries no top-level id/urn; the parser
        # synthesizes a stable per-index id.
        raw = self._make_post()
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual("LI1", items[0]["id"])

    def test_explicit_urn_used_as_id(self):
        raw = self._make_post(urn="urn:li:activity:999")
        items = parse_linkedin_response({"posts": [raw]})
        self.assertEqual("urn:li:activity:999", items[0]["id"])

    def test_alternate_field_names(self):
        raw = {
            "postId": "p1",
            "content": "alternate field names",
            "postUrl": "https://example.com/p1",
            "authorName": "Alt Author",
            "postedAt": "2026-05-15T10:00:00Z",
            "likesCount": 10,
            "commentsCount": 3,
            "shares": 1,
        }
        items = parse_linkedin_response({"posts": [raw]})
        item = items[0]
        self.assertEqual("p1", item["id"])
        self.assertEqual("alternate field names", item["text"])
        self.assertEqual("https://example.com/p1", item["url"])
        self.assertEqual("Alt Author", item["author"])
        self.assertEqual("2026-05-15", item["date"])
        self.assertEqual(10, item["engagement"]["likes"])
        self.assertEqual(3, item["engagement"]["comments"])
        self.assertEqual(1, item["engagement"]["reposts"])


class TestExtractPosts(unittest.TestCase):
    def test_extracts_from_posts_key(self):
        self.assertEqual([{"a": 1}], _extract_posts({"posts": [{"a": 1}]}))

    def test_extracts_from_items_key(self):
        self.assertEqual([{"a": 1}], _extract_posts({"items": [{"a": 1}]}))

    def test_extracts_from_data_key(self):
        self.assertEqual([{"a": 1}], _extract_posts({"data": [{"a": 1}]}))

    def test_extracts_from_results_key(self):
        self.assertEqual([{"a": 1}], _extract_posts({"results": [{"a": 1}]}))

    def test_non_dict_response_returns_empty(self):
        self.assertEqual([], _extract_posts(["not", "a", "dict"]))
        self.assertEqual([], _extract_posts(None))

    def test_no_matching_key_returns_empty(self):
        self.assertEqual([], _extract_posts({"unexpected": [1, 2, 3]}))


class TestParseDate(unittest.TestCase):
    def test_extracts_date_from_iso_string(self):
        self.assertEqual("2026-06-01", _parse_date("2026-06-01T12:30:00Z"))

    def test_plain_date_string(self):
        self.assertEqual("2026-06-01", _parse_date("2026-06-01"))

    def test_none_returns_none(self):
        self.assertIsNone(_parse_date(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_date(""))

    def test_no_date_pattern_returns_none(self):
        self.assertIsNone(_parse_date("not a date"))


class TestIntField(unittest.TestCase):
    def test_returns_first_present_key(self):
        self.assertEqual(5, _int_field({"likes": 5, "likesCount": 10}, "likes", "likesCount"))

    def test_falls_back_to_second_key(self):
        self.assertEqual(10, _int_field({"likesCount": 10}, "likes", "likesCount"))

    def test_missing_all_keys_returns_zero(self):
        self.assertEqual(0, _int_field({}, "likes", "likesCount"))

    def test_non_numeric_value_returns_zero(self):
        self.assertEqual(0, _int_field({"likes": "not-a-number"}, "likes"))

    def test_string_numeric_value_is_coerced(self):
        self.assertEqual(7, _int_field({"likes": "7"}, "likes"))


class TestSearchLinkedin(unittest.TestCase):
    def test_no_token_returns_empty_without_network_call(self):
        with mock.patch("lib.linkedin.http.request") as mock_request:
            result = search_linkedin("AI agents", "2026-05-27", "2026-06-26", token="")
            self.assertEqual({"posts": []}, result)
            mock_request.assert_not_called()

    def test_successful_search_returns_capped_posts(self):
        raw_posts = [{"text": f"post {i}"} for i in range(20)]
        with mock.patch(
            "lib.linkedin.http.request", return_value={"posts": raw_posts}
        ):
            result = search_linkedin(
                "AI agents", "2026-05-27", "2026-06-26", depth="quick", token="fake-token"
            )
            # "quick" depth caps at 10 results per DEPTH_CONFIG
            self.assertEqual(10, len(result["posts"]))

    def test_http_error_returns_empty_with_error_message(self):
        from lib import http as http_module

        with mock.patch(
            "lib.linkedin.http.request",
            side_effect=http_module.HTTPError("rate limited", status_code=429),
        ):
            result = search_linkedin(
                "AI agents", "2026-05-27", "2026-06-26", token="fake-token"
            )
            self.assertEqual([], result["posts"])
            self.assertIn("error", result)


class TestDateRangeFiltering(unittest.TestCase):
    def test_filters_out_posts_outside_range(self):
        posts = [
            {"text": "in range", "date": "2026-06-15"},
            {"text": "too old", "date": "2026-01-01"},
        ]
        items = parse_linkedin_response(
            {"posts": posts}, from_date="2026-05-27", to_date="2026-06-26"
        )
        self.assertEqual(1, len(items))
        self.assertEqual("in range", items[0]["text"])

    def test_keeps_all_when_none_in_range(self):
        # Graceful fallback: SC doesn't always return a parseable date, so if
        # the filter would drop everything, keep the unfiltered set instead
        # of returning zero results.
        posts = [
            {"text": "old post", "date": "2026-01-01"},
            {"text": "older post", "date": "2025-12-01"},
        ]
        items = parse_linkedin_response(
            {"posts": posts}, from_date="2026-05-27", to_date="2026-06-26"
        )
        self.assertEqual(2, len(items))

    def test_no_filtering_when_dates_not_provided(self):
        posts = [{"text": "any date", "date": "2020-01-01"}]
        items = parse_linkedin_response({"posts": posts})
        self.assertEqual(1, len(items))

    def test_posts_without_date_excluded_from_in_range_but_not_counted_as_failure(self):
        posts = [
            {"text": "has date in range", "date": "2026-06-15"},
            {"text": "no date at all"},
        ]
        items = parse_linkedin_response(
            {"posts": posts}, from_date="2026-05-27", to_date="2026-06-26"
        )
        # Only the dated, in-range post survives; the undated one isn't
        # counted toward "in range" and gets dropped since in_range is non-empty.
        self.assertEqual(1, len(items))
        self.assertEqual("has date in range", items[0]["text"])


class TestArticleDetection(unittest.TestCase):
    def test_pulse_url_tagged_as_article_and_boosted(self):
        raw = {
            "description": "long-form piece",
            "datePublished": "2026-06-10",
            "url": "https://www.linkedin.com/pulse/wtf-is-a-loop-matt-van-horn-abc",
        }
        item = parse_linkedin_response({"posts": [raw]})[0]
        self.assertTrue(item["is_article"])
        self.assertEqual(0.9, item["relevance"])

    def test_posts_url_not_article_default_relevance(self):
        raw = {
            "description": "ordinary status update",
            "datePublished": "2026-06-10",
            "url": "https://www.linkedin.com/posts/example_123",
        }
        item = parse_linkedin_response({"posts": [raw]})[0]
        self.assertFalse(item["is_article"])
        self.assertEqual(0.5, item["relevance"])


class TestProfileArticles(unittest.TestCase):
    def _profile(self, **overrides):
        base = {
            "name": "Matt Van Horn",
            "articles": [
                {
                    "headline": "WTF Is a Loop? Part 2",
                    "url": "https://www.linkedin.com/pulse/wtf-loop-part-2-abc",
                    "datePublished": "2026-06-20T21:06:18.000+00:00",
                    "articleBody": "",
                },
                {
                    "headline": "Every Agentic Engineering Hack I Know",
                    "url": "https://www.linkedin.com/pulse/every-hack-def",
                    "datePublished": "2026-06-04T02:31:36.000+00:00",
                    "articleBody": "",
                },
            ],
        }
        base.update(overrides)
        return base

    def test_articles_parse_as_high_signal(self):
        items = parse_profile_articles(self._profile())
        self.assertEqual(2, len(items))
        for it in items:
            self.assertTrue(it["is_article"])
            self.assertEqual(0.9, it["relevance"])
            self.assertEqual("Matt Van Horn", it["author"])
        self.assertEqual("WTF Is a Loop? Part 2", items[0]["text"])

    def test_articles_respect_date_range(self):
        items = parse_profile_articles(
            self._profile(), from_date="2026-06-15", to_date="2026-06-26"
        )
        self.assertEqual(1, len(items))
        self.assertEqual("WTF Is a Loop? Part 2", items[0]["text"])

    def test_empty_or_missing_articles(self):
        self.assertEqual([], parse_profile_articles({"name": "X"}))
        self.assertEqual([], parse_profile_articles({"name": "X", "articles": []}))

    def test_article_without_headline_skipped(self):
        prof = {"name": "X", "articles": [{"url": "u", "datePublished": "2026-06-10"}]}
        self.assertEqual([], parse_profile_articles(prof))


class TestBestAuthorMatch(unittest.TestCase):
    def test_exact_person_topic_matches(self):
        items = [{"author": "Matt Van Horn", "author_url": "u-mvh"}]
        self.assertEqual("u-mvh", _best_author_match(items, "Matt Van Horn"))

    def test_name_contained_in_longer_topic(self):
        items = [{"author": "Matt Van Horn", "author_url": "u-mvh"}]
        self.assertEqual(
            "u-mvh", _best_author_match(items, "what is Matt Van Horn building")
        )

    def test_single_word_topic_never_enriches(self):
        # "AI" is one token — a keyword topic, not a person.
        items = [{"author": "Daisuke Tanaka", "author_url": "u-dt"}]
        self.assertEqual("", _best_author_match(items, "AI"))

    def test_no_substring_false_positive_across_token_boundaries(self):
        # Regression for the Greptile finding: a topic token must not match
        # inside an unrelated author's name. "ai" must not hit "daisuke".
        items = [{"author": "Daisuke Tanaka", "author_url": "u-dt"}]
        self.assertEqual("", _best_author_match(items, "AI agents"))

    def test_picks_matching_author_not_first(self):
        items = [
            {"author": "Eric Siu", "author_url": "u-eric"},
            {"author": "Matt Van Horn", "author_url": "u-mvh"},
        ]
        self.assertEqual("u-mvh", _best_author_match(items, "Matt Van Horn loops"))


class TestEnrichArticles(unittest.TestCase):
    def _person_items(self):
        # Parsed post items as enrich_articles receives them: a person-topic
        # search returns posts authored by the subject.
        return [
            {"author": "Eric Siu", "author_url": "https://www.linkedin.com/in/ericosiu"},
            {"author": "Matt Van Horn", "author_url": "https://www.linkedin.com/in/mattvanhorn"},
        ]

    def test_person_topic_enriches_via_one_profile_call(self):
        profile = {
            "name": "Matt Van Horn",
            "articles": [
                {"headline": "WTF Is a Loop?", "url": "https://www.linkedin.com/pulse/a", "datePublished": "2026-06-20"},
            ],
        }
        with mock.patch(
            "lib.linkedin.http.request", return_value=profile
        ) as mock_request:
            arts = enrich_articles(
                self._person_items(), "Matt Van Horn", token="fake",
                from_date="2026-05-27", to_date="2026-06-26",
            )
            self.assertEqual(1, len(arts))
            self.assertTrue(arts[0]["is_article"])
            self.assertEqual(0.9, arts[0]["relevance"])
            # Bounded: exactly one profile call.
            self.assertEqual(1, mock_request.call_count)
            # And it fetched the matching author's profile, not the first author.
            called_profile = mock_request.call_args.kwargs["params"]["url"]
            self.assertIn("mattvanhorn", called_profile)

    def test_keyword_topic_makes_no_profile_call(self):
        with mock.patch("lib.linkedin.http.request") as mock_request:
            arts = enrich_articles(
                self._person_items(), "AI agents", token="fake",
                from_date="2026-05-27", to_date="2026-06-26",
            )
            self.assertEqual([], arts)
            mock_request.assert_not_called()

    def test_no_token_no_call(self):
        with mock.patch("lib.linkedin.http.request") as mock_request:
            arts = enrich_articles(self._person_items(), "Matt Van Horn", token="")
            self.assertEqual([], arts)
            mock_request.assert_not_called()

    def test_profile_error_returns_empty(self):
        from lib import http as http_module

        with mock.patch(
            "lib.linkedin.http.request",
            side_effect=http_module.HTTPError("boom", status_code=500),
        ):
            arts = enrich_articles(self._person_items(), "Matt Van Horn", token="fake")
            self.assertEqual([], arts)

    def test_no_author_url_no_call(self):
        items = [{"author": "Matt Van Horn", "author_url": ""}]
        with mock.patch("lib.linkedin.http.request") as mock_request:
            arts = enrich_articles(items, "Matt Van Horn", token="fake")
            self.assertEqual([], arts)
            mock_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

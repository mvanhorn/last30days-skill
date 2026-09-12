"""Tests for scripts/lib/reddit_shreddit.py — keyless shreddit comment scrape."""

from pathlib import Path
from unittest import mock

from lib import reddit_shreddit as rs

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_shreddit_comments_sample.html"


def _html():
    return FIXTURE.read_text(encoding="utf-8")


# The same shreddit markup as fetched through a real browser (the Scrapling
# fallback): the DOM lowercases every attribute NAME, so ``thingId`` -> ``thingid``.
# The parser must read this shape too, or every comment loses its body and drops.
BROWSER_SHAPED_HTML = """
<shreddit-comment-tree-stats total-comments="2" sort="TOP"></shreddit-comment-tree-stats>
<shreddit-comment created="2026-08-09T12:45:51+0000" author="alice" thingid="t1_aaa"
  permalink="/r/videos/comments/x1/comment/aaa/" score="1136" depth="0">
  <div id="t1_aaa-post-rtjson-content"><div><p>First body about $750 pending.</p></div></div>
</shreddit-comment>
<shreddit-comment created="2026-08-09T12:46:00+0000" author="bob" thingid="t1_bbb"
  permalink="/r/videos/comments/x1/comment/bbb/" score="42" depth="0">
  <div id="t1_bbb-post-rtjson-content"><div><p>Second body.</p></div></div>
</shreddit-comment>
"""


class TestExtractPostRef:
    def test_extracts_sub_and_id(self):
        ref = rs.extract_post_ref("https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/")
        assert ref == ("Rakuten", "1taeiw0")

    def test_non_thread_url_returns_none(self):
        assert rs.extract_post_ref("https://www.reddit.com/r/Rakuten/") is None
        assert rs.extract_post_ref("") is None

    def test_svc_url_shape(self):
        # sort=top guarantees the highest-scored comments land on page 1.
        assert rs._svc_url("Rakuten", "1taeiw0") == (
            "https://www.reddit.com/svc/shreddit/comments/r/Rakuten/t3_1taeiw0?sort=top"
        )


class TestParseComments:
    """parse_comments reads <shreddit-comment> elements into scored dicts."""

    def test_happy_path(self):
        comments = rs.parse_comments(_html())
        assert len(comments) >= 1
        for c in comments:
            assert isinstance(c["score"], int)
            assert c["author"] and c["author"] not in ("[deleted]", "[removed]")
            assert c["body"]

    def test_sorted_by_score_desc(self):
        scores = [c["score"] for c in rs.parse_comments(_html())]
        assert scores == sorted(scores, reverse=True)

    def test_deleted_and_removed_filtered(self):
        authors = [c["author"] for c in rs.parse_comments(_html())]
        assert "[deleted]" not in authors and "[removed]" not in authors

    def test_negative_score_retained(self):
        scores = [c["score"] for c in rs.parse_comments(_html())]
        assert -7 in scores  # synthetic downvoted-but-real comment

    def test_limit_honored(self):
        assert len(rs.parse_comments(_html(), limit=2)) == 2

    def test_body_text_extracted(self):
        bodies = [c["body"] for c in rs.parse_comments(_html())]
        assert any("$750" in b or "pending" in b for b in bodies)

    def test_comment_url_built(self):
        for c in rs.parse_comments(_html()):
            if c["url"]:
                assert c["url"].startswith("https://reddit.com/r/")

    def test_empty_html_returns_empty(self):
        assert rs.parse_comments("") == []
        assert rs.parse_comments("<html>no comments here</html>") == []

    def test_browser_lowercased_attrs_parsed(self):
        # Regression guard for the Scrapling fallback: browser-serialized markup
        # carries `thingid` (lowercased), which must still yield full comments.
        comments = rs.parse_comments(BROWSER_SHAPED_HTML)
        assert [c["score"] for c in comments] == [1136, 42]
        assert comments[0]["author"] == "alice"
        assert "$750" in comments[0]["body"]


class TestBotFilter:
    """Bot comments occupy top-comment slots without carrying community signal."""

    @staticmethod
    def _comment_html(author, thing_id="t1_botfilter1", score=999):
        return (
            f'<shreddit-comment author="{author}" thingId="{thing_id}" '
            f'score="{score}" permalink="/r/test/comments/1/x/{thing_id}/">'
            f'</shreddit-comment>'
            f'<div id="{thing_id}-post-rtjson-content">'
            f'<p>I will be messaging you in 3 days to remind you of this link.</p>'
            f'</div>'
        )

    def test_known_bots_dropped(self):
        for bot in ("RemindMeBot", "AutoModerator", "sneakpeekbot"):
            assert rs.parse_comments(self._comment_html(bot)) == [], bot

    def test_bot_match_is_case_insensitive(self):
        assert rs.parse_comments(self._comment_html("remindmebot")) == []

    def test_separator_suffix_bots_dropped(self):
        for bot in ("some-random-bot", "subreddit_bot"):
            assert rs.parse_comments(self._comment_html(bot)) == [], bot

    def test_camelcase_bots_dropped(self):
        # The separator-free convention is the common one on Reddit.
        for bot in ("WikiTextBot", "RepostSleuthBot", "RemindMeBot2"):
            assert rs.parse_comments(self._comment_html(bot)) == [], bot

    def test_human_authors_kept(self):
        # Names merely ending in "bot" are people, not bots. The capital B in
        # the camelCase rule is what separates "WikiTextBot" from "Talbot".
        for human in ("Talbot", "abbot", "u_bothell_local", "MSRS-",
                      "TheBotanist", "Botany101", "Robotics_fan"):
            out = rs.parse_comments(self._comment_html(human))
            assert len(out) == 1, human
            assert out[0]["author"] == human

    def test_bot_does_not_displace_human_from_slot(self):
        # The reported failure was a bot *taking a slot*, not merely appearing:
        # it outscores the humans, so it wins the ranking before truncation.
        html = (self._comment_html("RemindMeBot", thing_id="t1_bot", score=999)
                + self._comment_html("real_person", thing_id="t1_human", score=5))
        out = rs.parse_comments(html, limit=1)
        assert [c["author"] for c in out] == ["real_person"]

    def test_is_bot_author_handles_blank(self):
        assert rs._is_bot_author("") is False
        assert rs._is_bot_author(None) is False


class TestTotalComments:
    def test_reads_total(self):
        assert rs._total_comments(_html()) == 14

    def test_missing_returns_none(self):
        assert rs._total_comments("<html></html>") is None



def _keyless_403(*_args, **_kwargs):
    """Stand-in for ``http.reddit_keyless_get_text`` when Reddit refuses the GET:
    ``get_text`` swallows the HTTPError and returns None, but the 403 has already
    been recorded in the context-local failure sink -- exactly what the browser
    fallback keys on."""
    rs.http._record_failure(rs.http.HTTPError("HTTP 403: Blocked", status_code=403))
    return None


def _keyless_miss(status):
    """A keyless miss that is NOT Reddit's block wall (5xx, 429, ...)."""
    def _side_effect(*_args, **_kwargs):
        rs.http._record_failure(rs.http.HTTPError(f"HTTP {status}", status_code=status))
        return None
    return _side_effect


class TestFetchComments:
    """fetch_comments wires URL -> svc fetch -> parse, never raising."""

    def test_happy_path(self):
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=_html()) as m:
            out = rs.fetch_comments(url)
        # svc endpoint, not .json
        assert "/svc/shreddit/comments/" in m.call_args[0][0]
        assert ".json" not in m.call_args[0][0]
        assert out["num_comments"] == 14
        assert len(out["top_comments"]) >= 1
        first = out["top_comments"][0]
        assert {"score", "date", "author", "excerpt", "url"} <= set(first.keys())
        assert isinstance(out["comment_insights"], list)

    def test_bad_url_returns_empty(self):
        out = rs.fetch_comments("https://www.reddit.com/r/Rakuten/")
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_fetch_failure_returns_empty(self):
        # Keyless HTTP fails AND Scrapling is unavailable (the CI/Cowork case):
        # behaves exactly as before the fallback existed. is_available is forced
        # False so the test is deterministic on a dev box where scrapling is on PATH.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=False):
            out = rs.fetch_comments(url)
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_scrapling_fallback_used_when_keyless_blocked(self):
        # Keyless HTTP returns nothing (403'd); Scrapling is installed and its
        # browser fetch returns the same (lowercased-attr) markup -> comments.
        url = "https://www.reddit.com/r/videos/comments/1vjokkz/title/"
        with mock.patch.object(rs.http, "reddit_keyless_get_text", side_effect=_keyless_403), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch", return_value=BROWSER_SHAPED_HTML) as sf:
            out = rs.fetch_comments(url)
        # stealthy-fetch, html format, on the svc URL
        assert sf.call_args.kwargs["mode"] == rs.scrapling_fetch.MODE_STEALTHY
        assert sf.call_args.kwargs["fmt"] == "html"
        assert "/svc/shreddit/comments/" in sf.call_args[0][0]
        assert [c["score"] for c in out["top_comments"]] == [1136, 42]
        assert out["num_comments"] == 2

    def test_scrapling_not_called_when_keyless_succeeds(self):
        # No regression: when the HTTP path works, the browser fallback is never
        # invoked (it is slow and should stay a last resort).
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "get_text", return_value=_html()), \
                mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
            out = rs.fetch_comments(url)
        sf.assert_not_called()
        assert len(out["top_comments"]) >= 1

    def test_scrapling_not_called_on_non_403_miss(self):
        # Greptile (#976): the fallback must key on Reddit's 403 block wall, not
        # on "the HTTP path returned nothing". A 5xx or a 429 is not something a
        # real browser clears, so the slow browser launch must stay off.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        for status in (500, 502, 429):
            with mock.patch.object(rs.http, "reddit_keyless_get_text",
                                   side_effect=_keyless_miss(status)), \
                    mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                    mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
                out = rs.fetch_comments(url)
            sf.assert_not_called()
            assert out["top_comments"] == [] and out["num_comments"] is None

    def test_scrapling_not_called_when_keyless_gives_up_silently(self):
        # reddit_keyless_get_text can return None without recording any failure
        # (memo election gave up, or a timeout with no status): no 403, no browser.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "reddit_keyless_get_text", return_value=None), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
            out = rs.fetch_comments(url)
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_scrapling_not_called_on_empty_200_body(self):
        # An empty-but-successful body is a parse miss, not a block.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "reddit_keyless_get_text", return_value=""), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
            out = rs.fetch_comments(url)
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_scrapling_absent_no_fallback(self):
        # Keyless fails and scrapling is not installed: no fetch attempt, empty out.
        url = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"
        with mock.patch.object(rs.http, "reddit_keyless_get_text", side_effect=_keyless_403), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=False), \
                mock.patch.object(rs.scrapling_fetch, "fetch") as sf:
            out = rs.fetch_comments(url)
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None

class TestEnrichmentBudget:
    """Busy topics enrich more threads and carry more comments per thread."""

    def test_enrich_limits_by_depth(self):
        assert rs.ENRICH_LIMITS == {"quick": 4, "default": 8, "deep": 12}

    def test_parse_comments_returns_up_to_twelve(self):
        html = "".join(
            f'<shreddit-comment author="user{i}" thingId="t1_c{i}" score="{100 - i}" '
            f'permalink="/r/test/comments/1/x/t1_c{i}/"></shreddit-comment>'
            f'<div id="t1_c{i}-post-rtjson-content"><p>comment number {i} body text</p></div>'
            for i in range(30)
        )
        out = rs.parse_comments(html)
        assert len(out) == rs.MAX_COMMENTS == 12
        assert [c["score"] for c in out] == list(range(100, 88, -1))


class TestScraplingDeadline:
    """The browser fallback must respect the caller's aggregate enrichment
    budget: its subprocess timeout is capped to the time remaining before
    ``deadline`` (so run_with_timeout kills the browser at the budget edge
    instead of outliving a cancelled future), and it is skipped outright
    when too little budget remains to be worth a browser launch."""

    URL = "https://www.reddit.com/r/videos/comments/1vjokkz/title/"

    def _fetch(self, deadline, now=1000.0):
        with mock.patch.object(rs.http, "reddit_keyless_get_text", side_effect=_keyless_403), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch",
                                  return_value=BROWSER_SHAPED_HTML) as sf, \
                mock.patch.object(rs.time, "monotonic", return_value=now):
            out = rs.fetch_comments(self.URL, deadline=deadline)
        return out, sf

    def test_timeout_capped_to_remaining_budget(self):
        # 30s left of a 45s budget -> the 75s default must shrink to fit, and
        # it must NOT be handed the whole 30s: run_with_timeout's SIGTERM ->
        # wait(5) -> SIGKILL -> wait(5) cleanup runs AFTER the timeout fires,
        # so a subprocess given the full remainder outlives the deadline by up
        # to ten seconds (Greptile, #976). The cap reserves that grace.
        out, sf = self._fetch(deadline=1030.0)
        assert sf.call_args.kwargs["timeout"] < 30
        assert sf.call_args.kwargs["timeout"] + rs.SCRAPLING_CLEANUP_GRACE <= 30
        assert [c["score"] for c in out["top_comments"]] == [1136, 42]

    def test_skipped_when_only_cleanup_grace_remains(self):
        # Enough seconds for a browser launch on paper, but not once the
        # process-group cleanup is reserved: skip rather than overrun.
        out, sf = self._fetch(
            deadline=1000.0 + rs.SCRAPLING_MIN_BUDGET + rs.SCRAPLING_CLEANUP_GRACE - 1
        )
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_ample_budget_keeps_default_timeout(self):
        # More time left than the default: never widen beyond SCRAPLING_TIMEOUT.
        _, sf = self._fetch(deadline=1000.0 + rs.SCRAPLING_TIMEOUT + 60)
        assert sf.call_args.kwargs["timeout"] == rs.SCRAPLING_TIMEOUT

    def test_skipped_when_budget_nearly_spent(self):
        # Below the minimum useful window a browser launch can only burn the
        # tail of the budget -> no fetch attempt, empty result.
        out, sf = self._fetch(deadline=1000.0 + rs.SCRAPLING_MIN_BUDGET - 1)
        sf.assert_not_called()
        assert out["top_comments"] == [] and out["num_comments"] is None

    def test_no_deadline_keeps_default_timeout(self):
        with mock.patch.object(rs.http, "reddit_keyless_get_text", side_effect=_keyless_403), \
                mock.patch.object(rs.scrapling_fetch, "is_available", return_value=True), \
                mock.patch.object(rs.scrapling_fetch, "fetch",
                                  return_value=BROWSER_SHAPED_HTML) as sf:
            rs.fetch_comments(self.URL)
        assert sf.call_args.kwargs["timeout"] == rs.SCRAPLING_TIMEOUT

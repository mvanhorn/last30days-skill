"""GitHub keyword search keeps only results that are about the topic.

GitHub's issue search matches on body text, so a non-tech topic pulls in
keyword-stuffed spam issues and incidental mentions in unrelated repos. Their
search rank is high, so a rank-weighted score cannot tell them from real hits.
A result must name the topic in its title or live in a repo named for it.
"""

from unittest import mock

from lib import github, pipeline, schema

_FROM = "2026-10-11"
_TO = "2026-11-10"


def _raw(repo, number, title, *, body="", reactions=0, comments=0, pr=False):
    kind = "pull" if pr else "issues"
    item = {
        "id": hash((repo, number)),
        "html_url": f"https://github.com/{repo}/{kind}/{number}",
        "title": title,
        "body": body,
        "created_at": "2026-10-20T10:00:00Z",
        "state": "open",
        "comments": comments,
        "reactions": {"total_count": reactions},
        "labels": [],
        "user": {"login": "someone"},
    }
    if pr:
        item["pull_request"] = {"url": "..."}
    return item


def _envelope(core, raw_items):
    return {
        "items": raw_items,
        "context": {"core": core, "from_date": _FROM, "to_date": _TO, "count": 25},
    }


_KANYE_RAW = [
    _raw(
        "qa-helpdesk-7/tickets", 8922,
        "Bug report: MESSAGE #L8922 — THE PROMOCODE — $5000 LEARN MORE",
        body="kanye west kanye west promo code claim now",
        reactions=40,
    ),
    _raw(
        "qa-helpdesk-7/tickets", 9285,
        "Bug report: ticket №F9285 ★ PROMO CODE ◆ $5000 CHECK",
        body="kanye west bonus promo",
        reactions=35,
    ),
    _raw(
        "stablehand/horse-counter", 12,
        "Weekly count is off by one for the north paddock",
        body="Repro: name two horses Kanye and West, then run the weekly count.",
        comments=3,
    ),
]


def test_non_tech_topic_with_spam_and_incidental_matches_returns_nothing():
    items = github.parse_github_response(
        _envelope("kanye west", _KANYE_RAW), topic="Kanye West",
    )
    assert items == []


def test_floor_applies_without_an_explicit_topic():
    """The search's own subject is the fallback when no topic is passed."""
    assert github.parse_github_response(_envelope("kanye west", _KANYE_RAW)) == []


def test_floor_does_not_depend_on_result_rank_or_engagement():
    spam_first = _raw(
        "qa-helpdesk-7/tickets", 1,
        "Bug report: MESSAGE #L8922 — THE PROMOCODE — $5000 LEARN MORE",
        reactions=5000, comments=900,
    )
    real_last = _raw("yeezy-fans/discography", 4, "Add Kanye West 2026 tour dates")
    filler = [
        _raw("qa-helpdesk-7/tickets", n, f"Bug report: ticket #{n} PROMO CODE")
        for n in range(2, 22)
    ]
    items = github.parse_github_response(
        _envelope("kanye west", [spam_first, *filler, real_last]), topic="Kanye West",
    )
    assert [i["url"] for i in items] == [
        "https://github.com/yeezy-fans/discography/issues/4",
    ]


def test_tech_topic_named_in_titles_is_still_returned():
    raw = [
        _raw("anthropics/claude-code", 4101,
             "Claude Code hangs after resuming a long session", reactions=30),
        _raw("microsoft/vscode", 880,
             "Terminal flickers when Claude Code streams output", comments=6),
        _raw("vercel/next.js", 77,
             "docs: add a Claude Code setup guide", pr=True),
        _raw("community/community", 150,
             "Discussion: is Claude Code worth it for monorepos?", comments=40),
    ]
    items = github.parse_github_response(
        _envelope("claude code", raw), topic="Claude Code",
    )
    assert {i["url"] for i in items} == {
        "https://github.com/anthropics/claude-code/issues/4101",
        "https://github.com/microsoft/vscode/issues/880",
        "https://github.com/vercel/next.js/pull/77",
        "https://github.com/community/community/issues/150",
    }


def test_results_in_the_topics_own_repo_are_returned_whatever_the_title():
    raw = [
        _raw("openclaw/openclaw", 501, "Gateway drops websocket on restart"),
        _raw("openclaw/openclaw", 502, "Fix flaky cron scheduler test", pr=True),
    ]
    items = github.parse_github_response(_envelope("openclaw", raw), topic="OpenClaw")
    assert len(items) == 2


def test_results_in_the_topics_org_are_returned_whatever_the_title():
    raw = [_raw("vercel/next.js", 9001, "Turbopack build fails on Windows paths")]
    items = github.parse_github_response(_envelope("vercel", raw), topic="Vercel")
    assert len(items) == 1


def test_hyphenated_repo_name_matches_a_multi_word_topic():
    raw = [_raw("anthropics/claude-code", 4102, "Hooks stop firing after compaction")]
    items = github.parse_github_response(
        _envelope("claude code", raw), topic="Claude Code",
    )
    assert len(items) == 1


# ---- the pipeline stream ----

def _github_stream(topic, search_query, raw_items):
    subquery = schema.SubQuery(
        label="primary", search_query=search_query,
        ranking_query=topic, sources=["github"], weight=1.0,
    )
    with mock.patch.object(github, "_resolve_token", return_value="t"), \
            mock.patch.object(github, "resolve_token", return_value="t"), \
            mock.patch.object(
                github, "_fetch_json",
                side_effect=lambda url, **kw: (
                    {"items": raw_items} if "is%3Aissue" in url else {"items": []}
                ),
            ), \
            mock.patch.object(
                github, "enrich_with_comments", side_effect=lambda items, **kw: items,
            ):
        return pipeline._retrieve_stream_impl(
            topic=topic, subquery=subquery, source="github",
            config={}, depth="quick", date_range=(_FROM, _TO),
            runtime=mock.Mock(), mock=False, raw_topic=topic,
        )


def test_stream_reports_an_off_topic_search_as_empty_not_as_an_error():
    items, artifact = _github_stream("Kanye West", "kanye west bully", _KANYE_RAW)
    assert items == []
    # No outcome artifact means the stream is recorded as a clean no-results
    # run; an error would carry a `_source_outcome` here.
    assert artifact == {}


def test_stream_judges_against_the_research_topic_not_the_narrowed_subquery():
    """A planner subquery adds angle words the title need not repeat."""
    raw = [_raw("microsoft/vscode", 881, "Claude loses the terminal session on reload")]
    items, artifact = _github_stream(
        "Claude Code", "claude code hooks subagents worktrees release", raw,
    )
    assert [i["url"] for i in items] == ["https://github.com/microsoft/vscode/issues/881"]
    assert artifact == {}


# ---- person and project modes take no topical floor ----

def _refuse(*args, **kwargs):
    raise AssertionError("the topical floor must not run in person or project mode")


def test_person_mode_does_not_apply_the_topical_floor():
    event = {
        "id": "1",
        "type": "PushEvent",
        "actor": {"login": "kurt"},
        "repo": {"name": "stablehand/horse-counter"},
        "created_at": "2026-10-22T20:28:18Z",
    }

    def fetch(url, **kwargs):
        return None if "search/issues" in url else [event]

    with mock.patch.object(github, "_clears_topical_floor", _refuse, create=True), \
            mock.patch.object(github, "_resolve_token", return_value="t"), \
            mock.patch.object(github, "_enrich_own_repo", return_value={}), \
            mock.patch.object(github, "_fetch_repo_info", return_value={
                "stars": 3, "forks": 0, "description": "Counts horses",
                "language": "Python", "open_issues": 1,
            }), \
            mock.patch.object(github, "_fetch_json", side_effect=fetch):
        items = github.search_github_person("kurt", _FROM, _TO, token="t")

    assert [i["container"] for i in items] == ["stablehand/horse-counter"]


def test_project_mode_does_not_apply_the_topical_floor():
    enrichment = {
        "info": {"stars": 12, "open_issues": 2, "description": "Counts horses",
                 "language": "Python"},
        "readme": "Counts horses per paddock.",
        "releases": [{"name": "v1.2", "date": "2026-10-30", "body": ""}],
        "top_issues": {},
    }
    with mock.patch.object(github, "_clears_topical_floor", _refuse, create=True), \
            mock.patch.object(github, "_resolve_token", return_value="t"), \
            mock.patch.object(github, "_enrich_project_repo", return_value=enrichment):
        items = github.search_github_project(["stablehand/horse-counter"], _FROM, _TO)

    assert [i["container"] for i in items] == ["stablehand/horse-counter"]

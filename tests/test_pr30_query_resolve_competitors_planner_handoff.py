"""Regression tests for PR 30 (HIGH-088, HIGH-089, HIGH-090, HIGH-092, HIGH-093).

Covers:
- HIGH-088: Word-bounded compound term extraction in query.py and short-entity
  quoting in planner._keyword_query (no PascalCase mid-word slicing, no
  verbatim quoting of full Title-Cased prompts).
- HIGH-089: Anchored subreddit, X/Twitter handle, and GitHub regexes in
  resolve.py (no *x.com false positives, no email @handle leaks, no slash-pair
  or non-Reddit /r/ false subreddits).
- HIGH-090: Aligned brand tokenization and stopword exclusion in competitors.py
  so punctuated brands filter themselves without blacklisting real peers.
- HIGH-092: Requested/available source intersection and Hebrew grounding source
  boundary enforcement in planner._fallback_plan.
- HIGH-093: Non-finite worthiness, invalid UTF-8 files, and malformed nested
  bundle/pending fields in discovery_handoff.py.
"""

from __future__ import annotations

import io
import json
import math
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from lib import competitors, discovery_handoff as handoff, pipeline, planner, query, resolve, schema


# ---------------------------------------------------------------------------
# HIGH-088: query.extract_compound_terms & planner._keyword_query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "topic,expected_phrase,forbidden_slice",
    [
        ("GitHub Actions", "GitHub Actions", "Hub Actions"),
        ("YouTube Shorts", "YouTube Shorts", "Tube Shorts"),
        ("TypeScript Compiler", "TypeScript Compiler", "Script Compiler"),
        ("OpenClaw Agent", "OpenClaw Agent", "Claw Agent"),
        ("LangChain Agents", "LangChain Agents", "Chain Agents"),
        ("DeepSeek Coder", "DeepSeek Coder", "Seek Coder"),
        ("MacBook Pro", "MacBook Pro", "Book Pro"),
        ("LinkedIn Jobs", "LinkedIn Jobs", "In Jobs"),
        ("PyTorch Lightning", "PyTorch Lightning", "Torch Lightning"),
        ("OpenAI Codex", "OpenAI Codex", "AI Codex"),
        ("iPhone Pro", "iPhone Pro", "Phone Pro"),
        ("macOS Sequoia", "macOS Sequoia", "OS Sequoia"),
        ("xAI Grok", "xAI Grok", "AI Grok"),
    ],
)
def test_high088_pascal_and_camel_case_brands_never_split_mid_word(
    topic: str, expected_phrase: str, forbidden_slice: str,
):
    compounds = query.extract_compound_terms(topic)
    assert expected_phrase in compounds
    assert forbidden_slice not in compounds

    core = query.extract_core_subject(topic, max_words=6, strip_suffixes=True)
    kw = planner._keyword_query(topic, core)
    assert f'"{expected_phrase}"' in kw
    assert f'"{forbidden_slice}"' not in kw
    # Phrase words must not be duplicated unquoted alongside the quoted phrase.
    assert kw == f'"{expected_phrase}"'


@pytest.mark.parametrize("brand", ["OpenAI", "GitHub", "MacBook", "DeepSeek", "iPhone"])
def test_high088_single_brand_token_yields_no_compound(brand: str):
    assert query.extract_compound_terms(brand) == []
    assert planner._keyword_query(brand, brand.lower()) == brand.lower()


@pytest.mark.parametrize(
    "prompt",
    [
        "Best AI Coding Tools For Python Developers",
        "Best AI Coding Tools for Python Developers",
        "Hermes Agent Actual Use Cases",
        "What Are The Best Claude Code Alternatives",
        "What Are the Best Claude Code Alternatives",
        "How To Use GitHub Actions Today",
        "How to Build AI Agents with Python",
    ],
)
def test_high088_full_title_cased_prompts_not_quoted_verbatim(prompt: str):
    compounds = query.extract_compound_terms(prompt)
    assert prompt not in compounds
    assert compounds == []

    core = query.extract_core_subject(prompt, max_words=6, strip_suffixes=True)
    kw = planner._keyword_query(prompt, core)
    assert f'"{prompt}"' not in kw
    assert '"' not in kw


@pytest.mark.parametrize(
    "topic,forbidden_slice",
    [
        ("Claude Résumé", "Claude R"),
        ("OpenAI Café", "OpenAI Caf"),
        ("Next.JS Framework", "JS Framework"),
        ("OpenAI Next.JS", "OpenAI Next"),
        ("Visual C++", "Visual C"),
        ("Visual C#", "Visual C"),
        ("O\u2019Reilly Media", "Reilly Media"),
        ("O'Reilly Media", "Reilly Media"),
        ("AT&T Wireless", "T Wireless"),
    ],
)
def test_high088_unicode_dotted_curly_apostrophe_and_symbol_brands_never_sliced(
    topic: str, forbidden_slice: str,
):
    compounds = query.extract_compound_terms(topic)
    assert forbidden_slice not in compounds
    assert compounds == []

    core = query.extract_core_subject(topic, max_words=6, strip_suffixes=True)
    kw = planner._keyword_query(topic, core)
    assert f'"{forbidden_slice}"' not in kw


def test_high088_sentence_trailing_period_still_allows_valid_brand():
    assert query.extract_compound_terms("Check out GitHub Actions.") == ["GitHub Actions"]
    assert query.extract_compound_terms("DeepSeek R1") == ["DeepSeek R1"]


def test_high088_mixed_case_topics_strip_edge_noise_and_cap_length():
    assert query.extract_compound_terms("Best GitHub Actions workflows for Python") == [
        "GitHub Actions"
    ]
    assert query.extract_compound_terms("Claude Code Use Cases in production") == [
        "Claude Code"
    ]
    assert query.extract_compound_terms("Best React Native Tips for beginners") == [
        "React Native"
    ]
    assert query.extract_compound_terms("React Server Components vs Astro Islands") == [
        "React Server Components",
        "Astro Islands",
    ]
    # Runs longer than 3 words after edge trimming are not quoted as entities.
    assert query.extract_compound_terms(
        "Alpha Beta Gamma Delta Epsilon in lowercase text"
    ) == []


def test_high088_hyphenated_compound_not_sliced_into_title_case_phrase():
    compounds = query.extract_compound_terms("Multi-Agent Systems")
    assert "Multi-Agent" in compounds
    assert "Agent Systems" not in compounds
    kw = planner._keyword_query("Multi-Agent Systems", "multi-agent systems")
    assert '"Agent Systems"' not in kw
    assert '"' not in kw


def test_high088_keyword_query_guards_against_overlong_or_hyphenated_compounds():
    with mock.patch.object(
        query,
        "extract_compound_terms",
        return_value=[
            "Best AI Coding Tools For Python Developers",
            "multi-agent",
            "GitHub Actions",
        ],
    ):
        kw = planner._keyword_query(
            "GitHub Actions multi-agent",
            "github actions multi-agent",
        )
    assert kw == '"GitHub Actions" multi-agent'


# ---------------------------------------------------------------------------
# HIGH-089: resolve.py anchored regexes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        {"title": "Profile", "snippet": "user/foo editor/bar", "url": ""},
        {"title": "Perf", "snippet": "server/client better/faster", "url": ""},
        {"title": "User", "snippet": "", "url": "https://www.reddit.com/user/johndoe"},
        {"title": "Repo", "snippet": "", "url": "https://github.com/owner/repo"},
        {"title": "Tweet", "snippet": "", "url": "https://twitter.com/author/status/123"},
        {"title": "FakePath", "snippet": "See https://twitter.com/r/python", "url": "https://twitter.com/r/notreddit"},
        {"title": "OtherHost", "snippet": "Visit https://example.com/r/programming", "url": "https://notreddit.com/r/python"},
        {"title": "PathSpoof", "snippet": "https://example.com/reddit.com/r/spoof", "url": "https://example.com/reddit.com/r/spoof"},
        {"title": "Languages", "snippet": "Comparing R/Python for statistics", "url": ""},
        {"title": "TooLong", "snippet": "r/abcdefghijklmnopqrstuvwxyz", "url": ""},
    ],
)
def test_high089_extract_subreddits_rejects_false_positives(item: dict):
    assert resolve._extract_subreddits([item]) == []


def test_high089_extract_subreddits_accepts_valid_prose_and_reddit_urls():
    items = [
        {
            "title": "r/MachineLearning and /r/artificial",
            "snippet": "Also (r/datascience), https://old.reddit.com/r/LocalLLaMA?sort=top",
            "url": "https://www.reddit.com/r/python/comments/123/test",
        },
        {
            "title": "Upper URL",
            "snippet": "",
            "url": "HTTPS://REDDIT.COM/r/rust/",
        },
    ]
    assert resolve._extract_subreddits(items) == [
        "MachineLearning",
        "artificial",
        "datascience",
        "LocalLLaMA",
        "python",
        "rust",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://netflix.com/watch/123",
        "https://www.netflix.com/title/8123",
        "https://dropbox.com/s/abc123",
        "https://linux.com/news/kernel",
        "https://firefox.com/en-US/download",
        "https://spacex.com/launches",
        "https://brex.com/product",
        "https://roblox.com/games",
        "https://fedex.com/tracking",
        "https://fox.com/shows",
        "https://example.com/x.com/spoof",
        "https://my-twitter.com/spoof",
    ],
)
def test_high089_extract_x_handle_rejects_suffix_x_domains(url: str):
    items = [{"title": "Site page", "snippet": "Details", "url": url}]
    assert resolve._extract_x_handle(items) == ""


@pytest.mark.parametrize(
    "text",
    [
        "Contact foo@bar.com for support",
        "Press inquiries: press@gmail.com",
        "Send to first.last+tag@company.co",
        "Reach user_1@sub.domain.org or a-b@x.com",
        "Overlong handle @abcdefghijklmnop should not truncate",
    ],
)
def test_high089_extract_x_handle_rejects_emails_and_overlong_handles(text: str):
    items = [{"title": text, "snippet": text, "url": ""}]
    assert resolve._extract_x_handle(items) == ""


def test_high089_extract_x_handle_accepts_valid_urls_and_mentions():
    assert resolve._extract_x_handle([
        {"title": "News", "snippet": "contact press@spacex.com", "url": "https://spacex.com/launches"},
        {"title": "Official", "snippet": "Follow (@SpaceX).", "url": "https://mobile.x.com/SpaceX?s=20"},
    ]) == "spacex"
    assert resolve._extract_x_handle([
        {"title": "", "snippet": "", "url": "HTTPS://WWW.TWITTER.COM/OpenAI/status/999"},
    ]) == "openai"


def test_high089_github_extractors_reject_spoofed_hosts():
    bad_items = [
        {"title": "Mirror", "snippet": "notgithub.com/eviluser/repo", "url": "https://notgithub.com/eviluser/repo"},
        {"title": "Archive", "snippet": "https://example.com/github.com/fake/repo", "url": "https://example.com/github.com/fake/repo"},
        {"title": "Hyphen", "snippet": "my-github.com/fakeuser", "url": "https://my-github.com/fakeuser/proj"},
    ]
    assert resolve._extract_github_user(bad_items) == ""
    assert resolve._extract_github_repos(bad_items) == []

    good_items = [
        {"title": "Repo", "snippet": "See github.com/anthropics", "url": "https://www.github.com/anthropics/claude-code/issues/1"},
    ]
    assert resolve._extract_github_user(good_items) == "anthropics"
    assert resolve._extract_github_repos(good_items) == ["anthropics/claude-code"]


# ---------------------------------------------------------------------------
# HIGH-090: competitors.py tokenization & stopword filtering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "brand,self_variants,valid_peers",
    [
        ("AT&T", ["AT&T", "AT&T Wireless", "ATT", "AT T"], ["T-Mobile", "Verizon"]),
        ("T-Mobile", ["T-Mobile", "T-Mobile Plans", "T Mobile"], ["AT&T", "Verizon", "Mint Mobile"]),
        ("Node.js", ["Node.js", "NodeJS", "Node"], ["Next.js", "Deno", "Bun"]),
        ("Next.js", ["Next.js", "NextJS", "Next"], ["Nuxt.js", "Node.js", "Remix"]),
        ("Coca-Cola", ["Coca-Cola", "Coca-Cola Zero", "Coca Cola"], ["Pepsi", "Pepsi-Cola"]),
        ("A&W", ["A&W", "A&W Restaurants"], ["Burger King", "B&H"]),
        ("Booking.com", ["Booking.com", "Booking", "Booking Holdings"], ["Hotels.com", "Expedia.com"]),
        ("Monday.com", ["Monday.com", "Monday"], ["Asana", "ClickUp"]),
        ("C3.ai", ["C3.ai", "C3"], ["Scale AI", "Palantir AI"]),
        ("Continue.dev", ["Continue.dev", "Continue"], ["Cursor", "Aider.dev"]),
        ("Fly.io", ["Fly.io", "Fly"], ["Render.io", "Railway"]),
        ("D-Wave", ["D-Wave", "D-Wave Quantum", "DWave"], ["IonQ", "Rigetti"]),
    ],
)
def test_high090_punctuated_brands_filter_self_without_blocking_peers(
    brand: str, self_variants: list[str], valid_peers: list[str],
):
    tokens = competitors._topic_tokens(brand)
    for variant in self_variants:
        assert not competitors._candidate_ok(variant, tokens), (
            f"{variant!r} leaked past self-filter for topic {brand!r} (tokens={tokens})"
        )
    for peer in valid_peers:
        assert competitors._candidate_ok(peer, tokens), (
            f"Valid peer {peer!r} wrongly rejected for topic {brand!r} (tokens={tokens})"
        )


def test_high090_stopwords_in_topic_do_not_blacklist_real_competitors():
    scale_tokens = competitors._topic_tokens("Scale AI")
    assert "ai" not in scale_tokens
    assert competitors._candidate_ok("Mistral AI", scale_tokens)
    assert competitors._candidate_ok("Meta AI", scale_tokens)
    assert not competitors._candidate_ok("Scale AI", scale_tokens)
    assert not competitors._candidate_ok("Scale", scale_tokens)

    nyt_tokens = competitors._topic_tokens("The New York Times")
    assert "the" not in nyt_tokens
    assert competitors._candidate_ok("The Washington Post", nyt_tokens)
    assert not competitors._candidate_ok("The New York Times", nyt_tokens)
    assert not competitors._candidate_ok("New York Times", nyt_tokens)

    # All-stopword topics fall back cleanly rather than returning an empty set.
    assert competitors._topic_tokens("Top AI")


def test_high090_extract_peer_entities_never_returns_punctuated_brand_as_competitor():
    serp = [
        {"title": "AT&T vs Verizon vs T-Mobile", "snippet": "AT&T competes with Verizon and T-Mobile.", "url": "https://example.test/1"},
        {"title": "Best AT&T Alternatives", "snippet": "AT&T customers switch to T-Mobile and Verizon.", "url": "https://example.test/2"},
    ]
    peers = competitors._extract_peer_entities(serp, "AT&T", limit=3)
    assert "AT&T" not in peers
    assert set(peers) >= {"Verizon", "T-Mobile"}

    serp_ai = [
        {"title": "Scale AI vs Mistral AI vs Labelbox", "snippet": "Mistral AI and Labelbox compete with Scale AI.", "url": "https://example.test/3"},
    ]
    ai_peers = competitors._extract_peer_entities(serp_ai, "Scale AI", limit=3)
    assert "Scale AI" not in ai_peers
    assert "Mistral AI" in ai_peers


# ---------------------------------------------------------------------------
# HIGH-092: planner._fallback_plan source intersection & Hebrew grounding
# ---------------------------------------------------------------------------


def test_high092_fallback_plan_intersects_requested_with_available_sources():
    plan = planner._fallback_plan(
        topic="Claude Code",
        available_sources=["reddit", "hackernews", "grounding"],
        requested_sources=["reddit", "x"],
        depth="default",
    )
    assert "x" not in plan.source_weights
    assert list(plan.source_weights) == ["reddit"]
    for sq in plan.subqueries:
        assert sq.sources == ["reddit"]


def test_high092_fallback_plan_disjoint_requested_sources_never_schedules_unavailable_or_unrequested():
    plan = planner._fallback_plan(
        topic="Claude Code",
        available_sources=["reddit", "hackernews"],
        requested_sources=["x", "youtube"],
        depth="default",
    )
    assert plan.source_weights == {}
    assert plan.subqueries == []


def test_high092_hebrew_explicit_search_does_not_inject_grounding_or_evict_quick_slots():
    hebrew_topic = "קפה עלית חדשות"
    for depth in ("quick", "default"):
        plan = planner._fallback_plan(
            topic=hebrew_topic,
            available_sources=["reddit", "x", "grounding"],
            requested_sources=["reddit", "x"],
            depth=depth,
        )
        assert "grounding" not in plan.source_weights, f"grounding leaked in {depth}"
        for sq in plan.subqueries:
            assert "grounding" not in sq.sources
            assert set(sq.sources) == {"reddit", "x"}


def test_high092_hebrew_elevates_grounding_only_when_auto_or_explicitly_requested_and_available():
    hebrew_topic = "חדשות טכנולוגיה בישראל"

    # Auto mode (no requested_sources): grounding available -> elevated to front.
    auto_plan = planner._fallback_plan(
        topic=hebrew_topic,
        available_sources=["reddit", "x", "grounding"],
        requested_sources=None,
        depth="default",
    )
    assert list(auto_plan.source_weights)[0] == "grounding"
    assert auto_plan.subqueries[0].sources[0] == "grounding"

    # Explicit --search including available grounding: grounding elevated to front.
    explicit_plan = planner._fallback_plan(
        topic=hebrew_topic,
        available_sources=["reddit", "x", "grounding"],
        requested_sources=["reddit", "x", "grounding"],
        depth="quick",
    )
    assert list(explicit_plan.source_weights)[0] == "grounding"
    assert explicit_plan.subqueries[0].sources[0] == "grounding"
    assert len(explicit_plan.subqueries[0].sources) == 2

    # Explicit --search including UNAVAILABLE grounding: grounding must not be scheduled.
    unavail_plan = planner._fallback_plan(
        topic=hebrew_topic,
        available_sources=["reddit", "x"],
        requested_sources=["reddit", "grounding"],
        depth="default",
    )
    assert "grounding" not in unavail_plan.source_weights
    for sq in unavail_plan.subqueries:
        assert sq.sources == ["reddit"]


# ---------------------------------------------------------------------------
# HIGH-093: discovery_handoff.py robustness & contract enforcement
# ---------------------------------------------------------------------------


def _sample_bundle(tmp_path: Path) -> handoff.NominationsBundle:
    item = schema.SourceItem(
        item_id="hn1",
        source="hackernews",
        title="Sample Story",
        body="Sample Story",
        url="https://hackernews.example/hn1",
        published_at="2026-07-18",
        engagement={"points": 100},
        snippet="Snippet text",
        metadata={},
    )
    nom = pipeline.Nomination(
        name="Sample Story",
        seed_score=42.0,
        items=[item],
        summary="Summary",
    )
    entry = handoff.PoolEntry(
        nomination=nom,
        cluster_id="c1",
        heuristic_name="Sample Story",
        heuristic_junk=False,
    )
    return handoff.write_nominations_bundle(
        [entry],
        domain="AI",
        tier="deep",
        from_date="2026-06-21",
        to_date="2026-07-21",
        lookback_days=30,
        enrichment_source_boundary=None,
        requested_sources=["hackernews"],
        config_dir=tmp_path,
    )


@pytest.mark.parametrize(
    "bad_value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        "nan",
        "NaN",
        "inf",
        "-inf",
        "Infinity",
        "-Infinity",
        "1e400",
        10**400,
        True,
        False,
        None,
        "not-a-number",
    ],
)
def test_high093_clamped_worthiness_rejects_non_finite_and_invalid_values(bad_value):
    assert handoff._clamped_worthiness(bad_value) is None


def test_high093_read_judgments_handles_non_finite_json_worthiness(tmp_path: Path):
    bundle = _sample_bundle(tmp_path)
    jpath = tmp_path / "judgments.json"
    # Python's json.loads accepts bare NaN/Infinity tokens.
    jpath.write_text(
        '{"bundle_id": "' + bundle.bundle_id + '", "judgments": ['
        '{"id": "n1", "name": "Valid Name", "worthiness": NaN}'
        ']}',
        encoding="utf-8",
    )
    judgments = handoff.read_judgments(jpath, bundle, config_dir=tmp_path)
    assert judgments["n1"].name == "Valid Name"
    assert judgments["n1"].worthiness is None

    jpath.write_text(
        '{"bundle_id": "' + bundle.bundle_id + '", "judgments": ['
        '{"id": "n1", "worthiness": Infinity}'
        ']}',
        encoding="utf-8",
    )
    judgments_inf = handoff.read_judgments(jpath, bundle, config_dir=tmp_path)
    assert judgments_inf["n1"].worthiness is None


def test_high093_non_utf8_files_raise_handoff_contract_error(tmp_path: Path):
    bad_bytes = b"\xff\xfe\x00\x80not-utf8"

    # Bundle file
    bundle_path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    bundle_path.write_bytes(bad_bytes)
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_nominations_bundle(config_dir=tmp_path)

    # Pending report file
    pending_path = tmp_path / handoff.PENDING_REPORT_FILENAME
    pending_path.write_bytes(bad_bytes)
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_pending_report(config_dir=tmp_path)

    # Restore valid bundle to test host judgments & angles files
    bundle = _sample_bundle(tmp_path)
    bad_host = tmp_path / "bad-host.json"
    bad_host.write_bytes(bad_bytes)
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_judgments(bad_host, bundle, config_dir=tmp_path)
    with pytest.raises(handoff.HandoffContractError):
        handoff.read_angles(bad_host, bundle, config_dir=tmp_path)


@pytest.mark.parametrize("bad_context", ["bad", ["list"], 123, True])
def test_high093_bundle_non_dict_context_raises_contract_error(tmp_path: Path, bad_context):
    _sample_bundle(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["context"] = bad_context
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match="context"):
        handoff.read_nominations_bundle(config_dir=tmp_path)


@pytest.mark.parametrize(
    "bad_lookback",
    [0, -5, 1.5, 30.0, 99999, 10**400, True, False, "bad", "30", float("inf"), float("nan")],
)
def test_high093_bundle_invalid_lookback_days_raises_contract_error(tmp_path: Path, bad_lookback):
    _sample_bundle(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["context"]["lookback_days"] = bad_lookback
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match="lookback_days"):
        handoff.read_nominations_bundle(config_dir=tmp_path)


def test_high093_huge_json_int_literal_raises_contract_error(tmp_path: Path):
    """Python 3.11+ raises ValueError (not JSONDecodeError) on >4300-digit ints."""
    huge_int = "9" * 5000
    bundle_path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    bundle_path.write_text(f'{{"lookback_days": {huge_int}}}', encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match="not valid JSON"):
        handoff.read_nominations_bundle(config_dir=tmp_path)

    bundle = _sample_bundle(tmp_path)
    judgments_path = tmp_path / "huge-judgments.json"
    judgments_path.write_text(
        f'{{"bundle_id": "{bundle.bundle_id}", "judgments": [{{"id": "n1", "worthiness": {huge_int}}}]}}',
        encoding="utf-8",
    )
    with pytest.raises(handoff.HandoffContractError, match="not valid JSON"):
        handoff.read_judgments(judgments_path, bundle, config_dir=tmp_path)


@pytest.mark.parametrize("bad_mock", ["false", "true", 1, 0, []])
def test_high093_non_bool_mock_rejected_in_bundle_and_pending(tmp_path: Path, bad_mock):
    _sample_bundle(tmp_path)
    bundle_path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["mock"] = bad_mock
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match="mock"):
        handoff.read_nominations_bundle(config_dir=tmp_path)

    pending_path = tmp_path / handoff.PENDING_REPORT_FILENAME
    pending_payload = {
        "kind": schema.DISCOVERY_PENDING_KIND,
        "schema_version": schema.DISCOVERY_PENDING_SCHEMA_VERSION,
        "bundle_id": "abcd1234abcd1234",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_ref": "discover:AI:now",
        "report": {"domain": "AI", "topics": []},
        "angle_inputs": {},
        "mock": bad_mock,
    }
    pending_path.write_text(json.dumps(pending_payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match="mock"):
        handoff.read_pending_report(config_dir=tmp_path)


def test_high093_bundle_duplicate_nomination_ids_rejected(tmp_path: Path):
    _sample_bundle(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_row = payload["nominations"][0]
    payload["nominations"] = [dict(first_row, id="n1"), dict(first_row, id="n1")]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match="duplicate nomination id"):
        handoff.read_nominations_bundle(config_dir=tmp_path)


def test_high093_bundle_malformed_nested_row_fields_degrade_cleanly(tmp_path: Path):
    _sample_bundle(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    valid_row = payload["nominations"][0]

    # Row with non-dict items element, row with non-dict nomination, row with
    # non-finite seed_score, plus valid rows with malformed sources (int & str).
    bad_items_row = {
        "id": "nbad1",
        "nomination": {"name": "Broken Items", "seed_score": 10.0, "items": ["not-a-dict"]},
    }
    non_dict_nom_row = {"id": "nbad2", "nomination": ["not-a-dict"]}
    non_list_items_row = {
        "id": "nbad3",
        "nomination": {"name": "Str Items", "seed_score": 10.0, "items": "abc"},
    }
    nan_score_row = {
        "id": "nbad4",
        "nomination": {"name": "NaN Score", "seed_score": math.nan, "items": []},
    }
    int_sources_row = dict(valid_row, id="n_int_src", sources=123)
    str_sources_row = dict(valid_row, id="n_str_src", sources="reddit")

    payload["nominations"] = [
        bad_items_row,
        non_dict_nom_row,
        non_list_items_row,
        nan_score_row,
        int_sources_row,
        str_sources_row,
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    err = io.StringIO()
    with redirect_stderr(err):
        bundle = handoff.read_nominations_bundle(config_dir=tmp_path)

    assert [row.nomination_id for row in bundle.nominations] == ["n_int_src", "n_str_src"]
    # Neither int nor str sources crashes or splits into single characters.
    assert bundle.nominations[0].sources == []
    assert bundle.nominations[1].sources == []
    warnings_out = err.getvalue()
    assert "skipping unparseable nomination row 1" in warnings_out
    assert "skipping unparseable nomination row 2" in warnings_out
    assert "skipping unparseable nomination row 3" in warnings_out
    assert "skipping unparseable nomination row 4" in warnings_out
    assert "ignoring malformed sources list" in warnings_out


@pytest.mark.parametrize("field", ["enrichment_source_boundary", "requested_sources"])
@pytest.mark.parametrize("bad_val", ["reddit", 42, ["reddit", 7]])
def test_high093_bundle_malformed_context_source_lists_rejected(tmp_path: Path, field: str, bad_val):
    _sample_bundle(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["context"][field] = bad_val
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(handoff.HandoffContractError, match=field):
        handoff.read_nominations_bundle(config_dir=tmp_path)


# ---------------------------------------------------------------------------
# Greptile Round 1 fixes (Findings 1-5)
# ---------------------------------------------------------------------------


def test_greptile_f1_lookback_days_3650_round_trips_and_over_limit_rejected_before_write_and_sweep(
    tmp_path: Path,
):
    import argparse
    import last30days as cli

    # (a) Boundary value 3650 writes and reads back cleanly.
    item = schema.SourceItem(
        item_id="hn1",
        source="hackernews",
        title="Max Lookback Story",
        body="Max Lookback Story",
        url="https://hackernews.example/hn1",
        published_at="2026-07-18",
        engagement={"points": 100},
        snippet="Snippet",
        metadata={},
    )
    entry = handoff.PoolEntry(
        nomination=pipeline.Nomination(
            name="Max Lookback Story",
            seed_score=42.0,
            items=[item],
            summary="Summary",
        ),
        cluster_id="c1",
        heuristic_name="Max Lookback Story",
        heuristic_junk=False,
    )
    written = handoff.write_nominations_bundle(
        [entry],
        domain="AI",
        tier="deep",
        from_date="2016-07-23",
        to_date="2026-07-21",
        lookback_days=handoff.MAX_LOOKBACK_DAYS,
        enrichment_source_boundary=None,
        requested_sources=["hackernews"],
        config_dir=tmp_path,
    )
    reread = handoff.read_nominations_bundle(config_dir=tmp_path)
    assert written.lookback_days == 3650
    assert reread.lookback_days == 3650

    # (b) Out-of-bounds / invalid lookback_days rejected before writing a bundle.
    for bad_days in (4000, 0, -1, 1.5, True):
        out_dir = tmp_path / f"bad_{type(bad_days).__name__}_{bad_days}"
        with pytest.raises(handoff.HandoffContractError, match="lookback_days"):
            handoff.write_nominations_bundle(
                [entry],
                domain="AI",
                tier="deep",
                from_date="2026-06-21",
                to_date="2026-07-21",
                lookback_days=bad_days,  # type: ignore[arg-type]
                enrichment_source_boundary=None,
                requested_sources=["hackernews"],
                config_dir=out_dir,
            )
        assert not (out_dir / handoff.NOMINATIONS_BUNDLE_FILENAME).exists()

    # (c) CLI leg-1 entry rejects --days 4000 / 0 / -1 BEFORE running the sweep.
    for bad_days in (4000, 0, -1):
        args = argparse.Namespace(
            discover="AI",
            nominate_only=True,
            judgments=None,
            finalize=False,
            lookback_days=bad_days,
            deep=False,
            quick=False,
            mock=True,
            subreddits=None,
            as_of_date=None,
            discover_shallow=False,
            save_dir=str(tmp_path / "cli_reject"),
            search=None,
            exclude_sources=None,
        )
        err = io.StringIO()
        with mock.patch.object(
            cli, "_resolve_discovery_source_boundary", return_value=(None, None),
        ), mock.patch.object(
            cli.pipeline, "run_discover_nominate",
        ) as mock_sweep, redirect_stderr(err):
            rc = cli._run_discover_protocol_leg(args, {})
        assert rc == 2
        mock_sweep.assert_not_called()
        assert "lookback_days" in err.getvalue()


@pytest.mark.parametrize(
    "topic,expected_phrase",
    [
        ("New Balance", "New Balance"),
        ("On Running", "On Running"),
        ("Open AI", "Open AI"),
        ("New Balance shoes", "New Balance"),
        ("On Running review", "On Running"),
        ("New York Times", "New York Times"),
        ("On Running Cloudmonster", "On Running Cloudmonster"),
    ],
)
def test_greptile_f2_query_preserves_noise_prefixed_brand_names(
    topic: str, expected_phrase: str,
):
    compounds = query.extract_compound_terms(topic)
    assert expected_phrase in compounds


def test_greptile_f2_query_still_strips_prompt_filler_and_keeps_brand_comparisons():
    assert query.extract_compound_terms("what are the best Claude Code skills") == [
        "Claude Code"
    ]
    assert query.extract_compound_terms("latest OpenClaw releases") == []
    assert query.extract_compound_terms("Best GitHub Actions workflows for Python") == [
        "GitHub Actions"
    ]
    assert query.extract_compound_terms("Best AI Tools") == []
    assert query.extract_compound_terms("Best VPN for travel") == []
    assert query.extract_compound_terms("Top CRM for startups") == []
    assert query.extract_compound_terms("Top AI tools for coding") == []
    assert query.extract_compound_terms("The best CRM for startups") == []
    assert query.extract_compound_terms("New VPN for travel") == []
    assert query.extract_compound_terms("New Balance vs On Running") == [
        "New Balance",
        "On Running",
    ]


@pytest.mark.parametrize(
    "topic,expected_search",
    [
        ("Best VPN for travel", "vpn travel"),
        ("Top CRM for startups", "crm startups"),
        ("Top AI tools for coding", "ai coding"),
        ("The best CRM for startups", "crm startups"),
    ],
)
def test_greptile_r2_f1_recommendation_prompts_never_quote_prompt_filler(
    topic: str, expected_search: str,
):
    core = query.extract_core_subject(topic, max_words=6, strip_suffixes=True)
    kw = planner._keyword_query(topic, core)
    assert '"' not in kw
    assert kw == expected_search

    plan = planner._fallback_plan(
        topic=topic,
        available_sources=["reddit", "x"],
        requested_sources=None,
        depth="default",
    )
    assert plan.subqueries
    assert plan.subqueries[0].search_query == expected_search


@pytest.mark.parametrize("brand", ["New Balance", "On Running"])
def test_greptile_f2_planner_keeps_full_brand_intact_in_fallback_queries(brand: str):
    core = query.extract_core_subject(brand, max_words=6, strip_suffixes=True)
    kw = planner._keyword_query(brand, core)
    assert f'"{brand}"' == kw

    plan = planner._fallback_plan(
        topic=brand,
        available_sources=["reddit", "x"],
        requested_sources=None,
        depth="default",
    )
    assert plan.subqueries
    assert f'"{brand}"' in plan.subqueries[0].search_query


def test_greptile_f3_competitors_filters_multiword_spaced_self_brand_variants():
    coke_tokens = competitors._topic_tokens("Coca-Cola")
    for self_variant in (
        "Coca Cola Zero",
        "Diet Coca Cola",
        "CocaCola Zero",
        "The Official Coca Cola Zero Beverage Company",
    ):
        assert not competitors._candidate_ok(self_variant, coke_tokens), (
            f"Self-brand variant {self_variant!r} leaked for Coca-Cola"
        )
    assert competitors._candidate_ok("Pepsi", coke_tokens)
    assert competitors._candidate_ok("Pepsi-Cola", coke_tokens)

    serp = [
        {
            "title": "Coca Cola Zero vs Pepsi",
            "snippet": "Coca Cola Zero and Pepsi compete for sugar-free soda drinkers.",
            "url": "https://example.test/soda",
        },
    ]
    peers = competitors._extract_peer_entities(serp, "Coca-Cola", limit=3)
    assert "Coca Cola Zero" not in peers
    assert "Pepsi" in peers

    assert not competitors._candidate_ok("AT T Fiber", competitors._topic_tokens("AT&T"))
    assert not competitors._candidate_ok(
        "T Mobile Home Internet", competitors._topic_tokens("T-Mobile")
    )
    assert not competitors._candidate_ok("Node JS Runtime", competitors._topic_tokens("Node.js"))

    # No substring false positives inside unrelated words.
    on_tokens = competitors._topic_tokens("On")
    assert competitors._candidate_ok("Notion", on_tokens)
    assert competitors._candidate_ok("Discord", on_tokens)
    assert competitors._candidate_ok("OpenAI", competitors._topic_tokens("Open"))


def test_greptile_f4_bad_item_source_null_row_skipped_without_crashing_resume(
    tmp_path: Path,
):
    _sample_bundle(tmp_path)
    path = tmp_path / handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    valid_row = payload["nominations"][0]
    valid_item = valid_row["nomination"]["items"][0]

    # Row with mixed null + string item.source (used to crash sorted(...) in
    # resolve_name_collisions with TypeError).
    null_source_row = {
        "id": "n_bad_source",
        "cluster_id": "c-bad",
        "heuristic_name": "Broken Source Row",
        "heuristic_junk": False,
        "sources": ["reddit"],
        "nomination": {
            "name": "Broken Source Row",
            "seed_score": 30.0,
            "summary": "Summary",
            "items": [
                dict(valid_item, item_id="i1", source=None),
                dict(valid_item, item_id="i2", source="reddit"),
            ],
        },
    }
    bad_title_row = {
        "id": "n_bad_title",
        "nomination": {
            "name": "Bad Title Row",
            "seed_score": 25.0,
            "items": [dict(valid_item, item_id="i3", title=123)],
        },
    }
    bad_summary_row = {
        "id": "n_bad_summary",
        "nomination": {
            "name": "Bad Summary Row",
            "seed_score": 20.0,
            "summary": ["not-a-str"],
            "items": [valid_item],
        },
    }
    bad_comments_row = {
        "id": "n_bad_comments",
        "nomination": {
            "name": "Bad Comments Row",
            "seed_score": 15.0,
            "items": [dict(valid_item, metadata={"top_comments": 42})],
        },
    }

    payload["nominations"] = [
        null_source_row,
        bad_title_row,
        bad_summary_row,
        bad_comments_row,
        valid_row,
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    err = io.StringIO()
    with redirect_stderr(err):
        bundle = handoff.read_nominations_bundle(config_dir=tmp_path)

    assert [row.nomination_id for row in bundle.nominations] == ["n1"]
    warnings_out = err.getvalue()
    assert "skipping unparseable nomination row 1" in warnings_out
    assert "skipping unparseable nomination row 2" in warnings_out
    assert "skipping unparseable nomination row 3" in warnings_out
    assert "skipping unparseable nomination row 4" in warnings_out

    # Downstream resume helpers run cleanly over the surviving healthy rows.
    resolved = handoff.resolve_name_collisions(
        [(row.nomination, row.heuristic_name) for row in bundle.nominations]
    )
    assert resolved == ["Sample Story"]
    digest = handoff.build_host_digest(bundle)
    assert "Sample Story" in digest


def test_greptile_f5_resolve_github_and_prose_url_segment_boundaries():
    # Dotted segment must NOT be truncated to "team".
    dotted_snippet = [
        {"title": "Docs", "snippet": "See https://github.com/team.example/project", "url": ""},
    ]
    dotted_url = [
        {"title": "Docs", "snippet": "", "url": "https://github.com/team.example/project"},
    ]
    assert resolve._extract_github_user(dotted_snippet) == ""
    assert resolve._extract_github_user(dotted_url) == ""
    assert resolve._extract_github_repos(dotted_snippet) == []
    assert resolve._extract_github_repos(dotted_url) == []

    # Trailing prose punctuation and normal repo paths MUST still match "team".
    assert resolve._extract_github_user([
        {"title": "Profile", "snippet": "See https://github.com/team.", "url": ""},
    ]) == "team"
    assert resolve._extract_github_user([
        {"title": "Profile", "snippet": "(https://github.com/team)", "url": ""},
    ]) == "team"
    assert resolve._extract_github_user([
        {"title": "Repo", "snippet": "Check https://github.com/team/repo", "url": ""},
    ]) == "team"
    assert resolve._extract_github_repos([
        {"title": "Repo", "snippet": "Check https://github.com/team/repo.", "url": ""},
    ]) == ["team/repo"]
    assert resolve._extract_github_repos([
        {"title": "Repo", "snippet": "See https://github.com/vercel/next.js.", "url": ""},
    ]) == ["vercel/next.js"]

    # Same path-segment discipline on Reddit prose URLs and dotted @handles.
    assert resolve._extract_subreddits([
        {"title": "Link", "snippet": "See https://reddit.com/r/team.example/comments/1", "url": ""},
    ]) == []
    assert resolve._extract_x_handle([
        {"title": "Bluesky", "snippet": "Follow @alice.bsky.social on Bluesky", "url": ""},
    ]) == ""

    # Curly quotes around handles, subreddits, and GitHub links still extract cleanly.
    assert resolve._extract_x_handle([
        {"title": "Quote", "snippet": "Follow \u201c@SpaceX\u201d for launch updates", "url": ""},
    ]) == "spacex"
    assert resolve._extract_x_handle([
        {"title": "SingleQuote", "snippet": "Follow \u2018@SpaceX\u2019", "url": ""},
    ]) == "spacex"
    assert resolve._extract_x_handle([
        {"title": "QuotePeriod", "snippet": "Follow \u201c@SpaceX.\u201d", "url": ""},
    ]) == "spacex"
    assert resolve._extract_subreddits([
        {"title": "QuoteSub", "snippet": "Check \u201cr/python\u201d and \u2018r/rust\u2019", "url": ""},
    ]) == ["python", "rust"]
    assert resolve._extract_github_user([
        {"title": "QuoteGH", "snippet": "See \u201chttps://github.com/team\u201d", "url": ""},
    ]) == "team"


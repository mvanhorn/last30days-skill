"""Tests for diffbot.py - Diffbot Knowledge Graph Article search."""

from unittest.mock import patch

import pytest

from lib import diffbot, schema


# === Fixtures ===


def make_entity(
    title="OpenAI ships a new model",
    page_url="https://example.com/openai-news",
    summary="A concise summary of the article.",
    text="The full body text of the article goes here.",
    site_name="Example News",
    entity_id="ART186928082907",
    timestamp_ms=1781380740000,  # 2026-06-13T19:59 UTC
    estimated_ms=None,
):
    """Build a Diffbot Article entity matching the live response shape."""
    entity = {
        "type": "Article",
        "title": title,
        "pageUrl": page_url,
        "summary": summary,
        "text": text,
        "siteName": site_name,
        "id": entity_id,
    }
    if timestamp_ms is not None:
        entity["date"] = {
            "str": "d2026-06-13T19:59",
            "precision": 4,
            "timestamp": timestamp_ms,
        }
    if estimated_ms is not None:
        entity["estimatedDate"] = {
            "str": "d2026-06-13T17:46:52",
            "precision": 4,
            "timestamp": estimated_ms,
        }
    return entity


def make_response(*entities):
    return {
        "version": 4,
        "hits": 91041,
        "results": len(entities),
        "data": [{"entity": e, "entity_ctx": {"cluster_id": 123}} for e in entities],
    }


# === DQL builder ===


def test_build_article_dql_includes_type_dates_and_recency_boost():
    dql = diffbot.build_article_dql("OpenAI", "2026-05-01", "2026-06-13")
    assert "type:Article" in dql
    assert 'date>="2026-05-01"' in dql
    assert 'date<="2026-06-13"' in dql
    assert 'text:"OpenAI"' in dql
    # sortBy:date discarded relevance ranking and floated same-day spam to the
    # top; replaced by default-relevance ordering + graduated recency should-boosts.
    assert "sortBy" not in dql
    assert 'should[60]:date>="2026-06-11"' in dql  # to_date - 2 days
    assert 'should[40]:date>="2026-06-06"' in dql  # to_date - 7 days
    assert 'should[20]:date>="2026-05-30"' in dql  # to_date - 14 days


def test_recency_should_clauses_clamp_and_dedupe_for_narrow_window():
    # A 1-day window collapses all tiers onto from_date; keep a single strongest boost.
    clauses = diffbot._recency_should_clauses("2026-06-12", "2026-06-13")
    assert clauses == ['should[60]:date>="2026-06-12"']


def test_build_article_dql_title_boost_scales_with_specificity():
    dql = diffbot.build_article_dql("spacex ipo", "2026-05-14", "2026-06-13")
    # "spacex" (len 6) is more specific than the generic "ipo" (len 3), so it
    # earns a higher title-boost weight.
    assert 'should[20]:title:"spacex"' in dql
    assert 'should[10]:title:"ipo"' in dql
    # Design invariant: a fresh article (all recency tiers stacked) still outscores
    # the largest possible total title boost, so freshness keeps priority even when
    # a per-clause title match can exceed the weakest single recency tier.
    max_freshness = sum(w for _, w in diffbot._RECENCY_TIERS)
    max_total_title = diffbot._TITLE_WEIGHT_MAX * diffbot._MAX_TEXT_CLAUSES
    assert max_freshness > max_total_title


def test_title_clause_weight_ranks_terms_by_length():
    w = diffbot._title_clause_weight
    assert w("ipo") == 10          # short generic -> floor
    assert w("spcx") == 10         # (4-2)*5 = 10
    assert w("stock") == 15        # (5-2)*5 = 15
    assert w("spacex") == 20       # (6-2)*5 = 20
    assert w("starlink") == 30     # (8-2)*5 = 30, hits cap
    assert w("valuation") == 30    # clamped to cap
    assert w("Jensen Huang") == 30 # compound proper noun -> max


def test_build_article_dql_title_boost_uses_proper_noun_phrase():
    # Title boost mirrors the text-clause decomposition (no single-token split).
    dql = diffbot.build_article_dql("Jensen Huang", "2026-05-14", "2026-06-13")
    assert 'should[30]:title:"Jensen Huang"' in dql
    assert 'title:"Jensen"' not in dql


def test_specificity_to_weight_maps_score_into_title_band():
    assert diffbot._specificity_to_weight(0) == diffbot._TITLE_WEIGHT_MIN     # 10
    assert diffbot._specificity_to_weight(50) == 20
    assert diffbot._specificity_to_weight(100) == diffbot._TITLE_WEIGHT_MAX   # 30
    # Out-of-range scores clamp; the title boost can never exceed the cap, so
    # recency keeps priority no matter what the driving LLM emits.
    assert diffbot._specificity_to_weight(999) == diffbot._TITLE_WEIGHT_MAX
    assert diffbot._specificity_to_weight(-5) == diffbot._TITLE_WEIGHT_MIN


def test_build_article_dql_honors_llm_specificity_over_length():
    # The driving LLM judges the short ticker "spcx" as highly distinctive (95)
    # and "stock" as generic (20) - the opposite of what length alone produces.
    dql = diffbot.build_article_dql(
        "spacex spcx stock", "2026-05-14", "2026-06-13",
        title_specificity={"spcx": 95, "stock": 20, "spacex": 90},
    )
    assert 'should[29]:title:"spcx"' in dql    # 95 -> 10 + 20*0.95 = 29
    assert 'should[14]:title:"stock"' in dql   # 20 -> 10 + 20*0.20 = 14
    assert 'should[28]:title:"spacex"' in dql  # 90 -> 10 + 20*0.90 = 28


def test_build_article_dql_falls_back_to_length_when_term_absent():
    # "ipo" has no LLM score -> length heuristic (weight 10); "spacex" is scored.
    dql = diffbot.build_article_dql(
        "spacex ipo", "2026-05-14", "2026-06-13",
        title_specificity={"spacex": 100},
    )
    assert 'should[30]:title:"spacex"' in dql  # 100 -> cap
    assert 'should[10]:title:"ipo"' in dql     # fallback length heuristic


def test_subquery_from_dict_parses_term_specificity():
    sq = schema.subquery_from_dict({
        "label": "primary",
        "search_query": "spacex ipo",
        "ranking_query": "what happened",
        "sources": ["diffbot"],
        "term_specificity": {"spacex": 95, "ipo": "25", "bad": "x"},
    })
    # numeric strings coerce; non-numeric entries are dropped defensively.
    assert sq.term_specificity == {"spacex": 95.0, "ipo": 25.0}


def test_build_article_dql_splits_multiword_into_anded_clauses():
    # Diffbot ANDs separate text: clauses; one strict phrase would 0-hit.
    dql = diffbot.build_article_dql("OpenAI agent safety", "2026-05-01", "2026-06-13")
    assert 'text:"OpenAI"' in dql
    assert 'text:"agent"' in dql
    assert 'text:"safety"' in dql


def test_build_article_dql_keeps_proper_noun_phrase_intact():
    dql = diffbot.build_article_dql("Jensen Huang", "2026-05-01", "2026-06-13")
    assert 'text:"Jensen Huang"' in dql
    assert 'text:"Jensen"' not in dql  # not split into single tokens


def test_build_article_dql_dedupes_planner_lowercase_echo():
    # The deterministic planner emits a quoted compound plus a lowercase echo;
    # the echo words must not become redundant AND clauses that over-constrain.
    dql = diffbot.build_article_dql('"Jensen Huang" jensen huang', "2026-05-01", "2026-06-13")
    assert dql.count('text:"') == 1
    assert 'text:"Jensen Huang"' in dql
    assert 'text:"jensen"' not in dql


def test_build_article_dql_drops_noise_words():
    dql = diffbot.build_article_dql("the best OpenAI tools", "2026-05-01", "2026-06-13")
    assert 'text:"OpenAI"' in dql
    assert '"best"' not in dql
    assert '"tools"' not in dql


def test_build_article_dql_caps_clause_count_at_three():
    dql = diffbot.build_article_dql(
        "alpha bravo charlie delta echo", "2026-05-01", "2026-06-13"
    )
    assert dql.count('text:"') == diffbot._MAX_TEXT_CLAUSES == 3


def test_build_article_dql_falls_back_to_raw_phrase_when_all_noise():
    # Every word is a noise/modifier term -> fall back to the raw phrase.
    dql = diffbot.build_article_dql("the best latest", "2026-05-01", "2026-06-13")
    assert 'text:"the best latest"' in dql


def test_escape_handles_quotes_and_backslashes():
    assert diffbot._escape('a"b') == r"a\"b"
    assert diffbot._escape("a\\b") == r"a\\b"


# === Parser: happy path ===


def test_parse_maps_entity_to_grounding_item_shape():
    resp = make_response(make_entity())
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "OpenAI ships a new model"
    assert item["url"] == "https://example.com/openai-news"
    assert item["source_domain"] == "Example News"
    assert item["snippet"] == "A concise summary of the article."
    assert item["date"] == "2026-06-13"
    assert item["relevance"] == 1.0  # rank 1 (top result) -> 100%
    assert item["why_relevant"] == "Diffbot KG Article search"
    assert item["id"] == "DB1"
    assert item["metadata"]["diffbot_id"] == "ART186928082907"


def test_parse_falls_back_to_text_when_summary_missing():
    resp = make_response(make_entity(summary=""))
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    assert items[0]["snippet"] == "The full body text of the article goes here."


def test_parse_truncates_snippet_to_500_chars():
    resp = make_response(make_entity(summary="x" * 1000))
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    assert len(items[0]["snippet"]) == 500


def test_parse_uses_domain_when_sitename_missing():
    resp = make_response(make_entity(site_name=""))
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    assert items[0]["source_domain"] == "example.com"


# === Parser: date handling ===


def test_parse_resolves_date_from_timestamp_ms():
    resp = make_response(make_entity(timestamp_ms=1781380740000))
    items = diffbot.parse_diffbot_response(resp)
    assert items[0]["date"] == "2026-06-13"


def test_parse_falls_back_to_estimated_date_when_date_missing():
    entity = make_entity(timestamp_ms=None, estimated_ms=1781372812000)
    items = diffbot.parse_diffbot_response(make_response(entity))
    assert items[0]["date"] == "2026-06-13"


def test_parse_handles_string_date_with_d_prefix():
    entity = make_entity(timestamp_ms=None)
    entity["date"] = {"str": "d2026-06-10", "precision": 3}
    items = diffbot.parse_diffbot_response(make_response(entity))
    assert items[0]["date"] == "2026-06-10"


# === Parser: filtering ===


def test_parse_drops_items_outside_date_window():
    in_window = make_entity(page_url="https://a.com/1", timestamp_ms=1781380740000)  # 2026-06-13
    out_window = make_entity(page_url="https://a.com/2", timestamp_ms=1700000000000)  # 2023-11
    items = diffbot.parse_diffbot_response(
        make_response(in_window, out_window), "2026-05-01", "2026-06-13"
    )
    urls = [i["url"] for i in items]
    assert "https://a.com/1" in urls
    assert "https://a.com/2" not in urls


def test_parse_keeps_all_when_no_window_given():
    out_window = make_entity(timestamp_ms=1700000000000)
    items = diffbot.parse_diffbot_response(make_response(out_window))
    assert len(items) == 1


def test_parse_skips_entities_without_url():
    entity = make_entity(page_url="")
    items = diffbot.parse_diffbot_response(make_response(entity))
    assert items == []


def test_parse_handles_empty_and_malformed_responses():
    assert diffbot.parse_diffbot_response({}) == []
    assert diffbot.parse_diffbot_response({"data": []}) == []
    assert diffbot.parse_diffbot_response({"data": [None, "junk", {}]}) == []


# === search_diffbot ===


def test_search_returns_empty_without_token():
    result = diffbot.search_diffbot("OpenAI", "2026-05-01", "2026-06-13", token="")
    assert result == {"data": [], "hits": 0}


@patch("lib.diffbot.http.request")
def test_search_posts_expected_dql_payload(mock_request):
    mock_request.return_value = make_response(make_entity())
    diffbot.search_diffbot(
        "OpenAI", "2026-05-01", "2026-06-13", depth="deep", token="tok-123"
    )
    args, kwargs = mock_request.call_args
    assert args[0] == "POST"
    assert args[1] == diffbot.DQL_ENDPOINT
    assert kwargs["params"] == {"token": "tok-123"}
    body = kwargs["json_data"]
    assert body["size"] == diffbot.DEPTH_CONFIG["deep"]["size"]
    assert body["cluster"] == "dedupe"
    assert body["type"] == "queryTextFallback"
    assert "type:Article" in body["query"]


@patch("lib.diffbot.http.request")
def test_search_query_mode_when_text_fallback_disabled(mock_request):
    mock_request.return_value = make_response()
    diffbot.search_diffbot(
        "OpenAI", "2026-05-01", "2026-06-13", token="tok", text_fallback=False
    )
    assert mock_request.call_args.kwargs["json_data"]["type"] == "query"


@patch("lib.diffbot.http.request")
def test_depth_sets_per_query_size(mock_request):
    mock_request.return_value = make_response()
    # Single-term topic -> one formulation per depth, so size is observable directly.
    for depth, cfg in diffbot.DEPTH_CONFIG.items():
        diffbot.search_diffbot("openai", "2026-05-01", "2026-06-13", depth=depth, token="t")
        assert mock_request.call_args.kwargs["json_data"]["size"] == cfg["size"]


@patch("lib.diffbot.http.request")
def test_depth_controls_number_of_queries(mock_request):
    mock_request.return_value = make_response()
    # "openai agent safety" -> 3 clauses, enough subsets to fill every budget.
    for depth, cfg in diffbot.DEPTH_CONFIG.items():
        mock_request.reset_mock()
        diffbot.search_diffbot(
            "openai agent safety", "2026-05-01", "2026-06-13", depth=depth, token="t"
        )
        assert mock_request.call_count == cfg["queries"]


@patch("lib.diffbot.http.request")
def test_single_term_topic_runs_one_query(mock_request):
    # A one-clause topic has nothing to fan out across, so deep still issues 1 query.
    mock_request.return_value = make_response()
    diffbot.search_diffbot("openai", "2026-05-01", "2026-06-13", depth="deep", token="t")
    assert mock_request.call_count == 1


@patch("lib.diffbot.http.request")
def test_fanout_runs_most_specific_formulation_first(mock_request):
    mock_request.return_value = make_response()
    diffbot.search_diffbot(
        "openai agent safety", "2026-05-01", "2026-06-13", depth="deep", token="t"
    )
    queries = [c.kwargs["json_data"]["query"] for c in mock_request.call_args_list]
    # First formulation ANDs the full clause set; later ones broaden to subsets.
    assert queries[0].count('text:"') == 3
    assert queries[1].count('text:"') == 2


def test_clause_combinations_orders_subsets_by_specificity():
    combos = diffbot._clause_combinations(
        ["spacex", "ipo", "stock"], {"spacex": 95, "ipo": 5, "stock": 5}, 4
    )
    # Full set first; then the most distinctive 2-term subset (the two containing
    # "spacex") ahead of the generic "ipo"+"stock" pair.
    assert combos[0] == ["spacex", "ipo", "stock"]
    assert ["ipo", "stock"] not in combos[:3]


@patch("lib.diffbot.http.request")
def test_search_then_parse_end_to_end(mock_request):
    mock_request.return_value = make_response(make_entity())
    resp = diffbot.search_diffbot("openai", "2026-05-01", "2026-06-13", token="t")
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/openai-news"


def test_rank_relevance_scale():
    assert diffbot._rank_relevance(1) == 1.0    # top result -> 100%
    assert diffbot._rank_relevance(5) == 0.8    # 5th result -> 80%
    assert diffbot._rank_relevance(10) == 0.55  # 10th result -> lower
    # Floor keeps very deep ranks from going to zero/negative.
    assert diffbot._rank_relevance(100) == diffbot._RELEVANCE_FLOOR


def test_parse_uses_tagged_rank_for_relevance():
    e1 = make_entity(page_url="https://a.com/1")
    e5 = make_entity(page_url="https://a.com/5")
    resp = {"data": [
        {"entity": e1, "entity_ctx": {}, "_rank": 1},
        {"entity": e5, "entity_ctx": {}, "_rank": 5},
    ]}
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    rel = {i["url"]: i["relevance"] for i in items}
    assert rel["https://a.com/1"] == 1.0
    assert rel["https://a.com/5"] == 0.8


def test_parse_falls_back_to_position_when_rank_untagged():
    # A raw single response (no fan-out tags) ranks by position in the list.
    e1 = make_entity(page_url="https://a.com/1")
    e2 = make_entity(page_url="https://a.com/2")
    items = diffbot.parse_diffbot_response(make_response(e1, e2))
    assert items[0]["relevance"] == 1.0    # position 1
    assert items[1]["relevance"] == 0.95   # position 2


def test_parse_dedupes_cross_formulation_matches_keeping_first():
    # The same article from two formulations collapses to one; the first (most-
    # specific formulation, tagged rank 1) wins.
    entity = make_entity(page_url="https://a.com/x")
    resp = {"data": [
        {"entity": entity, "entity_ctx": {}, "_rank": 1},
        {"entity": entity, "entity_ctx": {}, "_rank": 4},
    ]}
    items = diffbot.parse_diffbot_response(resp, "2026-05-01", "2026-06-13")
    assert len(items) == 1
    assert items[0]["relevance"] == 1.0


@patch("lib.diffbot.log.source_log")
@patch("lib.diffbot.http.request")
def test_search_logs_dql_with_length_provenance(mock_request, mock_log):
    mock_request.return_value = make_response()
    diffbot.search_diffbot("spacex ipo", "2026-05-14", "2026-06-13", token="t")
    logged = " ".join(call.args[1] for call in mock_log.call_args_list)
    assert "DQL [title-weights from length heuristic]:" in logged
    assert 'should[10]:title:"ipo"' in logged  # the actual query is recorded
    # The DQL log line is durable on non-interactive hosts (tty_only=False).
    assert any(
        c.args[0] == "Diffbot" and c.args[1].startswith("DQL ")
        and c.kwargs.get("tty_only") is False
        for c in mock_log.call_args_list
    )


@patch("lib.diffbot.log.source_log")
@patch("lib.diffbot.http.request")
def test_search_logs_dql_with_llm_specificity_provenance(mock_request, mock_log):
    mock_request.return_value = make_response()
    diffbot.search_diffbot(
        "spacex spcx", "2026-05-14", "2026-06-13", token="t",
        title_specificity={"spcx": 95},
    )
    logged = " ".join(call.args[1] for call in mock_log.call_args_list)
    assert "title-weights from LLM term_specificity=" in logged
    assert 'should[29]:title:"spcx"' in logged

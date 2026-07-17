"""DripStack source: requested-only gating, windowing, and normalization."""

from unittest import mock

from lib import dripstack, pipeline


def _api_item(**overrides):
    item = {
        "publicationSlug": "newsletter.semianalysis.com",
        "slug": "nvidia-gpu-debt-backstop",
        "title": "Nvidia GPU Debt Backstop",
        "subtitle": "Capital, offtake and datacenters.",
        "snippet": "Nvidia's backstop economics explained.",
        "publishedAt": "2026-07-06T21:53:44.000Z",
        "relevanceScore": 84,
        "matchConfidence": "strong",
        "topicCoverageRatio": 1,
        "whyMatched": ["Matched article body for: nvidia", "Hybrid RRF — blended"],
    }
    item.update(overrides)
    return item


def test_dripstack_absent_from_available_sources_by_default():
    assert "dripstack" not in pipeline.available_sources({}, None, x_pending=False)


def test_dripstack_available_only_when_explicitly_requested():
    available = pipeline.available_sources({}, ["dripstack"], x_pending=False)
    assert "dripstack" in available


def test_search_goes_through_the_shared_http_choke_point(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, **kwargs):
        seen["url"] = url
        seen["retries"] = kwargs.get("retries")
        return {"items": [_api_item()], "matchConfidence": "strong"}

    monkeypatch.setattr(dripstack.http, "get", fake_get)
    items = dripstack.search_dripstack("Nvidia earnings", "2026-06-12", "2026-07-12")

    assert len(items) == 1
    assert seen["url"].startswith("https://dripstack.xyz/api/v1/search?")
    assert seen["retries"] == 2


def test_search_drops_results_outside_the_window(monkeypatch):
    stale = _api_item(publishedAt="2026-05-20T19:06:01.000Z", slug="old-post")
    fresh = _api_item()
    undated = _api_item(publishedAt="", slug="undated-post")
    monkeypatch.setattr(
        dripstack.http, "get",
        lambda *a, **k: {"items": [stale, fresh, undated], "matchConfidence": "strong"},
    )

    items = dripstack.search_dripstack("Nvidia", "2026-06-12", "2026-07-12")

    slugs = [item["slug"] for item in items]
    assert "old-post" not in slugs
    assert "nvidia-gpu-debt-backstop" in slugs
    assert "undated-post" in slugs  # undated results are kept, not guessed


def test_search_failure_returns_empty_list(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(dripstack.http, "get", boom)
    assert dripstack.search_dripstack("Nvidia", "2026-06-12", "2026-07-12") == []


def test_parse_normalizes_fields_and_relevance():
    parsed = dripstack.parse_dripstack_response([_api_item()], query="nvidia")

    item = parsed[0]
    assert item["url"] == "https://newsletter.semianalysis.com/nvidia-gpu-debt-backstop"
    assert item["date"] == "2026-07-06"
    assert item["relevance"] == 0.84
    assert item["engagement"] == {}
    assert item["author"] == "newsletter.semianalysis"
    assert "Hybrid" not in item["why_relevant"]
    assert item["metadata"]["publication_slug"] == "newsletter.semianalysis.com"


def test_parse_handles_missing_fields_conservatively():
    parsed = dripstack.parse_dripstack_response(
        [{"title": "", "relevanceScore": "not-a-number"}], query="x"
    )

    item = parsed[0]
    assert item["title"] == "DripStack result 1"
    assert item["url"] == ""
    assert item["relevance"] == 0.5


def test_parse_emits_body_and_normalizer_prefers_it():
    parsed = dripstack.parse_dripstack_response([_api_item()], query="nvidia")

    assert parsed[0]["body"] == "Capital, offtake and datacenters."

    from lib import normalize
    normalized = normalize._normalize_dripstack(
        "dripstack", parsed[0], 0, "2026-06-12", "2026-07-12"
    )
    assert normalized.body == "Capital, offtake and datacenters."


def test_dripstack_activates_via_persisted_include_sources():
    """INCLUDE_SOURCES is the .env checkbox for persistent opt-ins (the
    LinkedIn/Perplexity pattern); dripstack must honor it, not only --search."""
    available = pipeline.available_sources(
        {"INCLUDE_SOURCES": "dripstack"}, None, x_pending=False
    )
    assert "dripstack" in available


def test_unrelated_include_sources_keeps_dripstack_off():
    available = pipeline.available_sources(
        {"INCLUDE_SOURCES": "linkedin,tiktok"}, None, x_pending=False
    )
    assert "dripstack" not in available


def test_include_sources_tolerates_whitespace_around_commas():
    available = pipeline.available_sources(
        {"INCLUDE_SOURCES": "linkedin, dripstack"}, None, x_pending=False
    )
    assert "dripstack" in available


# --------------------------------------------------------------------------- #
# Publication browsing (free)                                                  #
# --------------------------------------------------------------------------- #


def _publication(**overrides):
    pub = {
        "slug": "newsletter.semianalysis.com",
        "title": "SemiAnalysis",
        "description": "Analysis of semiconductors, AI, and cloud.",
        "siteUrl": "https://newsletter.semianalysis.com",
        "lastSyncedAt": "2026-07-06T08:30:00Z",
    }
    pub.update(overrides)
    return pub


def _post_summary(**overrides):
    post = {
        "slug": "nvidia-gpu-debt-backstop",
        "title": "Nvidia GPU Debt Backstop",
        "subtitle": "Capital, offtake and datacenters.",
        "publishedAt": "2026-07-06T21:53:44.000Z",
        "priceCents": 500,
    }
    post.update(overrides)
    return post


def test_get_publications_returns_list(monkeypatch):
    pubs = [_publication(), _publication(slug="stratechery", title="Stratechery")]
    monkeypatch.setattr(
        dripstack.http, "get",
        lambda *a, **k: {"publications": pubs},
    )
    result = dripstack.get_publications()
    assert len(result) == 2
    assert result[0]["slug"] == "newsletter.semianalysis.com"


def test_get_publications_returns_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(dripstack.http, "get", boom)
    assert dripstack.get_publications() == []


def test_search_publications_returns_matches(monkeypatch):
    items = [_publication(), _publication(slug="stratechery")]
    monkeypatch.setattr(
        dripstack.http, "get",
        lambda *a, **k: {"items": items},
    )
    result = dripstack.search_publications("semianalysis")
    assert len(result) == 2


def test_search_publications_rejects_short_query():
    assert dripstack.search_publications("a") == []
    assert dripstack.search_publications("") == []


def test_search_publications_returns_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(dripstack.http, "get", boom)
    assert dripstack.search_publications("semianalysis") == []


def test_get_publication_posts_returns_detail(monkeypatch):
    detail = {
        "slug": "newsletter.semianalysis.com",
        "title": "SemiAnalysis",
        "posts": [_post_summary(), _post_summary(slug="other-post")],
    }
    seen = {}

    def fake_get(url, headers=None, **kwargs):
        seen["url"] = url
        return detail

    monkeypatch.setattr(dripstack.http, "get", fake_get)
    result = dripstack.get_publication_posts("newsletter.semianalysis.com", limit=5)
    assert len(result["posts"]) == 2
    assert "newsletter.semianalysis.com" in seen["url"]
    assert "limit=5" in seen["url"]


def test_get_publication_posts_returns_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(dripstack.http, "get", boom)
    assert dripstack.get_publication_posts("newsletter.semianalysis.com") == {}


# --------------------------------------------------------------------------- #
# Paid post fetch                                                              #
# --------------------------------------------------------------------------- #


def test_get_publication_post_skips_without_key():
    assert dripstack.get_publication_post("pub", "post", api_key=None) == {}
    assert dripstack.get_publication_post("pub", "post", api_key="") == {}


def test_get_publication_post_sends_bearer_auth(monkeypatch):
    seen = {}
    post_data = {"synthesizedSummary": "Nvidia backstop explained...", "title": "Nvidia GPU Debt Backstop"}

    def fake_get(url, headers=None, **kwargs):
        seen["url"] = url
        seen["headers"] = headers
        return post_data

    monkeypatch.setattr(dripstack.http, "get", fake_get)
    result = dripstack.get_publication_post(
        "newsletter.semianalysis.com",
        "nvidia-gpu-debt-backstop",
        api_key="pk_drip_test123",
    )
    assert result == post_data
    assert seen["headers"]["Authorization"] == "Bearer pk_drip_test123"
    assert "nvidia-gpu-debt-backstop" in seen["url"]


def test_get_publication_post_returns_empty_on_402(monkeypatch):
    from lib import http

    def fake_get(url, headers=None, **kwargs):
        raise http.HTTPError("HTTP 402: Payment Required", status_code=402)

    monkeypatch.setattr(dripstack.http, "get", fake_get)
    result = dripstack.get_publication_post(
        "pub", "post", api_key="pk_drip_test123"
    )
    assert result == {}


def test_get_publication_post_returns_empty_on_404(monkeypatch):
    from lib import http

    def fake_get(url, headers=None, **kwargs):
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    monkeypatch.setattr(dripstack.http, "get", fake_get)
    result = dripstack.get_publication_post(
        "pub", "post", api_key="pk_drip_test123"
    )
    assert result == {}


def test_get_publication_post_returns_empty_on_503(monkeypatch):
    from lib import http

    def fake_get(url, headers=None, **kwargs):
        raise http.HTTPError("HTTP 503: Service Unavailable", status_code=503)

    monkeypatch.setattr(dripstack.http, "get", fake_get)
    result = dripstack.get_publication_post(
        "pub", "post", api_key="pk_drip_test123"
    )
    assert result == {}


def test_get_publication_post_returns_empty_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(dripstack.http, "get", boom)
    result = dripstack.get_publication_post(
        "pub", "post", api_key="pk_drip_test123"
    )
    assert result == {}

"""Story terms: the run's own stories, fixed before the jobs lane starts.

The jobs lane searches each board for a few terms drawn from the run's own
stories. The host plan sends them as ``jobs_queries``; when it sends none the
engine derives them from the plan's subqueries, leaving out the company, the
people, the other side of a comparison and generic words.
"""

from unittest.mock import patch

from lib import jobs_story, pipeline


def _plan(*search_queries, jobs_queries=None):
    plan = {
        "intent": "opinion",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "debate",
        "subqueries": [
            {"label": f"q{i}", "search_query": query, "ranking_query": query,
             "sources": ["reddit", "jobs"], "weight": 1.0}
            for i, query in enumerate(search_queries)
        ],
    }
    if jobs_queries is not None:
        plan["jobs_queries"] = jobs_queries
    return plan


def _jobs_terms(topic, plan, config=None):
    """The story terms the jobs lane was handed, and the report."""
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic=topic, config=dict(config or {}), mock=True, web_backend="none",
            depth="default", external_plan=plan,
        )
    jobs_calls = [c for c in retrieve.call_args_list if c.kwargs["source"] == "jobs"]
    assert jobs_calls, "the jobs lane did not run"
    terms = {tuple(c.kwargs["config"].get("_jobs_story_terms") or ()) for c in jobs_calls}
    assert len(terms) == 1, "every jobs stream sees the same terms"
    return list(terms.pop()), report


def test_host_story_terms_are_used_as_sent(capsys):
    terms, report = _jobs_terms(
        "Amazon",
        _plan("Amazon agentic payments news", jobs_queries=["agentic payments", "humanoid"]),
    )
    assert terms == ["agentic payments", "humanoid"]
    assert report.query_plan.jobs_queries == ["agentic payments", "humanoid"]
    err = capsys.readouterr().err
    assert err.count("story terms") == 1
    assert "agentic payments, humanoid" in err


def test_host_story_terms_keep_the_first_four():
    sent = ["agentic payments", "humanoid", "Kuiper", "Zoox", "Alexa", "AWS Trainium", "Prime Air"]
    terms, _ = _jobs_terms("Amazon", _plan("Amazon news", jobs_queries=sent))
    assert terms == ["agentic payments", "humanoid", "Kuiper", "Zoox"]


def test_host_story_terms_are_trimmed_deduped_and_length_capped():
    sent = ["  humanoid ", "Humanoid", "", "x" * 41, "agentic payments"]
    terms, _ = _jobs_terms("Amazon", _plan("Amazon news", jobs_queries=sent))
    assert terms == ["humanoid", "agentic payments"]


def test_engine_terms_leave_out_company_people_comparison_entity_and_generic_words():
    plan = _plan(
        "Amazon Andy Jassy agentic payments",
        "Walmart vs Amazon grocery delivery news",
        "latest Amazon humanoid robots review",
    )
    terms, report = _jobs_terms(
        "Amazon",
        plan,
        config={
            "_company_signal": "the company Amazon.com",
            "_company": {"company": "Amazon.com"},
            "_comparison_entities": ["Amazon", "Walmart"],
        },
    )
    assert report.query_plan.jobs_queries == []
    assert terms == ["agentic payments", "grocery delivery", "humanoid robots"]


def test_engine_terms_leave_out_the_person_the_topic_names():
    terms, _ = _jobs_terms(
        "Palmer Luckey",
        _plan("Palmer Luckey drone interceptors", "Luckey hypersonics update"),
        config={"_company": {"company": "Anduril Industries", "company_via_person": True}},
    )
    assert terms == ["drone interceptors", "hypersonics"]


def test_engine_terms_leave_out_both_sides_of_a_comparison_topic():
    terms, _ = _jobs_terms(
        "Amazon vs Walmart",
        _plan("Amazon vs Walmart drone delivery", "Walmart Amazon pharmacy 2026"),
        config={"_company_signal": "--official-site"},
    )
    assert terms == ["drone delivery", "pharmacy"]


def test_engine_terms_keep_the_first_four():
    terms, _ = _jobs_terms(
        "Amazon",
        _plan("Amazon Kuiper satellites", "Amazon Zoox robotaxi",
              "Amazon Trainium chips", "Amazon pharmacy", "Amazon grocery"),
        config={"_company": {"company": "Amazon"}},
    )
    assert terms == ["Kuiper satellites", "Zoox robotaxi", "Trainium chips", "pharmacy"]


def test_short_term_matches_whole_words_only():
    assert jobs_story.matches("AI", "Senior Engineer, AI Native Products")
    assert not jobs_story.matches("AI", "Maintenance Technician")
    assert not jobs_story.matches("AWS", "Laws and Policy Counsel")
    assert jobs_story.matches("aws", "AWS Solutions Architect")


def test_longer_term_matches_anywhere_ignoring_case():
    assert jobs_story.matches("humanoid", "Software Engineer, Humanoids")
    assert jobs_story.matches("agentic payments", "Lead PM - Agentic Payments")
    assert not jobs_story.matches("humanoid", "Warehouse Associate")


def test_every_comparison_subrun_knows_every_entity(monkeypatch, tmp_path):
    from tests.competitor_cli_audit_helpers import run_competitor_cli

    observed = run_competitor_cli(monkeypatch, tmp_path)

    for entity in ("MainBrand", "PeerOne", "PeerTwo"):
        assert observed.calls[entity]["config"]["_comparison_entities"] == [
            "MainBrand", "PeerOne", "PeerTwo",
        ]

"""Regression tests for PR 27 CLI contracts and Polymarket/quote-pool rendering.

Covers:
- HIGH-008: `--search stocktwits` and `LAST30DAYS_DEFAULT_SEARCH=stocktwits`
- HIGH-009: `--emit=brief` rejected pre-run on comparison / competitor runs and cached freshness
- HIGH-010: comparison `--save-dir` saves full raw evidence to main `{slug}-raw.md`
- HIGH-012: normalized `DEFAULT_MEMORY_DIR` checks keep store, library search/feed/context, and save_output unified
- HIGH-041: `_polymarket_matched_part` label-first matching and displayed `[:3]` slice deduplication
- HIGH-042: `_polymarket_top_markets` skips unpriced/impossible_set/duplicate items before applying `limit`
- HIGH-043: `_render_top_comments` grounds on stripped quote text and exact whole-word matching for short comparison entities
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import library, library_index, pipeline, render, schema


def _make_item(
    item_id: str,
    *,
    source: str = "reddit",
    title: str = "Title",
    url: str = "https://www.reddit.com/r/test/comments/abc/title/",
    author: str = "op",
    score: int = 500,
    snippet: str = "Snippet text",
    metadata: dict | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=snippet,
        url=url,
        author=author,
        container="test",
        published_at="2026-11-10",
        date_confidence="high",
        engagement={"score": score},
        snippet=snippet,
        metadata=metadata or {},
    )


def _make_candidate(
    cand_id: str,
    item: schema.SourceItem,
    *,
    final_score: float = 80.0,
) -> schema.Candidate:
    cand = schema.Candidate(
        candidate_id=cand_id,
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=["primary"],
        native_ranks={f"primary:{item.source}": 1},
        local_relevance=0.9,
        freshness=90,
        engagement=80,
        source_quality=0.8,
        rrf_score=0.02,
        sources=[item.source],
        source_items=[item],
    )
    cand.final_score = final_score
    return cand


def _make_report(
    topic: str,
    *,
    candidates: list[schema.Candidate] | None = None,
    clusters: list[schema.Cluster] | None = None,
    items_by_source: dict[str, list[schema.SourceItem]] | None = None,
    resolved: dict | None = None,
) -> schema.Report:
    report = schema.Report(
        topic=topic,
        range_from="2026-10-15",
        range_to="2026-11-15",
        generated_at="2026-11-15T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="fixture",
            rerank_model="fixture",
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic=topic,
            subqueries=[],
            source_weights={},
        ),
        clusters=list(clusters or []),
        ranked_candidates=list(candidates or []),
        items_by_source=dict(items_by_source or {}),
        errors_by_source={},
    )
    if resolved is not None:
        report.artifacts["resolved"] = resolved
    return report


# ---------------------------------------------------------------------------
# HIGH-008: --search stocktwits & LAST30DAYS_DEFAULT_SEARCH=stocktwits
# ---------------------------------------------------------------------------


class TestHigh008StocktwitsSearchFlag:
    def test_parse_search_flag_accepts_stocktwits_alone_and_combined(self):
        assert cli.parse_search_flag("stocktwits") == ["stocktwits"]
        assert cli.parse_search_flag("reddit,stocktwits") == ["reddit", "stocktwits"]
        assert cli.parse_search_flag("STOCKTWITS,hn") == ["stocktwits", "hackernews"]

    def test_default_search_config_accepts_stocktwits(self):
        resolved = cli.resolve_requested_sources(
            None, {"LAST30DAYS_DEFAULT_SEARCH": "reddit,stocktwits"}
        )
        assert resolved == ["reddit", "stocktwits"]

    def test_unknown_source_still_rejected(self):
        with pytest.raises(SystemExit, match="Unknown search source in --search: bogus"):
            cli.parse_search_flag("reddit,bogus")

    def test_mock_available_sources_unchanged_without_stocktwits(self):
        assert "stocktwits" in pipeline.KNOWN_SEARCH_SOURCES
        assert "stocktwits" not in pipeline.MOCK_AVAILABLE_SOURCES


# ---------------------------------------------------------------------------
# HIGH-009: --emit=brief rejected pre-run on comparison / competitor runs
# ---------------------------------------------------------------------------


class TestHigh009ComparisonEmitBriefPreRunGate:
    @pytest.mark.parametrize(
        "argv",
        [
            ["OpenAI vs Anthropic", "--emit=brief"],
            ["OpenAI", "--competitors", "2", "--emit=brief"],
            ["OpenAI", "--competitors-list", "Anthropic,xAI", "--emit=brief"],
            ["OpenAI", "--competitors-plan", '{"Anthropic":{}}', "--emit=brief"],
        ],
    )
    def test_live_comparison_brief_fails_fast_before_pipeline(
        self, argv: list[str], monkeypatch, capsys
    ):
        monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
        monkeypatch.setattr(cli.env, "get_config", lambda **_k: {})
        monkeypatch.setattr(cli.env, "hosted_backend_configured", lambda: False)
        monkeypatch.setattr(
            cli.pipeline,
            "diagnose",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("diagnose must not run")),
        )
        monkeypatch.setattr(
            cli.pipeline,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("pipeline.run must not run")),
        )
        monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])

        rc = cli.main()

        assert rc == 2
        assert "--emit=brief is not supported for comparison" in capsys.readouterr().err

    def test_cached_freshness_comparison_brief_fails_before_verify(
        self, monkeypatch, capsys
    ):
        report_a = _make_report("OpenAI")
        report_b = _make_report("Anthropic")
        cached = cli._CachedReport(
            report=report_a,
            entity_reports=[("OpenAI", report_a), ("Anthropic", report_b)],
            path=Path("/tmp/fake-last-report.json"),
            x_envelope_sha256="",
            fingerprint="a" * 32,
        )
        monkeypatch.setattr(cli, "_load_followup_report", lambda _a, _c: cached)
        monkeypatch.setattr(
            cli,
            "_verify_report_set",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("verify must not run")),
        )
        args = cli.build_parser().parse_args(["--verify-freshness", "--emit=brief"])

        rc = cli._run_cached_freshness(args, {})

        assert rc == 2
        assert "--emit=brief is not supported for comparison" in capsys.readouterr().err

    def test_render_save_and_print_comparison_brief_returns_exit_2(self, capsys):
        report_a = _make_report("OpenAI")
        report_b = _make_report("Anthropic")
        args = cli.build_parser().parse_args(["OpenAI vs Anthropic", "--emit=brief"])

        rc = cli._render_save_and_print(
            args,
            report_a,
            [("OpenAI", report_a), ("Anthropic", report_b)],
            None,
            {},
        )

        assert rc == 2
        assert "--emit=brief is not supported for comparison" in capsys.readouterr().err

    def test_single_topic_brief_still_allowed(self, capsys):
        report = _make_report("OpenAI")
        args = cli.build_parser().parse_args(["OpenAI", "--emit=brief"])

        rc = cli._render_save_and_print(args, report, None, None, {})

        assert rc == 0
        assert capsys.readouterr().out.strip()

    def test_diagnose_with_vs_topic_and_brief_still_succeeds(self, monkeypatch, capsys):
        monkeypatch.setattr(cli.env, "get_config", lambda **_k: {})
        monkeypatch.setattr(cli.env, "hosted_backend_configured", lambda: False)
        monkeypatch.setattr(cli.pipeline, "diagnose", lambda *a, **k: {"available_sources": ["reddit"]})
        monkeypatch.setattr(
            sys, "argv", ["last30days.py", "OpenAI vs Anthropic", "--emit=brief", "--diagnose"]
        )

        rc = cli.main()

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["available_sources"] == ["reddit"]


# ---------------------------------------------------------------------------
# HIGH-010: comparison --save-dir writes full raw evidence to main {slug}-raw.md
# ---------------------------------------------------------------------------


class TestHigh010ComparisonMainRawFullEvidence:
    def _build_rich_entity_report(self, entity: str) -> schema.Report:
        candidates: list[schema.Candidate] = []
        clusters: list[schema.Cluster] = []
        items: list[schema.SourceItem] = []
        # Create 14 clusters/items so render_full carries items beyond compact truncation.
        for idx in range(1, 15):
            item = _make_item(
                f"{entity.lower()}-item-{idx}",
                title=f"{entity} exclusive story number {idx}",
                url=f"https://www.reddit.com/r/tech/comments/{entity.lower()}{idx}/story/",
                snippet=f"Detailed evidence body for {entity} item {idx}.",
            )
            cand = _make_candidate(
                f"{entity.lower()}-cand-{idx}", item, final_score=100.0 - idx
            )
            cluster = schema.Cluster(
                cluster_id=f"{entity.lower()}-cluster-{idx}",
                title=f"{entity} storyline {idx}",
                candidate_ids=[cand.candidate_id],
                representative_ids=[cand.candidate_id],
                sources=["reddit"],
                score=100.0 - idx,
            )
            items.append(item)
            candidates.append(cand)
            clusters.append(cluster)
        return _make_report(
            entity,
            candidates=candidates,
            clusters=clusters,
            items_by_source={"reddit": items},
            resolved={
                "entity": entity,
                "x_handle": entity.lower(),
                "subreddits": [entity.lower()],
                "github_user": "",
                "github_repos": [],
                "trustpilot_domain": "",
                "context": "",
            },
        )

    @pytest.mark.parametrize("emit", ["compact", "md", "context"])
    def test_comparison_markdown_save_writes_full_raw_for_main_and_comparison_to_stdout(
        self, emit: str, tmp_path: Path, capsys
    ):
        main_report = self._build_rich_entity_report("OpenAI")
        peer_report = self._build_rich_entity_report("Anthropic")
        entity_reports = [("OpenAI", main_report), ("Anthropic", peer_report)]
        args = cli.build_parser().parse_args(
            ["OpenAI vs Anthropic", f"--emit={emit}", "--save-dir", str(tmp_path)]
        )

        rc = cli._render_save_and_print(args, main_report, entity_reports, None, {})
        captured = capsys.readouterr()

        assert rc == 0
        main_file = tmp_path / "openai-raw.md"
        peer_file = tmp_path / "anthropic-raw.md"
        assert main_file.is_file()
        assert peer_file.is_file()

        main_text = main_file.read_text(encoding="utf-8")
        peer_text = peer_file.read_text(encoding="utf-8")

        # Main raw file carries full render_full evidence (matching peer raw file).
        assert "## Resolved Entities" in main_text
        assert "**OpenAI**" in main_text
        assert "## All Items by Source" in main_text
        assert "OpenAI exclusive story number 14" in main_text
        assert "| Dimension | OpenAI | Anthropic |" not in main_text

        assert "## Resolved Entities" in peer_text
        assert "**Anthropic**" in peer_text
        assert "Anthropic exclusive story number 14" in peer_text

        # Stdout still renders the merged comparison output citing the saved path.
        if emit == "context":
            assert "Comparison: OpenAI vs Anthropic" in captured.out
        else:
            assert "| Dimension | OpenAI | Anthropic |" in captured.out
            assert "openai-raw.md" in captured.out

    def test_comparison_json_and_html_saves_preserve_wire_format(self, tmp_path: Path):
        main_report = self._build_rich_entity_report("OpenAI")
        peer_report = self._build_rich_entity_report("Anthropic")
        entity_reports = [("OpenAI", main_report), ("Anthropic", peer_report)]

        json_dir = tmp_path / "json_out"
        args_json = cli.build_parser().parse_args(
            ["OpenAI vs Anthropic", "--emit=json", "--save-dir", str(json_dir)]
        )
        assert cli._render_save_and_print(args_json, main_report, entity_reports, None, {}) == 0
        saved_json = json.loads((json_dir / "openai-raw.json").read_text(encoding="utf-8"))
        assert saved_json["comparison"] is True
        assert saved_json["entities"] == ["OpenAI", "Anthropic"]

        html_dir = tmp_path / "html_out"
        args_html = cli.build_parser().parse_args(
            ["OpenAI vs Anthropic", "--emit=html", "--save-dir", str(html_dir)]
        )
        assert cli._render_save_and_print(args_html, main_report, entity_reports, None, {}) == 0
        saved_html = (html_dir / "openai-vs-anthropic-raw-html.html").read_text(encoding="utf-8")
        assert "<html" in saved_html.lower()
        assert "Anthropic" in saved_html


# ---------------------------------------------------------------------------
# HIGH-012: Normalized DEFAULT_MEMORY_DIR checks keep library/store unified
# ---------------------------------------------------------------------------


class TestHigh012DefaultMemoryDirNormalization:
    def test_is_scoped_save_dir_normalizes_default_paths_and_symlinks(
        self, tmp_path: Path, monkeypatch
    ):
        default_dir = tmp_path / "Documents" / "Last30Days"
        default_dir.mkdir(parents=True)
        symlink_dir = tmp_path / "link-to-default"
        symlink_dir.symlink_to(default_dir)
        scoped_dir = tmp_path / "client-a"
        scoped_dir.mkdir()

        monkeypatch.setattr(library, "DEFAULT_MEMORY_DIR", default_dir)

        assert pipeline.is_scoped_save_dir(None) is False
        assert pipeline.is_scoped_save_dir("") is False
        assert pipeline.is_scoped_save_dir("   ") is False
        assert pipeline.is_scoped_save_dir(default_dir) is False
        assert pipeline.is_scoped_save_dir(str(default_dir) + os.sep) is False
        assert pipeline.is_scoped_save_dir(str(default_dir / "." / ".." / "Last30Days")) is False
        assert pipeline.is_scoped_save_dir(symlink_dir) is False
        assert pipeline.is_scoped_save_dir(scoped_dir) is True

        assert cli._scoped_store_db(argparse.Namespace(save_dir=str(default_dir))) is None
        assert cli._scoped_store_db(argparse.Namespace(save_dir=str(symlink_dir))) is None
        assert cli._scoped_store_db(argparse.Namespace(save_dir=str(scoped_dir))) == (
            scoped_dir.resolve() / "research.db"
        )

    def test_library_context_index_path_and_loader_unify_default_memory_dir(
        self, tmp_path: Path, monkeypatch
    ):
        default_dir = tmp_path / "Documents" / "Last30Days"
        default_dir.mkdir(parents=True)
        scoped_dir = tmp_path / "client-a"
        scoped_dir.mkdir()

        monkeypatch.setattr(library, "DEFAULT_MEMORY_DIR", default_dir)
        cfg = {"LAST30DAYS_LIBRARY_CONTEXT": "on"}

        non_canonical_default = str(default_dir / ".." / "Last30Days")
        assert (
            pipeline.library_context_index_path(cfg, non_canonical_default)
            == library_index.DEFAULT_LIBRARY_DB
        )
        assert pipeline.library_context_index_path(cfg, str(scoped_dir)) == (
            scoped_dir.resolve() / ".last30days-library.db"
        )
        assert pipeline.library_context_index_path(cfg, "") is None

        sync_calls: list[tuple[Path, Path, Path]] = []
        search_calls: list[tuple[Path, Path]] = []

        def fake_sync(memory_dir, briefs_dir, *, db_path):
            sync_calls.append((Path(memory_dir), Path(briefs_dir), Path(db_path)))

        def fake_search(query_text, *, limit, db_path, store_db_path):
            search_calls.append((Path(db_path), Path(store_db_path)))
            return []

        monkeypatch.setattr(pipeline.library_index, "sync_library", fake_sync)
        monkeypatch.setattr(pipeline.library_index, "search", fake_search)

        pipeline._load_library_context(
            topic="MCP servers",
            config=cfg,
            mock=False,
            internal_subrun=False,
            x_handle=None,
            github_user=None,
            github_repos=None,
            save_dir=non_canonical_default,
        )

        assert sync_calls == [
            (
                Path(non_canonical_default),
                library.DEFAULT_BRIEFS_DIR,
                library_index.DEFAULT_LIBRARY_DB,
            )
        ]
        assert search_calls == [
            (library_index.DEFAULT_LIBRARY_DB, library_index.DEFAULT_STORE_DB)
        ]

    def test_library_search_feed_and_save_output_unify_on_default_memory_dir(
        self, tmp_path: Path, monkeypatch
    ):
        default_dir = tmp_path / "Documents" / "Last30Days"
        default_dir.mkdir(parents=True)
        non_canonical = str(default_dir / "." / ".." / "Last30Days")
        monkeypatch.setattr(library, "DEFAULT_MEMORY_DIR", default_dir)

        # 1. library search via LAST30DAYS_MEMORY_DIR == DEFAULT_MEMORY_DIR
        search_kwargs: dict[str, Path] = {}

        def fake_sync_and_search(query, *, memory_dir, briefs_dir, db_path, store_db_path):
            search_kwargs["memory_dir"] = Path(memory_dir)
            search_kwargs["briefs_dir"] = Path(briefs_dir)
            search_kwargs["db_path"] = Path(db_path)
            search_kwargs["store_db_path"] = Path(store_db_path)
            return [], mock.Mock(notes=[], rebuilt=False)

        monkeypatch.setattr(library_index, "sync_and_search", fake_sync_and_search)
        monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", non_canonical)
        monkeypatch.setattr(cli.env, "get_config", lambda **_k: {})
        monkeypatch.setattr(sys, "argv", ["last30days.py", "library", "search", "MCP"])
        assert cli.main() == 0
        assert search_kwargs["briefs_dir"] == library.DEFAULT_BRIEFS_DIR
        assert search_kwargs["db_path"] == library_index.DEFAULT_LIBRARY_DB
        assert search_kwargs["store_db_path"] == library_index.DEFAULT_STORE_DB

        # 2. library feed with --save-dir == DEFAULT_MEMORY_DIR includes DEFAULT_BRIEFS_DIR
        scanned_briefs: list[Path] = []

        def fake_scan_library(memory_dir, briefs_dir):
            scanned_briefs.append(Path(briefs_dir))
            return [], []

        monkeypatch.setattr(library, "scan_library", fake_scan_library)
        args_feed = cli.build_parser().parse_args(
            ["library", "feed", "--save-dir", non_canonical]
        )
        assert cli._run_library_feed(args_feed, {}) == 0
        assert scanned_briefs == [library.DEFAULT_BRIEFS_DIR]

        # 3. save_output into DEFAULT_MEMORY_DIR syncs default index (without scoped briefs override)
        sync_calls: list[tuple] = []
        monkeypatch.setattr(
            library_index,
            "sync_library",
            lambda *a, **k: sync_calls.append((a, k)),
        )
        report = _make_report("Test Topic")
        saved = cli.save_output(report, "md", non_canonical)
        assert saved.is_file()
        assert sync_calls == [((default_dir.resolve(),), {})]


# ---------------------------------------------------------------------------
# HIGH-041: _polymarket_matched_part label-first match & displayed [:3] slice
# ---------------------------------------------------------------------------


class TestHigh041PolymarketMatchedPart:
    def test_tied_price_contender_does_not_steal_rival_move_or_duplicate_self(self):
        rival = {"label": "Kansas City Chiefs", "price": 0.14, "move": [3.5, "this week"]}
        subject = {"label": "San Francisco 49ers", "price": 0.14, "move": [-1.5, "this week"]}
        third = {"label": "Buffalo Bills", "price": 0.12, "move": None}
        item = _make_item(
            "pm-1",
            source="polymarket",
            title="Pro Football: 2027 Champion",
            url="https://polymarket.com/event/pro-football-2027",
            metadata={
                "odds": {
                    "kind": "outcomes",
                    "subject": "Pro Football: 2027 Champion",
                    "parts": [rival, subject, third],
                },
                "matched_market": {
                    "label": "San Francisco 49ers",
                    "price": 0.14,
                    "contender": True,
                    "rank": 2,
                    "race_size": 32,
                },
            },
        )

        line = render._polymarket_odds_line(item, "2026")

        assert line.count("San Francisco 49ers") == 1
        assert "San Francisco 49ers 14%, #2 of 32 (down 1.5 pts this week)" in line
        assert "Kansas City Chiefs at 14% (up 3.5 pts this week)" in line
        assert "Buffalo Bills at 12%" in line

    def test_tied_price_non_contender_outside_top3_not_suppressed_by_rival(self):
        parts = [
            {"label": "Kansas City Chiefs", "price": 0.14, "move": [2.0, "today"]},
            {"label": "Buffalo Bills", "price": 0.12, "move": None},
            {"label": "Detroit Lions", "price": 0.10, "move": None},
        ]
        item = _make_item(
            "pm-2",
            source="polymarket",
            title="Pro Football: 2027 Champion",
            url="https://polymarket.com/event/pro-football-2027",
            metadata={
                "odds": {
                    "kind": "outcomes",
                    "subject": "Pro Football: 2027 Champion",
                    "parts": parts,
                },
                "matched_market": {
                    "label": "Baltimore Ravens",
                    "price": 0.14,
                    "contender": False,
                    "rank": 4,
                    "race_size": 32,
                },
            },
        )

        matched = render._polymarket_matched_part(item, parts)
        assert matched is not None
        assert matched[0] == "Baltimore Ravens 14%"
        assert matched[1] is None

        line = render._polymarket_odds_line(item, "2026")
        assert "Baltimore Ravens 14%" in line
        assert "Kansas City Chiefs at 14% (up 2 pts today)" in line

    def test_matched_outcome_at_index_3_not_dropped_by_pre_slice_check(self):
        parts = [
            {"label": "Team A", "price": 0.30, "move": None},
            {"label": "Team B", "price": 0.25, "move": None},
            {"label": "Team C", "price": 0.20, "move": None},
            {"label": "Team D", "price": 0.10, "move": [1.0, "today"]},
        ]
        item = _make_item(
            "pm-3",
            source="polymarket",
            title="Championship Winner",
            url="https://polymarket.com/event/championship",
            metadata={
                "odds": {
                    "kind": "outcomes",
                    "subject": "Championship Winner",
                    "parts": parts,
                },
                "matched_market": {
                    "label": "Team D",
                    "price": 0.10,
                    "contender": False,
                },
            },
        )

        line = render._polymarket_odds_line(item, "2026")

        assert "Team D 10%" in line
        assert "Team A at 30%" in line
        assert "Team B at 25%" in line

    def test_casefold_whitespace_label_match_and_unlabeled_price_fallback(self):
        # Case/whitespace difference on labeled entry still deduplicates.
        entry_labeled = {"label": "San Francisco 49ers", "price": 0.145, "move": [1.0, "today"]}
        item_labeled = _make_item(
            "pm-4",
            source="polymarket",
            title="NFL Champion",
            metadata={
                "matched_market": {
                    "label": "  san   francisco 49ers ",
                    "price": 0.145,
                    "contender": True,
                    "rank": 1,
                    "race_size": 32,
                }
            },
        )
        matched = render._polymarket_matched_part(item_labeled, [entry_labeled])
        assert matched is not None
        assert matched[1] is entry_labeled
        assert "(up 1 pt today)" in matched[0]

        # Unlabeled single Yes/No entry still matches via price fallback.
        entry_unlabeled = {"price": 0.74, "move": [2.0, "today"]}
        item_single = _make_item(
            "pm-5",
            source="polymarket",
            title="Will Waymo launch in Denver?",
            metadata={"matched_market": {"label": "", "price": 0.74}},
        )
        assert render._polymarket_matched_part(item_single, [entry_unlabeled]) is None

    def test_boolean_rank_and_size_rejected(self):
        item = _make_item(
            "pm-bool",
            source="polymarket",
            title="NFL Champion",
            metadata={
                "matched_market": {
                    "label": "49ers",
                    "price": 0.2,
                    "contender": True,
                    "rank": True,
                    "race_size": True,
                }
            },
        )
        matched = render._polymarket_matched_part(item, [])
        assert matched is not None
        assert "#True" not in matched[0]
        assert matched[0] == "49ers 20%"
        # Withheld odds path also rejects bool rank.
        assert render._polymarket_odds_line(item, "2026") == ""


# ---------------------------------------------------------------------------
# HIGH-042: _polymarket_top_markets skips unpriced/impossible/dupes before limit
# ---------------------------------------------------------------------------


class TestHigh042PolymarketTopMarketsNoPreSlice:
    def _pm_item(
        self,
        item_id: str,
        question: str,
        *,
        outcome_prices=None,
        headline=None,
        volume: float = 100.0,
    ) -> schema.SourceItem:
        meta = {
            "question": question,
            "volume24hr": volume,
            "outcome_prices": [] if outcome_prices is None else outcome_prices,
            "headline": headline,
        }
        return _make_item(
            item_id,
            source="polymarket",
            title=question,
            url=f"https://polymarket.com/event/{item_id}",
            metadata=meta,
        )

    def test_unpriced_and_impossible_set_items_do_not_consume_footer_slots(self):
        items = [
            self._pm_item("unpriced-1", "Will Acme close at ___?", outcome_prices=[], volume=900),
            self._pm_item("unpriced-2", "Will Acme hit ___?", outcome_prices=[], volume=800),
            self._pm_item("unpriced-3", "Will Acme trade at ___?", outcome_prices=[], volume=700),
            self._pm_item(
                "priced-1",
                "Will Acme IPO in 2026?",
                headline={"question": "Will Acme IPO in 2026?", "price": 0.42, "label": ""},
                volume=600,
            ),
            self._pm_item(
                "priced-2",
                "Will Acme ship v2 in 2026?",
                headline={"question": "Will Acme ship v2 in 2026?", "price": 0.65, "label": ""},
                volume=500,
            ),
            self._pm_item(
                "priced-3",
                "Will Acme acquire Beta in 2026?",
                headline={"question": "Will Acme acquire Beta in 2026?", "price": 0.18, "label": ""},
                volume=400,
            ),
        ]

        summaries = render._polymarket_top_markets(items, limit=3)

        assert summaries == [
            "Acme IPO in 2026 42%",
            "Acme ship v2 in 2026 65%",
            "Acme acquire Beta in 2026 18%",
        ]

    def test_duplicate_and_malformed_entries_do_not_starve_later_valid_markets(self):
        items = [
            self._pm_item(
                "dup-1",
                "Will Acme IPO in 2026?",
                outcome_prices=[("Yes", 0.40)],
                volume=900,
            ),
            self._pm_item(
                "dup-2",
                "Will Acme IPO in 2026?",
                outcome_prices=[("Yes", 0.40)],
                volume=850,
            ),
            self._pm_item(
                "bad-shape",
                "Will Acme crash?",
                outcome_prices=[("only-one",), ("Yes", True), ("a", 0.2, "extra")],
                volume=800,
            ),
            self._pm_item(
                "valid-2",
                "Will Acme beat Q4 earnings?",
                outcome_prices=[("Yes", 0.71)],
                volume=700,
            ),
            self._pm_item(
                "valid-3",
                "Will Acme open a Tokyo office?",
                outcome_prices=[("Yes", 0.29)],
                volume=600,
            ),
        ]

        summaries = render._polymarket_top_markets(items, limit=3)

        assert len(summaries) == 3
        assert summaries[0].endswith("40%")
        assert summaries[1].endswith("71%")
        assert summaries[2].endswith("29%")

    def test_unshown_fourth_market_does_not_expand_first_market_disambiguation(self):
        items = [
            self._pm_item(
                "m1",
                "Will Fed cut rates by March 31, 2027?",
                outcome_prices=[("Yes", 0.25)],
                volume=900,
            ),
            self._pm_item(
                "m2",
                "Will Nvidia launch Rubin in 2026?",
                outcome_prices=[("Yes", 0.60)],
                volume=800,
            ),
            self._pm_item(
                "m3",
                "Will Apple release foldable iPhone in 2026?",
                outcome_prices=[("Yes", 0.15)],
                volume=700,
            ),
            self._pm_item(
                "m4",
                "Will Fed cut rates by June 30, 2027?",
                outcome_prices=[("Yes", 0.50)],
                volume=600,
            ),
        ]

        summaries = render._polymarket_top_markets(items, limit=3)

        assert len(summaries) == 3
        assert "March 31, 2027" not in summaries[0]


# ---------------------------------------------------------------------------
# HIGH-043: Comparison comment grounding (stripped body + short entities)
# ---------------------------------------------------------------------------


class TestHigh043ComparisonCommentGrounding:
    def _reddit_report(self, topic: str, title: str, comments: list[dict]) -> schema.Report:
        item = _make_item(
            "r-1",
            source="reddit",
            title=title,
            url="https://www.reddit.com/r/programming/comments/abc123/thread/",
            metadata={"top_comments": comments},
        )
        cand = _make_candidate("c-1", item)
        return _make_report(topic, candidates=[cand])

    @pytest.mark.parametrize(
        ("topic", "short_entity", "long_entity", "long_only_comment", "substring_trap", "short_comment"),
        [
            (
                "Go vs Rust",
                "Go",
                "Rust",
                "Rust borrow checker caught three data races for us.",
                "I am going to move my Cargo project next quarter.",
                "Go compiles in seconds and goroutines keep services simple.",
            ),
            (
                "AWS vs Azure",
                "AWS",
                "Azure",
                "Azure AD integration is the reason enterprise teams pick it.",
                "There are obvious flaws in how billing alerts work here.",
                "AWS Lambda cold starts improved dramatically this year.",
            ),
            (
                "AMD vs Nvidia",
                "AMD",
                "Nvidia",
                "Nvidia CUDA tooling still dominates ML training clusters.",
                "Random unrelated remark about hardware availability today.",
                "AMD ROCm support has gotten much better on Linux lately.",
            ),
            (
                "OpenAI vs X",
                "X",
                "OpenAI",
                "OpenAI keeps expanding its enterprise agent API lineup.",
                "Extra context on latency benchmarks from yesterday evening.",
                "On X the developer community reacted immediately to the news.",
            ),
        ],
    )
    def test_short_entity_comparison_requires_whole_word_match(
        self,
        topic: str,
        short_entity: str,
        long_entity: str,
        long_only_comment: str,
        substring_trap: str,
        short_comment: str,
    ):
        report = self._reddit_report(
            topic,
            topic,
            [
                {"author": "u1", "score": 300, "depth": 0, "excerpt": long_only_comment},
                {"author": "u2", "score": 250, "depth": 0, "excerpt": substring_trap},
                {"author": "u3", "score": 200, "depth": 0, "excerpt": "Haha great post, totally agree!"},
                {"author": "u4", "score": 150, "depth": 0, "excerpt": short_comment},
            ],
        )

        lines = render._render_top_comments(report)
        joined = "\n".join(lines)

        assert "Haha great post" not in joined
        assert substring_trap[:20] not in joined

        long_line = next(line for line in lines if long_only_comment[:20] in line)
        assert f"(about {long_entity})" in long_line
        assert short_entity not in long_line.split(" (about ")[-1]

        short_line = next(line for line in lines if short_comment[:20] in line)
        assert f"(about {short_entity})" in short_line
        assert long_entity not in short_line.split(" (about ")[-1]

    def test_parent_quote_and_raw_url_stripped_before_grounding(self):
        # Comparison: entity appears ONLY in stripped parent quote or stripped URL.
        comp_report = self._reddit_report(
            "Claude vs OpenAI",
            "Claude vs OpenAI coding benchmark",
            [
                {
                    "author": "u1",
                    "score": 400,
                    "depth": 0,
                    "excerpt": "> Claude is way faster on refactors\nLOL I completely agree with this!",
                },
                {
                    "author": "u2",
                    "score": 350,
                    "depth": 0,
                    "excerpt": "Check out this benchmark https://claude.ai/docs/overview right now!",
                },
                {
                    "author": "u3",
                    "score": 300,
                    "depth": 0,
                    "excerpt": "> OpenAI launched a new tier\nClaude handles large repos much better for me.",
                },
                {
                    "author": "u4",
                    "score": 250,
                    "depth": 0,
                    "excerpt": "OpenAI latency on streaming tool calls improved a lot this month.",
                },
            ],
        )

        lines = render._render_top_comments(comp_report)
        joined = "\n".join(lines)

        assert "LOL I completely agree" not in joined
        assert "Check out this benchmark" not in joined
        real_line = next(line for line in lines if "Claude handles large repos" in line)
        assert "(about Claude)" in real_line
        assert "OpenAI" not in real_line.split(" (about ")[-1]

        # Single-topic deep reply (no title exemption): parent quote and URL must not ground it.
        single_report = self._reddit_report(
            "Claude",
            "Claude 4.7 discussion",
            [
                {
                    "author": "u1",
                    "score": 400,
                    "depth": 2,
                    "excerpt": "> Claude is faster\nTotally agree with your point here!",
                },
                {
                    "author": "u2",
                    "score": 350,
                    "depth": 2,
                    "excerpt": "Look at these numbers https://claude.ai/pricing from today.",
                },
                {
                    "author": "u3",
                    "score": 300,
                    "depth": 2,
                    "excerpt": "Claude code review catches edge cases reliably.",
                },
                {
                    "author": "u4",
                    "score": 250,
                    "depth": 2,
                    "excerpt": "Claude context window stayed coherent across ten files.",
                },
            ],
        )
        single_out = "\n".join(render._render_top_comments(single_report))
        assert "Totally agree with your point" not in single_out
        assert "Look at these numbers" not in single_out
        assert "Claude code review catches edge cases reliably." in single_out

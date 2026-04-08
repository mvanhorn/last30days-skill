import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import schema


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate-synthesis-inputs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("generate_synthesis_inputs", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class GenerateSynthesisInputsV3Tests(unittest.TestCase):
    def test_main_uses_v3_report_deserializer(self):
        module = load_module()
        report = schema.Report(
            topic="test topic",
            range_from="2026-02-14",
            range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite-preview",
                rerank_model="gemini-3.1-flash-lite-preview",
            ),
            query_plan=schema.QueryPlan(
                intent="breaking_news",
                freshness_mode="strict_recent",
                cluster_mode="story",
                raw_topic="test topic",
                subqueries=[
                    schema.SubQuery(
                        label="primary",
                        search_query="test topic",
                        ranking_query="What happened with test topic?",
                        sources=["grounding"],
                    )
                ],
                source_weights={"grounding": 1.0},
            ),
            clusters=[
                schema.Cluster(
                    cluster_id="cluster-1",
                    title="Title",
                    candidate_ids=["c1"],
                    representative_ids=["c1"],
                    sources=["grounding"],
                    score=90.0,
                )
            ],
            ranked_candidates=[
                schema.Candidate(
                    candidate_id="c1",
                    item_id="i1",
                    source="grounding",
                    sources=["grounding"],
                    title="Title",
                    url="https://example.com",
                    snippet="Snippet",
                    subquery_labels=["primary"],
                    native_ranks={"primary:grounding": 1},
                    local_relevance=0.8,
                    freshness=90,
                    engagement=None,
                    source_quality=1.0,
                    rrf_score=0.02,
                    rerank_score=91.0,
                    final_score=90.0,
                    source_items=[
                        schema.SourceItem(
                            item_id="i1",
                            source="grounding",
                            title="Title",
                            body="Body",
                            url="https://example.com",
                            published_at="2026-03-16",
                        )
                    ],
                )
            ],
            items_by_source={
                "grounding": [
                    schema.SourceItem(
                        item_id="i1",
                        source="grounding",
                        title="Title",
                        body="Body",
                        url="https://example.com",
                    )
                ]
            },
            errors_by_source={},
        )

        with tempfile.TemporaryDirectory() as tmp:
            json_dir = Path(tmp) / "json"
            compact_dir = Path(tmp) / "compact"
            json_dir.mkdir()
            (json_dir / "sample.json").write_text(json.dumps(schema.to_dict(report)))

            module.JSON_DIR = json_dir
            module.COMPACT_DIR = compact_dir

            result = module.main()

            self.assertEqual(0, result)
            output = (compact_dir / "sample.md").read_text()
            self.assertIn("# last30days-3 v3.0.0-alpha: test topic", output)


if __name__ == "__main__":
    unittest.main()

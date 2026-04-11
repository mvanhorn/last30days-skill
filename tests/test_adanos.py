import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import adanos, normalize, pipeline


class AdanosProviderTests(unittest.TestCase):
    def test_extract_tickers_handles_cashtags_and_plain_comparisons(self):
        self.assertEqual(["TSLA", "NVDA"], adanos.extract_tickers("$TSLA vs NVDA"))
        self.assertEqual(["AAPL", "MSFT"], adanos.extract_tickers("AAPL versus MSFT"))

    def test_finance_heuristic_is_conservative(self):
        self.assertTrue(adanos.looks_financial("Tesla stock sentiment"))
        self.assertTrue(adanos.looks_financial("TSLA vs NVDA"))
        self.assertFalse(adanos.looks_financial("how to deploy MCP servers"))

    @patch("lib.adanos.http.get")
    def test_search_skips_without_key(self, mock_get):
        items, artifact = adanos.search(
            "TSLA",
            ("2026-03-01", "2026-03-30"),
            {},
        )
        self.assertEqual([], items)
        self.assertEqual({}, artifact)
        mock_get.assert_not_called()

    @patch("lib.adanos.http.get")
    def test_search_skips_non_finance_query_with_key(self, mock_get):
        items, artifact = adanos.search(
            "best coffee shops in berlin",
            ("2026-03-01", "2026-03-30"),
            {"ADANOS_API_KEY": "test-key"},
        )
        self.assertEqual([], items)
        self.assertEqual("non_finance_query", artifact["skipped"])
        mock_get.assert_not_called()

    @patch("lib.adanos.http.get")
    def test_compare_query_maps_stock_rows_to_items(self, mock_get):
        mock_get.return_value = {
            "period_days": 30,
            "stocks": [
                {
                    "ticker": "TSLA",
                    "company_name": "Tesla, Inc.",
                    "buzz_score": 72.4,
                    "trend": "rising",
                    "mentions": 180,
                    "sentiment_score": 0.31,
                    "bullish_pct": 61.0,
                    "bearish_pct": 19.0,
                    "total_upvotes": 920,
                }
            ],
        }
        items, artifact = adanos.search(
            "$TSLA",
            ("2026-03-01", "2026-03-30"),
            {"ADANOS_API_KEY": "test-key", "ADANOS_PLATFORMS": "reddit"},
        )

        self.assertEqual(1, len(items))
        self.assertEqual("reddit", items[0]["platform"])
        self.assertEqual("TSLA", items[0]["ticker"])
        self.assertEqual(["TSLA"], artifact["tickers"])
        self.assertIn("/reddit/stocks/v1/compare?tickers=TSLA&days=30", mock_get.call_args.args[0])
        self.assertEqual("test-key", mock_get.call_args.kwargs["headers"]["X-API-Key"])

    @patch("lib.adanos.http.get")
    def test_search_query_uses_asset_search_when_no_ticker_is_known(self, mock_get):
        mock_get.return_value = {
            "results": [
                {
                    "ticker": "TSLA",
                    "name": "Tesla, Inc.",
                    "summary": {
                        "buzz_score": 64.0,
                        "mentions": 90,
                        "sentiment_score": 0.18,
                    },
                }
            ]
        }
        items, _artifact = adanos.search(
            "Tesla stock sentiment",
            ("2026-03-01", "2026-03-30"),
            {"ADANOS_API_KEY": "test-key", "ADANOS_PLATFORMS": "news"},
        )

        self.assertEqual(1, len(items))
        self.assertEqual("news", items[0]["platform"])
        self.assertIn("/news/stocks/v1/search?", mock_get.call_args.args[0])


class AdanosPipelineTests(unittest.TestCase):
    def test_adanos_requires_opt_in_even_with_key(self):
        config = {"ADANOS_API_KEY": "test-key"}
        self.assertNotIn("adanos", pipeline.available_sources(config))
        self.assertIn("adanos", pipeline.available_sources({**config, "INCLUDE_SOURCES": "adanos"}))
        self.assertIn("adanos", pipeline.available_sources(config, requested_sources=["adanos"]))

    @patch("lib.pipeline._retrieve_stream")
    def test_adanos_artifacts_use_source_bucket(self, mock_retrieve):
        def fake_retrieve(**kwargs):
            items, _artifact = pipeline._mock_stream_results(kwargs["source"], kwargs["subquery"])
            return items, {"label": "adanos", "resultCount": len(items)}

        mock_retrieve.side_effect = fake_retrieve
        report = pipeline.run(
            topic="TSLA stock sentiment",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["adanos"],
            mock=True,
        )

        self.assertIn("adanos", report.artifacts)
        self.assertEqual("adanos", report.artifacts["adanos"][0]["label"])


class AdanosNormalizeTests(unittest.TestCase):
    def test_normalize_adanos_preserves_sentiment_metadata(self):
        normalized = normalize.normalize_source_items(
            "adanos",
            [
                {
                    "id": "ADANOS-reddit-TSLA",
                    "ticker": "TSLA",
                    "company_name": "Tesla, Inc.",
                    "platform": "reddit",
                    "title": "TSLA Reddit market sentiment",
                    "text": "buzz_score=72.4; sentiment_score=0.31; trend=rising",
                    "date": "2026-03-30",
                    "date_confidence": "med",
                    "engagement": {"buzz_score": 72.4, "mentions": 180},
                    "metadata": {"trend": "rising"},
                }
            ],
            "2026-03-01",
            "2026-03-30",
        )

        self.assertEqual(1, len(normalized))
        item = normalized[0]
        self.assertEqual("adanos", item.source)
        self.assertEqual("Adanos Market Sentiment", item.container)
        self.assertEqual("TSLA", item.metadata["ticker"])
        self.assertEqual("reddit", item.metadata["platform"])


if __name__ == "__main__":
    unittest.main()

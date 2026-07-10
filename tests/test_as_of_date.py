# ruff: noqa: E402
"""Tests for --as-of date support."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "last30days" / "scripts"))

import last30days as cli
from lib import dates


class AsOfDateTests(unittest.TestCase):
    def test_get_date_range_with_as_of_date_and_7_days(self):
        from_date, to_date = dates.get_date_range(7, "2020-05-25")

        self.assertEqual("2020-05-18", from_date)
        self.assertEqual("2020-05-25", to_date)

    def test_get_date_range_with_as_of_date_and_30_days(self):
        from_date, to_date = dates.get_date_range(30, "2020-05-25")

        self.assertEqual("2020-04-25", from_date)
        self.assertEqual("2020-05-25", to_date)

    def test_get_date_range_rejects_invalid_as_of_date(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            dates.get_date_range(7, "2020/05/25")

    def test_build_parser_accepts_as_of_date(self):
        parser = cli.build_parser()
        args, extra = parser.parse_known_args(
            ["--as-of", "2020-05-25", "--days", "7", "OpenAI"]
        )

        self.assertEqual("2020-05-25", args.as_of_date)
        self.assertEqual(7, args.lookback_days)
        self.assertEqual(["OpenAI"], args.topic)
        self.assertEqual([], extra)

    def test_mock_json_cli_uses_as_of_date_range(self):
        result = subprocess.run(
    [
        sys.executable,
        "skills/last30days/scripts/last30days.py",
        "OpenAI",
        "--mock",
        "--emit=json",
        "--json-profile=raw",
        "--as-of",
        "2020-05-25",
        "--days",
        "7",
    ],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    check=False,
)

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)

        self.assertEqual("2020-05-18", payload["range_from"])
        self.assertEqual("2020-05-25", payload["range_to"])

    def test_cli_rejects_invalid_as_of_date(self):
        result = subprocess.run(
            [
                sys.executable,
                "skills/last30days/scripts/last30days.py",
                "OpenAI",
                "--mock",
                "--emit=json",
                "--as-of",
                "2020/05/25",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("YYYY-MM-DD", result.stderr)

    def test_days_ago_uses_reference_date(self):
        self.assertEqual(
            5,
            dates.days_ago("2020-05-20", reference_date="2020-05-25"),
        )

    def test_recency_score_uses_reference_date(self):
        self.assertEqual(
            50,
            dates.recency_score(
                "2020-05-20",
                max_days=10,
                reference_date="2020-05-25",
            ),
        )


if __name__ == "__main__":
    unittest.main()

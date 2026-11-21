# ruff: noqa: E402
"""Tests for --as-of date support."""

import io
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


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


class AsOfTodayIsLiveTests(unittest.TestCase):
    """--as-of naming today (UTC or the reader's calendar) is a live run."""

    LA = timezone(timedelta(hours=-8))

    def _fake_now(self):
        # Local Nov 13 5pm in Los Angeles is Nov 14 in UTC.
        return datetime(2026, 11, 13, 17, 0, tzinfo=self.LA)

    def _run_main(self, argv, target="_main"):
        seen = {}

        def fake(*call_args, **call_kwargs):
            seen["args"] = call_args[1] if target == "_main" else call_args[0]
            return 0

        with mock.patch.object(dates, "_local_now", self._fake_now), \
                mock.patch.object(sys, "argv", ["last30days.py", *argv]), \
                mock.patch.object(cli, target, fake):
            rc = cli.main()
        return rc, seen.get("args")

    def test_as_of_local_today_is_cleared(self):
        rc, args = self._run_main(["OpenAI", "--as-of=2026-11-13"])
        self.assertEqual(0, rc)
        self.assertIsNone(args.as_of_date)

    def test_as_of_utc_today_is_cleared_when_local_is_a_day_behind(self):
        rc, args = self._run_main(["OpenAI", "--as-of=2026-11-14"])
        self.assertEqual(0, rc)
        self.assertIsNone(args.as_of_date)

    def test_as_of_yesterday_is_kept(self):
        rc, args = self._run_main(["OpenAI", "--as-of=2026-11-12"])
        self.assertEqual(0, rc)
        self.assertEqual("2026-11-12", args.as_of_date)

    def test_past_as_of_still_skips_jobs_lane(self):
        past = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
        result = subprocess.run(
            [
                sys.executable,
                "skills/last30days/scripts/last30days.py",
                "Microsoft",
                "--mock",
                "--emit=json",
                "--json-profile=raw",
                "--search=jobs",
                "--as-of",
                past,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        report = json.loads(result.stdout)
        jobs = report["source_status"]["jobs"]
        self.assertFalse(jobs["attempted"])
        self.assertIn("past window", jobs["detail"])

    def test_as_of_today_with_discover_is_accepted(self):
        stderr = io.StringIO()
        with mock.patch.object(dates, "_local_now", self._fake_now), \
                mock.patch.object(
                    sys, "argv", ["last30days.py", "--discover", "--as-of=2026-11-13"]
                ), \
                mock.patch.object(cli, "_run_discover", return_value=0) as run_discover, \
                mock.patch.object(sys, "stderr", stderr):
            rc = cli.main()
        self.assertEqual(0, rc)
        run_discover.assert_called_once()
        self.assertNotIn("--as-of cannot be used", stderr.getvalue())

    def test_past_as_of_with_discover_is_still_rejected(self):
        stderr = io.StringIO()
        with mock.patch.object(dates, "_local_now", self._fake_now), \
                mock.patch.object(
                    sys, "argv", ["last30days.py", "--discover", "--as-of=2026-11-12"]
                ), \
                mock.patch.object(sys, "stderr", stderr):
            rc = cli.main()
        self.assertEqual(2, rc)
        self.assertIn("--as-of cannot be used", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

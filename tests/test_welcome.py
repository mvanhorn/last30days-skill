"""U1: the first-run welcome is engine-owned so the model relays it (rather than
authoring prose it reliably skips) and it cannot drift from the source set."""

import subprocess
import sys
import unittest
from pathlib import Path

from lib import setup_wizard

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "skills" / "last30days" / "scripts" / "last30days.py"


class TestWelcome(unittest.TestCase):
    def test_render_welcome_names_the_source_set(self):
        text = setup_wizard.render_welcome()
        for source in (
            "X/Twitter",
            "Reddit",
            "YouTube",
            "Digg",
            "arXiv",
            "Techmeme",
            "StockTwits",
            "Trustpilot",
            "Hacker News",
            "Polymarket",
            "GitHub",
        ):
            self.assertIn(source, text, source)

    def test_render_welcome_offers_scrapecreators(self):
        text = setup_wizard.render_welcome()
        self.assertIn("ScrapeCreators", text)
        self.assertIn("10,000 free calls", text)

    def test_welcome_command_prints_and_exits_zero(self):
        proc = subprocess.run(
            [sys.executable, str(ENGINE), "--welcome"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Welcome to /last30days!", proc.stdout)
        # The command output is exactly the engine welcome (model relays verbatim).
        self.assertEqual(proc.stdout.strip(), setup_wizard.render_welcome().strip())


if __name__ == "__main__":
    unittest.main()

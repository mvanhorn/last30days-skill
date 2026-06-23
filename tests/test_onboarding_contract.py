"""Contract tests for the consent-driven first-run onboarding in SKILL.md.

These assert the structural guarantees of Step 0: consent is requested before
any cookie read, the decline and Full Disk Access branches are documented, the
ScrapeCreators signup is gated on a consent question, and the old silent-wizard
instruction is gone. They read SKILL.md as text (the model's runtime contract),
matching tests/test_runtime_preflight_contract.py.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"


class TestOnboardingContract(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        # Scope assertions to the Step 0 section so generic substrings (e.g.
        # "setup") elsewhere in the file do not satisfy ordering checks.
        start = self.text.index("## Step 0: First-Run Setup Wizard")
        end = self.text.index("## CRITICAL: Parse User Intent", start)
        self.step0 = self.text[start:end]

    def test_cookie_consent_requested_before_setup_invocation(self):
        """The cookie-consent question must appear before the first `setup` run."""
        consent_idx = self.step0.find("Cookie consent")
        setup_idx = self.step0.find("last30days.py setup")
        self.assertGreater(consent_idx, -1, "no Cookie consent step found")
        self.assertGreater(setup_idx, -1, "no setup invocation found")
        self.assertLess(
            consent_idx, setup_idx,
            "cookie consent must be requested before the setup command",
        )

    def test_decline_branch_uses_from_browser_off(self):
        """Declining cookies must route to FROM_BROWSER=off (skip reads, keep installs)."""
        self.assertIn("FROM_BROWSER=off", self.step0)

    def test_full_disk_access_remediation_present(self):
        """The macOS permission-denied remediation must be documented."""
        self.assertIn("Permission denied reading Cookies.binarycookies", self.step0)
        self.assertIn("Full Disk Access", self.step0)

    def test_scrapecreators_signup_gated_on_consent(self):
        """The signup runs `setup --github` and is offered after a consent question."""
        self.assertIn("setup --github", self.step0)
        offer_idx = self.step0.find("ScrapeCreators signup offer")
        github_idx = self.step0.find("setup --github")
        self.assertGreater(offer_idx, -1, "no ScrapeCreators signup offer step")
        self.assertLess(
            offer_idx, github_idx,
            "the signup offer/consent must precede the --github invocation",
        )

    def test_signup_does_not_hardcode_credit_count(self):
        """Onboarding copy must not assert an unverified credit number."""
        self.assertNotIn("1000 free credit", self.step0)
        self.assertNotIn("1000 credits", self.step0)

    def test_old_silent_wizard_instruction_removed(self):
        """The misleading 'follow the wizard's prompts' line must be gone."""
        self.assertNotIn("Follow the wizard's prompts end-to-end", self.text)

    def test_consent_is_conversational_contract_documented(self):
        """The named onboarding contract explains why consent is in-chat."""
        self.assertIn("Named onboarding contract", self.step0)
        self.assertIn("non-interactive subprocess", self.step0)


if __name__ == "__main__":
    unittest.main()

"""Contract tests for the restored first-run NUX wizard in SKILL.md.

Step 0 has two branches: a **Claude Code Modal Flow** (AskUserQuestion-driven,
the restored v3.0.0 NUX) and a **Non-Modal Prose Flow** for hosts without modals
(OpenClaw, Codex, Cursor, Gemini CLI). These tests assert the structural
guarantees of both branches, plus the cross-cutting copy rules: the hard
"Step 0 before Step 1" gate, Digg threaded alongside yt-dlp, the 10,000-free-calls
credit count, and Threads/Pinterest kept out of the onboarding offers. They read
SKILL.md as text - the model's runtime contract - matching
tests/test_runtime_preflight_contract.py.

These lock the flow against silent re-erosion (the failure mode that orphaned the
wizard in PR #659 and flattened it before this restoration).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"


class TestOnboardingContract(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")
        # Scope assertions to Step 0 so generic substrings elsewhere in the file
        # do not satisfy ordering/presence checks.
        start = self.text.index("## Step 0: First-Run Setup Wizard")
        end = self.text.index("## CRITICAL: Parse User Intent", start)
        self.step0 = self.text[start:end]
        # Branch slices.
        modal_start = self.step0.index("### Claude Code Modal Flow")
        prose_start = self.step0.index("### Non-Modal Prose Flow")
        manual_start = self.step0.index("### Manual Setup Guide")
        self.modal = self.step0[modal_start:prose_start]
        self.prose = self.step0[prose_start:manual_start]
        self.manual = self.step0[manual_start:]

    # --- Platform split + hard gate ---

    def test_platform_split_present(self):
        """Step 0 routes modal-capable hosts and prose hosts to distinct flows."""
        self.assertIn("Platform split", self.step0)
        self.assertIn("### Claude Code Modal Flow", self.step0)
        self.assertIn("### Non-Modal Prose Flow", self.step0)

    def test_hard_gate_step0_before_step1(self):
        """The erosion-resistant gate that orphaned the wizard in #659 is restored."""
        self.assertIn("ALWAYS execute Step 0 BEFORE Step 1", self.step0)

    # --- Modal flow: the restored NUX, stages in order ---

    def test_modal_flow_stage_order(self):
        """Welcome -> setup modal -> cookie consent -> SC offer -> opt-in -> picker."""
        anchors = [
            "Welcome to /last30days!",
            "How would you like to set up?",
            "scan your browser",  # cookie-consent modal
            "Want to add TikTok, Instagram, and the ScrapeCreators backups?",  # SC offer
            "Which ScrapeCreators sources do you want on?",  # source opt-in
            "What do you want to research first?",  # topic picker
        ]
        idxs = [self.modal.find(a) for a in anchors]
        for a, i in zip(anchors, idxs):
            self.assertGreater(i, -1, f"modal flow missing stage anchor: {a!r}")
        self.assertEqual(idxs, sorted(idxs), "modal flow stages are out of order")

    def test_modal_uses_askuserquestion(self):
        self.assertIn("AskUserQuestion", self.modal)

    def test_modal_cookie_consent_before_setup(self):
        consent = self.modal.find("scan your browser")
        setup = self.modal.find("last30days.py setup")
        self.assertGreater(consent, -1, "no cookie-consent modal in modal flow")
        self.assertGreater(setup, -1, "no setup invocation in modal flow")
        self.assertLess(consent, setup, "cookie consent must precede setup in modal flow")

    def test_topic_picker_skips_when_topic_supplied(self):
        """The picker documents skipping when the user already gave a topic."""
        self.assertIn("What do you want to research first?", self.modal)
        self.assertIn("SKIP this picker", self.modal)

    # --- Prose flow: same work, modal-free ---

    def test_prose_flow_has_no_modals(self):
        self.assertNotIn("AskUserQuestion", self.prose)

    def test_prose_cookie_consent_before_setup(self):
        consent = self.prose.find("Cookie consent")
        setup = self.prose.find("last30days.py setup")
        self.assertGreater(consent, -1, "no cookie-consent step in prose flow")
        self.assertGreater(setup, -1, "no setup invocation in prose flow")
        self.assertLess(consent, setup, "cookie consent must precede setup in prose flow")

    def test_prose_decline_uses_from_browser_off(self):
        self.assertIn("FROM_BROWSER=off", self.prose)

    # --- Full Disk Access remediation (both branches) ---

    def test_full_disk_access_remediation_present(self):
        self.assertIn("Permission denied reading Cookies.binarycookies", self.modal)
        self.assertIn("Full Disk Access", self.modal)
        self.assertIn("Permission denied reading Cookies.binarycookies", self.prose)
        self.assertIn("Full Disk Access", self.prose)

    def test_skip_path_writes_setup_complete(self):
        """The 'Skip for now' setup choice must write SETUP_COMPLETE or the wizard loops."""
        skip_idx = self.modal.find("If the user picks Skip for now")
        self.assertGreater(skip_idx, -1, "no Skip-for-now handling in modal flow")
        # The skip branch must persist the completion flag in its own paragraph.
        skip_para = self.modal[skip_idx:skip_idx + 400]
        self.assertIn("SETUP_COMPLETE=true", skip_para)

    # --- ScrapeCreators signup + persisted edge case ---

    def test_scrapecreators_signup_present_both_branches(self):
        self.assertIn("setup --github", self.modal)
        self.assertIn("setup --github", self.prose)

    def test_persisted_false_edge_case_documented(self):
        self.assertIn('"persisted": false', self.step0)

    # --- Digg threaded alongside yt-dlp everywhere it appears ---

    def test_digg_threaded_with_ytdlp(self):
        self.assertIn("Digg", self.modal)
        self.assertIn("Digg", self.prose)
        self.assertIn("Digg", self.manual)
        # The Auto-setup modal option names both tools together.
        self.assertIn("yt-dlp (YouTube) and the Digg CLI", self.modal)

    # --- Credit count = 10,000, no conflicting numbers in onboarding ---

    def test_credit_count_is_10000(self):
        self.assertIn("10,000 free calls", self.step0)
        self.assertNotIn("1,000 free", self.step0)
        self.assertNotIn("1000 free credit", self.step0)
        self.assertNotIn("1000 credits", self.step0)
        self.assertNotIn("100 free call", self.step0)

    # --- Threads/Pinterest kept out of the onboarding offers ---

    def test_threads_pinterest_absent_from_modal_and_prose(self):
        """They stay a power-user INCLUDE_SOURCES note in the manual guide only."""
        self.assertNotIn("Threads", self.modal)
        self.assertNotIn("Pinterest", self.modal)
        self.assertNotIn("Threads", self.prose)
        self.assertNotIn("Pinterest", self.prose)

    # --- Legacy guarantees retained ---

    def test_old_silent_wizard_instruction_removed(self):
        self.assertNotIn("Follow the wizard's prompts end-to-end", self.text)

    def test_consent_is_conversational_contract_documented(self):
        self.assertIn("Named onboarding contract", self.step0)
        self.assertIn("non-interactive subprocess", self.step0)


if __name__ == "__main__":
    unittest.main()

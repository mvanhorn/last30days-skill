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

from lib import setup_wizard

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
            "Welcome to /last30days!",  # welcome pitch, embedded in the setup modal
            "How would you like to set up?",
            "your browser's x.com cookies",  # cookie-consent modal
            "Want to add TikTok and Instagram?",  # SC offer
            "Which ScrapeCreators sources?",  # source opt-in
            "What do you want to research first?",  # topic picker
        ]
        idxs = [self.modal.find(a) for a in anchors]
        for a, i in zip(anchors, idxs):
            self.assertGreater(i, -1, f"modal flow missing stage anchor: {a!r}")
        self.assertEqual(idxs, sorted(idxs), "modal flow stages are out of order")

    def test_modal_uses_askuserquestion(self):
        self.assertIn("AskUserQuestion", self.modal)

    def test_cookie_consent_names_all_installed_clis(self):
        """The cookie-consent modal must not frame X cookies as instead-of the CLIs,
        and must name arXiv + Techmeme (not just 'YouTube + Digg') since auto-setup
        installs all four regardless of the cookie choice."""
        consent = self.modal[self.modal.find("your browser's x.com cookies"):]
        consent = consent[: consent.find("Full Disk Access")]  # bound to the consent modal
        for cli in ("yt-dlp", "Digg", "arXiv", "Techmeme"):
            self.assertIn(cli, consent, cli)
        # The "skip X" option still installs the CLIs (not framed as X-or-CLIs).
        self.assertIn("Skip X - just the CLIs", consent)

    def test_github_option_advertises_auto_clipboard(self):
        """The recommended GitHub option tells the user the code is auto-copied to
        their clipboard, so they just paste it."""
        self.assertIn("clipboard automatically", self.modal)

    def test_modal_cookie_consent_before_setup(self):
        consent = self.modal.find("your browser's x.com cookies")
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
        # The Auto-setup modal option names every installed CLI, not just two.
        self.assertIn("yt-dlp (YouTube), Digg, arXiv, Techmeme", self.modal)

    # --- Credit count = 10,000, no conflicting numbers in onboarding ---

    def test_credit_count_is_10000(self):
        self.assertIn("10,000 free calls", self.step0)
        self.assertNotIn("1,000 free", self.step0)
        self.assertNotIn("1000 free credit", self.step0)
        self.assertNotIn("1000 credits", self.step0)
        self.assertNotIn("100 free call", self.step0)

    # --- Threads/Pinterest live ONLY in the Step 5 "Everything" opt-in ---

    def _modal_step5(self):
        start = self.modal.index("**Step 5:")
        end = self.modal.index("**Step 6:", start)
        return self.modal[start:end]

    def _modal_before_step5(self):
        # Welcome (Step 1) through the Step 4 ScrapeCreators offer.
        return self.modal[: self.modal.index("**Step 5:")]

    def test_threads_pinterest_only_in_step5_everything(self):
        """Threads/Pinterest are offered in the Step 5 Everything tier, and

        must NOT appear in the welcome or the Step 4 offer (where they would
        read as default-on). They are opt-in via INCLUDE_SOURCES.
        """
        step5 = self._modal_step5()
        self.assertIn("Threads", step5)
        self.assertIn("Pinterest", step5)
        before = self._modal_before_step5()
        self.assertNotIn("Threads", before)
        self.assertNotIn("Pinterest", before)

    def test_offer_copy_names_comments_and_auto_enrichment(self):
        """The Step 4 offer states comments are part of the default value and
        describes the key's real auto-enrichment (Reddit merged + YouTube
        backstop), not a vague 'backup'."""
        before = self._modal_before_step5()
        self.assertIn("comments", before.lower())
        self.assertIn("Reddit", before)
        self.assertIn("YouTube", before)
        self.assertIn("10,000 free calls", before)

    def test_recommended_tier_writes_comments_by_default(self):
        """Comments are the DEFAULT: the recommended option enables YouTube +
        TikTok + Instagram comments (posts on -> comments on)."""
        step5 = self._modal_step5()
        self.assertIn(
            "INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments",
            step5,
        )
        # There is no posts-only tier.
        self.assertIn("recommended", step5.lower())
        self.assertIn("comments", step5.lower())

    def test_everything_tier_writes_full_include_sources(self):
        """The Everything option persists the full list incl. Threads + Pinterest."""
        step5 = self._modal_step5()
        self.assertIn(
            "INCLUDE_SOURCES=tiktok,instagram,youtube_comments,tiktok_comments,instagram_comments,threads,pinterest",
            step5,
        )

    # --- Chrome-first cookie scan (U2/U3) ---

    def test_cookie_consent_leads_with_chrome(self):
        """Both flows tell the user Chrome is checked first, with the Keychain cue."""
        for slice_name, slice_text in (("modal", self.modal), ("prose", self.prose)):
            self.assertIn("Chrome", slice_text, f"{slice_name} cookie copy omits Chrome")
            self.assertIn("Always Allow", slice_text, f"{slice_name} omits the Keychain cue")

    def test_fda_reframed_as_safari_fallback(self):
        """Full Disk Access is framed as Safari-only, not the default path."""
        self.assertNotIn("scan your browser (Firefox/Safari)", self.modal)

    def test_welcome_embedded_in_modal(self):
        """The welcome pitch lives INSIDE the setup modal (the only always-visible
        surface), not as a separate message/command that Claude Code folds away.
        The engine --welcome command is kept for the non-modal prose flow."""
        # Pitch is in the modal question.
        self.assertIn("Welcome to /last30days!", self.modal)
        self.assertIn("How would you like to set up?", self.modal)
        # The modal flow explicitly does NOT run a separate --welcome command.
        self.assertIn("Do NOT run a separate `--welcome`", self.modal)
        # The non-modal flow still uses the engine welcome command.
        self.assertIn("last30days.py --welcome", self.prose)

    def test_stocktwits_surfaced_as_conditional(self):
        """StockTwits is advertised in the engine welcome as a ticker/crypto-gated
        source (welcome text moved out of SKILL.md into the engine)."""
        self.assertIn("StockTwits", setup_wizard.render_welcome())

    # --- Honest GitHub device-code copy (U4/U7) ---

    def test_no_false_instant_gh_promise(self):
        """The '~2 seconds - no browser' claim (a nonexistent code path) is gone."""
        self.assertNotIn("~2 seconds - no browser", self.step0)
        self.assertNotIn("Registers via GitHub CLI in ~2 seconds", self.step0)

    def test_device_code_surfacing_orchestration_present(self):
        """Both flows use the deterministic two-command split (start returns the
        code fast, then poll) instead of a background-and-surface spinner."""
        self.assertIn("setup --github-start", self.modal)
        self.assertIn("setup --github-poll", self.modal)
        self.assertIn("setup --github-start", self.prose)
        self.assertIn("setup --github-poll", self.prose)

    def test_already_registered_status_handled(self):
        self.assertIn("already_registered", self.modal)
        self.assertIn("already_registered", self.prose)

    # --- Welcome must render before the modal (U1) ---

    def test_welcome_pitch_is_in_the_modal_question(self):
        """The welcome pitch names the core sources inside the modal question, so
        the user sees it without expanding folded tool output. The old skip-prone
        'IMMEDIATELY call AskUserQuestion' wording stays gone."""
        # Pitch names the core sources right in the modal.
        for source in ("Reddit", "X,", "YouTube", "TikTok"):
            self.assertIn(source, self.modal, source)
        self.assertNotIn("Then IMMEDIATELY call AskUserQuestion", self.modal)

    # --- Device code surfaced with a clipboard-paste hint (U3) ---

    def test_device_code_clipboard_paste_instruction(self):
        """The GitHub flow tells the user the code is on their clipboard to paste,

        and makes surfacing the code a required step (the bug the user hit).
        """
        self.assertIn("on your clipboard", self.modal)
        # Surfacing the code is a required, explicit step in the new split flow.
        self.assertIn("SHOW THE CODE", self.modal)
        self.assertIn("just paste", self.modal)

    # --- Honest 'authorized but no key' branch, distinct from auth-failed (U4) ---

    def test_authorized_but_no_key_branch_present(self):
        """A key-fetch failure after successful auth is handled honestly (likely

        an already-linked account), not lumped into 'auth didn't complete'.
        """
        for slice_name, slice_text in (("modal", self.modal), ("prose", self.prose)):
            self.assertIn("Authorized but failed to fetch API key", slice_text, slice_name)
            self.assertIn("already linked", slice_text, slice_name)

    # --- Legacy guarantees retained ---

    def test_old_silent_wizard_instruction_removed(self):
        self.assertNotIn("Follow the wizard's prompts end-to-end", self.text)

    def test_consent_is_conversational_contract_documented(self):
        self.assertIn("Named onboarding contract", self.step0)
        self.assertIn("non-interactive subprocess", self.step0)


if __name__ == "__main__":
    unittest.main()

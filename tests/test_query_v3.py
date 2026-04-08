import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import query


class QueryV3Tests(unittest.TestCase):
    def test_extract_core_subject_strips_prefix_and_noise(self):
        result = query.extract_core_subject("What are the best Claude Code skills for startups?")
        self.assertEqual("claude code startups", result)

    def test_extract_core_subject_supports_suffix_stripping_and_max_words(self):
        result = query.extract_core_subject(
            "How to use Claude Code prompting techniques",
            strip_suffixes=True,
            max_words=2,
        )
        self.assertEqual("claude code", result)

    def test_extract_core_subject_preserves_original_when_noise_removes_everything(self):
        result = query.extract_core_subject("all tokens", noise=frozenset({"all", "tokens"}))
        self.assertEqual("all tokens", result)

    def test_extract_core_subject_handles_empty_query_and_custom_noise(self):
        self.assertEqual("", query.extract_core_subject("   "))
        result = query.extract_core_subject(
            "OpenClaw release notes",
            noise=frozenset({"release"}),
            max_words=2,
        )
        self.assertEqual("openclaw notes", result)

    def test_extract_compound_terms_finds_hyphenated_and_title_cased_phrases(self):
        terms = query.extract_compound_terms("Best multi-agent patterns in Claude Code and React Native")
        self.assertIn("multi-agent", terms)
        self.assertIn("Claude Code", terms)
        self.assertIn("React Native", terms)


if __name__ == "__main__":
    unittest.main()

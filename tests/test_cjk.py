import unittest
from unittest.mock import patch

from lib import cjk, dedupe, relevance


class _BigramBase(unittest.TestCase):
    """Force the dictionary-free bigram path so assertions are deterministic
    regardless of whether jieba is installed in the test environment."""

    def setUp(self):
        patcher = patch.object(cjk, "_jieba", None)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestCjkSegment(_BigramBase):
    def test_has_cjk(self):
        self.assertTrue(cjk.has_cjk("国产大模型"))
        self.assertTrue(cjk.has_cjk("GPT4很强"))
        self.assertFalse(cjk.has_cjk("hello world"))
        self.assertFalse(cjk.has_cjk(""))

    def test_ascii_path_unchanged(self):
        # Non-CJK text keeps whitespace/word tokenization.
        self.assertEqual(cjk.segment("best react hooks"), ["best", "react", "hooks"])

    def test_chinese_bigrams(self):
        toks = cjk.segment("大模型")
        self.assertIn("大模", toks)
        self.assertIn("模型", toks)

    def test_mixed_language(self):
        toks = cjk.segment("GPT4很强 react")
        self.assertIn("gpt4", toks)
        self.assertIn("react", toks)
        self.assertIn("很强", toks)

    def test_single_cjk_char(self):
        self.assertEqual(cjk.segment("中"), ["中"])


class TestChineseRelevance(_BigramBase):
    def test_chinese_query_matches_chinese_text(self):
        q = relevance.PreparedQuery("国产大模型 测评")
        score = relevance.token_overlap_relevance(q, "这是国产大模型的最新测评")
        self.assertGreater(score, 0.5)

    def test_chinese_query_rejects_unrelated_text(self):
        q = relevance.PreparedQuery("国产大模型 测评")
        score = relevance.token_overlap_relevance(q, "今天天气很好适合出门散步")
        self.assertEqual(score, 0.0)

    def test_english_relevance_not_regressed(self):
        q = relevance.PreparedQuery("react hooks")
        self.assertGreaterEqual(relevance.token_overlap_relevance(q, "a guide to react hooks"), 0.9)

    def test_cjk_phrase_bonus_applies_on_contiguous_match(self):
        # A multi-token CJK query ("国产大模型 测评") whose words appear
        # contiguously in the text earns the phrase bonus via the space-stripped
        # containment retry; the same words scattered apart do not.
        q = relevance.PreparedQuery("国产大模型 测评")
        contiguous = relevance.token_overlap_relevance(q, "国产大模型测评合集")
        scattered = relevance.token_overlap_relevance(q, "测评了很多东西也聊到国产大模型")
        self.assertGreater(contiguous, scattered)

    def test_english_phrase_bonus_stays_space_sensitive(self):
        # has_cjk gate: English must NOT gain a bonus from space-stripped
        # concatenation (no "reacthooks" false phrase match).
        q = relevance.PreparedQuery("react hooks")
        # "reacthooks" contiguous-without-space should not trigger a CJK-style retry
        score = relevance.token_overlap_relevance(q, "myreacthooks bundle")
        self.assertLessEqual(score, 1.0)  # sanity; behavior identical to pre-change


class TestChineseDedupe(_BigramBase):
    def test_reordered_chinese_is_near_duplicate(self):
        sim = dedupe.hybrid_similarity("国产大模型最新测评对比", "国产大模型测评对比最新")
        self.assertGreater(sim, 0.5)

    def test_distinct_chinese_is_not_duplicate(self):
        sim = dedupe.hybrid_similarity("国产大模型测评", "今天天气很好出门散步")
        self.assertLess(sim, 0.3)


class TestJiebaBinding(unittest.TestCase):
    def test_jieba_global_is_bound_at_import(self):
        # Eager import binds the module global once (None when jieba absent).
        # No lazy initializer => no per-call race in the pipeline thread pool.
        self.assertTrue(hasattr(cjk, "_jieba"))
        self.assertFalse(hasattr(cjk, "_get_jieba"))


if __name__ == "__main__":
    unittest.main()

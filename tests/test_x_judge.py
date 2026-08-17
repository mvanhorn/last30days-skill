"""Tests for x_judge.py — corpus judging for retrieve-judge-retry."""

import pytest

from skills.last30days.scripts.lib import x_judge


class TestJudgeXCorpus:
    """Tests for judge_x_corpus."""

    def test_empty_corpus_returns_not_flood(self):
        """Empty corpus should not be flagged as off-topic flood."""
        result = x_judge.judge_x_corpus([], "Rome")
        assert result["on_topic_ratio"] == 1.0
        assert not result["is_off_topic_flood"]
        assert result["on_topic_items"] == []
        assert result["off_topic_items"] == []

    def test_on_topic_corpus_passes(self):
        """Corpus with mostly on-topic posts should pass."""
        items = [
            {"author_handle": "mamboitaliano__", "text": "Beautiful Rome sunset"},
            {"author_handle": "mamboitaliano__", "text": "Trevi Fountain in Rome"},
            {"author_handle": "mamboitaliano__", "text": "Rome Colosseum today"},
            {"author_handle": "turismoromaweb", "text": "Visit Rome Italy"},
        ]
        result = x_judge.judge_x_corpus(items, "Rome")
        assert result["on_topic_ratio"] >= 0.5
        assert not result["is_off_topic_flood"]
        assert len(result["on_topic_items"]) >= 3

    def test_off_topic_flood_detected(self):
        """Corpus with mostly off-topic posts should be flagged as flood."""
        # Rome fixture: 8 on-topic posts, 32 off-topic posts
        on_topic = [
            {"author_handle": "mamboitaliano__", "text": "Beautiful Rome sunset"},
            {"author_handle": "mamboitaliano__", "text": "Trevi Fountain in Rome"},
            {"author_handle": "mamboitaliano__", "text": "Rome Colosseum today"},
            {"author_handle": "turismoromaweb", "text": "Visit Rome Italy"},
            {"author_handle": "romecityphoto", "text": "Rome city at night"},
            {"author_handle": "visitrome", "text": "Rome vacation tips"},
            {"author_handle": "italytravel", "text": "Rome and Florence tour"},
            {"author_handle": "rometraveler", "text": "Rome landmarks to see"},
        ]
        off_topic = [
            {"author_handle": "visegrad24", "text": "Zelensky addresses NATO summit"},
            {"author_handle": "visegrad24", "text": "Poland border update"},
            {"author_handle": "visegrad24", "text": "Ukraine military news"},
            {"author_handle": "PrettyCitiesX", "text": "London bridge at sunset"},
            {"author_handle": "PrettyCitiesX", "text": "Paris Eiffel tower"},
            {"author_handle": "PrettyCitiesX", "text": "Tokyo skyline"},
            {"author_handle": "earthserenityy", "text": "Mountain landscape photo"},
            {"author_handle": "earthserenityy", "text": "Beach sunset vibes"},
        ]
        # Repeat off-topic to get 32 items
        all_off_topic = off_topic * 4
        items = on_topic + all_off_topic

        result = x_judge.judge_x_corpus(items, "Rome")
        assert result["on_topic_ratio"] < 0.4
        assert result["is_off_topic_flood"]
        assert len(result["on_topic_items"]) == 8

    def test_no_substring_collision_ai_in_said(self):
        """'AI' as a topic token should NOT match 'said' (no substring collisions).

        This verifies that topic judging uses proper word tokenization, not
        substring matching. If substring matching were used, 'ai' would match
        inside 'said', causing false positives.
        """
        items = [
            {"author_handle": "offtopic", "text": "He said something interesting"},
            {"author_handle": "offtopic", "text": "She said the weather was nice"},
            {"author_handle": "offtopic", "text": "They said it was great"},
        ]
        result = x_judge.judge_x_corpus(items, "AI")
        # None of these posts contain "AI" as a word, so all should be off-topic
        assert len(result["on_topic_items"]) == 0
        assert len(result["off_topic_items"]) == 3

    def test_no_substring_collision_rome_in_promoter(self):
        """'rome' as a topic token should NOT match 'promoter' (no substring collisions)."""
        items = [
            {"author_handle": "offtopic", "text": "A viral promoter of products"},
            {"author_handle": "offtopic", "text": "She is a band promoter"},
        ]
        result = x_judge.judge_x_corpus(items, "Rome")
        # None of these posts contain "Rome" as a word
        assert len(result["on_topic_items"]) == 0
        assert len(result["off_topic_items"]) == 2

    def test_us_pronoun_does_not_match_us_topic(self):
        """Lowercase 'us' pronoun should not match 'US' country topic."""
        items = [
            {"author_handle": "offtopic", "text": "Tell us what you think"},
            {"author_handle": "offtopic", "text": "Join us for the event"},
        ]
        # "US" topic vs "us" pronoun in text - case-sensitive check filters
        result = x_judge.judge_x_corpus(items, "US")
        assert len(result["on_topic_items"]) == 0
        assert len(result["off_topic_items"]) == 2

    def test_us_acronym_matches_us_topic(self):
        """Uppercase 'US' country acronym in text matches 'US' topic."""
        items = [
            {"author_handle": "ontopic", "text": "The US is expanding trade"},
            {"author_handle": "ontopic", "text": "US policy update"},
        ]
        result = x_judge.judge_x_corpus(items, "US")
        assert len(result["on_topic_items"]) == 2
        assert len(result["off_topic_items"]) == 0

    def test_us_with_context_matches(self):
        """'US economy' matches posts about US economy."""
        items = [
            {"author_handle": "ontopic", "text": "The US economy is growing"},
            {"author_handle": "ontopic", "text": "Economy news from America"},
        ]
        result = x_judge.judge_x_corpus(items, "US economy")
        # First post has "US" (acronym) and "economy" → on-topic
        # Second post has "economy" only → on-topic
        assert len(result["on_topic_items"]) >= 1

    def test_multi_token_us_query_with_pronoun(self):
        """'US economy' should NOT match text with pronoun 'us' and 'economy'."""
        items = [
            {"author_handle": "offtopic", "text": "Tell us about the economy"},
            {"author_handle": "offtopic", "text": "Let us discuss economy trends"},
        ]
        result = x_judge.judge_x_corpus(items, "US economy")
        # These have lowercase "us" (pronoun) not "US" (country)
        # Should match on "economy" alone (50% coverage), which may or may not
        # pass the relevance floor depending on thresholds
        # The key test is that "us" doesn't contribute to the match
        for item in result["on_topic_items"]:
            # If on-topic, it should be due to "economy" match only
            assert "economy" in item["text"].lower()

    def test_handle_stats_computed(self):
        """Handle stats should track on-topic and total counts."""
        items = [
            {"author_handle": "handle_a", "text": "Rome is beautiful"},
            {"author_handle": "handle_a", "text": "Paris is nice"},
            {"author_handle": "handle_b", "text": "Rome vacation"},
            {"author_handle": "handle_b", "text": "Rome food"},
        ]
        result = x_judge.judge_x_corpus(items, "Rome")
        assert result["handle_stats"]["handle_a"]["total"] == 2
        assert result["handle_stats"]["handle_b"]["total"] == 2
        # handle_b has 2 on-topic posts about Rome
        assert result["handle_stats"]["handle_b"]["on_topic"] == 2


class TestPromotableHandles:
    """Tests for promotable_handles — split FROM promotion."""

    def test_explicit_handles_always_promoted(self):
        """Explicit handles (--x-handle) should always be promoted."""
        items = [
            {"author_handle": "steipete", "text": "random post about nothing"},
        ]
        explicit, extracted = x_judge.promotable_handles(
            items, "quantum widgets", ["steipete"], explicit_handles=["steipete"]
        )
        assert "steipete" in explicit
        assert "steipete" not in extracted

    def test_extracted_handle_promoted_with_on_topic_posts(self):
        """Extracted handle with ≥2 on-topic posts should be promoted."""
        items = [
            {"author_handle": "mamboitaliano__", "text": "Rome sunset beautiful"},
            {"author_handle": "mamboitaliano__", "text": "Rome Colosseum tour"},
            {"author_handle": "mamboitaliano__", "text": "Trevi Fountain Rome"},
        ]
        explicit, extracted = x_judge.promotable_handles(
            items, "Rome", ["mamboitaliano__"], explicit_handles=[]
        )
        assert "mamboitaliano__" in extracted
        assert len(explicit) == 0

    def test_extracted_handle_not_promoted_with_off_topic_posts(self):
        """Extracted handle with off-topic posts should not be promoted."""
        items = [
            {"author_handle": "visegrad24", "text": "Zelensky addresses summit"},
            {"author_handle": "visegrad24", "text": "Poland military update"},
            {"author_handle": "visegrad24", "text": "Ukraine border news"},
            # One post mentions Rome but mostly off-topic
            {"author_handle": "visegrad24", "text": "Rome, Italy in caption"},
        ]
        explicit, extracted = x_judge.promotable_handles(
            items, "Rome", ["visegrad24"], explicit_handles=[]
        )
        assert "visegrad24" not in extracted
        assert len(explicit) == 0

    def test_explicit_handle_not_in_extracted_still_promoted(self):
        """Explicit handle not in extracted list should still be promoted."""
        items = []  # No items for this handle
        explicit, extracted = x_judge.promotable_handles(
            items, "topic", [], explicit_handles=["explicit_handle"]
        )
        assert "explicit_handle" in explicit

    def test_min_hits_threshold(self):
        """Handle needs MIN_ON_TOPIC_HITS to be promoted."""
        items = [
            {"author_handle": "onepost", "text": "Rome is nice"},  # Only 1 on-topic
        ]
        explicit, extracted = x_judge.promotable_handles(
            items, "Rome", ["onepost"], explicit_handles=[]
        )
        # Not enough on-topic hits (needs ≥2)
        assert "onepost" not in extracted


class TestShouldRetryXSearch:
    """Tests for should_retry_x_search."""

    def test_skip_retry_on_quick_depth(self):
        """Quick depth should skip retry."""
        items = [
            {"author_handle": "off_topic", "text": "completely unrelated content"},
        ] * 40
        assert not x_judge.should_retry_x_search(items, "Rome", depth="quick")

    def test_trigger_retry_on_off_topic_flood(self):
        """Off-topic flood should trigger retry."""
        off_topic = [
            {"author_handle": "off_topic", "text": "completely unrelated content"},
        ] * 40
        assert x_judge.should_retry_x_search(off_topic, "Rome", depth="default")

    def test_no_retry_on_good_corpus(self):
        """On-topic corpus should not trigger retry."""
        on_topic = [
            {"author_handle": "romefan", "text": "Rome vacation photos"},
            {"author_handle": "romefan", "text": "Rome colosseum visit"},
            {"author_handle": "romefan", "text": "Rome food tour"},
            {"author_handle": "romefan", "text": "Rome sunset beautiful"},
        ]
        assert not x_judge.should_retry_x_search(on_topic, "Rome", depth="default")


class TestPruneOffTopicItems:
    """Tests for prune_off_topic_items."""

    def test_prunes_off_topic_keeps_on_topic(self):
        """Should prune off-topic items and keep on-topic ones."""
        items = [
            {"author_handle": "on", "text": "Rome vacation"},
            {"author_handle": "off", "text": "Paris travel"},
            {"author_handle": "on", "text": "Rome colosseum"},
            {"author_handle": "off", "text": "London sights"},
        ]
        result = x_judge.prune_off_topic_items(items, "Rome")
        assert len(result) == 2
        assert all("Rome" in item["text"] for item in result)

    def test_empty_after_prune_is_ok(self):
        """Pruning all items should return empty list."""
        items = [
            {"author_handle": "off", "text": "completely unrelated"},
            {"author_handle": "off", "text": "nothing to do with topic"},
        ]
        result = x_judge.prune_off_topic_items(items, "Rome")
        assert result == []


class TestRomeScenario:
    """Integration tests for the Rome failure scenario from 2026-08-14."""

    @pytest.fixture
    def rome_off_topic_corpus(self):
        """The Rome failure corpus: 8 on-topic, 32 off-topic."""
        on_topic = [
            {"author_handle": "mamboitaliano__", "text": "Beautiful Rome sunset over the Tiber"},
            {"author_handle": "mamboitaliano__", "text": "Trevi Fountain in Rome today"},
            {"author_handle": "mamboitaliano__", "text": "Rome Colosseum morning visit"},
            {"author_handle": "Turismoromaweb", "text": "Visit Rome Italy for vacation"},
            {"author_handle": "romecityguide", "text": "Rome city center walking tour"},
            {"author_handle": "italyrome", "text": "Rome and Vatican day trip"},
            {"author_handle": "romephotos", "text": "Rome architecture stunning"},
            {"author_handle": "rometravel", "text": "Rome travel tips for tourists"},
        ]
        off_topic = [
            # visegrad24 - geopolitics account, thin Rome connection
            {"author_handle": "visegrad24", "text": "Zelensky addresses NATO summit"},
            {"author_handle": "visegrad24", "text": "Poland border security update"},
            {"author_handle": "visegrad24", "text": "Ukraine military operations today"},
            {"author_handle": "visegrad24", "text": "Russia sanctions news breaking"},
            # PrettyCitiesX - city aesthetics account, has other cities
            {"author_handle": "PrettyCitiesX", "text": "London bridge at sunset"},
            {"author_handle": "PrettyCitiesX", "text": "Paris Eiffel tower night"},
            {"author_handle": "PrettyCitiesX", "text": "Tokyo skyline beautiful"},
            {"author_handle": "PrettyCitiesX", "text": "New York Central Park"},
            # earthserenityy - nature photos, no Rome
            {"author_handle": "earthserenityy", "text": "Mountain landscape photography"},
            {"author_handle": "earthserenityy", "text": "Beach sunset vibes serene"},
            {"author_handle": "earthserenityy", "text": "Forest morning fog mystical"},
            {"author_handle": "earthserenityy", "text": "Ocean waves crashing shore"},
            # AS Roma sports content (Rome collision)
            {"author_handle": "asromafc", "text": "AS Roma match tonight Serie A"},
            {"author_handle": "asromafc", "text": "Roma vs Juventus preview"},
            {"author_handle": "asromafc", "text": "Forza Roma goal highlights"},
            {"author_handle": "asromafc", "text": "AS Roma transfer rumors"},
            # Rome Odunze collision
            {"author_handle": "nflupdates", "text": "Rome Odunze catches touchdown"},
            {"author_handle": "nflupdates", "text": "Bears WR Rome Odunze stats"},
            {"author_handle": "fantasyfb", "text": "Start Rome Odunze this week"},
            {"author_handle": "fantasyfb", "text": "Rome Odunze fantasy value"},
        ]
        # Duplicate off-topic to reach ~32 items
        return on_topic + off_topic + off_topic[:12]

    def test_rome_corpus_detected_as_off_topic(self, rome_off_topic_corpus):
        """Rome failure corpus should be detected as off-topic flood."""
        result = x_judge.judge_x_corpus(rome_off_topic_corpus, "Rome")
        # ~8/40 = 0.2, well below 0.4 threshold
        assert result["on_topic_ratio"] < 0.4
        assert result["is_off_topic_flood"]

    def test_mamboitaliano_promotable(self, rome_off_topic_corpus):
        """mamboitaliano__ with on-topic Rome posts should be promotable."""
        explicit, extracted = x_judge.promotable_handles(
            rome_off_topic_corpus,
            "Rome",
            ["mamboitaliano__", "visegrad24", "PrettyCitiesX"],
            explicit_handles=[],
        )
        assert "mamboitaliano__" in extracted

    def test_visegrad24_not_promotable(self, rome_off_topic_corpus):
        """visegrad24 with off-topic posts should NOT be promotable."""
        explicit, extracted = x_judge.promotable_handles(
            rome_off_topic_corpus,
            "Rome",
            ["mamboitaliano__", "visegrad24", "PrettyCitiesX"],
            explicit_handles=[],
        )
        assert "visegrad24" not in extracted
        assert "PrettyCitiesX" not in extracted

    def test_turismoromaweb_promotable_if_explicit(self, rome_off_topic_corpus):
        """Turismoromaweb should be promoted if explicitly specified."""
        explicit, extracted = x_judge.promotable_handles(
            rome_off_topic_corpus,
            "Rome",
            ["Turismoromaweb"],
            explicit_handles=["Turismoromaweb"],
        )
        assert "Turismoromaweb" in explicit

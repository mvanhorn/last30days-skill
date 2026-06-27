"""Diverse-topic evaluation harness for the three default-on Printing Press
sources (arxiv, techmeme, trustpilot).

Two layers:

1. Deterministic fire/quiet matrix over five diverse topics drawn from real
   run history. Asserts each source's *gating* decision without calling any CLI
   -- this is the part that proves "Trustpilot fires only on company topics" and
   "arXiv stays quiet on stale/off-topic matches".

2. Opt-in --live mode (set LAST30DAYS_EVAL_LIVE=1) that actually invokes the
   installed CLIs against the same topics, prints a sample for human review, and
   asserts the negative controls stay quiet. Skipped by default so CI stays
   deterministic and offline.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from lib import arxiv, techmeme, trustpilot


# Five diverse topics from real run history, with the expected fire/quiet call
# for each source. None = "not asserted deterministically" (depends on live
# corpus; checked only in --live mode).
TOPICS = {
    "AI coding agents":         {"arxiv": True,  "techmeme": True,  "trustpilot": False},
    "agent memory":             {"arxiv": True,  "techmeme": True,  "trustpilot": False},
    "Nothing Phone":            {"arxiv": None,  "techmeme": None,  "trustpilot": True},
    "ChowNow":                  {"arxiv": None,  "techmeme": None,  "trustpilot": True},
    "Golden State Warriors":    {"arxiv": False, "techmeme": None,  "trustpilot": False},
}

NOW = datetime(2026, 6, 27, tzinfo=timezone.utc)


# ---- Layer 1: deterministic gating matrix ----

@pytest.mark.parametrize("topic,expected", [(t, e["trustpilot"]) for t, e in TOPICS.items()])
def test_trustpilot_brand_gate_matrix(topic, expected):
    """Trustpilot's brand-shape gate is the primary quiet-keeper."""
    assert trustpilot.is_brand_shaped(topic) is expected


def test_arxiv_quiet_on_offtopic_stale_match():
    """The "Golden State Warriors" control resolves to a 2017 stats paper;
    arXiv's recency cutoff drops it, keeping the source quiet off-topic."""
    stale = "2017-06-12T12:00:00Z"
    resp = {"results": [{
        "id": "http://arxiv.org/abs/1706.03442v2",
        "title": "Do Steph Curry and Klay Thompson Have Hot Hands?",
        "summary": "An analysis of Golden State Warriors shooting.",
        "published": stale,
        "links": [{"rel": "alternate", "href": "https://arxiv.org/abs/1706.03442v2"}],
    }]}
    assert arxiv.parse_arxiv_response(resp, query="Golden State Warriors", today=NOW) == []


def test_arxiv_fires_on_recent_ontopic():
    recent = (NOW - timedelta(days=20)).strftime("%Y-%m-%dT12:00:00Z")
    resp = {"results": [{
        "id": "http://arxiv.org/abs/2606.1v1",
        "title": "Is Agent Memory a Database?",
        "summary": "Rethinking data foundations for long-term AI agent memory.",
        "published": recent,
        "authors": [{"name": "A"}],
        "links": [{"rel": "alternate", "href": "https://arxiv.org/abs/2606.1v1"}],
    }]}
    items = arxiv.parse_arxiv_response(resp, query="agent memory", today=NOW)
    assert len(items) == 1


def test_techmeme_drops_header_rows_keeps_stories():
    resp = {"results": [
        {"num": 1, "source": "techcrunch.com", "headline": "TechCrunch", "link": "http://techcrunch.com/"},
        {"num": 2, "source": "techcrunch.com",
         "headline": "OpenAI ships a new coding agent for developers today",
         "link": "https://www.techmeme.com/x"},
    ]}
    items = techmeme.parse_techmeme_response(resp, query="AI coding agents")
    assert len(items) == 1
    assert items[0]["title"].startswith("OpenAI")


# ---- Layer 2: opt-in live exploration ----

_LIVE = os.environ.get("LAST30DAYS_EVAL_LIVE", "").strip().lower() in ("1", "true", "yes")


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


@pytest.mark.skipif(not _LIVE, reason="set LAST30DAYS_EVAL_LIVE=1 to run live source exploration")
def test_live_fire_matrix(capsys):
    """Live: run the real CLIs against the diverse topics, print samples, and
    assert the negative controls stay quiet."""
    fd, td = "2026-05-28", "2026-06-27"
    lines = []
    for topic, expected in TOPICS.items():
        lines.append(f"\n=== {topic} ===")

        if _have("arxiv-pp-cli"):
            items = arxiv.parse_arxiv_response(
                arxiv.search_arxiv(topic, fd, td), query=topic)
            lines.append(f"  arXiv: {len(items)} papers")
            for it in items[:2]:
                lines.append(f"    - {it['title'][:80]}")
            if expected["arxiv"] is False:
                assert items == [], f"arXiv should stay quiet on {topic!r}"

        if _have("techmeme-pp-cli"):
            items = techmeme.parse_techmeme_response(
                techmeme.search_techmeme(topic, fd, td), query=topic)
            lines.append(f"  Techmeme: {len(items)} headlines")
            for it in items[:2]:
                lines.append(f"    - {it['title'][:80]}")

        if _have("trustpilot-pp-cli"):
            items = trustpilot.parse_trustpilot_response(
                trustpilot.search_trustpilot(topic, fd, td), query=topic)
            lines.append(f"  Trustpilot: {len(items)} companies")
            for it in items[:1]:
                lines.append(f"    - {it['title'][:80]}")
            if expected["trustpilot"] is False:
                assert items == [], f"Trustpilot should stay quiet on {topic!r}"

    with capsys.disabled():
        print("\n".join(lines))

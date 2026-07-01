"""Arctic-shift score resolver — post upvote counts by id, keyless and free.

``search.json`` and ``/comments/{id}.json`` are 403 keyless, and ``search.rss``
(used for discovery) carries titles but NO score; the shreddit listing partials
score only posts that appear in a pulled listing. For a thread found only via
global RSS search in a broad sub, the free score comes from arctic-shift
(https://arctic-shift.photon-reddit.com), a public Reddit archive whose
``/api/posts/ids`` returns the post object (score, num_comments, title) for a
batch of base36 post ids. Scores are point-in-time snapshots — slightly stale vs
live, which is fine for ranking and display.

Best-effort, never raises. On rate-limit (HTTP 422 "slow down"), error, or an
unreachable host it returns ``{}`` so the caller shows the thread without a point
count rather than failing the Reddit source.
"""

import sys
import time
from typing import Dict, List

from . import http

API = "https://arctic-shift.photon-reddit.com/api/posts/ids"
BATCH = 50          # ids per request
TIMEOUT = 15
MAX_BATCHES = 3     # cap total requests per run (bounds latency + rate-limit risk)
PACE_SECONDS = 0.4  # gap between batches; arctic-shift answers 422 "slow down"
CACHE_MAX = 4096    # hard size bound so the in-run memo can never grow unbounded
# In-run memo: base36 id -> {score, num_comments}. Module-level so repeated
# fetch_scores calls within one `/last30days` run (e.g. across subqueries) reuse
# results, but capped at CACHE_MAX entries (never reached in a normal CLI run).
# Tests clear it via reddit_arctic._cache.clear().
_cache: Dict[str, Dict[str, int]] = {}


def _log(msg: str) -> None:
    sys.stderr.write(f"[ArcticShift] {msg}\n")
    sys.stderr.flush()


def fetch_scores(post_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """Return ``{base36_post_id: {"score", "num_comments"}}`` for the given ids.

    Batched, paced, in-run cached, and never raises. Ids that fail or are absent
    from the archive are simply missing from the result (caller degrades to no
    point count for those threads).
    """
    out: Dict[str, Dict[str, int]] = {}
    todo: List[str] = []
    for pid in post_ids:
        if not pid:
            continue
        if pid in _cache:
            out[pid] = _cache[pid]
        elif pid not in todo:
            todo.append(pid)

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)][:MAX_BATCHES]
    for n, batch in enumerate(batches):
        if n:
            time.sleep(PACE_SECONDS)
        try:
            data = http.get(
                f"{API}?ids={','.join(batch)}",
                headers={"User-Agent": http.BROWSER_USER_AGENT},
                timeout=TIMEOUT,
            )
        except Exception as e:  # network error / non-200 — degrade, never raise
            _log(f"lookup failed ({e}); {len(batch)} ids left unscored")
            break
        rows = (data or {}).get("data")
        if not isinstance(rows, list):
            # arctic-shift returns {"error": "..."} on rate-limit / bad request.
            _log(f"unexpected response (rate-limited?): {str(data)[:80]}")
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "").removeprefix("t3_")
            if not rid:
                continue
            try:
                entry = {
                    "score": int(row.get("score") or 0),
                    "num_comments": int(row.get("num_comments") or 0),
                }
            except (TypeError, ValueError):
                continue
            if len(_cache) < CACHE_MAX:
                _cache[rid] = entry
            out[rid] = entry
    return out

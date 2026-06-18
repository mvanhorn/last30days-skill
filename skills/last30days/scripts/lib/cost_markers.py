"""Per-call cost markers for real-cost search billing.

The research engine runs as a subprocess of the last30days-com research-worker,
which captures this engine's stderr and parses ``[cost]`` markers back out to
settle each run on its real provider spend (worker side: research-worker/cost.py
``parse_run_usage``). This module emits markers in the EXACT grammar that parser
expects:

    [cost] provider=<slug> model=<id> prompt_tokens=<n> completion_tokens=<n> \\
           reasoning_tokens=<n> cached_tokens=<n> calls=<n> cost_usd=<float>

The engine's providers do not expose token counts and the dominant spend is
per-call paid APIs (ScrapeCreators credits, web-search backends, Perplexity), so
cost is modeled PER CALL from a flat rate card rather than token-metered. Token
fields stay 0; ``cost_usd`` carries the rate-card price for the call.

Plan: docs/plans/2026-06-17-001-feat-measured-search-cost-markers-plan.md (U1).
Cost accounting must NEVER break a research run: every function here is
exception-safe and returns/does-nothing on bad input.
"""

from __future__ import annotations

import sys
from typing import Optional, TextIO

# ---------------------------------------------------------------------------
# Rate card: USD charged per single call, by provider (and model where the
# price varies by model). SEED ESTIMATES — real values are an Open Question in
# the plan (confirm ScrapeCreators, search-vendor, and Perplexity pricing before
# the live billed run). Centralized here so prices change without touching call
# sites. Unknown providers price at 0 (a missing price never fabricates cost).
# ---------------------------------------------------------------------------
RATE_CARD: dict[str, float] = {
    # ScrapeCreators: 1 credit per call; their credit price in USD. Dominant
    # spend on social-heavy runs (Reddit/TikTok/Instagram/Threads/Pinterest/YT).
    "scrapecreators": 0.003,
    # Web-search backends — per query.
    "brave": 0.005,
    "exa": 0.005,
    "serper": 0.001,
    "parallel": 0.005,
    # Reasoning LLMs — flat per call (no token data exposed). Near-zero vs the
    # paid APIs above; included for completeness, priced low.
    "gemini": 0.0005,
    "openai": 0.0005,
    "xai": 0.0005,
    "openrouter": 0.0010,
}

# Model-keyed overrides (provider:model) for cases where price varies by model.
RATE_CARD_BY_MODEL: dict[str, float] = {
    # Perplexity via OpenRouter: deep research is ~100x sonar-pro.
    "perplexity:sonar-pro": 0.008,
    "perplexity:sonar-deep-research": 0.90,
}


def price_for(provider: str, model: str = "") -> float:
    """Resolve the per-call USD price for a provider/model from the rate card.
    Unknown provider/model -> 0.0 (never fabricate cost)."""
    try:
        if model:
            keyed = RATE_CARD_BY_MODEL.get(f"{provider}:{model}")
            if keyed is not None:
                return float(keyed)
        return float(RATE_CARD.get(provider, 0.0))
    except Exception:  # pragma: no cover - defensive
        return 0.0


def emit_cost(
    provider: str,
    *,
    model: str = "",
    calls: int = 1,
    cost_usd: Optional[float] = None,
    stream: Optional[TextIO] = None,
) -> float:
    """Write one ``[cost]`` marker for a billable call and return the cost.

    ``cost_usd`` defaults to the rate-card price for (provider, model) times
    ``calls``. Pass an explicit ``cost_usd`` only when a call site already knows
    the real cost. A ``model`` is included so the worker can build a per-model
    breakdown; for paid APIs with no model, pass the endpoint/source as model
    (e.g. ``model="reddit"``) or leave it blank (cost still counts toward the
    run total, just not the per-model rows).

    Never raises.
    """
    out = stream if stream is not None else sys.stderr
    try:
        n = int(calls) if calls else 0
        if cost_usd is None:
            cost = price_for(provider, model) * max(n, 0)
        else:
            cost = float(cost_usd)
        out.write(
            f"[cost] provider={provider} model={model} "
            f"prompt_tokens=0 completion_tokens=0 "
            f"reasoning_tokens=0 cached_tokens=0 "
            f"calls={n} cost_usd={cost:.6f}\n"
        )
        return cost
    except Exception:  # pragma: no cover - defensive
        return 0.0

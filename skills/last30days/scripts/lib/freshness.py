"""Deterministic, source-grounded act-time freshness verification."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import github, grounding, health, polymarket, schema, stocktwits


@dataclass(frozen=True)
class Claim:
    """A conservative, machine-verifiable claim extracted from one source item."""

    claim_id: str
    candidate_id: str
    text: str
    source: str
    source_item_id: str
    source_url: str
    source_timestamp: str | None
    datum_kind: str
    datum_key: str
    original_value: Any


@dataclass(frozen=True)
class RefetchedDatum:
    value: Any
    url: str
    timestamp: str | None = None


Refetcher = Callable[[schema.SourceItem, str], RefetchedDatum | dict[str, Any] | Any]

_STATUS_PATTERN = re.compile(
    r"\b(?P<subject>[A-Z][A-Za-z0-9&.'’/+_-]*(?:\s+[A-Z0-9][A-Za-z0-9&.'’/+_-]*){0,5})"
    r"\s+(?:is|was|remains|became|has been)\s+"
    r"(?P<status>open|closed|active|inactive|available|unavailable|"
    r"approved|rejected|launched|discontinued|online|offline)\b"
)
_OPPOSITE_STATUS = {
    "open": "closed",
    "closed": "open",
    "active": "inactive",
    "inactive": "active",
    "available": "unavailable",
    "unavailable": "available",
    "approved": "rejected",
    "rejected": "approved",
    "launched": "discontinued",
    "discontinued": "launched",
    "online": "offline",
    "offline": "online",
}
_REFETCHABLE_SOURCES = frozenset({"polymarket", "github", "stocktwits"})
_USABLE_SOURCE_STATES = frozenset({health.OK, schema.PARTIAL})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _claim_id(candidate_id: str, kind: str, key: str) -> str:
    digest = hashlib.sha256(f"{candidate_id}\0{kind}\0{key}".encode()).hexdigest()[:12]
    return f"claim-{digest}"


def _claim(
    grounded: grounding.GroundedClaimText,
    kind: str,
    key: str,
    value: Any,
    text: str,
) -> Claim:
    item = grounded.item
    return Claim(
        claim_id=_claim_id(grounded.candidate_id, kind, key),
        candidate_id=grounded.candidate_id,
        text=text,
        source=item.source,
        source_item_id=item.item_id,
        source_url=item.url,
        source_timestamp=item.published_at,
        datum_kind=kind,
        datum_key=key,
        original_value=value,
    )


def extract_claims(report: schema.Report) -> list[Claim]:
    """Extract only structured numerics/dates and tightly shaped status claims."""
    claims: list[Claim] = []
    for grounded in grounding.claim_source_map(report).values():
        item = grounded.item
        if item.source == "polymarket":
            outcome_pairs = item.metadata.get("outcome_prices") or []
            outcome_counts = Counter(
                str(pair[0]).strip().casefold()
                for pair in outcome_pairs
                if isinstance(pair, (list, tuple)) and len(pair) == 2
            )
            seen_outcomes: dict[str, int] = defaultdict(int)
            for pair in outcome_pairs:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    continue
                name, value = pair
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                key = str(name).strip()
                if not key:
                    continue
                normalized_key = key.casefold()
                occurrence = seen_outcomes[normalized_key]
                seen_outcomes[normalized_key] += 1
                datum_key = (
                    f"{key}\x1f{occurrence}"
                    if outcome_counts[normalized_key] > 1
                    else key
                )
                claims.append(
                    _claim(
                        grounded,
                        "polymarket_probability",
                        datum_key,
                        float(value),
                        f"{item.title}: {key} is {float(value) * 100:g}%",
                    )
                )
            end_date = item.metadata.get("end_date")
            if isinstance(end_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end_date):
                claims.append(
                    _claim(
                        grounded,
                        "polymarket_end_date",
                        "end_date",
                        end_date,
                        f"{item.title} closes {end_date}",
                    )
                )
        elif item.source == "github":
            stars = item.engagement.get("stars")
            repo = _github_repo(item)
            if repo and isinstance(stars, (int, float)) and not isinstance(stars, bool):
                claims.append(
                    _claim(
                        grounded,
                        "github_stars",
                        "stars",
                        int(stars),
                        f"{repo} has {int(stars):,} GitHub stars",
                    )
                )
        elif item.source == "stocktwits":
            aggregate = item.metadata.get("sentiment_aggregate") or {}
            pct = aggregate.get("pct_bullish") if isinstance(aggregate, dict) else None
            symbol = str(item.metadata.get("symbol") or item.container or "").strip()
            if symbol and isinstance(pct, (int, float)) and not isinstance(pct, bool):
                claims.append(
                    _claim(
                        grounded,
                        "stocktwits_bullish_pct",
                        "pct_bullish",
                        float(pct),
                        f"StockTwits ${symbol} tagged sentiment is {float(pct):g}% bullish",
                    )
                )

        # Status assertions are accepted only when a short, explicit subject +
        # copula + status occurs in the exact candidate text tied above.
        status_text = " ".join(part for part in (grounded.title, grounded.summary) if part)
        match = _STATUS_PATTERN.search(status_text)
        if match:
            subject = match.group("subject").strip()
            status = match.group("status").lower()
            claims.append(
                _claim(
                    grounded,
                    "status_assertion",
                    subject.lower(),
                    status,
                    match.group(0),
                )
            )
    return claims


def _github_repo(item: schema.SourceItem) -> str | None:
    if item.container and re.fullmatch(r"[^/\s]+/[^/\s]+", item.container):
        return item.container
    match = re.match(r"https?://github\.com/([^/]+/[^/#?]+)", item.url)
    return match.group(1).removesuffix(".git") if match else None


def _default_refetchers() -> dict[str, Refetcher]:
    return {
        "polymarket": polymarket.refetch_datum,
        "github": github.refetch_datum,
        "stocktwits": stocktwits.refetch_datum,
    }


def _coerce_refetched(value: RefetchedDatum | dict[str, Any] | Any, fallback_url: str) -> RefetchedDatum:
    if isinstance(value, RefetchedDatum):
        return value
    if isinstance(value, dict) and "value" in value:
        return RefetchedDatum(
            value=value["value"],
            url=str(value.get("url") or fallback_url),
            timestamp=value.get("timestamp"),
        )
    return RefetchedDatum(value=value, url=fallback_url)


def _values_match(claim: Claim, current: Any) -> bool:
    if claim.datum_kind == "polymarket_probability":
        try:
            return abs(float(claim.original_value) - float(current)) < 0.005
        except (TypeError, ValueError):
            return False
    if isinstance(claim.original_value, (int, float)) and isinstance(current, (int, float)):
        return float(claim.original_value) == float(current)
    return claim.original_value == current


def _newer_status_contradiction(
    report: schema.Report,
    claim: Claim,
) -> schema.SourceItem | None:
    opposite = _OPPOSITE_STATUS.get(str(claim.original_value))
    if not opposite:
        return None
    subject_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", claim.datum_key)
        if len(token) >= 3
    }
    if not subject_tokens:
        return None
    candidates = [
        item
        for items in report.items_by_source.values()
        for item in items
        if (item.source, item.item_id) != (claim.source, claim.source_item_id)
        and item.published_at
        and (not claim.source_timestamp or item.published_at > claim.source_timestamp)
    ]
    candidates.sort(key=lambda item: item.published_at or "", reverse=True)
    for item in candidates:
        text = f"{item.title} {item.snippet} {item.body}".lower()
        if re.search(rf"\b{re.escape(opposite)}\b", text) and all(
            re.search(rf"\b{re.escape(token)}\b", text)
            for token in subject_tokens
        ):
            return item
    return None


def _unsupported(
    claim: Claim,
    checked_at: str,
    detail: str,
) -> schema.FreshnessVerdict:
    return schema.FreshnessVerdict(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim=claim.text,
        source=claim.source,
        source_item_id=claim.source_item_id,
        verdict="unsupported",
        checked_at=checked_at,
        source_url=claim.source_url,
        source_timestamp=claim.source_timestamp,
        evidence_url=claim.source_url,
        evidence_timestamp=checked_at,
        original_value=claim.original_value,
        detail=detail,
    )


def verify_report(
    report: schema.Report,
    *,
    refetchers: dict[str, Refetcher] | None = None,
    allow_network: bool = True,
    checked_at: str | None = None,
) -> list[schema.FreshnessVerdict]:
    """Attach and return deterministic freshness verdicts for ``report``."""
    checked = checked_at or _now()
    dispatch = _default_refetchers() if refetchers is None else refetchers
    items = {
        (item.source, item.item_id): item
        for source_items in report.items_by_source.values()
        for item in source_items
    }
    for candidate in report.ranked_candidates:
        for item in candidate.source_items:
            items.setdefault((item.source, item.item_id), item)

    verdicts: list[schema.FreshnessVerdict] = []
    for claim in extract_claims(report):
        if claim.datum_kind == "status_assertion":
            contradiction = _newer_status_contradiction(report, claim)
            if contradiction:
                verdicts.append(
                    schema.FreshnessVerdict(
                        claim_id=claim.claim_id,
                        candidate_id=claim.candidate_id,
                        claim=claim.text,
                        source=claim.source,
                        source_item_id=claim.source_item_id,
                        verdict="contradicted",
                        checked_at=checked,
                        source_url=claim.source_url,
                        source_timestamp=claim.source_timestamp,
                        evidence_url=contradiction.url,
                        evidence_timestamp=contradiction.published_at,
                        original_value=claim.original_value,
                        current_value=_OPPOSITE_STATUS.get(str(claim.original_value)),
                        detail=f"Newer {contradiction.source} item disagrees",
                    )
                )
            else:
                verdicts.append(
                    schema.FreshnessVerdict(
                        claim_id=claim.claim_id,
                        candidate_id=claim.candidate_id,
                        claim=claim.text,
                        source=claim.source,
                        source_item_id=claim.source_item_id,
                        verdict="current",
                        checked_at=checked,
                        source_url=claim.source_url,
                        source_timestamp=claim.source_timestamp,
                        evidence_url=claim.source_url,
                        evidence_timestamp=claim.source_timestamp,
                        original_value=claim.original_value,
                        current_value=claim.original_value,
                        detail="No newer contradictory item in the report window",
                    )
                )
            continue

        item = items.get((claim.source, claim.source_item_id))
        outcome = report.source_status.get(claim.source)
        if item is None:
            verdicts.append(_unsupported(claim, checked, "Grounding source item is unavailable"))
            continue
        if outcome and outcome.state not in _USABLE_SOURCE_STATES:
            verdicts.append(
                _unsupported(
                    claim,
                    checked,
                    f"Source status is {outcome.state}; the datum could not be re-checked",
                )
            )
            continue
        refetcher = dispatch.get(claim.source)
        if claim.source not in _REFETCHABLE_SOURCES or refetcher is None:
            verdicts.append(_unsupported(claim, checked, "No point-refetch verifier is registered"))
            continue
        if not allow_network:
            verdicts.append(_unsupported(claim, checked, "Network verification is disabled for this run"))
            continue
        try:
            refreshed = _coerce_refetched(refetcher(item, claim.datum_key), claim.source_url)
            matches = _values_match(claim, refreshed.value)
            verdicts.append(
                schema.FreshnessVerdict(
                    claim_id=claim.claim_id,
                    candidate_id=claim.candidate_id,
                    claim=claim.text,
                    source=claim.source,
                    source_item_id=claim.source_item_id,
                    verdict="current" if matches else "stale",
                    checked_at=checked,
                    source_url=claim.source_url,
                    source_timestamp=claim.source_timestamp,
                    evidence_url=refreshed.url,
                    evidence_timestamp=refreshed.timestamp or checked,
                    original_value=claim.original_value,
                    current_value=refreshed.value,
                    detail=None if matches else "Re-fetched value moved",
                )
            )
        except Exception as exc:  # verifier failures degrade to a typed verdict
            verdicts.append(_unsupported(claim, checked, f"Re-check failed: {exc}"))

    report.freshness_verdicts = verdicts
    return verdicts

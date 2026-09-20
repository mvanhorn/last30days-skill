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
    values: dict[str, Any] | None = None


Refetcher = Callable[[schema.SourceItem | None, str], RefetchedDatum | dict[str, Any] | Any]

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
    item_level_repos: set[str] = set()
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
                item_level_repos.add((grounded.candidate_id, repo.casefold()))
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

    claims.extend(_candidate_star_claims(report, item_level_repos))
    return claims


def _candidate_star_claims(
    report: schema.Report,
    item_level_repos: set[tuple[str, str]],
) -> list[Claim]:
    """Emit star claims from candidate enrichment metadata.

    Star enrichment attaches ``metadata["github_stars"]`` (repo -> stars)
    after reranking, so these facts never appear on item-level engagement -
    typically the candidate's primary item is a non-GitHub source. Each repo
    becomes one repo-keyed claim unless the same candidate already claimed it
    at item level; a different candidate's item-level claim never suppresses
    this candidate's own verdict (and its inline freshness flag).
    """
    claims: list[Claim] = []
    candidates_by_id = {
        candidate.candidate_id: candidate for candidate in report.ranked_candidates
    }
    for grounded in grounding.claim_source_map(report).values():
        candidate = candidates_by_id.get(grounded.candidate_id)
        if candidate is None:
            continue
        stars_map = candidate.metadata.get("github_stars")
        if not isinstance(stars_map, dict):
            continue
        for repo, stars in sorted(stars_map.items()):
            if not isinstance(repo, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
                continue
            if isinstance(stars, bool) or not isinstance(stars, (int, float)):
                continue
            if (grounded.candidate_id, repo.casefold()) in item_level_repos:
                continue
            item = grounded.item
            claims.append(
                Claim(
                    claim_id=_claim_id(grounded.candidate_id, "github_stars", repo),
                    candidate_id=grounded.candidate_id,
                    text=f"{repo} has {int(stars):,} GitHub stars",
                    source="github",
                    source_item_id=item.item_id,
                    source_url=f"https://github.com/{repo}",
                    source_timestamp=item.published_at,
                    datum_kind="github_stars",
                    datum_key=repo,
                    original_value=int(stars),
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
            values=value.get("values") if isinstance(value.get("values"), dict) else None,
        )
    return RefetchedDatum(value=value, url=fallback_url)


def _format_verdict_value(kind: str, value: Any) -> str:
    """Format a verdict value the way the matching claim text renders it."""
    if kind == "polymarket_probability":
        try:
            return f"{float(value) * 100:g}%"
        except (TypeError, ValueError):
            return str(value)
    if kind == "stocktwits_bullish_pct":
        try:
            return f"{float(value):g}%"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


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
    subject_tokens = [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", claim.datum_key)
        if len(token) >= 3
    ]
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
        text = f"{item.title} {item.snippet} {item.body}"
        for match in _STATUS_PATTERN.finditer(text):
            asserted_subject = [
                token.lower()
                for token in re.findall(r"[A-Za-z0-9]+", match.group("subject"))
                if len(token) >= 3
            ]
            if asserted_subject == subject_tokens and match.group("status").lower() == opposite:
                return item
    return None


def _point_refetch_key(item: schema.SourceItem, claim: Claim) -> tuple[str, ...]:
    """Identify the source snapshot shared by claims in one verification pass."""
    if claim.source == "polymarket":
        key = item.metadata.get("event_id") or item.url
    elif claim.source == "stocktwits":
        window = item.metadata.get("freshness_window") or {}
        return tuple(
            str(value or "").strip().casefold()
            for value in (
                claim.source,
                item.metadata.get("symbol") or item.container or item.url,
                window.get("depth"),
                window.get("from_date"),
                window.get("to_date"),
            )
        )
    elif claim.source == "github":
        key = _github_repo(item) or item.url
    else:
        key = item.item_id
    return claim.source, str(key).strip().casefold()


def _point_verdict(
    claim: Claim,
    checked_at: str,
    refreshed: RefetchedDatum,
) -> schema.FreshnessVerdict:
    """Build the current/stale verdict for a successfully re-fetched datum."""
    matches = _values_match(claim, refreshed.value)
    return schema.FreshnessVerdict(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim=claim.text,
        source=claim.source,
        source_item_id=claim.source_item_id,
        verdict="current" if matches else "stale",
        checked_at=checked_at,
        source_url=claim.source_url,
        source_timestamp=claim.source_timestamp,
        evidence_url=refreshed.url,
        evidence_timestamp=refreshed.timestamp or checked_at,
        original_value=claim.original_value,
        current_value=refreshed.value,
        detail=None if matches else (
            "moved: "
            f"{_format_verdict_value(claim.datum_kind, claim.original_value)}"
            " -> "
            f"{_format_verdict_value(claim.datum_kind, refreshed.value)}"
        ),
    )


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
        # No fresh evidence was obtained; the original source stays on
        # source_url/source_timestamp and the evidence fields stay empty.
        evidence_url="",
        evidence_timestamp=None,
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
    point_cache: dict[tuple[str, ...], tuple[str, RefetchedDatum]] = {}
    point_errors: dict[tuple[str, ...], str] = {}
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
                    _unsupported(
                        claim,
                        checked,
                        "Status could not be positively re-derived from a current source",
                    )
                )
            continue

        if claim.datum_kind == "github_stars" and claim.datum_key != "stars":
            # Candidate-enrichment star claim: the repo slug in datum_key is
            # the refetch subject. The datum came from post-rerank enrichment,
            # not the github search source, so it bypasses the grounding-item
            # lookup and the per-source outcome gate.
            refetcher = dispatch.get("github")
            if refetcher is None:
                verdicts.append(
                    _unsupported(claim, checked, "No point-refetch verifier is registered")
                )
                continue
            if not allow_network:
                verdicts.append(
                    _unsupported(claim, checked, "Network verification is disabled for this run")
                )
                continue
            cache_key = ("github", claim.datum_key.strip().casefold())
            if cache_key in point_errors:
                verdicts.append(_unsupported(claim, checked, point_errors[cache_key]))
                continue
            try:
                cached = point_cache.get(cache_key)
                if cached:
                    # Any snapshot for this repo is the star count, whether an
                    # item-level claim ("stars") or a repo-keyed one fetched it.
                    refreshed = cached[1]
                else:
                    refreshed = _coerce_refetched(
                        refetcher(None, claim.datum_key), claim.source_url
                    )
                    point_cache[cache_key] = (claim.datum_key, refreshed)
                verdicts.append(_point_verdict(claim, checked, refreshed))
            except Exception as exc:  # verifier failures degrade to a typed verdict
                detail = f"Re-check failed: {exc}"
                point_errors[cache_key] = detail
                verdicts.append(_unsupported(claim, checked, detail))
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
        cache_key = _point_refetch_key(item, claim)
        if cache_key in point_errors:
            verdicts.append(_unsupported(claim, checked, point_errors[cache_key]))
            continue
        try:
            cached = point_cache.get(cache_key)
            if cached and cached[0] == claim.datum_key:
                refreshed = cached[1]
            elif cached and cached[1].values and claim.datum_key in cached[1].values:
                refreshed = RefetchedDatum(
                    value=cached[1].values[claim.datum_key],
                    url=cached[1].url,
                    timestamp=cached[1].timestamp,
                    values=cached[1].values,
                )
            elif cached:
                verdicts.append(
                    _unsupported(
                        claim,
                        checked,
                        "Re-fetched snapshot did not include this datum",
                    )
                )
                continue
            else:
                refreshed = _coerce_refetched(refetcher(item, claim.datum_key), claim.source_url)
                point_cache[cache_key] = (claim.datum_key, refreshed)
            verdicts.append(_point_verdict(claim, checked, refreshed))
        except Exception as exc:  # verifier failures degrade to a typed verdict
            detail = f"Re-check failed: {exc}"
            point_errors[cache_key] = detail
            verdicts.append(_unsupported(claim, checked, detail))

    report.freshness_verdicts = verdicts
    return verdicts

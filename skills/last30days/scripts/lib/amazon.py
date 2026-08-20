"""Amazon product and review signals via the Bright Data CLI.

Two-stage source, following the digg discover-then-enrich shape:

1. **Discovery** -- one ``amazon_product_search`` per run turns a
   model-supplied product keyword into product records carrying live
   aggregate stats (rating, rating count, price). Cheap and fast.
2. **Enrichment** -- ``amazon_product_reviews`` pulls a capped sample of
   written reviews for the top few surviving products, in parallel, under
   a lane deadline. Reviews ride on their product item as metadata
   comments and feed community-voice weaving.

The signature signal is the fusion of those two: an all-time rating from
thousands of ratings, set against the average of just the reviews inside
the last-30-day window. When those disagree, something changed this month,
and the review text says what. No Amazon page shows that.

Metering (R13): one credit per pipeline request regardless of records
returned, so the caps here bound paid-tier *records*, not credits. A
default run is 1 search + up to 3 review pulls = 4 requests.

Field names and quirks below are verified against live payloads pulled
2026-08-13; see the plan's schema block. Three fields arrive doubled
(``review_posted_date``, ``review_header``, ``badge``) and are repaired
here rather than downstream.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from . import brightdata, log
from .relevance import token_overlap_relevance


SEARCH_PIPELINE = "amazon_product_search"
REVIEWS_PIPELINE = "amazon_product_reviews"

DEFAULT_DOMAIN = "https://www.amazon.com"

# Reviews requested per pull. Uniform across topic shapes and depths by
# decision: billing is per *request*, not per record, so a bigger cap is
# free on the monthly credit tier, and the in-window sample is what the
# drift signal rests on. Live-verified that latency does not scale with
# this number (50 reviews in 22s vs 20 reviews in 115s on a slower SKU).
#
# It is a ceiling, never a quota -- a SKU with 31 total reviews returns 31.
MAX_REVIEWS = 50

# How many products get a review pull, per depth. Quick spends one credit
# on discovery only: aggregate stats with no recent window.
DEPTH_CONFIG = {
    "quick": 0,
    "default": 3,
    "deep": 5,
}

SEARCH_TIMEOUT = 90
REVIEW_TIMEOUT = 180

# Wall-clock ceiling for the whole parallel review lane. Pulls that miss it
# are abandoned, and their products degrade to `quiet` rather than
# disappearing (a slow SKU is real and unrelated to the cap: one live pull
# took 115s).
LANE_DEADLINE = 180

# The engine's foreground contract. The lane deadline is clamped against
# whatever remains of it, minus room to render.
FOREGROUND_CONTRACT = 300
RENDER_MARGIN = 20

# Minimum useful budget for the review lane. Below this threshold, Bright
# Data pulls reliably time out (cli_timeout = max(5, timeout-10), so budget
# 11s → CLI timeout 1s). Crumbs are a skip, not a short timeout: firing
# doomed pulls still spends 3 credits with no reviews returned.
MIN_USEFUL_REVIEW_BUDGET = 90

# Minimum dated reviews inside the window before a drift arrow is honest.
# Live census: a 50-cap pull returned 31 records of which only 5 were
# inside 30 days, so an unguarded arrow would routinely publish a "trend"
# computed from one or two reviews.
MIN_DRIFT_SAMPLE = 5

RECENT_WINDOW_DAYS = 30

# Product names run long and pipe-delimited; the footer needs a scannable
# handle, not a title.
SHORT_NAME_MAX = 18

_STAR_FIELDS = (
    ("one_star", 1),
    ("two_star", 2),
    ("three_star", 3),
    ("four_star", 4),
    ("five_star", 5),
)


def _log(msg: str) -> None:
    log.source_log("Amazon", msg, tty_only=False)


def _today() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------- parsing


def undouble(text: str) -> str:
    """Repair the CLI's doubled string fields.

    Observed live: ``review_header`` arrives as ``"Best Box!Best Box!"`` and
    ``badge`` as ``"Verified Purchase, Verified Purchase"``. Handles the
    exact-repeat case and the comma-joined repeat, and leaves anything else
    untouched -- a genuinely repetitive title must survive intact.
    """
    value = (text or "").strip()
    if not value:
        return ""
    half, odd = divmod(len(value), 2)
    # Only treat an exact repeat as doubling when the halves are substantial
    # and look like a phrase rather than a syllable -- otherwise a real title
    # of "ByeBye" or "NoNo" gets silently truncated to half of itself. The
    # observed artifact doubles whole headlines, so requiring some length and
    # either whitespace or terminal punctuation keeps the repair targeted.
    if not odd and half >= 6 and value[:half] == value[half:]:
        first = value[:half]
        if " " in first or first[-1] in ".!?":
            return first.strip()
    parts = [p.strip() for p in value.split(",")]
    if len(parts) == 2 and parts[0] and parts[0] == parts[1]:
        return parts[0]
    return value


_DATE_HEAD = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})")


def parse_review_date(raw: Any) -> Optional[str]:
    """Pull the ISO date out of the CLI's prose-wrapped date field.

    Live shape: ``"August 3, 2026Reviewed in the United States on August 3,
    2026"``. Only the leading ``%B %d, %Y`` is trustworthy; the tail is
    localized prose that varies by marketplace.

    Returns ``YYYY-MM-DD`` or None.
    """
    match = _DATE_HEAD.match(str(raw or "").strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def short_name(name: str, brand: str = "") -> str:
    """Derive a scannable footer handle from a long product name.

    Live names are pipe-delimited marketing strings with the brand carried
    in its own field rather than as a prefix ("Chill Max Leak-Proof XL
    Bento-Style Lunch Box | Included Ice Pack Keeps Food Cold"). Take the
    segment before the first delimiter, drop a leading brand token if one
    did sneak in, and clip to a scannable width on a word boundary.
    """
    text = re.split(r"[|(–—]", str(name or ""), maxsplit=1)[0].strip(" -,")
    brand_token = str(brand or "").strip()
    if brand_token:
        # Word-boundary anchored: a bare startswith() eats into sub-brands and
        # coincidental prefixes ("AnkerWork" under brand "Anker" would become
        # "Work", "Chillax" under "Chill" would become "ax").
        stripped = re.sub(
            rf"^{re.escape(brand_token)}\b[\s\-,]*", "", text, count=1, flags=re.IGNORECASE
        )
        if stripped:
            text = stripped.strip(" -,")
    if len(text) <= SHORT_NAME_MAX:
        return text
    clipped = text[:SHORT_NAME_MAX].rsplit(" ", 1)[0].strip(" -,")
    return clipped or text[:SHORT_NAME_MAX].strip()


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_sponsored(value: Any) -> bool:
    """The flag arrives as the string 'true'/'false', not a bool.

    Recorded in metadata but never used to filter (R4): its distribution
    swings hard with keyword phrasing, so filtering on it can blank the
    lane on an unlucky query.
    """
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


def _valid_product_url(url: str, domain: str) -> bool:
    """Accept only https URLs on the configured Amazon host."""
    try:
        parsed = urlparse(url)
        expected = urlparse(domain or DEFAULT_DOMAIN)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = parsed.netloc.lower().removeprefix("www.")
    want = (expected.netloc or "").lower().removeprefix("www.")
    return bool(want) and host == want


# Amazon ASINs are a fixed shape. Validating it matters because the value
# is interpolated into a URL that is then refetched through the CLI *and*
# rendered as a link in the report -- two sinks, one unvalidated API field.
_ASIN_RE = re.compile(r"^[A-Za-z0-9]{10}$")


def _valid_asin(asin: str) -> bool:
    return bool(_ASIN_RE.match(asin or ""))


def canonical_product_url(url: str, asin: str, domain: str) -> str:
    """Strip Amazon's tracking tail down to a stable /dp/<asin> link.

    Search records carry 200+ character URLs with session-scoped ``dib``
    tokens. Those work but are unreadable in a report and unstable across
    runs, which breaks dedupe on re-runs of the same topic.

    Falls back to the (already host-validated) original URL if the ASIN is
    not well-formed, so a malformed record can never shape the rebuilt URL.
    """
    if not _valid_asin(asin):
        return url
    base = (domain or DEFAULT_DOMAIN).rstrip("/")
    return f"{base}/dp/{asin}"


# ------------------------------------------------------------- discovery


def search_products(
    keyword: str,
    *,
    domain: str = DEFAULT_DOMAIN,
    config: Optional[Dict[str, Any]] = None,
    timeout: int = SEARCH_TIMEOUT,
) -> Dict[str, Any]:
    """Run one product search. Never raises; returns the adapter envelope."""
    query = (keyword or "").strip()
    if not query:
        return {"records": []}
    # A leading dash would be parsed as a CLI option rather than a search
    # term. The keyword is model-supplied and can be influenced by
    # pre-research over untrusted web content, so reject rather than
    # sanitize -- a keyword starting with '-' is never a real product.
    if query.startswith("-"):
        _log(f"rejecting option-shaped keyword: {query!r}")
        return {"records": [], "error": "amazon keyword may not begin with '-'"}
    _log(f"search '{query}' on {domain}")
    response = brightdata.run_pipeline(
        SEARCH_PIPELINE, [query, domain or DEFAULT_DOMAIN],
        timeout=timeout, config=config,
    )
    if response.get("error"):
        _log(f"search failed: {response['error']}")
    else:
        _log(f"search returned {len(response.get('records') or [])} records")
    return response


def parse_search_response(
    response: Dict[str, Any],
    keyword: str,
    *,
    domain: str = DEFAULT_DOMAIN,
    min_relevance: float = 0.15,
) -> List[Dict[str, Any]]:
    """Turn raw search records into deduped, relevance-gated product dicts.

    Dedupe is by ASIN: live payloads repeat a single product up to five
    times across the result set (64 unique of 66 records on one pull).
    Relevance is scored against the *supplied keyword*, not the run topic,
    because the model may search "June Oven" on a topic about a person.
    """
    records = response.get("records") if isinstance(response, dict) else None
    if not isinstance(records, list):
        return []

    today = _today().date().isoformat()
    seen: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        asin = str(record.get("asin") or "").strip()
        raw_url = str(record.get("url") or "").strip()
        if not _valid_asin(asin) or not _valid_product_url(raw_url, domain):
            continue

        name = str(record.get("name") or "").strip()
        brand = str(record.get("brand") or "").strip()
        if not name:
            continue

        relevance = token_overlap_relevance(keyword, f"{brand} {name}".strip())
        if relevance < min_relevance:
            continue

        num_ratings = _as_int(record.get("num_ratings"))
        existing = seen.get(asin)
        # Duplicates of one ASIN can disagree on rating count (variant-level
        # records); keep the richest.
        if existing and _as_int(existing.get("num_ratings")) >= num_ratings:
            continue

        seen[asin] = {
            "asin": asin,
            # Current-date stamped (KTD6, trustpilot precedent): a live
            # aggregate rating is a fact about now, not about the product's
            # launch date, so it must not be dropped by the 30-day filter.
            "date": today,
            "name": name,
            "short_name": short_name(name, brand),
            "brand": brand,
            "url": canonical_product_url(raw_url, asin, domain),
            "rating": _as_float(record.get("rating")),
            "num_ratings": num_ratings,
            "price": _as_float(record.get("final_price")),
            "currency": str(record.get("currency") or "").strip(),
            "badge": undouble(str(record.get("badge") or "")),
            "sponsored": _is_sponsored(record.get("sponsored")),
            "bought_past_month": _as_int(record.get("bought_past_month")),
            "rank_on_page": _as_int(record.get("rank_on_page")),
            "relevance": relevance,
        }

    products = sorted(
        seen.values(),
        key=lambda p: (p["num_ratings"], p["relevance"]),
        reverse=True,
    )
    _log(f"{len(products)} unique on-keyword products after dedupe")
    return products


def infer_brand(products: Sequence[Dict[str, Any]], keyword: str) -> str:
    """Detect a brand topic by matching record brands against the keyword.

    This is the guard against paying to review a competitor. Rival brands
    buy ads against a brand keyword and can outrank the brand's own catalog
    on raw rating count: on a live "bentgo lunch box" search a competitor
    held the top two slots and would have taken two of the three review
    pulls, putting a rival's reviews in a Bentgo report.

    Matching the *keyword's own tokens*, rather than picking the most
    common brand in the results, is what keeps category topics unfiltered.
    "best bluetooth speaker" names no brand, so nothing is constrained and
    the top products across brands compete on merit -- which is exactly
    what that topic shape wants.
    """
    normalized_keyword = " ".join(re.findall(r"[a-z0-9]+", (keyword or "").lower()))
    if not normalized_keyword:
        return ""
    keyword_tokens = set(normalized_keyword.split())

    # Keyed by the lowercased brand so one vendor spelled two ways ("Bentgo"
    # and "BENTGO" in the same result set) reads as one candidate. Without
    # this the set has two members, the function bails, and the guard it
    # exists to provide silently turns off.
    candidates: Dict[str, str] = {}
    for product in products:
        brand = str(product.get("brand") or "").strip()
        if not brand:
            continue
        brand_tokens = re.findall(r"[a-z0-9]+", brand.lower())
        if not brand_tokens:
            continue
        # Multi-word brands ("Hydro Flask") can never match a single-token
        # test, so compare the brand's whole token sequence against the
        # keyword's -- otherwise the guard is off for every two-word brand.
        if len(brand_tokens) == 1:
            matched = brand_tokens[0] in keyword_tokens and len(brand_tokens[0]) > 2
        else:
            matched = " ".join(brand_tokens) in normalized_keyword
        if matched:
            # First spelling wins, so the result is deterministic across runs.
            candidates.setdefault(brand.lower(), brand)
    return next(iter(candidates.values())) if len(candidates) == 1 else ""


def select_enrichment_targets(
    products: Sequence[Dict[str, Any]],
    *,
    limit: int,
    brand: str = "",
    keyword: str = "",
) -> List[Dict[str, Any]]:
    """Pick which products get a review pull.

    Ranked by rating count, which is a coarse signal: search records carry
    variant-level counts that can undercount badly (84 on a record whose
    review pull reported 8,446). The review pull's own
    ``product_rating_count`` is authoritative once available.

    Two filters run before the cut:

    * **Brand**, supplied or inferred from the keyword (see ``infer_brand``).
      The record's own ``brand`` field does the work, which also solves
      accessory contamination outright -- a "grill brush for Weber" carries
      the brush maker's brand, not Weber. A front-anchored name match covers
      the few records where ``brand`` is null.
    * **Variant collapse.** Live results repeat one product across colors
      and sizes under distinct ASINs with near-identical names. Two of those
      would burn two of three pulls on the same product and render as
      duplicate footer entries, so only the best-ranked of each short-name
      group stays eligible.
    """
    if limit <= 0:
        return []
    pool = list(products)

    wanted = (brand or "").strip().lower() or infer_brand(pool, keyword).lower()
    if wanted:
        matched = [
            p for p in pool
            if (p.get("brand") or "").strip().lower() == wanted
            or (not (p.get("brand") or "").strip()
                and str(p.get("name") or "").strip().lower().startswith(wanted))
        ]
        if matched:
            pool = matched

    deduped: List[Dict[str, Any]] = []
    seen_names: set[str] = set()
    for product in pool:
        key = (product.get("short_name") or "").strip().lower()
        if key and key in seen_names:
            continue
        if key:
            seen_names.add(key)
        deduped.append(product)
    return deduped[:limit]


# ------------------------------------------------------------ enrichment


def fetch_reviews(
    product_url: str,
    *,
    max_reviews: int = MAX_REVIEWS,
    config: Optional[Dict[str, Any]] = None,
    timeout: int = REVIEW_TIMEOUT,
) -> Dict[str, Any]:
    """Pull a capped review sample for one product. Never raises."""
    if not product_url:
        return {"records": []}
    return brightdata.run_pipeline(
        REVIEWS_PIPELINE, [product_url, str(max_reviews)],
        timeout=timeout, config=config,
    )


def parse_reviews(response: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Split a review payload into comment dicts and product-level stats.

    Product-level fields (``product_rating``, ``product_rating_count``, the
    ``product_rating_object`` star distribution) ride on *every* review
    record, so they are read off the first one.

    Comments are built directly in the shared score/excerpt shape rather
    than routed through ``normalize._remap_comments``, which strips keys it
    does not know -- and rating, date, and verified are exactly the keys
    this source needs to keep. Sorted newest first so the woven sample
    favors recent voices.
    """
    records = response.get("records") if isinstance(response, dict) else None
    if not isinstance(records, list) or not records:
        return [], {}

    first = records[0]
    distribution = first.get("product_rating_object")
    stats: Dict[str, Any] = {
        "product_rating": _as_float(first.get("product_rating")),
        "product_rating_count": _as_int(first.get("product_rating_count")),
        "star_distribution": distribution if isinstance(distribution, dict) else {},
    }

    comments: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        body = str(record.get("review_text") or "").strip()
        header = undouble(str(record.get("review_header") or ""))
        excerpt = body or header
        if not excerpt:
            continue
        comments.append(
            {
                # Shared comment shape: downstream weaving reads score/excerpt.
                "score": _as_int(record.get("helpful_count")),
                "excerpt": excerpt,
                "author": str(record.get("author_name") or "").strip(),
                "rating": _as_int(record.get("rating")),
                "date": parse_review_date(record.get("review_posted_date")),
                "verified": bool(record.get("is_verified")),
                "vine": bool(record.get("is_amazon_vine")),
                "title": header,
            }
        )

    # Newest first; undated records sink rather than disappear (R2a).
    comments.sort(key=lambda c: (c["date"] or "", c["score"]), reverse=True)
    return comments, stats


def _remaining_lane_budget(elapsed: float) -> int:
    """Compute the review lane's wall-clock budget.

    Returns the lesser of LANE_DEADLINE and whatever remains of the foreground
    contract. If the remaining time is below MIN_USEFUL_REVIEW_BUDGET, returns
    0 (skip the lane entirely) rather than firing doomed short pulls that spend
    Bright Data credits without returning reviews.
    """
    remaining = FOREGROUND_CONTRACT - elapsed - RENDER_MARGIN
    if remaining < MIN_USEFUL_REVIEW_BUDGET:
        return 0
    return int(min(LANE_DEADLINE, remaining))


def enrich_with_reviews(
    products: Sequence[Dict[str, Any]],
    *,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
    elapsed: float = 0.0,
    max_reviews: int = MAX_REVIEWS,
    brand: str = "",
    keyword: str = "",
    fetcher=None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Attach review samples to the top products, in parallel, under a deadline.

    Every product is returned either way. A product whose pull is dropped
    by the deadline keeps its search-record stats and simply carries no
    review sample -- it renders as ``quiet`` rather than vanishing, because
    losing a top product entirely is a worse failure than losing its
    recent-window read. The dropped pull has spent its credit regardless.

    Returns (enriched_products, status_detail). status_detail is None when
    enrichment succeeded normally, or a string describing a degraded outcome:
    - ``"review lane skipped (budget 0s)"`` -- crumb budget, lane did not run
    - ``"review lane timed out"`` -- all pulls dropped by the deadline
    """
    enriched = [dict(p) for p in products]
    pull_count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    if pull_count <= 0:
        _log(f"depth={depth}: discovery only, no review pulls")
        return enriched, None

    budget = _remaining_lane_budget(elapsed)
    if budget <= 0:
        _log(f"review lane skipped (budget {budget}s, floor {MIN_USEFUL_REVIEW_BUDGET}s)")
        return enriched, "review lane skipped (budget 0s)"

    targets = select_enrichment_targets(
        enriched, limit=pull_count, brand=brand, keyword=keyword
    )
    if not targets:
        return enriched, None

    by_asin = {p["asin"]: p for p in enriched}
    pull = fetcher or (
        lambda url: fetch_reviews(
            url, max_reviews=max_reviews, config=config,
            timeout=min(REVIEW_TIMEOUT, budget),
        )
    )

    _log(f"pulling up to {max_reviews} reviews for {len(targets)} products (budget {budget}s)")
    started = time.monotonic()
    completed_count = 0
    dropped_count = 0
    # Not a `with` block on purpose. Every future is already running (one
    # worker per target), so `future.cancel()` can never succeed, and
    # ThreadPoolExecutor's context-manager exit calls shutdown(wait=True) --
    # which would block on the very straggler the deadline just declared
    # dropped, making the deadline advisory rather than real. Shutting down
    # without waiting lets the abandoned thread finish and discard its result
    # in the background while the run proceeds.
    pool = ThreadPoolExecutor(max_workers=max(1, len(targets)))
    try:
        futures = {pool.submit(pull, t["url"]): t["asin"] for t in targets}
        try:
            for future in as_completed(futures, timeout=budget):
                asin = futures[future]
                try:
                    response = future.result()
                except Exception as exc:  # never let one pull kill siblings
                    _log(f"review pull failed for {asin}: {exc}")
                    continue
                if response.get("error"):
                    _log(f"review pull error for {asin}: {response['error']}")
                    continue
                comments, stats = parse_reviews(response)
                product = by_asin.get(asin)
                if product is None:
                    continue
                product["top_comments"] = comments
                product.update({k: v for k, v in stats.items() if v})
                completed_count += 1
        except TimeoutError:
            dropped_count = sum(1 for f in futures if not f.done())
            _log(f"lane deadline {budget}s hit; dropped {dropped_count} straggling pull(s)")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    _log(f"review lane finished in {time.monotonic() - started:.0f}s")

    # Report degraded outcome if all pulls dropped (none completed)
    status_detail = None
    if completed_count == 0 and dropped_count > 0:
        status_detail = "review lane timed out"

    return enriched, status_detail


def enrich_source_items(
    items: List[Any],
    *,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
    keyword: str = "",
    elapsed: float = 0.0,
    max_reviews: int = MAX_REVIEWS,
    fetcher=None,
) -> List[Any]:
    """Attach review samples to the amazon SourceItems that survived dedupe.

    Reads product identity out of ``metadata`` and writes ``top_comments``
    plus the computed stat block back into it, in place. Runs from
    ``pipeline._finalize_items_by_source`` so the review budget is spent on
    the products the brief will actually show, not on the top of the raw
    fanout (the digg enrichment precedent).
    """
    products: List[Dict[str, Any]] = []
    by_asin: Dict[str, Any] = {}
    for item in items:
        if getattr(item, "source", None) != "amazon":
            continue
        metadata = getattr(item, "metadata", None) or {}
        asin = str(metadata.get("asin") or "").strip()
        if not asin or metadata.get("top_comments"):
            continue
        products.append(
            {
                "asin": asin,
                "url": getattr(item, "url", "") or metadata.get("url", ""),
                "name": metadata.get("name") or getattr(item, "title", ""),
                "short_name": metadata.get("short_name") or "",
                "brand": metadata.get("brand") or "",
                "num_ratings": metadata.get("num_ratings") or 0,
                "rating": metadata.get("rating"),
            }
        )
        by_asin[asin] = item

    if not products:
        return items

    enriched, _status = enrich_with_reviews(
        products, depth=depth, config=config, elapsed=elapsed,
        max_reviews=max_reviews, keyword=keyword, fetcher=fetcher,
    )
    for product in enriched:
        item = by_asin.get(product["asin"])
        if item is None:
            continue
        metadata = getattr(item, "metadata", None)
        if metadata is None:
            continue
        if product.get("top_comments"):
            metadata["top_comments"] = product["top_comments"]
        stats = product_stats(product)
        metadata["stats"] = stats
        # The review pull's product_rating_count supersedes the search
        # record's, which is variant-level and can undercount by orders of
        # magnitude (84 on a record whose pull reported 8,446). Normalization
        # ran before enrichment, so refresh the surfaces that already baked
        # the old number in -- otherwise one product shows two different
        # rating counts in the same report.
        for key in ("product_rating", "product_rating_count", "star_distribution"):
            if product.get(key):
                metadata[key] = product[key]
        authoritative = stats.get("ratings_total") or 0
        if authoritative and getattr(item, "engagement", None) is not None:
            item.engagement["ratings"] = authoritative
            metadata["num_ratings"] = authoritative
            _refresh_title(item, stats)
    return items


def _refresh_title(item: Any, stats: Dict[str, Any]) -> None:
    """Rewrite the trailing "- 4.4/5 (N ratings)" headline after enrichment."""
    title = getattr(item, "title", "") or ""
    rating = stats.get("all_time")
    total = stats.get("ratings_total") or 0
    if not title or rating is None or not total:
        return
    headline = f"{rating}/5 ({total:,} ratings)"
    base = title.rsplit(" - ", 1)[0] if " - " in title else title
    item.title = f"{base} - {headline}"


# ------------------------------------------------------------------ stats


def stats_from_item(item: Any, *, today: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute the stat block for a rendered SourceItem.

    Enrichment stores a precomputed block, but mock runs and replayed
    fixtures skip enrichment entirely, so render recomputes from metadata
    when it is absent. Cheap and pure -- all the inputs already live on
    the item.
    """
    metadata = getattr(item, "metadata", None) or {}
    cached = metadata.get("stats")
    if isinstance(cached, dict) and cached:
        return cached
    return product_stats(
        {
            "short_name": metadata.get("short_name") or "",
            "name": metadata.get("name") or getattr(item, "title", ""),
            "url": getattr(item, "url", "") or "",
            "rating": metadata.get("rating"),
            "num_ratings": metadata.get("num_ratings") or 0,
            "product_rating": metadata.get("product_rating"),
            "product_rating_count": metadata.get("product_rating_count") or 0,
            "star_distribution": metadata.get("star_distribution") or {},
            "top_comments": metadata.get("top_comments") or [],
        },
        today=today,
    )


def footer_entry(stats: Dict[str, Any], *, quote: str = "") -> str:
    """Render one product's segment of the emoji-footer line (R1c).

    Shapes, by drift state::

        Chill Max XL 4.4★→3.8★ ↓ "the lid jams"   negative drift (+ quote)
        Deluxe Bag 4.7★→5.0★                      positive or flat drift
        Spirit E-325 4.4★ quiet                   too few in-window reviews
        BLUEY Set new                             no all-time baseline

    The ``↓`` is asymmetric on purpose: a sagging product is the alarm
    worth catching at a glance, and a healthy one needs no decoration.
    """
    name = stats.get("short_name") or "Product"
    all_time = stats.get("all_time")
    recent = stats.get("recent_avg")
    drift = stats.get("drift")

    if drift == "new" or all_time is None:
        return f"{name} new"
    if drift == "quiet" or recent is None:
        return f"{name} {all_time}★ quiet"

    entry = f"{name} {all_time}★→{recent}★"
    if drift == "down":
        entry += " ↓"
        if quote:
            entry += f' "{quote}"'
    return entry


def five_star_share(distribution: Dict[str, Any]) -> Optional[float]:
    """Share of ratings that are 5-star, from the star-distribution object."""
    if not isinstance(distribution, dict) or not distribution:
        return None
    total = sum(_as_int(distribution.get(key)) for key, _ in _STAR_FIELDS)
    if total <= 0:
        return None
    return _as_int(distribution.get("five_star")) / total


def recent_window_stats(
    comments: Iterable[Dict[str, Any]],
    *,
    today: Optional[datetime] = None,
    window_days: int = RECENT_WINDOW_DAYS,
) -> Dict[str, Any]:
    """Average rating and sample size inside the recent window."""
    reference = (today or _today()).date()
    ratings: List[int] = []
    for comment in comments or []:
        iso = comment.get("date")
        if not iso:
            continue
        try:
            posted = datetime.strptime(iso, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if 0 <= (reference - posted).days <= window_days:
            rating = _as_int(comment.get("rating"))
            if rating:
                ratings.append(rating)
    if not ratings:
        return {"recent_n": 0, "recent_avg": None}
    return {"recent_n": len(ratings), "recent_avg": sum(ratings) / len(ratings)}


def product_stats(
    product: Dict[str, Any],
    *,
    today: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compute the render-facing stat block for one product.

    ``drift`` is one of:
      * ``"new"``   -- no all-time baseline to move away from
      * ``"quiet"`` -- baseline exists but the window has too few dated
                       reviews to average honestly (below MIN_DRIFT_SAMPLE)
      * ``"up"`` / ``"down"`` / ``"flat"`` -- a real, sample-backed move

    The engine owns every number here; the model owns the words (R1b).
    """
    # The review pull's rating count supersedes the search record's, which
    # can be variant-level and badly low.
    all_time = product.get("product_rating")
    if all_time is None:
        all_time = product.get("rating")
    ratings_total = product.get("product_rating_count") or product.get("num_ratings") or 0

    window = recent_window_stats(product.get("top_comments") or [], today=today)
    recent_avg = window["recent_avg"]
    recent_n = window["recent_n"]

    if all_time is None:
        drift = "new"
    elif recent_n < MIN_DRIFT_SAMPLE or recent_avg is None:
        drift = "quiet"
    elif round(recent_avg, 1) > round(float(all_time), 1):
        drift = "up"
    elif round(recent_avg, 1) < round(float(all_time), 1):
        drift = "down"
    else:
        drift = "flat"

    return {
        "short_name": product.get("short_name") or short_name(product.get("name", "")),
        "url": product.get("url", ""),
        "all_time": round(float(all_time), 1) if all_time is not None else None,
        "ratings_total": _as_int(ratings_total),
        "five_star_share": five_star_share(product.get("star_distribution") or {}),
        "recent_avg": round(recent_avg, 1) if recent_avg is not None else None,
        "recent_n": recent_n,
        "reviews_pulled": len(product.get("top_comments") or []),
        "drift": drift,
    }

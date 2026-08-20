"""File contracts for the three-command host-judged discovery protocol.

Leg 1 (``--discover --nominate-only``) writes the nominations bundle: the
FULL judge pool, each nomination with its complete seed item set, serialized
losslessly so leg 2 can recompute floor/velocity/entity-token disambiguation
exactly as an in-memory run would. Leg 2 (``--discover --judgments <file>``)
reads host judgments (names/junk/worthiness) bound to the bundle by
bundle_id. Leg 3 (``--discover --finalize [--angles <file>]``) applies
host-written content angles.

This module owns the handoff contracts - bundle writer/reader, judgments
reader, pending-report reader (the leg-2 output leg 3 finalizes from),
angles reader - plus the host-facing digest and the post-judgment
name-collision resolver. Readers are strict at the top level (typed
``HandoffContractError``, mapped to exit 2 by the CLI layer) and lenient per
row: a malformed or omitted row falls back to the bundle's heuristics rather
than failing the run.
"""

from __future__ import annotations

import json
import secrets
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from . import env, log, pipeline, rerank, schema


# How long a nominations bundle stays valid. Deliberately a module constant
# and NOT the LAST30DAYS_REPORT_CACHE_TTL_SECONDS env knob: a user who
# lowered the report-cache TTL for drill freshness must not shrink the
# window a host has to author judgments.
DISCOVERY_HANDOFF_TTL_SECONDS = 3600.0

NOMINATIONS_BUNDLE_FILENAME = "discover-nominations.json"
PENDING_REPORT_FILENAME = "discover-pending.json"

_VALID_TIERS = ("deep", "shallow")

_RESWEEP_REMEDY = "Run a fresh `--discover --nominate-only` re-sweep."

# Leg-3 remedy: the pending report is leg-2 output, so the first fix is to
# re-run the resume leg; only when the bundle itself has also gone stale does
# the whole protocol restart.
_RESUME_REMEDY = (
    "Re-run the resume leg (`--discover --judgments <file>`), or the full "
    "protocol from `--discover --nominate-only` if the bundle is stale too."
)

# Defensive caps on host-supplied text, ported from the retired engine-judge
# pass: names become search queries and the /last30days handoff, angles
# render verbatim on trend cards, so a runaway (or adversarial) value never
# yields an unbounded string.
_NAME_MAX_CHARS = 96
_ANGLE_MAX_CHARS = 200

# Unified trailing-punctuation charset for word-boundary truncation: names
# and angle sentences share it so the strip sets cannot drift.
_TRUNCATE_STRIP_CHARS = " \"'`.,;:!?-"

# Digest evidence caps: the surface the engine judge used to see per
# nomination (leader title, leader snippet, strongest community comment).
_DIGEST_TITLE_MAX_CHARS = 220
_DIGEST_SNIPPET_MAX_CHARS = 420
_DIGEST_COMMENT_MAX_CHARS = 340


class HandoffContractError(Exception):
    """A handoff file failed its contract: unreadable, invalid JSON, wrong
    shape or schema version, stale, or not bound to the current bundle.
    The CLI layer maps this to exit code 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PoolEntry:
    """One judge-pool nomination as handed to the bundle writer (leg 1).

    ``heuristic_name`` and ``heuristic_junk`` are the deterministic
    topic_shape fallbacks, kept alongside the nomination so leg 2 can fill
    any row the host omitted without re-deriving them.
    """

    nomination: pipeline.Nomination
    cluster_id: str
    heuristic_name: str
    heuristic_junk: bool


@dataclass(frozen=True)
class BundleNomination:
    """One nomination read back from a bundle, with its stable id."""

    nomination_id: str
    nomination: pipeline.Nomination
    cluster_id: str
    heuristic_name: str
    heuristic_junk: bool
    sources: list[str]
    engagement_by_source: dict[str, dict[str, float | int]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class NominationsBundle:
    """A parsed leg-1 nominations bundle (also returned by the writer).

    ``source_status`` is the leg-1 sweep's finalized per-source outcome map:
    legs 2 and 3 restore it so degraded sweep coverage survives the protocol
    instead of silently reading as clean. ``mock`` is the writing run's
    provenance - mock-born state must never be finalized by a real run (and
    vice versa); files written before either field existed read as an empty
    map and a real run."""

    schema_version: str
    bundle_id: str
    generated_at: str
    from_date: str
    to_date: str
    domain: str
    tier: str
    enrichment_source_boundary: list[str] | None
    requested_sources: list[str] | None
    lookback_days: int
    nominations: list[BundleNomination]
    source_status: dict[str, schema.SourceOutcome] = field(default_factory=dict)
    mock: bool = False
    path: Path | None = None


@dataclass(frozen=True)
class HostJudgment:
    """One host verdict row. ``None`` on any field means the host left it
    absent for that row and the caller falls back to the bundle's heuristic
    value (name/junk) or to no worthiness signal."""

    name: str | None
    junk: bool | None
    worthiness: int | None


# The per-row-absent marker: what ``judgment_for`` returns for a nomination
# the host omitted entirely. Every field falls back to the bundle heuristics.
ROW_ABSENT = HostJudgment(name=None, junk=None, worthiness=None)


@dataclass(frozen=True)
class HostAngles:
    """One host-written angle row; either field may be absent."""

    podcast: str | None
    x_article: str | None


@dataclass(frozen=True)
class PendingReport:
    """A parsed leg-2 pending report: the floored/folded/ranked discovery
    report (as its raw ``schema.to_dict`` payload - leg 3 rebuilds it via
    ``schema.discovery_report_from_dict``) plus the angle inputs keyed by
    surviving nomination id. ``run_ref`` is the leg-2 run identity the
    finalize leg replays into the topic queue so retries stay idempotent."""

    schema_version: str
    bundle_id: str
    generated_at: str
    run_ref: str
    report: dict[str, Any]
    angle_inputs: dict[str, dict[str, str]]
    # Leg-2 provenance: True when a --mock resume wrote this file. Files
    # written before the flag existed read as real (False).
    mock: bool = False
    path: Path | None = None


def _warn(message: str) -> None:
    log.source_log("Discover", message, tty_only=False)


def handoff_state_dir(
    save_dir: str | Path | None,
    config_dir: Path | None,
) -> Path | None:
    """Resolve the handoff state directory: ``save_dir`` when provided, else
    the config dir (mirrors the report-cache convention in last30days.py).
    Both are accepted as arguments so this module never imports the CLI
    layer above it. Returns None when neither location is available."""
    if save_dir:
        return Path(save_dir).expanduser().resolve()
    if config_dir is not None:
        return Path(config_dir)
    return None


def nominations_bundle_path(state_dir: str | Path) -> Path:
    """The nominations bundle file inside a handoff state directory."""
    return Path(state_dir) / NOMINATIONS_BUNDLE_FILENAME


def pending_report_path(state_dir: str | Path) -> Path:
    """The leg-2 pending-report file inside a handoff state directory."""
    return Path(state_dir) / PENDING_REPORT_FILENAME


def _search_paths(
    save_dir: str | Path | None,
    config_dir: Path | None,
    path_fn: Callable[[Path], Path],
) -> list[Path]:
    """Candidate handoff-file locations: ONLY the save dir when one was
    supplied, else the config dir. An explicit save dir is the protocol's
    single handoff store (mirroring ``_scoped_store_db`` and SKILL.md's "a
    different or missing save dir on a later leg means the leg cannot find
    them" contract), so a handoff file in the config dir must never silently
    satisfy a save-dir run. ``path_fn`` picks which handoff file (bundle vs
    pending)."""
    if save_dir:
        return [path_fn(Path(save_dir).expanduser().resolve())]
    if config_dir is not None:
        return [path_fn(Path(config_dir))]
    return []


def _searched_lines(searched: list[Path]) -> str:
    if not searched:
        return "  (no --save-dir and no config directory available)"
    return "\n".join(f"  - {path}" for path in searched)


def write_nominations_bundle(
    entries: Sequence[PoolEntry],
    *,
    domain: str,
    tier: str,
    from_date: str,
    to_date: str,
    lookback_days: int,
    enrichment_source_boundary: list[str] | None,
    requested_sources: list[str] | None,
    source_status: dict[str, schema.SourceOutcome] | None = None,
    mock: bool = False,
    save_dir: str | Path | None = None,
    config_dir: Path | None = None,
) -> NominationsBundle:
    """Write the leg-1 nominations bundle and return its parsed form.

    Nomination ids are assigned ``n1, n2, ...`` in pool order. The leg-1
    invocation context (enrichment source boundary, requested discovery
    sources, lookback days) rides along so leg 2 resumes with identical
    settings. ``None`` boundaries are preserved as null - "no boundary" and
    "empty boundary" are different contracts. ``source_status`` is the
    sweep's finalized per-source outcome map (serialized via the same
    ``schema.to_dict`` round trip every report uses) so degraded coverage
    survives into legs 2-3; ``mock`` stamps the writing run's provenance.
    """
    if tier not in _VALID_TIERS:
        raise ValueError(f"tier must be one of {_VALID_TIERS}, got {tier!r}")
    state_dir = handoff_state_dir(save_dir, config_dir)
    if state_dir is None:
        raise HandoffContractError(
            "No handoff location available to write the nominations bundle: "
            "pass --save-dir or configure ~/.config/last30days/."
        )
    bundle_id = secrets.token_hex(8)
    generated_at = schema._utc_now()

    rows: list[dict[str, Any]] = []
    nominations: list[BundleNomination] = []
    for index, entry in enumerate(entries, start=1):
        nomination_id = f"n{index}"
        sources = sorted({item.source for item in entry.nomination.items})
        engagement = pipeline._discovery_engagement(entry.nomination.items)
        rows.append({
            "id": nomination_id,
            "cluster_id": entry.cluster_id,
            "heuristic_name": entry.heuristic_name,
            "heuristic_junk": bool(entry.heuristic_junk),
            "sources": sources,
            "engagement_by_source": engagement,
            "nomination": schema.nomination_to_dict(entry.nomination),
        })
        nominations.append(BundleNomination(
            nomination_id=nomination_id,
            nomination=entry.nomination,
            cluster_id=entry.cluster_id,
            heuristic_name=entry.heuristic_name,
            heuristic_junk=bool(entry.heuristic_junk),
            sources=sources,
            engagement_by_source=engagement,
        ))

    payload = {
        "schema_version": schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION,
        "kind": schema.DISCOVERY_NOMINATIONS_KIND,
        "bundle_id": bundle_id,
        "generated_at": generated_at,
        "from_date": from_date,
        "to_date": to_date,
        "domain": domain,
        "tier": tier,
        "mock": bool(mock),
        "source_status": {
            source: schema.to_dict(outcome)
            for source, outcome in (source_status or {}).items()
        },
        "context": {
            "enrichment_source_boundary": (
                list(enrichment_source_boundary)
                if enrichment_source_boundary is not None
                else None
            ),
            "requested_sources": (
                list(requested_sources) if requested_sources is not None else None
            ),
            "lookback_days": int(lookback_days),
        },
        "nominations": rows,
    }
    path = nominations_bundle_path(state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        # A locked/read-only/full disk is the protocol's clean exit-2 path,
        # never a traceback.
        raise HandoffContractError(
            f"Could not write nominations bundle {path}: {exc}"
        ) from exc
    return NominationsBundle(
        schema_version=schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION,
        bundle_id=bundle_id,
        generated_at=generated_at,
        from_date=from_date,
        to_date=to_date,
        domain=domain,
        tier=tier,
        enrichment_source_boundary=(
            list(enrichment_source_boundary)
            if enrichment_source_boundary is not None
            else None
        ),
        requested_sources=(
            list(requested_sources) if requested_sources is not None else None
        ),
        lookback_days=int(lookback_days),
        nominations=nominations,
        source_status=dict(source_status or {}),
        mock=bool(mock),
        path=path,
    )


def read_nominations_bundle(
    *,
    save_dir: str | Path | None = None,
    config_dir: Path | None = None,
) -> NominationsBundle:
    """Locate and parse the nominations bundle for legs 2 and 3.

    The bundle lives in the save dir when one was supplied, else the config
    dir - never both (no cross-store fallback). Raises HandoffContractError
    (naming the searched location and the re-sweep remedy) when no bundle
    exists, and for any top-level contract violation in the file found.
    """
    searched = _search_paths(save_dir, config_dir, nominations_bundle_path)
    path = next((candidate for candidate in searched if candidate.exists()), None)
    if path is None:
        raise HandoffContractError(
            "No discovery nominations bundle found. Searched:\n"
            f"{_searched_lines(searched)}\n{_RESWEEP_REMEDY}"
        )
    return _parse_bundle_file(path)


def _parse_handoff_envelope(
    path: Path,
    *,
    label: str,
    kind: str,
    schema_version: str,
    remedy: str,
    missing_id_context: str,
    stale_context: str,
) -> tuple[dict[str, Any], str, Any]:
    """Shared strict top-level validation for the two engine-written handoff
    files (nominations bundle, pending report): readable, valid JSON object,
    right kind and schema version, bundle_id present, within TTL. Returns
    (payload, bundle_id, generated_at)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HandoffContractError(
            f"Could not read {label.lower()} {path}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HandoffContractError(
            f"{label} {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HandoffContractError(
            f"{label} {path} must be a top-level JSON object, "
            f"got {type(payload).__name__}."
        )
    version = payload.get("schema_version")
    if version != schema_version:
        raise HandoffContractError(
            f"{label} {path} has schema version {version!r}; this "
            f"build reads {schema_version!r}. {remedy}"
        )
    file_kind = payload.get("kind")
    if file_kind != kind:
        raise HandoffContractError(
            f"{label} {path} has kind {file_kind!r}; expected "
            f"{kind!r}. {remedy}"
        )
    bundle_id = str(payload.get("bundle_id") or "")
    if not bundle_id:
        raise HandoffContractError(
            f"{label} {path} is missing its bundle_id; "
            f"{missing_id_context}. {remedy}"
        )
    generated_at = payload.get("generated_at")
    if not env.is_timestamp_fresh(generated_at, DISCOVERY_HANDOFF_TTL_SECONDS):
        raise HandoffContractError(
            f"{label} {path} is stale (generated_at="
            f"{generated_at!r}, TTL {int(DISCOVERY_HANDOFF_TTL_SECONDS)}s): "
            f"{stale_context}. {remedy}"
        )
    return payload, bundle_id, generated_at


def _parse_bundle_file(path: Path) -> NominationsBundle:
    payload, bundle_id, generated_at = _parse_handoff_envelope(
        path,
        label="Nominations bundle",
        kind=schema.DISCOVERY_NOMINATIONS_KIND,
        schema_version=schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION,
        remedy=_RESWEEP_REMEDY,
        missing_id_context="judgments cannot bind to it",
        stale_context="the momentum window it captured has moved on",
    )
    version = payload.get("schema_version")

    context = payload.get("context") or {}
    boundary = context.get("enrichment_source_boundary")
    requested = context.get("requested_sources")
    try:
        lookback_days = int(context.get("lookback_days") or 30)
    except (TypeError, ValueError):
        lookback_days = 30

    rows_raw = payload.get("nominations")
    if not isinstance(rows_raw, list):
        raise HandoffContractError(
            f"Nominations bundle {path} must carry a top-level "
            f"\"nominations\" list, got {type(rows_raw).__name__}. "
            f"{_RESWEEP_REMEDY}"
        )

    nominations: list[BundleNomination] = []
    for position, row in enumerate(rows_raw, start=1):
        # Lenient per row: the bundle is engine-written, but one corrupted
        # row must not discard the rest of the pool.
        if not isinstance(row, dict):
            _warn(
                f"skipping malformed nomination row {position} in "
                f"{path.name} (not an object)"
            )
            continue
        try:
            nomination = pipeline.Nomination(
                **schema.nomination_kwargs_from_dict(row.get("nomination") or {})
            )
        except (KeyError, TypeError, ValueError) as exc:
            _warn(
                f"skipping unparseable nomination row {position} in "
                f"{path.name}: {type(exc).__name__}: {exc}"
            )
            continue
        engagement_raw = row.get("engagement_by_source")
        engagement = {
            str(source): dict(metrics)
            for source, metrics in (
                engagement_raw.items() if isinstance(engagement_raw, dict) else ()
            )
            if isinstance(metrics, dict)
        }
        nominations.append(BundleNomination(
            nomination_id=str(row.get("id") or f"n{position}"),
            nomination=nomination,
            cluster_id=str(row.get("cluster_id") or ""),
            heuristic_name=str(row.get("heuristic_name") or ""),
            heuristic_junk=bool(row.get("heuristic_junk")),
            sources=[str(source) for source in row.get("sources") or []],
            engagement_by_source=engagement,
        ))

    if not nominations:
        # Leg 1 never writes an empty bundle (a zero-nomination sweep
        # short-circuits with no bundle file), so an empty or all-invalid
        # nominations array is corrupt state: fail closed, never hand the
        # resume leg a silently empty pool.
        raise HandoffContractError(
            f"Nominations bundle {path} contains no readable nominations "
            f"(leg 1 never writes an empty pool). {_RESWEEP_REMEDY}"
        )

    # Sweep status is advisory coverage context: restore it through the same
    # deserializer every report uses, but degrade a malformed map to empty
    # rather than discarding an otherwise-valid pool.
    try:
        source_status = schema._source_status_from_dict(payload)
    except (AttributeError, KeyError, TypeError, ValueError):
        _warn(f"ignoring malformed source_status map in {path.name}")
        source_status = {}

    return NominationsBundle(
        schema_version=str(version),
        bundle_id=bundle_id,
        generated_at=str(generated_at or ""),
        from_date=str(payload.get("from_date") or ""),
        to_date=str(payload.get("to_date") or ""),
        domain=str(payload.get("domain") or ""),
        tier=str(payload.get("tier") or "deep"),
        enrichment_source_boundary=(
            [str(source) for source in boundary]
            if isinstance(boundary, list) else None
        ),
        requested_sources=(
            [str(source) for source in requested]
            if isinstance(requested, list) else None
        ),
        lookback_days=lookback_days,
        nominations=nominations,
        source_status=source_status,
        mock=bool(payload.get("mock")),
        path=path,
    )


def read_pending_report(
    *,
    save_dir: str | Path | None = None,
    config_dir: Path | None = None,
) -> PendingReport:
    """Locate and parse the leg-2 pending report for the finalize leg.

    Same strictness family as the bundle reader: missing file (the searched
    location named - save dir when supplied, else config dir, never a
    cross-store fallback), unreadable, invalid JSON, wrong kind or schema version,
    missing bundle_id, or stale TTL all raise HandoffContractError (mapped to
    exit 2 by the CLI layer). Staleness is measured from the PENDING report's
    own generated_at - the leg-2 write started a fresh authoring window - and
    the remedy is the resume leg, not a full re-sweep.
    """
    searched = _search_paths(save_dir, config_dir, pending_report_path)
    path = next((candidate for candidate in searched if candidate.exists()), None)
    if path is None:
        raise HandoffContractError(
            "No pending discovery report found. Searched:\n"
            f"{_searched_lines(searched)}\n{_RESUME_REMEDY}"
        )
    return _parse_pending_file(path)


def _parse_pending_file(path: Path) -> PendingReport:
    payload, bundle_id, generated_at = _parse_handoff_envelope(
        path,
        label="Pending discovery report",
        kind=schema.DISCOVERY_PENDING_KIND,
        schema_version=schema.DISCOVERY_PENDING_SCHEMA_VERSION,
        remedy=_RESUME_REMEDY,
        missing_id_context="angles cannot bind to it",
        stale_context="the judged window it captured has moved on",
    )
    version = payload.get("schema_version")
    report = payload.get("report")
    if not isinstance(report, dict):
        raise HandoffContractError(
            f"Pending discovery report {path} must carry a top-level "
            f"\"report\" object. {_RESUME_REMEDY}"
        )
    # Lenient per row (engine-written, but one corrupt row must not discard
    # the rest): keep only well-shaped angle-input entries.
    angle_inputs_raw = payload.get("angle_inputs")
    angle_inputs = {
        str(nomination_id): {
            str(key): str(value) for key, value in info.items()
        }
        for nomination_id, info in (
            angle_inputs_raw.items() if isinstance(angle_inputs_raw, dict) else ()
        )
        if isinstance(info, dict)
    }
    return PendingReport(
        schema_version=str(version),
        bundle_id=bundle_id,
        generated_at=str(generated_at or ""),
        run_ref=str(payload.get("run_ref") or ""),
        report=report,
        angle_inputs=angle_inputs,
        mock=bool(payload.get("mock")),
        path=path,
    )


def _load_host_file(path: str | Path, label: str) -> dict[str, Any]:
    """Load a host-authored handoff file with strict top-level checks."""
    file_path = Path(path).expanduser()
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HandoffContractError(
            f"Could not read {label} file {file_path}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HandoffContractError(
            f"{label.capitalize()} file {file_path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HandoffContractError(
            f"{label.capitalize()} file {file_path} must be a top-level JSON "
            f"object, got {type(payload).__name__}."
        )
    return payload


def _require_bundle_binding(
    payload: dict[str, Any],
    bundle: NominationsBundle | PendingReport,
    *,
    label: str,
    save_dir: str | Path | None,
    config_dir: Path | None,
) -> None:
    """Enforce bundle-id binding between a host file and the current bundle
    (or, on the finalize leg, the pending report that inherited its id).
    The mismatch message names the file actually validated against - the
    pending report on the finalize leg - so a host's retry is not misdirected
    at the nominations bundle. A mismatch means the host echoed the wrong id
    into an otherwise-current file, so the remedy is the cheap one - correct
    the bundle_id field and re-run this same leg - never the expensive
    re-sweep/resume remedies (those belong to missing/stale state)."""
    file_bundle_id = str(payload.get("bundle_id") or "")
    if file_bundle_id == bundle.bundle_id:
        return
    if isinstance(bundle, PendingReport):
        searched = _search_paths(save_dir, config_dir, pending_report_path)
        noun = "current pending discovery report"
        location_label = "Pending-report locations searched"
    else:
        searched = _search_paths(save_dir, config_dir, nominations_bundle_path)
        noun = "current nominations bundle"
        location_label = "Bundle locations searched"
    if not searched and bundle.path is not None:
        searched = [bundle.path]
    raise HandoffContractError(
        f"The {label} file is bound to bundle_id {file_bundle_id!r} but the "
        f"{noun} is {bundle.bundle_id!r}. {location_label}:\n"
        f"{_searched_lines(searched)}\n"
        f"Correct the bundle_id field in your {label} file to "
        f"{bundle.bundle_id!r} and re-run this same leg."
    )


def _truncate_at_word(text: str, max_chars: int) -> str:
    """Cap ``text`` at ``max_chars``, cutting back to a word boundary and
    stripping trailing punctuation. Text within the cap passes through
    untouched."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].rstrip(_TRUNCATE_STRIP_CHARS)


def _sanitized_name(raw: object) -> str | None:
    """One whitespace-collapsed, punctuation-stripped, length-capped topic
    name, or None for anything unusable (non-strings, and names that
    sanitize to empty - e.g. emoji-only - count as per-row-absent)."""
    if not isinstance(raw, str):
        return None
    name = " ".join(raw.split()).strip(_TRUNCATE_STRIP_CHARS)
    name = _truncate_at_word(name, _NAME_MAX_CHARS)
    if not any(char.isalnum() for char in name):
        return None
    return name


def _sanitized_angle(raw: object) -> str | None:
    """One whitespace-collapsed, length-capped angle sentence, or None for
    anything unusable. Non-strings are rejected outright, never coerced."""
    if not isinstance(raw, str):
        return None
    text = _truncate_at_word(" ".join(raw.split()), _ANGLE_MAX_CHARS)
    return text or None


def _known_rows(
    rows: list[Any],
    known: set[str],
    *,
    row_label: str,
    unknown_label: str,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Shared lenient per-row gate for host-authored files: skip non-object
    rows, rows with no nomination id, and rows for unknown ids - warning on
    each - and yield (row_id, row) for the rest."""
    for row in rows:
        if not isinstance(row, dict):
            _warn(f"skipping malformed {row_label} row (not an object)")
            continue
        row_id = str(row.get("id") or "").strip()
        if not row_id:
            _warn(f"skipping {row_label} row with no nomination id")
            continue
        if row_id not in known:
            _warn(f"ignoring {unknown_label} for unknown nomination id {row_id!r}")
            continue
        yield row_id, row


def _clamped_worthiness(raw: object) -> int | None:
    """Worthiness clamped to 0-100 integers; anything non-numeric is absent."""
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return max(0, min(100, round(value)))


def read_judgments(
    path: str | Path,
    bundle: NominationsBundle,
    *,
    save_dir: str | Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, HostJudgment]:
    """Read the host judgments file for leg 2, keyed by nomination id.

    Strict at the top level (readable, valid JSON object, ``judgments`` list,
    bundle_id bound to ``bundle``), lenient per row: an unknown id is warned
    and ignored, a missing/unusable name or junk field is per-row-absent, and
    worthiness is clamped to 0-100 integers. Nominations with no row at all
    are simply missing from the mapping - use ``judgment_for`` to get the
    ROW_ABSENT marker for them.
    """
    payload = _load_host_file(path, "judgments")
    _require_bundle_binding(
        payload, bundle, label="judgments", save_dir=save_dir, config_dir=config_dir,
    )
    rows = payload.get("judgments")
    if not isinstance(rows, list):
        raise HandoffContractError(
            f"Judgments file {path} must carry a top-level \"judgments\" list."
        )
    known = {entry.nomination_id for entry in bundle.nominations}
    judgments: dict[str, HostJudgment] = {}
    for row_id, row in _known_rows(
        rows, known, row_label="judgments", unknown_label="judgment"
    ):
        # Only a real JSON boolean is a junk verdict: null, "false", 0, or
        # any other non-bool value is per-row-absent (bundle heuristic),
        # never coerced - bool("false") is True.
        raw_junk = row.get("junk")
        judgments[row_id] = HostJudgment(
            name=_sanitized_name(row.get("name")),
            junk=raw_junk if isinstance(raw_junk, bool) else None,
            worthiness=_clamped_worthiness(row.get("worthiness")),
        )
    return judgments


def judgment_for(
    judgments: dict[str, HostJudgment],
    nomination_id: str,
) -> HostJudgment:
    """The host's verdict for one nomination, or ROW_ABSENT when the host
    omitted the row (caller falls back to the bundle's heuristic name/junk)."""
    return judgments.get(nomination_id, ROW_ABSENT)


def read_angles(
    path: str | Path | None,
    bundle: NominationsBundle | PendingReport,
    *,
    save_dir: str | Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, HostAngles]:
    """Read the host angles file for leg 3, keyed by nomination id.

    ``bundle`` is the binding target: the finalize leg passes the pending
    report (the bundle_id echo validates against it, and the known ids are
    its surviving ``angle_inputs`` ids), while a NominationsBundle binds
    against the full pool. A missing angles file is legal: ``path=None``
    returns an empty mapping and every topic ships without angles. When a
    path is given the same strict-top-level / lenient-per-row rules as
    judgments apply; angle sentences are word-boundary capped at 200 chars.
    """
    if path is None:
        return {}
    payload = _load_host_file(path, "angles")
    _require_bundle_binding(
        payload, bundle, label="angles", save_dir=save_dir, config_dir=config_dir,
    )
    rows = payload.get("angles")
    if not isinstance(rows, list):
        raise HandoffContractError(
            f"Angles file {path} must carry a top-level \"angles\" list."
        )
    known = (
        set(bundle.angle_inputs)
        if isinstance(bundle, PendingReport)
        else {entry.nomination_id for entry in bundle.nominations}
    )
    angles: dict[str, HostAngles] = {}
    for row_id, row in _known_rows(
        rows, known, row_label="angles", unknown_label="angles"
    ):
        podcast = _sanitized_angle(row.get("podcast"))
        x_article = _sanitized_angle(row.get("x_article"))
        if podcast is None and x_article is None:
            # No usable hook at all: treat the row as absent.
            continue
        angles[row_id] = HostAngles(podcast=podcast, x_article=x_article)
    return angles


def resolve_name_collisions(
    pairs: Sequence[tuple[pipeline.Nomination, str]],
) -> list[str]:
    """Re-run the nominate-stage casefold/entity-token collision rules over
    host-applied names, returning one collision-free name per input pair in
    order.

    Short host-judged names collide far more often than raw titles; a
    colliding name gets the later nomination's strongest non-shared entity
    token appended (``pipeline._disambiguated_topic_name``, fed synthetic
    per-nomination clusters built from the seed items). Unlike the nominate
    stage, a collision can never DROP a nomination here - the pool already
    de-duplicated same-story clusters at leg 1 - so when no distinguishing
    entity token exists the name falls back to an ordinal suffix.
    """
    candidate_map: dict[str, schema.Candidate] = {}
    clusters: list[schema.Cluster] = []
    for index, (nomination, _applied) in enumerate(pairs):
        candidate_ids: list[str] = []
        for item_index, item in enumerate(nomination.items):
            candidate_id = f"handoff-{index}-{item_index}"
            candidate_map[candidate_id] = schema.Candidate(
                candidate_id=candidate_id,
                item_id=item.item_id,
                source=item.source,
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                subquery_labels=[],
                native_ranks={},
                local_relevance=0.0,
                freshness=0,
                engagement=None,
                source_quality=0.0,
                rrf_score=0.0,
            )
            candidate_ids.append(candidate_id)
        clusters.append(schema.Cluster(
            cluster_id=f"handoff-n{index}",
            title=nomination.name,
            candidate_ids=candidate_ids,
            representative_ids=candidate_ids[:1],
            sources=sorted({item.source for item in nomination.items}),
            score=nomination.seed_score,
        ))

    resolved_names: list[str] = []
    taken: dict[str, schema.Cluster] = {}
    entity_counts_cache: dict[str, Counter] = {}
    for index, (_nomination, applied) in enumerate(pairs):
        cluster = clusters[index]
        name = applied
        key = name.casefold()
        if key in taken:
            resolved = pipeline._disambiguated_topic_name(
                name, cluster, taken[key], candidate_map, entity_counts_cache,
                taken,
            )
            if resolved is None:
                # Indistinguishable by content: keep the nomination anyway
                # (distinct stories at leg 1) under an ordinal suffix.
                suffix = 2
                while f"{name} {suffix}".casefold() in taken:
                    suffix += 1
                resolved = f"{name} {suffix}"
            name = resolved
            key = name.casefold()
        taken[key] = cluster
        resolved_names.append(name)
    return resolved_names


def _one_line(text: str) -> str:
    return " ".join(text.split())


def build_host_digest(bundle: NominationsBundle) -> str:
    """The host-facing judging digest for a nominations bundle: plain,
    promptable text with one structural line per nomination (id, seed source
    names, velocity/engagement signal) plus capped evidence lines (leader
    title, leader snippet, strongest community comment - the surface the
    engine judge used to see). Names the bundle file and instructs the host
    to read its full evidence before judging.

    The evidence lines are scraped third-party text, so they are fenced the
    way the deleted engine judge fenced its candidate block (the exact
    ``rerank._fenced_untrusted_content`` fence: a security-notice header
    stating the fenced content is data, never instructions, around
    ``<untrusted_content>`` tags). The structural lines - nomination ids,
    sources, signal, bundle path, judging instructions - stay outside the
    fence."""
    location = str(bundle.path) if bundle.path is not None else (
        NOMINATIONS_BUNDLE_FILENAME
    )
    domain_label = bundle.domain or "global trending (no domain filter)"
    lines = [
        f"Discovery nominations awaiting host judgment "
        f"({len(bundle.nominations)} topics).",
        f"Domain: {domain_label} | window {bundle.from_date} -> "
        f"{bundle.to_date} | tier {bundle.tier}",
        f"Bundle file: {location} (bundle_id {bundle.bundle_id})",
        "Read the bundle file's per-nomination evidence before judging; the "
        "lines below are only a digest.",
        "",
    ]
    evidence_lines: list[str] = []
    for entry in bundle.nominations:
        items = entry.nomination.items
        leader = items[0] if items else None
        title = _one_line((leader.title if leader else "") or entry.nomination.name)
        sources = ", ".join(entry.sources) if entry.sources else "unknown"
        native_total = sum(
            rerank.discovery_engagement_total(item) for item in items
        )
        lines.append(
            f"{entry.nomination_id} | sources: {sources} | "
            f"signal: seed velocity {entry.nomination.seed_score:.1f}, "
            f"{native_total:,.0f} native interactions"
        )
        evidence_lines.append(f"- id: {entry.nomination_id}")
        evidence_lines.append(f"  title: {title[:_DIGEST_TITLE_MAX_CHARS]}")
        snippet_text = _one_line(
            (leader.snippet if leader else "") or entry.nomination.summary
        )
        if snippet_text:
            evidence_lines.append(
                f"  snippet: {snippet_text[:_DIGEST_SNIPPET_MAX_CHARS]}"
            )
        top_comment = pipeline._best_community_comment(items)
        if top_comment:
            evidence_lines.append(
                f"  top comment: "
                f"{_one_line(top_comment)[:_DIGEST_COMMENT_MAX_CHARS]}"
            )
    if evidence_lines:
        lines.append("")
        lines.append(rerank._fenced_untrusted_content("\n".join(evidence_lines)))
    return "\n".join(lines)

"""Backend-chain descriptors with predicted selection (doctor, R4).

Chained sources declare their routing here ONCE — imported from the
definitions ``lib/env.py`` already owns (chain order, pin var names) — and
``resolve()`` turns side-effect-free probes into a truthful prediction of
what the next run will do.

Two resolution modes:

- ``alternative`` (X, YouTube, web search): the pipeline tries genuinely
  interchangeable backends in a declared order. Resolution probes ALL
  candidates first, then picks (collect-then-pick): the first fully-usable
  backend wins the "will use" prediction; otherwise the best degraded
  candidate resolves with a warn tier; otherwise the source is an error
  carrying the highest-priority backend's prescription. Collecting before
  picking prevents an installed-but-unauthenticated preferred backend from
  shadowing a fully working fallback.

- ``conditional`` (Reddit): routing is per-query and outcome-dependent —
  public keyless composite by default, ScrapeCreators backfill only when
  results fall below the configured thinness floor (see the gating in
  ``lib/pipeline.py``). No probe can pick one winner, so resolution renders
  honest conditional wording instead of an ``active_backend``. Reddit's
  internal keyless lanes (rss/listing/arctic/shreddit) are sub-probe detail
  inside the public composite, never chain entries.

``active_backend`` semantics: a PREDICTION — "the first backend the probes
say the next run will try" — rendered as "will use". It is not an
observation of what served a past run, and runtime failover can still
diverge mid-run (a present-but-expired paid key passes a presence probe).

Paid lanes (xai, xquik, serper, and every other API-key backend, including
ScrapeCreators) probe KEY PRESENCE ONLY: a dict lookup, never a network
call or credential spend. Binary-backed lanes reuse the U1 dependency
probe layer (``health.probe_dependency``) so a stale shim reads as BROKEN,
not available (#692).

This module observes and predicts only. It must never alter which backend
the pipeline actually uses; parity with the pipeline's pre-failover
selection is asserted in ``tests/test_backend_descriptors.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from shutil import which
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import env, health, prescriptions

# Resolution modes.
MODE_ALTERNATIVE = "alternative"  # probe-ordered chain, first-usable wins
MODE_CONDITIONAL = "conditional"  # per-query routing; wording, never a winner

# Rollup tiers for a resolved chain (doctor maps these into its R1 table).
TIER_OK = "ok"
TIER_WARN = "warn"
TIER_ERROR = "error"

# Web search backend order. grounding.web_search's auto branch owns the
# runtime behavior (brave -> exa -> serper -> parallel -> keyless floor);
# there is no importable constant there, so this declaration is guarded by
# the grounding-auto parity test rather than an import.
WEB_BACKEND_ORDER: Tuple[str, ...] = ("brave", "exa", "serper", "parallel", "keyless")

# YouTube backend order (pipeline: yt-dlp first, ScrapeCreators search
# fallback when yt-dlp is absent or fails — see lib/pipeline.py).
YOUTUBE_BACKEND_ORDER: Tuple[str, ...] = ("yt-dlp", "scrapecreators")

# Chain-failure fixes embed the registry's CLI forms (KTD 7): the command a
# backend finding prescribes and the one doctor/quality-nudge render for the
# same failure mode come from one entry and cannot drift.
_SC_PRESCRIPTION = (
    "set SCRAPECREATORS_API_KEY (free 10,000-call signup: "
    f"{prescriptions.get('scrapecreators', 'key_missing').fix_cli})"
)
_X_COOKIES_PRESCRIPTION = (
    "run setup with browser-cookie consent: "
    f"{prescriptions.get('x', 'cookies_missing').fix_cli}"
)


@dataclass
class BackendFinding:
    """Side-effect-free probe outcome for one backend of a chained source.

    ``status`` uses the ``lib.health`` vocabulary (OK/DEGRADED/MISSING/
    BROKEN/TIMEOUT/ERROR). ``prescription`` is the fix when non-OK.
    ``requires`` is the backend's requirement note for report rendering.
    """

    name: str
    status: str
    detail: str = ""
    prescription: str = ""
    requires: str = ""

    @property
    def usable(self) -> bool:
        """Fully or partially usable (OK/DEGRADED) — eligible for selection."""
        return self.status in (health.OK, health.DEGRADED)


@dataclass(frozen=True)
class BackendSpec:
    """One backend in a chain: name, probe, requirement note, paid flag.

    ``probe`` must be side-effect-free. When ``paid`` is True the probe is
    key-presence only: no subprocess, no network, no credential spend.
    """

    name: str
    requires: str
    probe: Callable[[Dict[str, Any]], "BackendFinding"]
    paid: bool = False


@dataclass(frozen=True)
class ChainDescriptor:
    """A chained source's declared routing: backends, mode, and pin knob."""

    source: str
    mode: str
    backends: Tuple[BackendSpec, ...]
    pin_var: Optional[str] = None   # env var pin (X, Reddit)
    pin_flag: Optional[str] = None  # CLI flag pin (web: --web-backend)


@dataclass
class BackendResolution:
    """Resolved routing for one chained source.

    ``active_backend`` is the will-use PREDICTION for alternative chains
    and always None for conditional mode (Reddit never gets a computed
    winner — ``conditional`` carries the honest wording instead).
    """

    source: str
    mode: str
    chain: List[str]
    findings: List[BackendFinding]
    active_backend: Optional[str] = None
    tier: str = TIER_OK
    pinned: bool = False
    pin: Optional[str] = None
    prescription: str = ""
    conditional: str = ""

    @property
    def summary(self) -> str:
        """One-line rendering: will-use prediction or conditional wording."""
        if self.mode == MODE_CONDITIONAL:
            return self.conditional
        if self.active_backend is None:
            line = f"no usable backend (chain: {' -> '.join(self.chain)})"
            if self.prescription:
                line += f"; fix: {self.prescription}"
            return line
        line = f"will use: {self.active_backend}"
        if self.pinned:
            line += f" (pinned via {self._pin_origin()})"
        return line

    def _pin_origin(self) -> str:
        d = DESCRIPTORS.get(self.source)
        if d is None:
            return "pin"
        return d.pin_var or d.pin_flag or "pin"


# ---------------------------------------------------------------------------
# Probes. All side-effect-free; paid lanes are pure dict lookups.
# ---------------------------------------------------------------------------

def _key_probe(name: str, key_var: str, requires: str, note: str = "") -> Callable:
    """Key-presence probe for a paid API lane. Never touches the network."""

    def probe(config: Dict[str, Any]) -> BackendFinding:
        if config.get(key_var):
            return BackendFinding(
                name=name,
                status=health.OK,
                detail=f"{key_var} present",
                requires=requires,
            )
        prescription = note or f"set {key_var} in ~/.config/last30days/.env"
        return BackendFinding(
            name=name,
            status=health.MISSING,
            detail=f"{key_var} not set",
            prescription=prescription,
            requires=requires,
        )

    return probe


def _probe_bird(config: Dict[str, Any]) -> BackendFinding:
    """Bird = vendored X GraphQL client (node script) + browser-cookie creds.

    Cookie presence is checked FIRST, mirroring ``env._x_backend_available``'s
    gating (``has_bird_creds and is_bird_installed()``): without cookies bird
    is unconfigured regardless of node/script state, and the fix is the
    cookie-consent flow — a broken node runtime must not turn an unconfigured
    backend into an error carrying a node prescription.
    """
    from . import bird_x

    requires = "X browser cookies (AUTH_TOKEN/CT0) + node"
    if not (config.get("AUTH_TOKEN") and config.get("CT0")):
        return BackendFinding(
            name="bird",
            status=health.MISSING,
            detail="X browser cookies (AUTH_TOKEN/CT0) not configured",
            prescription=_X_COOKIES_PRESCRIPTION,
            requires=requires,
        )
    if not bird_x.is_bird_installed():
        # Distinguish a missing/broken node runtime from a missing script.
        node = health.probe_dependency("node")
        if node.status != health.OK:
            return BackendFinding(
                name="bird",
                status=node.status,
                detail=node.detail,
                prescription=node.prescription,
                requires=requires,
            )
        return BackendFinding(
            name="bird",
            status=health.MISSING,
            detail="vendored bird-search client not found",
            prescription="reinstall the skill (npx skills add . -g -y) to restore lib/vendor/bird-search",
            requires=requires,
        )
    node = health.probe_dependency("node")
    if node.status != health.OK:
        # Resolvable-but-broken node (stale shim) must not read as usable.
        return BackendFinding(
            name="bird",
            status=node.status,
            detail=node.detail,
            prescription=node.prescription,
            requires=requires,
        )
    return BackendFinding(
        name="bird",
        status=health.OK,
        detail="browser-cookie auth (AUTH_TOKEN/CT0) configured",
        requires=requires,
    )


def _probe_xurl(config: Dict[str, Any]) -> BackendFinding:
    """xurl = official X API v2 CLI (OAuth2). Free lane; LOCAL-ONLY probe.

    Doctor's no-network guarantee forbids the live ``xurl whoami`` check
    (``xurl_x.is_available()`` — an authenticated X API call, reserved for
    research time). This probe keys on local evidence instead: the binary
    on PATH plus xurl's on-disk token store (~/.xurl). Stored credentials
    read as OK with an explicit "not live-verified" caveat; an unreadable
    token store is a typed ERROR (broken, not unconfigured).
    """
    from . import xurl_x

    requires = "xurl CLI installed + OAuth2 login"
    if which("xurl") is None:
        return BackendFinding(
            name="xurl",
            status=health.MISSING,
            detail="xurl CLI not found on PATH",
            prescription="npm install -g xurl && xurl auth oauth2 login",
            requires=requires,
        )
    store_status, store_detail = xurl_x.stored_auth_status()
    if store_status == xurl_x.AUTH_OK:
        return BackendFinding(
            name="xurl",
            status=health.OK,
            detail=(
                "installed; stored OAuth2 credentials present; "
                "auth not live-verified (no network)"
            ),
            requires=requires,
        )
    if store_status == xurl_x.AUTH_ERROR:
        return BackendFinding(
            name="xurl",
            status=health.ERROR,
            detail=store_detail,
            prescription="xurl auth oauth2 login",
            requires=requires,
        )
    return BackendFinding(
        name="xurl",
        status=health.MISSING,
        detail="xurl installed but not authenticated",
        prescription="xurl auth oauth2 login",
        requires=requires,
    )


def _probe_ytdlp(config: Dict[str, Any]) -> BackendFinding:
    """yt-dlp via the U1 dependency-probe layer (missing/broken/timeout)."""
    dep = health.probe_dependency("yt-dlp")
    return BackendFinding(
        name="yt-dlp",
        status=dep.status,
        detail=dep.detail,
        prescription=dep.prescription,
        requires="yt-dlp on the agent-subprocess PATH",
    )


def _probe_web_keyless(config: Dict[str, Any]) -> BackendFinding:
    """The keyless web-search floor: works keyless, but degraded quality."""
    requires = "no key; suppressed on native-search hosts"
    if env.keyless_web_allowed(config):
        return BackendFinding(
            name="keyless",
            status=health.DEGRADED,
            detail="keyless search floor (no paid key; lower quality)",
            requires=requires,
        )
    return BackendFinding(
        name="keyless",
        status=health.MISSING,
        detail="keyless floor suppressed: host has native web search",
        prescription="",
        requires=requires,
    )


def _probe_reddit_public(config: Dict[str, Any]) -> BackendFinding:
    """Public keyless Reddit composite; internal lanes are sub-probe detail."""
    return BackendFinding(
        name="public",
        status=health.OK,
        detail="public keyless composite (lanes: rss, listing, arctic, shreddit)",
        requires="none (public endpoints)",
    )


# ---------------------------------------------------------------------------
# Registry: routing declared once, from env.py's definitions where they exist.
# ---------------------------------------------------------------------------

_X_PROBES: Dict[str, Callable[[Dict[str, Any]], BackendFinding]] = {
    "xai": _key_probe("xai", "XAI_API_KEY", "XAI_API_KEY (xAI/Grok live search)"),
    "bird": _probe_bird,
    "xurl": _probe_xurl,
    "xquik": _key_probe("xquik", "XQUIK_API_KEY", "XQUIK_API_KEY (xquik.com)"),
}
_X_PAID = {"xai", "xquik"}

_WEB_PROBES: Dict[str, Callable[[Dict[str, Any]], BackendFinding]] = {
    "brave": _key_probe("brave", "BRAVE_API_KEY", "BRAVE_API_KEY"),
    "exa": _key_probe("exa", "EXA_API_KEY", "EXA_API_KEY"),
    "serper": _key_probe("serper", "SERPER_API_KEY", "SERPER_API_KEY"),
    "parallel": _key_probe("parallel", "PARALLEL_API_KEY", "PARALLEL_API_KEY"),
    "keyless": _probe_web_keyless,
}
_WEB_KEYED = {"brave", "exa", "serper", "parallel"}

_SC_SPEC = BackendSpec(
    name="scrapecreators",
    requires="SCRAPECREATORS_API_KEY",
    probe=_key_probe(
        "scrapecreators", "SCRAPECREATORS_API_KEY", "SCRAPECREATORS_API_KEY",
        note=_SC_PRESCRIPTION,
    ),
    paid=True,
)

DESCRIPTORS: Dict[str, ChainDescriptor] = {
    # X: chain order and pin var imported from env.py (single source of truth).
    "x": ChainDescriptor(
        source="x",
        mode=MODE_ALTERNATIVE,
        backends=tuple(
            BackendSpec(
                name=name,
                requires={
                    "xai": "XAI_API_KEY (xAI/Grok live search)",
                    "bird": "X browser cookies (AUTH_TOKEN/CT0) + node",
                    "xurl": "xurl CLI installed + OAuth2 login",
                    "xquik": "XQUIK_API_KEY (xquik.com)",
                }[name],
                probe=_X_PROBES[name],
                paid=name in _X_PAID,
            )
            for name in env.X_BACKEND_ORDER
        ),
        pin_var=env.X_BACKEND_PIN_VAR,
    ),
    "youtube": ChainDescriptor(
        source="youtube",
        mode=MODE_ALTERNATIVE,
        backends=(
            BackendSpec(
                name="yt-dlp",
                requires="yt-dlp on the agent-subprocess PATH",
                probe=_probe_ytdlp,
            ),
            _SC_SPEC,
        ),
        pin_var=None,  # no YouTube pin knob exists
    ),
    "web": ChainDescriptor(
        source="web",
        mode=MODE_ALTERNATIVE,
        backends=tuple(
            BackendSpec(
                name=name,
                requires=(f"{name.upper()}_API_KEY" if name in _WEB_KEYED
                          else "no key; suppressed on native-search hosts"),
                probe=_WEB_PROBES[name],
                paid=name in _WEB_KEYED,
            )
            for name in WEB_BACKEND_ORDER
        ),
        pin_var=None,  # pinned per-run via --web-backend, not an env var
        pin_flag="--web-backend",
    ),
    "reddit": ChainDescriptor(
        source="reddit",
        mode=MODE_CONDITIONAL,
        backends=(
            BackendSpec(
                name="public",
                requires="none (public endpoints)",
                probe=_probe_reddit_public,
            ),
            _SC_SPEC,
        ),
        pin_var=env.REDDIT_BACKEND_PIN_VAR,
    ),
}


def get_descriptor(source: str) -> ChainDescriptor:
    """Return the declared routing descriptor for ``source`` (KeyError if none)."""
    return DESCRIPTORS[source]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve(
    source: str,
    config: Dict[str, Any],
    pin: Optional[str] = None,
) -> BackendResolution:
    """Resolve a chained source's routing into a truthful prediction.

    ``pin`` is an explicit per-run pin (the ``--web-backend`` flag); it
    takes precedence over the descriptor's env pin var. ``"auto"``/None
    mean unpinned. Probing is side-effect-free and collect-then-pick.

    Time budget: backends are probed sequentially, so a chain's budget is
    ADDITIVE across its backends — each binary-backed probe is bounded by
    ``health.PROBE_TIMEOUT`` and paid/key lanes are dict lookups that cost
    nothing, giving a worst case of roughly (binary probes in the chain) x
    ``health.PROBE_TIMEOUT``. Deliberately no intra-chain concurrency:
    probes are memoized per process and the worst case only occurs when
    multiple binaries are simultaneously hung.
    """
    descriptor = get_descriptor(source)
    findings = [
        _run_probe(spec, config) for spec in descriptor.backends
    ]
    if descriptor.mode == MODE_CONDITIONAL:
        return _resolve_conditional(descriptor, config, findings)
    return _resolve_alternative(descriptor, config, findings, pin)


def _run_probe(spec: BackendSpec, config: Dict[str, Any]) -> BackendFinding:
    """Run one probe, isolating failures so one bad probe can't blank a chain."""
    try:
        finding = spec.probe(config)
    except Exception as exc:  # a probe bug must not take the report down
        finding = BackendFinding(
            name=spec.name,
            status=health.ERROR,
            detail=f"probe failed: {type(exc).__name__}: {exc}",
            requires=spec.requires,
        )
    if not finding.requires:
        finding.requires = spec.requires
    return finding


def _resolve_alternative(
    descriptor: ChainDescriptor,
    config: Dict[str, Any],
    findings: List[BackendFinding],
    pin: Optional[str],
) -> BackendResolution:
    names = [spec.name for spec in descriptor.backends]
    by_name = {f.name: f for f in findings}
    res = BackendResolution(
        source=descriptor.source,
        mode=MODE_ALTERNATIVE,
        chain=list(names),
        findings=findings,
    )

    pin_name: Optional[str] = None
    if pin and pin not in ("auto", "none") and pin in by_name:
        pin_name = pin
    elif descriptor.pin_var:
        raw = (config.get(descriptor.pin_var) or "").lower()
        if raw in by_name:
            pin_name = raw

    if pin_name:
        # A pin forces a single backend (no failover) — mirror
        # env.x_backend_chain's pin semantics exactly.
        res.pinned = True
        res.pin = pin_name
        finding = by_name[pin_name]
        if finding.status == health.OK:
            res.active_backend = pin_name
            res.tier = TIER_OK
        elif finding.status == health.DEGRADED:
            res.active_backend = pin_name
            res.tier = TIER_WARN
        else:
            res.tier = TIER_ERROR
            res.prescription = finding.prescription or (
                f"unpin {descriptor.pin_var or descriptor.pin_flag} or fix {pin_name}"
            )
        return res

    # Collect-then-pick: first fully-usable wins; else best degraded; else
    # error carrying the highest-priority backend's prescription.
    for finding in findings:
        if finding.status == health.OK:
            res.active_backend = finding.name
            res.tier = TIER_OK
            return res
    for finding in findings:
        if finding.status == health.DEGRADED:
            res.active_backend = finding.name
            res.tier = TIER_WARN
            return res
    res.tier = TIER_ERROR
    res.prescription = findings[0].prescription if findings else ""
    return res


def _reddit_sc_min_items(config: Dict[str, Any]) -> int:
    """The thinness floor, parsed exactly as the pipeline parses it
    (lib/pipeline.py reddit fetch: int(... or 0), malformed -> 0)."""
    try:
        return int(config.get(env.REDDIT_SC_MIN_ITEMS_VAR) or 0)
    except (TypeError, ValueError):
        return 0


def _resolve_conditional(
    descriptor: ChainDescriptor,
    config: Dict[str, Any],
    findings: List[BackendFinding],
) -> BackendResolution:
    """Reddit: render the real per-query semantics, never a computed winner."""
    res = BackendResolution(
        source=descriptor.source,
        mode=MODE_CONDITIONAL,
        chain=[spec.name for spec in descriptor.backends],
        findings=findings,
        active_backend=None,  # conditional mode never picks a winner
        tier=TIER_OK,  # the public keyless composite is always reachable
    )
    has_key = bool(config.get("SCRAPECREATORS_API_KEY"))
    raw_pin = (config.get(descriptor.pin_var) or "").lower() if descriptor.pin_var else ""
    pinned_sc = has_key and raw_pin == "scrapecreators"
    floor = _reddit_sc_min_items(config)

    if pinned_sc:
        res.pinned = True
        res.pin = "scrapecreators"
        res.conditional = (
            f"ScrapeCreators primary (pinned via {descriptor.pin_var}); "
            "public keyless composite fallback"
        )
        return res

    if has_key:
        if floor > 0:
            backfill = (
                f"ScrapeCreators backfill when results fall below the "
                f"{floor}-item floor"
            )
        else:
            backfill = "ScrapeCreators backfill when the free path returns nothing"
        res.conditional = f"public keyless composite (default); {backfill}"
        return res

    res.conditional = "public keyless composite (default); no ScrapeCreators key for backfill"
    if raw_pin == "scrapecreators":
        # The pipeline ignores the pin without a key; say so honestly.
        res.conditional += (
            f" ({descriptor.pin_var} pin ignored: SCRAPECREATORS_API_KEY not set)"
        )
    return res

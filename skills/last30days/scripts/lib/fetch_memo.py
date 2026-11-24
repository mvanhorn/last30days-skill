"""Run-scoped memo for source fetches.

Subquery streams for TikTok, Instagram, YouTube, and X all search the raw
topic, and the thin-source retry repeats that search (and, for Polymarket and
Digg, a planner subquery that equals the retry's core subject), so one run can
issue the same request several times. :func:`fetch_once` runs a fetch at most once per
key: the first caller fetches, concurrent callers wait for it (bounded by
their own timeout), and later callers get a copy of the cached result.

Only clean results are cached. A failure (a raised exception, a result the
caller's ``cacheable`` predicate rejects, or HTTP failures an adapter
swallowed into the failure sink) reaches only the callers already waiting on
that fetch; the next call fetches again, so the thin-source retry still makes
a real second attempt after a transient failure.

Reset once per command (``last30days.py``) and between tests (``conftest``).
"""

from __future__ import annotations

from collections import OrderedDict
import copy
import threading
import time
from typing import Any, Callable, Hashable, TypeVar

from . import health, http

T = TypeVar("T")

MAX_ENTRIES = 512


class MemoWaitTimeout(TimeoutError):
    """A waiter's budget expired before the in-flight fetch it shares finished."""

    outcome_state = health.TIMEOUT


class _Slot:
    __slots__ = ("event", "outcome")

    def __init__(self) -> None:
        self.event = threading.Event()
        # (ok, value_or_exception, recorded_failures) once the owner finishes.
        self.outcome: tuple[bool, Any, list] | None = None


_CACHE: OrderedDict[Hashable, Any] = OrderedDict()
_INFLIGHT: dict[Hashable, _Slot] = {}
_LOCK = threading.Lock()


def reset() -> None:
    """Forget cached and in-flight fetches. Called at run/fanout/enrichment boundaries and by tests."""
    with _LOCK:
        _CACHE.clear()
        _INFLIGHT.clear()


def _always(_value: Any) -> bool:
    return True


def _deliver_shared(outcome: tuple[bool, Any, list]) -> Any:
    ok, value, failures = outcome
    # The owner's swallowed HTTP failures reach this caller's sink too, or a
    # shared empty result reads as a clean no-results.
    for failure in failures:
        http._record_failure(failure)
    if not ok:
        raise value
    return copy.deepcopy(value)


def fetch_once(
    key: Hashable,
    fetch: Callable[[], T],
    *,
    timeout: float,
    cacheable: Callable[[T], bool] = _always,
    cancel: threading.Event | None = None,
) -> T:
    """Return ``fetch()``, running it at most once per ``key`` while it stays clean.

    ``key`` must include every input that changes the request. ``timeout``
    bounds how long a caller waits on another caller's in-flight fetch; past
    it, :class:`MemoWaitTimeout` is raised. When ``cancel`` is set, waiting
    callers unblock promptly with :class:`MemoWaitTimeout`. Every caller gets
    its own copy.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if cancel is not None and cancel.is_set():
            raise MemoWaitTimeout("fetch cancelled")
        with _LOCK:
            hit = key in _CACHE
            if hit:
                _CACHE.move_to_end(key)
                cached = _CACHE[key]
            else:
                slot = _INFLIGHT.get(key)
                owner = slot is None
                if owner:
                    slot = _Slot()
                    _INFLIGHT[key] = slot
        if hit:
            return copy.deepcopy(cached)
        if owner:
            break
        while not slot.event.is_set():
            if cancel is not None and cancel.is_set():
                raise MemoWaitTimeout("fetch cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MemoWaitTimeout(
                    f"waited {timeout:g}s for an identical in-flight fetch"
                )
            wait_step = min(0.1, remaining) if cancel is not None else remaining
            if slot.event.wait(wait_step):
                break
        if slot.outcome is not None:
            return _deliver_shared(slot.outcome)
        # The owner was interrupted before publishing (e.g. KeyboardInterrupt):
        # loop and elect a new owner.

    try:
        with http.tee_failures() as recorded:
            try:
                value = fetch()
                ok = True
            except Exception as exc:
                value = exc
                ok = False
        clean = ok and not recorded and cacheable(value)
        # Waiters and the cache copy from this snapshot, never from the
        # owner's own value, which the owner's caller may mutate.
        shared = copy.deepcopy(value) if ok else value
        slot.outcome = (ok, shared, list(recorded))
        with _LOCK:
            # Slot identity guards against a reset() during the fetch.
            if clean and _INFLIGHT.get(key) is slot:
                if MAX_ENTRIES > 0:
                    _CACHE[key] = shared
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > MAX_ENTRIES:
                        _CACHE.popitem(last=False)
    finally:
        with _LOCK:
            if _INFLIGHT.get(key) is slot:
                _INFLIGHT.pop(key)
        slot.event.set()
    if not ok:
        raise value
    return value

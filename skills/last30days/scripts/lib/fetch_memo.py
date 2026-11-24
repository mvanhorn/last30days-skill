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
import contextlib
import contextvars
import copy
import threading
import time
from typing import Any, Callable, Hashable, Iterator, TypeVar

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


class _MemoStore:
    __slots__ = ("cache", "inflight", "lock")

    def __init__(self) -> None:
        self.cache: OrderedDict[Hashable, Any] = OrderedDict()
        self.inflight: dict[Hashable, _Slot] = {}
        self.lock = threading.Lock()


_DEFAULT_STORE = _MemoStore()
_CACHE: OrderedDict[Hashable, Any] = _DEFAULT_STORE.cache
_INFLIGHT: dict[Hashable, _Slot] = _DEFAULT_STORE.inflight
_LOCK = _DEFAULT_STORE.lock

_CURRENT_STORE: contextvars.ContextVar[_MemoStore | None] = contextvars.ContextVar(
    "fetch_memo_store", default=None,
)


def _active_store() -> _MemoStore:
    store = _CURRENT_STORE.get()
    return store if store is not None else _DEFAULT_STORE


@contextlib.contextmanager
def run_scope() -> Iterator[_MemoStore]:
    """Scope fetch memo state to a single top-level run without touching concurrent runs."""
    store = _MemoStore()
    token = _CURRENT_STORE.set(store)
    try:
        yield store
    finally:
        _CURRENT_STORE.reset(token)


def reset() -> None:
    """Forget cached and in-flight fetches in the active store."""
    store = _active_store()
    with store.lock:
        store.cache.clear()
        store.inflight.clear()


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
    store = _active_store()
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if cancel is not None and cancel.is_set():
            raise MemoWaitTimeout("fetch cancelled")
        with store.lock:
            hit = key in store.cache
            if hit:
                store.cache.move_to_end(key)
                cached = store.cache[key]
            else:
                slot = store.inflight.get(key)
                owner = slot is None
                if owner:
                    slot = _Slot()
                    store.inflight[key] = slot
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
        with store.lock:
            # Slot identity guards against a reset() during the fetch.
            if clean and store.inflight.get(key) is slot:
                if MAX_ENTRIES > 0:
                    store.cache[key] = shared
                    store.cache.move_to_end(key)
                    while len(store.cache) > MAX_ENTRIES:
                        store.cache.popitem(last=False)
    finally:
        with store.lock:
            if store.inflight.get(key) is slot:
                store.inflight.pop(key)
        slot.event.set()
    if not ok:
        raise value
    return value

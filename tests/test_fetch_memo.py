"""Run-scoped fetch memo (U3, R5): one network fetch per distinct key per run,
shared by concurrent callers as independent copies. A failure reaches only the
callers already waiting on it; a later call fetches again."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from lib import fetch_memo, health, http


def _fetch_counter(payload):
    calls = []

    def _fetch():
        calls.append(1)
        return payload

    return calls, _fetch


def test_concurrent_same_key_fetches_once_and_returns_independent_copies():
    release = threading.Event()
    arrived = threading.Barrier(4, timeout=5)
    calls = []

    def _slow():
        calls.append(1)
        release.wait(timeout=5)
        return {"items": [{"id": "a", "tags": []}]}

    def _call():
        arrived.wait()
        return fetch_memo.fetch_once(("tiktok", "q"), _slow, timeout=10)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_call) for _ in range(4)]
        # Let every caller reach the memo before the owner finishes.
        threading.Timer(0.2, release.set).start()
        results = [f.result(timeout=10) for f in futures]

    assert len(calls) == 1
    assert all(r == {"items": [{"id": "a", "tags": []}]} for r in results)
    results[0]["items"][0]["tags"].append("mutated")
    assert all(r["items"][0]["tags"] == [] for r in results[1:])


def test_sequential_same_key_serves_an_independent_copy():
    calls, fetch = _fetch_counter({"items": [{"id": "a"}]})
    first = fetch_memo.fetch_once(("youtube", "q"), fetch, timeout=1)
    first["items"].append({"id": "mutated"})
    second = fetch_memo.fetch_once(("youtube", "q"), fetch, timeout=1)
    assert len(calls) == 1
    assert second == {"items": [{"id": "a"}]}


def test_different_keys_fetch_separately():
    calls, fetch = _fetch_counter({"items": []})
    fetch_memo.fetch_once(("youtube", "q", "2026-09-01", "2026-10-01", "default"), fetch, timeout=1)
    fetch_memo.fetch_once(("youtube", "q", "2026-09-02", "2026-10-01", "default"), fetch, timeout=1)
    fetch_memo.fetch_once(("youtube", "q", "2026-09-01", "2026-10-01", "deep"), fetch, timeout=1)
    assert len(calls) == 3


def test_owner_failure_reaches_concurrent_waiters_and_a_later_call_refetches():
    release = threading.Event()
    arrived = threading.Barrier(3, timeout=5)
    calls = []
    boom = RuntimeError("HTTP 503: Service Unavailable")

    def _failing():
        calls.append(1)
        release.wait(timeout=5)
        raise boom

    def _call():
        arrived.wait()
        return fetch_memo.fetch_once(("x", "q"), _failing, timeout=10)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_call) for _ in range(3)]
        threading.Timer(0.2, release.set).start()
        errors = []
        for future in futures:
            with pytest.raises(RuntimeError) as exc_info:
                future.result(timeout=10)
            errors.append(exc_info.value)

    assert len(calls) == 1
    assert all(err is boom for err in errors)

    later_calls, later = _fetch_counter({"items": [{"id": "b"}]})
    assert fetch_memo.fetch_once(("x", "q"), later, timeout=1) == {"items": [{"id": "b"}]}
    assert len(later_calls) == 1


def test_uncacheable_result_reaches_waiters_but_a_later_call_refetches():
    calls, fetch = _fetch_counter({"items": [], "error": "HTTP 429"})
    clean = lambda value: not value.get("error")  # noqa: E731
    fetch_memo.fetch_once(("tiktok", "q"), fetch, timeout=1, cacheable=clean)
    fetch_memo.fetch_once(("tiktok", "q"), fetch, timeout=1, cacheable=clean)
    assert len(calls) == 2


def test_swallowed_failure_reaches_waiters_and_is_not_cached():
    # Source adapters swallow HTTP errors into the stream's failure sink. A
    # waiter must see the owner's failure, and the result must not be cached.
    release = threading.Event()
    arrived = threading.Barrier(2, timeout=5)
    calls = []

    def _swallow_429():
        calls.append(1)
        release.wait(timeout=5)
        http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", status_code=429))
        return {"items": []}

    def _call():
        arrived.wait()
        with http.capture_failures() as sink:
            result = fetch_memo.fetch_once(("instagram", "q"), _swallow_429, timeout=10)
        return result, [f.status_code for f in sink]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_call) for _ in range(2)]
        threading.Timer(0.2, release.set).start()
        results = [f.result(timeout=10) for f in futures]

    assert len(calls) == 1
    assert all(sink == [429] for _result, sink in results)
    fetch_memo.fetch_once(("instagram", "q"), _swallow_429, timeout=1)
    assert len(calls) == 2


def test_waiter_times_out_instead_of_blocking_past_its_budget():
    release = threading.Event()
    owner_started = threading.Event()

    def _hung():
        owner_started.set()
        release.wait(timeout=5)
        return {"items": []}

    owner = threading.Thread(
        target=fetch_memo.fetch_once, args=(("youtube", "q"), _hung), kwargs={"timeout": 10},
        daemon=True,
    )
    owner.start()
    assert owner_started.wait(timeout=5)
    try:
        with pytest.raises(fetch_memo.MemoWaitTimeout) as exc_info:
            fetch_memo.fetch_once(("youtube", "q"), _hung, timeout=0.1)
        assert exc_info.value.outcome_state == health.TIMEOUT
    finally:
        release.set()
        owner.join(timeout=5)


def test_reset_forgets_cached_results():
    calls, fetch = _fetch_counter({"items": []})
    fetch_memo.fetch_once(("youtube", "q"), fetch, timeout=1)
    fetch_memo.reset()
    fetch_memo.fetch_once(("youtube", "q"), fetch, timeout=1)
    assert len(calls) == 2

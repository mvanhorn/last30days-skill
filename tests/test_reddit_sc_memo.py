"""Run-scoped ScrapeCreators Reddit memo (U5, R9): one run never pays for the
same Reddit query twice, across concurrent streams, the thin-source retry, and
the pinned-backend path. A failed call is not retried within the run."""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from lib import env, http, pipeline, reddit, schema

FROM, TO = "2026-05-26", "2026-06-25"


def _memo(query="kanye", depth="default", subreddits=None, token="k"):
    return reddit.search_and_enrich_memo(
        query, FROM, TO, depth=depth, token=token, subreddits=subreddits,
    )


class TestMemo:
    def test_sequential_same_key_calls_once_and_returns_equal_results(self):
        with mock.patch("lib.reddit.search_and_enrich",
                        return_value={"items": [{"id": "a"}]}) as sc:
            first = _memo()
            second = _memo()
        assert sc.call_count == 1
        assert first == second == {"items": [{"id": "a"}]}

    def test_concurrent_same_key_calls_once(self):
        release = threading.Event()
        calls = []

        def _slow(*args, **kwargs):
            calls.append(args)
            release.wait(timeout=5)
            return {"items": [{"id": "a"}]}

        with mock.patch("lib.reddit.search_and_enrich", side_effect=_slow):
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(_memo) for _ in range(4)]
                # Let every caller reach the memo before the owner finishes.
                threading.Timer(0.2, release.set).start()
                results = [f.result(timeout=10) for f in futures]
        assert len(calls) == 1
        assert all(r == {"items": [{"id": "a"}]} for r in results)

    def test_failure_is_cached_and_not_retried(self):
        boom = RuntimeError("HTTP 402: Payment Required")
        with mock.patch("lib.reddit.search_and_enrich", side_effect=boom) as sc:
            with pytest.raises(RuntimeError) as first:
                _memo()
            with pytest.raises(RuntimeError) as second:
                _memo()
        assert sc.call_count == 1
        assert str(first.value) == str(second.value) == "HTTP 402: Payment Required"

    def test_swallowed_failure_is_replayed_to_every_caller(self):
        # search_and_enrich swallows a ScrapeCreators HTTP error into the
        # caller's failure sink and returns no posts. A memo hit must replay
        # that failure, or a later stream reads it as a clean empty result.
        def _swallow_429(*_args, **_kwargs):
            http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", status_code=429))
            return {"items": []}

        with mock.patch("lib.reddit.search_and_enrich", side_effect=_swallow_429) as sc:
            with http.capture_failures() as first_sink:
                first = _memo()
            with http.capture_failures() as second_sink:
                second = _memo()
        assert sc.call_count == 1
        assert first == second == {"items": []}
        assert [f.status_code for f in first_sink] == [429]
        assert [f.status_code for f in second_sink] == [429]

    def test_depth_and_subreddits_are_part_of_the_key(self):
        with mock.patch("lib.reddit.search_and_enrich",
                        return_value={"items": []}) as sc:
            _memo(depth="default")
            _memo(depth="deep")
            _memo(subreddits=["a", "b"])
            _memo(subreddits=["b", "a"])  # same set, different order: same key
            _memo(subreddits=["a"])
        assert sc.call_count == 4

    def test_reset_calls_again(self):
        with mock.patch("lib.reddit.search_and_enrich",
                        return_value={"items": []}) as sc:
            _memo()
            reddit.reset_scrapecreators_memo()
            _memo()
        assert sc.call_count == 2

    def test_callers_get_independent_copies(self):
        with mock.patch("lib.reddit.search_and_enrich",
                        return_value={"items": [{"id": "a"}]}):
            first = _memo()
            first["items"][0]["id"] = "mutated"
            second = _memo()
        assert second["items"][0]["id"] == "a"


def _runtime():
    return schema.ProviderRuntime(reasoning_provider="mock", planner_model="mock",
                                  rerank_model="mock")


def _post(pid):
    return {
        "id": pid,
        "title": f"kanye post {pid}",
        "url": f"https://www.reddit.com/r/x/comments/{pid}/t/",
        "subreddit": "x",
        "date": "2026-06-01",
        "score": 10,
        "num_comments": 1,
        "relevance": 0.9,
    }


def _run_four_streams(config, depth="default"):
    subqueries = [
        schema.SubQuery(label=f"q{i}", search_query=f"kanye angle {i}",
                        ranking_query="kanye", sources=["reddit"])
        for i in range(4)
    ]

    def _one(sq):
        return pipeline._retrieve_stream(
            topic="kanye", subquery=sq, source="reddit", config=config,
            depth=depth, date_range=(FROM, TO), runtime=_runtime(), mock=False,
            raw_topic="kanye",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_one, subqueries))
    return subqueries, results


class TestPipelineIntegration:
    KEY = {"SCRAPECREATORS_API_KEY": "k"}

    def test_four_thin_streams_plus_thin_retry_make_one_sc_call(self):
        # Free lane returns 1 item (below the default floor of 5), so every
        # stream and the thin retry want a backfill. Only the underlying
        # ScrapeCreators search may run, once.
        with mock.patch("lib.reddit_public.search_reddit_public",
                        return_value=[_post("free1")]), \
             mock.patch("lib.reddit.search_reddit",
                        return_value={"items": [_post("sc1")]}) as sc_search, \
             mock.patch("lib.reddit.enrich_with_comments",
                        side_effect=lambda items, *_a, **_k: items):
            subqueries, results = _run_four_streams(self.KEY)
            assert all(items for items, _ in results)

            plan = schema.QueryPlan(
                intent="breaking_news", freshness_mode="balanced_recent",
                cluster_mode="none", raw_topic="kanye", subqueries=subqueries,
                source_weights={"reddit": 1.0},
            )
            bundle = schema.RetrievalBundle()  # reddit thin -> retried
            pipeline._retry_thin_sources(
                topic="kanye", bundle=bundle, plan=plan, config=self.KEY,
                depth="default", date_range=(FROM, TO), runtime=_runtime(),
                mock=False, rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings={"per_stream_limit": 20},
            )
        assert sc_search.call_count == 1

    def test_pinned_path_four_streams_share_one_call(self):
        cfg = {**self.KEY, env.REDDIT_BACKEND_PIN_VAR: "scrapecreators"}
        with mock.patch("lib.reddit_public.search_reddit_public",
                        return_value=[]) as public, \
             mock.patch("lib.reddit.search_reddit",
                        return_value={"items": [_post("sc1")]}) as sc_search, \
             mock.patch("lib.reddit.enrich_with_comments",
                        side_effect=lambda items, *_a, **_k: items):
            _subqueries, results = _run_four_streams(cfg)
        assert sc_search.call_count == 1
        public.assert_not_called()
        assert all(items for items, _ in results)

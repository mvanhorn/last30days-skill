"""Pipeline Reddit dispatch: free-first, SC thinness-floor backfill (U7), and
that the keyless path never calls search.json (U2)."""

from unittest import mock

from pathlib import Path

from lib import env, health, http, pipeline, reddit_keyless, schema


def _subquery():
    return schema.SubQuery(label="t", search_query="kanye", ranking_query="kanye",
                           sources=["reddit"])


def _runtime():
    return schema.ProviderRuntime(reasoning_provider="mock", planner_model="mock",
                                  rerank_model="mock")


def _item(rid):
    return {"url": f"https://www.reddit.com/r/x/comments/{rid}/t/", "title": rid}


def _ids(items):
    return [pipeline._reddit_post_key(i) for i in items]


class TestThinnessFloor:
    KEY = {"SCRAPECREATORS_API_KEY": "k"}
    FLOOR = env.REDDIT_SC_MIN_ITEMS_VAR

    def _run(self, config, public, sc_parsed):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=public), \
             mock.patch("lib.reddit.search_and_enrich", return_value={"raw": 1}) as sc, \
             mock.patch("lib.reddit.parse_reddit_response", return_value=sc_parsed):
            items, _ = _stream(config)
        return items, sc

    def test_unset_floor_backfills_thin_free_run_free_first(self):
        # Default floor is 5: 3 free items < 5 -> one SC call, free first.
        free = [_item("a"), _item("b"), _item("c")]
        items, sc = self._run(self.KEY, free, [_item("b"), _item("z")])
        sc.assert_called_once()
        assert _ids(items) == ["a", "b", "c", "z"]

    def test_unset_floor_six_free_items_spends_nothing(self):
        free = [_item(c) for c in "abcdef"]
        items, sc = self._run(self.KEY, free, [_item("z")])
        sc.assert_not_called()
        assert len(items) == 6

    def test_exactly_default_floor_is_acceptable(self):
        free = [_item(c) for c in "abcde"]
        items, sc = self._run(self.KEY, free, [_item("z")])
        sc.assert_not_called()
        assert len(items) == 5

    def test_empty_string_floor_behaves_as_unset(self):
        cfg = {**self.KEY, self.FLOOR: ""}
        items, sc = self._run(cfg, [_item("a"), _item("b"), _item("c")], [_item("z")])
        sc.assert_called_once()
        assert _ids(items) == ["a", "b", "c", "z"]

    def test_explicit_zero_is_empty_only(self):
        cfg = {**self.KEY, self.FLOOR: "0"}
        items, sc = self._run(cfg, [_item("a"), _item("b"), _item("c")], [_item("z")])
        sc.assert_not_called()
        assert len(items) == 3

    def test_explicit_zero_still_backfills_empty_free_run(self):
        cfg = {**self.KEY, self.FLOOR: "0"}
        items, sc = self._run(cfg, [], [_item("z")])
        sc.assert_called_once()
        assert _ids(items) == ["z"]

    def test_threshold_not_fired_when_free_above_floor(self):
        cfg = {**self.KEY, self.FLOOR: "2"}
        items, sc = self._run(cfg, [_item("a"), _item("b"), _item("c")], [_item("z")])
        sc.assert_not_called()
        assert len(items) == 3

    def test_no_key_never_calls_sc(self):
        for floor in (None, "", "0", "5", "junk"):
            cfg = {} if floor is None else {self.FLOOR: floor}
            items, sc = self._run(cfg, [_item("a")], [_item("z")])
            sc.assert_not_called()
            assert len(items) == 1

    def test_malformed_floor_spends_nothing(self):
        # Malformed means 0 (empty-only): 3 free items -> no SC call.
        cfg = {**self.KEY, self.FLOOR: "not-an-int"}
        items, sc = self._run(cfg, [_item("a"), _item("b"), _item("c")], [_item("z")])
        sc.assert_not_called()
        assert len(items) == 3


def _stream(config):
    return pipeline._retrieve_stream(
        topic="kanye", subquery=_subquery(), source="reddit", config=config,
        depth="quick", date_range=("2026-05-26", "2026-06-25"),
        runtime=_runtime(), mock=False,
    )


def _sc_402(*_args, **_kwargs):
    # Mirrors the real transport: http records the failure in the run's
    # capture sink, then raises.
    http._raise(http.HTTPError("HTTP 402: Payment Required", status_code=402))


class TestBackfillOutcome:
    KEY = {"SCRAPECREATORS_API_KEY": "k"}

    def test_backfill_402_after_free_items_keeps_source_working(self):
        free = [_item("a"), _item("b"), _item("c")]
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=free), \
             mock.patch("lib.reddit.search_and_enrich", side_effect=_sc_402):
            items, artifact = _stream(self.KEY)
        assert _ids(items) == ["a", "b", "c"]
        assert not artifact.get("_source_outcome")  # not branded failed
        detail = artifact.get("_source_outcome_detail") or ""
        assert "402" in detail
        assert "ScrapeCreators backfill failed" in detail
        assert artifact.get("_source_outcome_detail_state") == health.PAYMENT_REQUIRED

    def test_backfill_generic_failure_after_free_items_is_detail(self):
        free = [_item("a")]
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=free), \
             mock.patch("lib.reddit.search_and_enrich", side_effect=Exception("down")):
            items, artifact = _stream(self.KEY)
        assert _ids(items) == ["a"]
        assert not artifact.get("_source_outcome")
        assert "down" in (artifact.get("_source_outcome_detail") or "")

    def test_backfill_failure_with_no_free_items_keeps_explicit_failure(self):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]), \
             mock.patch("lib.reddit.search_and_enrich", side_effect=_sc_402):
            items, artifact = _stream(self.KEY)
        assert items == []
        outcome = artifact.get("_source_outcome") or {}
        assert outcome.get("state") == health.PAYMENT_REQUIRED

    def test_backfill_note_and_swallowed_keyless_403_both_survive(self):
        free = [_item("a"), _item("b"), _item("c")]

        def _public(*_args, **_kwargs):
            # A keyless lane swallowed a 403 but the source still delivered.
            http._record_failure(http.HTTPError("HTTP 403: Blocked", status_code=403))
            return free

        with mock.patch("lib.reddit_public.search_reddit_public", side_effect=_public), \
             mock.patch("lib.reddit.search_and_enrich", return_value={"raw": 1}), \
             mock.patch("lib.reddit.parse_reddit_response", return_value=[_item("z")]):
            items, artifact = _stream(self.KEY)
        assert _ids(items) == ["a", "b", "c", "z"]
        assert not artifact.get("_source_outcome")
        detail = artifact.get("_source_outcome_detail") or ""
        assert "ScrapeCreators backfill ran" in detail
        assert "1 sub-request blocked (HTTP 403)" in detail

    def test_backfill_that_ran_is_noted(self):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[_item("a")]), \
             mock.patch("lib.reddit.search_and_enrich", return_value={"raw": 1}), \
             mock.patch("lib.reddit.parse_reddit_response",
                        return_value=[_item("a"), _item("z")]):
            items, artifact = _stream(self.KEY)
        assert _ids(items) == ["a", "z"]
        assert not artifact.get("_source_outcome")
        detail = artifact.get("_source_outcome_detail") or ""
        assert "ScrapeCreators backfill ran" in detail
        assert "added 1" in detail

    def test_swallowed_sc_429_after_free_items_does_not_rate_limit_reddit(self):
        # ScrapeCreators 429s inside the backfill are swallowed by lib.reddit
        # (recorded in the capture sink, then []). reddit.com was never
        # rate-limited, so the stream must not carry RATE_LIMITED: the fan-out
        # would add 'reddit' to rate_limited_sources and skip later Reddit
        # streams and the thin-source retry.
        free = [_item("a"), _item("b"), _item("c")]

        def _sc_http_429(*_args, **_kwargs):
            http._raise(http.HTTPError("HTTP 429: Too Many Requests", status_code=429))

        with mock.patch("lib.reddit_public.search_reddit_public", return_value=free), \
             mock.patch("lib.http.get", side_effect=_sc_http_429) as sc_get:
            items, artifact = _stream(self.KEY)
        assert sc_get.called
        assert _ids(items) == ["a", "b", "c"]
        assert not artifact.get("_source_outcome")
        assert artifact.get("_source_outcome_detail_state") != health.RATE_LIMITED
        detail = artifact.get("_source_outcome_detail") or ""
        assert "ScrapeCreators backfill" in detail
        assert "HTTP 429" in detail

    def test_raised_sc_429_after_free_items_does_not_rate_limit_reddit(self):
        free = [_item("a"), _item("b"), _item("c")]

        def _sc_raise_429(*_args, **_kwargs):
            http._raise(http.HTTPError("HTTP 429: Too Many Requests", status_code=429))

        with mock.patch("lib.reddit_public.search_reddit_public", return_value=free), \
             mock.patch("lib.reddit.search_and_enrich", side_effect=_sc_raise_429):
            items, artifact = _stream(self.KEY)
        assert _ids(items) == ["a", "b", "c"]
        assert not artifact.get("_source_outcome")
        assert artifact.get("_source_outcome_detail_state") != health.RATE_LIMITED
        detail = artifact.get("_source_outcome_detail") or ""
        assert "ScrapeCreators backfill failed" in detail
        assert "429" in detail

    def test_sc_429_with_no_free_items_keeps_explicit_rate_limited_outcome(self):
        def _sc_raise_429(*_args, **_kwargs):
            http._raise(http.HTTPError("HTTP 429: Too Many Requests", status_code=429))

        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]), \
             mock.patch("lib.reddit.search_and_enrich", side_effect=_sc_raise_429):
            items, artifact = _stream(self.KEY)
        assert items == []
        outcome = artifact.get("_source_outcome") or {}
        assert outcome.get("state") == health.RATE_LIMITED

    def test_no_backfill_no_note(self):
        free = [_item(c) for c in "abcdef"]
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=free), \
             mock.patch("lib.reddit.search_and_enrich") as sc:
            _items, artifact = _stream(self.KEY)
        sc.assert_not_called()
        assert not artifact.get("_source_outcome_detail")


class TestMergeHelper:
    def test_dedup_by_post_id_free_first(self):
        out = pipeline._merge_reddit_items([_item("a"), _item("b")], [_item("b"), _item("c")])
        assert _ids(out) == ["a", "b", "c"]


class TestNoSearchJson:
    def test_keyless_discovery_never_calls_searchjson(self):
        # reddit_public.search (the .json caller) must never run in the keyless flow.
        with mock.patch("lib.reddit_public.search") as json_search, \
             mock.patch("lib.reddit_keyless.reddit_search.search", return_value=[]), \
             mock.patch("lib.reddit_keyless.reddit_listing.fetch_listings", return_value=[]):
            reddit_keyless._discover("topic", "default", ["test"])
        json_search.assert_not_called()


class TestEnvConstantParity:
    """F2 regression (restate-as-mirror drift): pipeline's Reddit gating must
    key off env's declared constants (env.REDDIT_BACKEND_PIN_VAR /
    env.REDDIT_SC_MIN_ITEMS_VAR) — never restated raw strings that can drift
    from the single source of truth in lib/env.py."""

    def _run(self, config, public, sc_parsed):
        with mock.patch("lib.reddit_public.search_reddit_public",
                        return_value=public) as pub, \
             mock.patch("lib.reddit.search_and_enrich", return_value={"raw": 1}) as sc, \
             mock.patch("lib.reddit.parse_reddit_response", return_value=sc_parsed):
            items, _ = pipeline._retrieve_stream(
                topic="kanye", subquery=_subquery(), source="reddit", config=config,
                depth="quick", date_range=("2026-05-26", "2026-06-25"),
                runtime=_runtime(), mock=False,
            )
        return items, pub, sc

    def test_pipeline_source_has_no_raw_reddit_env_literals(self):
        # The declared constants live in env.py; pipeline.py must not restate
        # the raw LAST30DAYS_REDDIT_* strings (comments included — they drift too).
        source = Path(pipeline.__file__).read_text()
        assert "LAST30DAYS_REDDIT_" not in source

    def test_backend_pin_constant_flips_gating_to_sc_primary(self):
        # Keyed via the env constant, not a raw string: pin=scrapecreators
        # makes SC primary and skips the free path entirely.
        cfg = {"SCRAPECREATORS_API_KEY": "k", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators"}
        items, pub, sc = self._run(cfg, [_item("a")], [_item("z")])
        sc.assert_called_once()
        pub.assert_not_called()
        assert _ids(items) == ["z"]

    def test_min_items_constant_drives_thinness_backfill(self):
        # Keyed via the env constant: floor of 5 vs 1 free item -> SC backfill.
        cfg = {"SCRAPECREATORS_API_KEY": "k", env.REDDIT_SC_MIN_ITEMS_VAR: "5"}
        items, pub, sc = self._run(cfg, [_item("a")], [_item("z")])
        sc.assert_called_once()
        assert _ids(items) == ["a", "z"]

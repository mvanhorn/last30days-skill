"""Pipeline Reddit dispatch: free-first, SC thinness-floor backfill (U7), and
that the keyless path never calls search.json (U2)."""

from unittest import mock

from lib import pipeline, reddit_keyless, schema


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

    def _run(self, config, public, sc_parsed):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=public), \
             mock.patch("lib.reddit.search_and_enrich", return_value={"raw": 1}) as sc, \
             mock.patch("lib.reddit.parse_reddit_response", return_value=sc_parsed):
            items, _ = pipeline._retrieve_stream(
                topic="kanye", subquery=_subquery(), source="reddit", config=config,
                depth="quick", date_range=("2026-05-26", "2026-06-25"),
                runtime=_runtime(), mock=False,
            )
        return items, sc

    def test_default_zero_does_not_call_sc_when_free_has_items(self):
        # min_items unset (0): today's empty-only behavior — free wins, no SC.
        items, sc = self._run(self.KEY, [_item("a"), _item("b"), _item("c")], [_item("z")])
        assert len(items) == 3
        sc.assert_not_called()

    def test_default_empty_free_falls_to_sc(self):
        items, sc = self._run(self.KEY, [], [_item("z")])
        assert _ids(items) == ["z"]
        sc.assert_called_once()

    def test_threshold_fires_on_thin_run_and_merges_deduped(self):
        cfg = {**self.KEY, "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "5"}
        free = [_item("a"), _item("b")]        # 2 < 5 -> backfill
        sc_parsed = [_item("b"), _item("c")]   # overlaps "b"
        items, sc = self._run(cfg, free, sc_parsed)
        sc.assert_called_once()
        assert _ids(items) == ["a", "b", "c"]  # free first, dedup b, append c

    def test_threshold_not_fired_when_free_above_floor(self):
        cfg = {**self.KEY, "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "2"}
        items, sc = self._run(cfg, [_item("a"), _item("b"), _item("c")], [_item("z")])
        sc.assert_not_called()
        assert len(items) == 3

    def test_exactly_floor_is_acceptable_no_backfill(self):
        # MIN_ITEMS=N means N results are acceptable; only fewer than N backfills.
        cfg = {**self.KEY, "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "3"}
        items, sc = self._run(cfg, [_item("a"), _item("b"), _item("c")], [_item("z")])
        sc.assert_not_called()
        assert len(items) == 3

    def test_no_key_never_calls_sc(self):
        items, sc = self._run({"LAST30DAYS_REDDIT_SC_MIN_ITEMS": "5"}, [_item("a")], [_item("z")])
        sc.assert_not_called()
        assert len(items) == 1

    def test_bad_threshold_value_defaults_to_empty_only(self):
        cfg = {**self.KEY, "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "not-an-int"}
        items, sc = self._run(cfg, [_item("a")], [_item("z")])
        sc.assert_not_called()  # falls back to 0 -> free (1 item) wins
        assert len(items) == 1

    def test_sc_failure_degrades_to_free(self):
        cfg = {**self.KEY, "LAST30DAYS_REDDIT_SC_MIN_ITEMS": "5"}
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[_item("a")]), \
             mock.patch("lib.reddit.search_and_enrich", side_effect=Exception("down")):
            items, _ = pipeline._retrieve_stream(
                topic="kanye", subquery=_subquery(), source="reddit", config=cfg,
                depth="quick", date_range=("2026-05-26", "2026-06-25"),
                runtime=_runtime(), mock=False,
            )
        assert _ids(items) == ["a"]  # backup failed -> keep the free items


class TestMergeHelper:
    def test_dedup_by_post_id_free_first(self):
        out = pipeline._merge_reddit_items([_item("a"), _item("b")], [_item("b"), _item("c")])
        assert _ids(out) == ["a", "b", "c"]


class TestNoSearchJson:
    def test_keyless_discovery_never_calls_searchjson(self):
        # reddit_public.search (the .json caller) must never run in the keyless flow.
        with mock.patch("lib.reddit_public.search") as json_search, \
             mock.patch("lib.reddit_keyless.reddit_rss.search_rss", return_value=[]), \
             mock.patch("lib.reddit_keyless.reddit_listing.fetch_listings", return_value=[]):
            reddit_keyless._discover("topic", "default", ["test"])
        json_search.assert_not_called()

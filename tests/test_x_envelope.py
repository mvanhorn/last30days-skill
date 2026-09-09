"""Host-fetched X envelope (``--x-posts``): the U5 contract.

The envelope is a file of flat post rows the hosting model fetched through its
X connector. It is validated strictly at the top level (fail closed, exit 2)
and leniently per row (drop and count), every citation is rebuilt from a
numeric id and a grammar-valid handle, and the envelope replaces the engine's
X fetch for exactly one serve.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import stat
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import dates, env, health, html_render, pipeline, render, schema, x_api, x_envelope

TOPIC = "ai agents"
FROM, TO = dates.get_date_range(30)
SUBJECT = "steipete"
RELATED = "peer1"
ACCOUNT_ID = "acct-987654321-SECRET"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _day(offset: int) -> str:
    return (date.fromisoformat(TO) - timedelta(days=offset)).isoformat()


def _snowflake(day: str, *, hour: int = 12, minute: int = 0, low: int = 0) -> str:
    when = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    when += timedelta(hours=hour, minutes=minute)
    ms = int(when.timestamp() * 1000)
    return str(((ms - x_api._SNOWFLAKE_EPOCH_MS) << 22) | (low & ((1 << 22) - 1)))


# Day offsets with irregular gaps so a normal fixture never reads as generated.
_OFFSETS = (1, 2, 4, 9, 11, 18)


def _row(
    n: int,
    handle: str = SUBJECT,
    text: str = "ai agents are shipping fast",
    *,
    created_at: str | None | object = "auto",
    likes: int = 10,
    **extra,
) -> dict:
    day = _day(_OFFSETS[n % len(_OFFSETS)])
    pid = _snowflake(day, hour=8 + n, minute=(n * 17) % 60, low=(n + 1) * 104729)
    row = {
        "id": pid,
        "author_handle": handle,
        "created_at": f"{day}T{8 + n:02d}:{(n * 17) % 60:02d}:00Z" if created_at == "auto" else created_at,
        "text": text,
        "likes": likes,
        "reposts": 1,
        "replies": 0,
        "quotes": 0,
    }
    if created_at is None:
        row.pop("created_at")
    row.update(extra)
    return row


def _call(lane: str = "topic", handles=(), posts=()) -> dict:
    return {"lane": lane, "handles": list(handles), "posts": list(posts)}


def _envelope(calls, **over) -> dict:
    payload = {
        "schema": x_envelope.SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": TOPIC,
        "window": {"from": FROM, "to": TO},
        "provider": "x-connector",
        "status": "ok",
        "calls": list(calls),
    }
    payload.update(over)
    return payload


def _write(tmp_path: Path, payload, name: str = "posts.json") -> str:
    path = tmp_path / name
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return str(path)


def _read(path: str, *, topic: str = TOPIC, handles=(SUBJECT,), related=(), window=(FROM, TO)):
    return x_envelope.read(path, window, topic, handles=list(handles), related=list(related))


def _basic(tmp_path: Path, **over) -> str:
    """Topic call with two rows plus a from call with one subject row."""
    return _write(tmp_path, _envelope([
        _call("topic", posts=[_row(0, "alice", "ai agents review"), _row(1, "bob", "agents everywhere")]),
        _call("from", handles=[SUBJECT], posts=[_row(2, SUBJECT, "shipping my agent today")]),
    ], **over))


def _plan(sources=("x",)) -> dict:
    return {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [{
            "label": "primary",
            "search_query": TOPIC,
            "ranking_query": f"What are people saying about {TOPIC}?",
            "sources": list(sources),
        }],
        "source_weights": {s: 1.0 for s in sources},
    }


def _two_x_plan() -> dict:
    plan = _plan()
    plan["subqueries"].append({
        "label": "angle",
        "search_query": "agent frameworks",
        "ranking_query": "Which agent frameworks are people using?",
        "sources": ["x"],
    })
    return plan


@contextlib.contextmanager
def _no_backend():
    with mock.patch("lib.env.x_backend_chain", return_value=[]), \
         mock.patch(
             "lib.pipeline._fetch_x_backend",
             side_effect=AssertionError("no X backend may be called on an envelope run"),
         ) as fetch:
        yield fetch


def _run(envelope, *, config=None, x_handle=None, x_related=None, depth="default",
         plan=None, requested=("x",), topic=TOPIC, **kwargs):
    with _no_backend():
        return pipeline.run(
            topic=topic, config=dict(config or {}), depth=depth,
            requested_sources=list(requested) if requested is not None else None,
            mock=False, x_handle=x_handle, x_related=x_related,
            external_plan=plan or _plan(), x_posts=envelope,
            web_backend="none", save_dir="", **kwargs,
        )


def _capture(fn):
    err = io.StringIO()
    with redirect_stderr(err):
        result = fn()
    return result, err.getvalue()


# ---------------------------------------------------------------------------
# Ingestion: happy path and row trust (R9, R10, AE5, AE6, AE6a)
# ---------------------------------------------------------------------------


class TestIngestion:
    def test_valid_envelope_serves_topic_and_from_lanes_without_a_backend(self, tmp_path):
        envelope, stderr = _capture(lambda: _read(_basic(tmp_path)))
        assert envelope.accepted == 3 and envelope.total == 3
        assert envelope.status == "ok"
        assert [c.lane for c in envelope.lane_calls] == ["from"]
        assert len(envelope.topic_items) == 2
        assert "host-fetched X: accepted 3 of 3" in stderr
        assert "lanes: topic 2, from 1, mention 0, related 0" in stderr

        with _no_backend() as fetch:
            report = pipeline.run(
                topic=TOPIC, config={}, depth="default", requested_sources=["x"],
                mock=False, x_handle=SUBJECT, external_plan=_plan(), x_posts=envelope,
                web_backend="none", save_dir="",
            )
        fetch.assert_not_called()
        urls = {item.url for item in report.items_by_source["x"]}
        assert len(urls) == 3
        assert all(u.startswith("https://x.com/") for u in urls)
        by_author = {item.author: item for item in report.items_by_source["x"]}
        assert SUBJECT in by_author, "from-lane row must land under x"
        assert report.source_status["x"].state == health.OK
        assert "x" not in report.errors_by_source

    def test_from_row_gets_first_party_handling(self, tmp_path):
        # A subject post that never repeats the topic must survive the
        # relevance floor: that is what first-party handling means.
        path = _write(tmp_path, _envelope([
            _call("topic", posts=[_row(0, "alice", "ai agents review")]),
            _call("from", handles=[SUBJECT], posts=[_row(2, SUBJECT, "lunch was great")]),
        ]))
        envelope = _read(path)
        report = _run(envelope, x_handle=SUBJECT)
        authors = {item.author for item in report.items_by_source["x"]}
        assert SUBJECT in authors

    def test_created_at_disagreeing_with_snowflake_is_date_mismatch(self, tmp_path):
        # created_at inside the window; the snowflake decodes two months earlier.
        early = (date.fromisoformat(FROM) - timedelta(days=60)).isoformat()
        forged = _row(0, "alice")
        forged["id"] = _snowflake(early, low=99)
        path = _write(tmp_path, _envelope([_call("topic", posts=[forged, _row(1, "bob")])]))
        envelope = _read(path)
        assert envelope.counters["date-mismatch"] == 1
        assert envelope.accepted == 1

    def test_off_domain_url_is_ignored_and_citation_rebuilt(self, tmp_path):
        row = _row(0, "alice", url="https://evil.example/alice/status/1")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.accepted == 1
        item = envelope.topic_items[0]
        assert item["url"] == f"https://x.com/i/status/{row['id']}"
        assert "evil.example" not in json.dumps(envelope.topic_items)

    def test_spoofed_subject_handle_with_off_domain_url_is_not_attributed(self, tmp_path):
        """AE6a: the row is kept, its citation is rebuilt from the id, the
        subject attribution is dropped, and handle-mismatch is counted."""
        row = _row(0, SUBJECT, "totally the subject", url="https://evil.example/x")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])), handles=[SUBJECT])
        assert envelope.counters["handle-mismatch"] == 1
        item = envelope.topic_items[0]
        assert item["author_handle"] == ""
        assert item["url"] == f"https://x.com/i/status/{row['id']}"

    def test_url_handle_or_id_disagreement_is_handle_mismatch(self, tmp_path):
        row = _row(0, SUBJECT)
        row["url"] = f"https://x.com/someoneelse/status/{row['id']}"
        other = _row(1, "bob")
        other["url"] = "https://x.com/bob/status/1234567890"
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row, other])])))
        assert envelope.counters["handle-mismatch"] == 2
        assert all(item["author_handle"] == "" for item in envelope.topic_items)

    def test_future_snowflake_is_dropped(self, tmp_path):
        future = (date.today() + timedelta(days=40)).isoformat()
        row = _row(0, "alice", created_at=None)
        row["id"] = _snowflake(future)
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row, _row(1, "bob")])])))
        assert envelope.accepted == 1
        assert envelope.counters["out-of-window"] == 1

    def test_uniform_id_sequence_rejects_the_whole_envelope(self, tmp_path):
        base = int(_snowflake(_day(10)))
        step = 1 << 30
        rows = []
        for i in range(5):
            row = _row(i, "alice", f"post {i}", created_at=None)
            row["id"] = str(base + i * step)
            rows.append(row)
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=rows)])))
        assert envelope.status == "error"
        assert envelope.accepted == 0 and envelope.topic_items == []
        report = _run(envelope)
        assert report.source_status["x"].state == health.ERROR
        assert "generated" in (report.source_status["x"].detail or "")

    def test_duplicate_ids_across_calls_first_occurrence_wins(self, tmp_path):
        first = _row(0, "alice", "first copy")
        second = dict(first, text="second copy", author_handle="bob")
        envelope = _read(_write(tmp_path, _envelope([
            _call("topic", posts=[first]),
            _call("topic", posts=[second]),
        ])))
        assert envelope.counters["duplicate"] == 1
        assert envelope.accepted == 1
        assert envelope.topic_items[0]["text"] == "first copy"

    def test_missing_created_at_derives_date_from_snowflake(self, tmp_path):
        row = _row(0, "alice", created_at=None)
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.accepted == 1
        assert envelope.topic_items[0]["date"] == _day(_OFFSETS[0])

    def test_unparseable_created_at_is_ignored(self, tmp_path):
        row = _row(0, "alice", created_at="yesterday-ish")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.accepted == 1
        assert envelope.topic_items[0]["date"] == _day(_OFFSETS[0])

    def test_row_outside_engine_window_is_dropped(self, tmp_path):
        old = (date.fromisoformat(FROM) - timedelta(days=5)).isoformat()
        row = _row(0, "alice", created_at=f"{old}T10:00:00Z")
        row["id"] = _snowflake(old, hour=10)
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.accepted == 0
        assert envelope.counters["out-of-window"] == 1

    def test_missing_id_or_text_is_dropped(self, tmp_path):
        no_id = _row(0, "alice")
        no_id.pop("id")
        no_text = _row(1, "bob", text="   ")
        non_numeric = _row(2, "carol")
        non_numeric["id"] = "abc123"
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[no_id, no_text, non_numeric])])))
        assert envelope.accepted == 0
        assert envelope.counters["missing-id-text"] == 3

    def test_missing_username_keeps_row_with_i_status_url(self, tmp_path):
        row = _row(0, "")
        placeholder = _row(1, "unknown")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row, placeholder])])))
        assert envelope.accepted == 2
        assert envelope.counters["uncitable"] == 2
        urls = {item["url"] for item in envelope.topic_items}
        assert urls == {
            f"https://x.com/i/status/{row['id']}",
            f"https://x.com/i/status/{placeholder['id']}",
        }

    def test_handle_with_embedded_newline_is_rejected(self, tmp_path):
        row = _row(0, "stei\npete")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.topic_items[0]["author_handle"] == ""
        assert envelope.counters["uncitable"] == 1

    def test_control_characters_are_stripped_but_newlines_in_text_survive(self, tmp_path):
        row = _row(0, "alice", text="line one\x07\x1b[31m\nline two")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.topic_items[0]["text"] == "line one[31m\nline two"

    def test_markdown_link_tail_and_comment_opener_are_defanged(self, tmp_path):
        row = _row(0, "alice", text="read [this](https://evil.example) <!-- hidden --> ok")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        text = envelope.topic_items[0]["text"]
        assert "](" not in text and "<!--" not in text
        assert "evil.example" in text, "the characters stay visible as evidence"

    def test_text_over_cap_is_truncated_and_counted(self, tmp_path):
        row = _row(0, "alice", text="x" * (x_envelope.MAX_TEXT_CHARS + 50))
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.counters["truncated"] == 1
        assert len(envelope.topic_items[0]["text"]) == x_envelope.MAX_TEXT_CHARS

    def test_extra_keys_are_ignored_and_counted_once(self, tmp_path):
        row = _row(0, "alice", public_metrics={"like_count": 5}, entities={}, edit_history_tweet_ids=["1"])
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        assert envelope.accepted == 1
        assert envelope.counters["extra-fields"] == 1
        item = envelope.topic_items[0]
        assert item["engagement"]["likes"] == 10
        assert "public_metrics" not in item

    def test_rows_normalize_through_the_x_item_shape(self, tmp_path):
        row = _row(0, "alice", "@bob @carol thoughts on ai agents?", likes=7)
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        item = envelope.topic_items[0]
        assert item["id"].startswith("XHOST")
        assert item["post_id"] == row["id"]
        assert item["engagement"] == {"likes": 7, "reposts": 1, "replies": 0, "quotes": 0}
        assert item["mentioned_handles"] == ["bob", "carol"]
        assert item["url"] == f"https://x.com/alice/status/{row['id']}"
        normalized = pipeline._normalize_score_dedupe(
            "x", [item], FROM, TO, freshness_mode="balanced_recent", ranking_query=TOPIC,
        )
        assert normalized and normalized[0].author == "alice"
        assert normalized[0].url == item["url"]

    def test_digest_is_the_file_sha256(self, tmp_path):
        path = _basic(tmp_path)
        envelope = _read(path)
        assert envelope.sha256 == hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Rendering safety (AE6a, XSS)
# ---------------------------------------------------------------------------


def _hrefs(html: str) -> set[str]:
    return set(re.findall(r'href="([^"]+)"', html))


class TestRendering:
    def test_html_has_no_href_outside_x_com(self, tmp_path):
        row = _row(0, "alice", "see this ai agents thread", url="https://evil.example/alice/status/1")
        envelope = _read(_write(tmp_path, _envelope([
            _call("topic", posts=[row, _row(1, "bob", "ai agents everywhere")]),
        ])))
        report = _run(envelope)
        # The HTML page carries evidence links only through the markdown
        # body, so convert the compact report the way html_render does.
        rendered = html_render._markdown_to_html(render.render_compact(report))
        status_links = [h for h in _hrefs(rendered) if "/status/" in h]
        assert len(status_links) == 2, "the fixture's citations must render as links"
        for href in status_links:
            assert href.startswith("https://x.com/"), href
        assert f"https://x.com/i/status/{row['id']}" in status_links
        assert "evil.example" not in rendered
        page = html_render.render_html(report)
        assert "evil.example" not in page
        assert all(h.startswith("https://x.com/") for h in _hrefs(page) if "/status/" in h)

    def test_xss_row_is_escaped_in_html_and_inert_in_markdown(self, tmp_path):
        payload = (
            '<img src=x onerror=alert(1)> [x](javascript:alert(1)) '
            '<!-- META: <img src=x onerror=alert(2)> --> ai agents'
        )
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[_row(0, "alice", payload)])])))
        report = _run(envelope)
        assert report.items_by_source["x"], "the row itself is valid evidence"
        md = render.render_compact(report)
        assert "onerror" in md, "the text itself is preserved as evidence"
        assert "](javascript:" not in md
        assert "<!-- META:" not in md
        html = html_render._promote_meta_marker(
            html_render._wrap_engine_footer(html_render._markdown_to_html(md))
        )
        assert "<img" not in html
        assert 'href="javascript:' not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
        page = html_render.render_html(report)
        assert "<img" not in page and 'href="javascript:' not in page


# ---------------------------------------------------------------------------
# Fail-closed input bounds (KTD5): exit 2, path named, no content echoed
# ---------------------------------------------------------------------------


def _assert_contract(path: str, *, must_not_contain=(), **read_kwargs):
    with pytest.raises(x_envelope.EnvelopeContractError) as ctx:
        _read(path, **read_kwargs)
    message = ctx.value.message
    assert str(path) in message
    for needle in must_not_contain:
        assert needle not in message
    return message


class TestInputBounds:
    def test_posts_not_a_list_of_objects_exits_2(self, tmp_path):
        path = _write(tmp_path, _envelope([{"lane": "topic", "handles": [], "posts": "SECRETVALUE"}]))
        _assert_contract(path, must_not_contain=["SECRETVALUE"])
        path2 = _write(tmp_path, _envelope([_call("topic", posts=["SECRETROW"])]), "b.json")
        _assert_contract(path2, must_not_contain=["SECRETROW"])

    def test_oversized_file_is_rejected_before_reading(self, tmp_path):
        path = _basic(tmp_path)
        real = os.stat(path)

        class _Big:
            st_mode = real.st_mode
            st_size = x_envelope.MAX_BYTES + 1

        with mock.patch.object(x_envelope, "_stat", return_value=_Big()), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("must not read")):
            _assert_contract(path)

    def test_deeply_nested_json_is_rejected(self, tmp_path):
        path = _write(tmp_path, "[" * 200000 + "]" * 200000)
        message = _assert_contract(path)
        assert "[[[" not in message

    def test_fifo_is_rejected_without_opening(self, tmp_path):
        fifo = tmp_path / "pipe.json"
        os.mkfifo(fifo)
        with mock.patch("builtins.open", side_effect=AssertionError("must not open a FIFO")):
            _assert_contract(str(fifo))

    def test_non_utf8_is_rejected_without_echo(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_bytes(b'{"schema": "x", "secret": "SECRETVALUE"' + b"\xff\xfe" + b"}")
        _assert_contract(str(path), must_not_contain=["SECRETVALUE"])

    def test_env_shaped_input_is_rejected(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("X_BEARER_TOKEN=SECRETVALUE\n", encoding="utf-8")
        _assert_contract(str(path), must_not_contain=["SECRETVALUE"])
        as_json = tmp_path / "config.json"
        as_json.write_text('{"X_BEARER_TOKEN": "SECRETVALUE"}', encoding="utf-8")
        _assert_contract(str(as_json), must_not_contain=["SECRETVALUE"])

    def test_config_dir_and_credential_stores_are_rejected(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        cfg = tmp_path / "cfg"
        for directory in (home / ".grok", home / ".xurl", cfg):
            directory.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(env, "CONFIG_DIR", cfg)
        monkeypatch.setattr(env, "CONFIG_FILE", cfg / ".env")
        for target in (home / ".grok" / "auth.json", home / ".xurl" / "tokens.json", cfg / "posts.json"):
            target.write_text(json.dumps({"token": "SECRETVALUE"}), encoding="utf-8")
            _assert_contract(str(target), must_not_contain=["SECRETVALUE"])
        # A symlink into a store is resolved before the check.
        link = tmp_path / "link.json"
        link.symlink_to(home / ".grok" / "auth.json")
        _assert_contract(str(link), must_not_contain=["SECRETVALUE"])

    def test_wrong_suffix_directory_and_missing_file_are_rejected(self, tmp_path):
        _assert_contract(_write(tmp_path, _envelope([]), "posts.txt"))
        _assert_contract(str(tmp_path))
        _assert_contract(str(tmp_path / "missing.json"))

    def test_call_and_row_caps_are_enforced(self, tmp_path):
        too_many_calls = _envelope([_call("topic", posts=[_row(0)]) for _ in range(x_envelope.MAX_CALLS + 1)])
        _assert_contract(_write(tmp_path, too_many_calls, "calls.json"))
        big_call = _envelope([_call("topic", posts=[_row(i % 6) for i in range(x_envelope.MAX_ROWS_PER_CALL + 1)])])
        _assert_contract(_write(tmp_path, big_call, "rows.json"))
        total = _envelope([
            _call("topic", posts=[_row(i % 6) for i in range(x_envelope.MAX_ROWS_PER_CALL)])
            for _ in range(3)
        ])
        _assert_contract(_write(tmp_path, total, "total.json"))


class TestMalformedEnvelope:
    @pytest.mark.parametrize("mutation", [
        {"schema": None},
        {"schema": "last30days-x-posts/2"},
        {"status": "great"},
        {"calls": {"lane": "topic"}},
        {"calls": []},
        {"generated_at": None},
        {"window": None},
        {"topic": None},
    ])
    def test_malformed_exits_2_with_two_fix_remedy(self, tmp_path, mutation):
        payload = _envelope([_call("topic", posts=[_row(0)])])
        for key, value in mutation.items():
            if value is None:
                payload.pop(key)
            else:
                payload[key] = value
        path = _write(tmp_path, payload)
        message = _assert_contract(path, must_not_contain=["great", "last30days-x-posts/2"])
        assert "rewrite" in message.lower()
        assert "--x-posts" in message

    def test_not_json_and_not_an_object_exit_2(self, tmp_path):
        _assert_contract(_write(tmp_path, "{not json SECRETVALUE", "a.json"), must_not_contain=["SECRETVALUE"])
        _assert_contract(_write(tmp_path, '["SECRETVALUE"]', "b.json"), must_not_contain=["SECRETVALUE"])

    def test_stale_generated_at_exits_2(self, tmp_path):
        stale = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        message = _assert_contract(_write(tmp_path, _envelope([_call("topic", posts=[_row(0)])], generated_at=stale)))
        assert stale not in message
        fresh = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        assert _read(_write(tmp_path, _envelope([_call("topic", posts=[_row(0)])], generated_at=fresh), "ok.json")).accepted == 1

    def test_mismatched_topic_exits_2(self, tmp_path):
        message = _assert_contract(
            _write(tmp_path, _envelope([_call("topic", posts=[_row(0)])], topic="rust async SECRETTOPIC")),
            must_not_contain=["SECRETTOPIC"],
        )
        assert "topic" in message
        # Normalization: case and whitespace do not count as a mismatch.
        assert _read(_write(tmp_path, _envelope([_call("topic", posts=[_row(0)])], topic="  AI   Agents "), "ok.json")).accepted == 1

    def test_window_entirely_before_engine_window_exits_2(self, tmp_path):
        early_from = (date.fromisoformat(FROM) - timedelta(days=40)).isoformat()
        early_to = (date.fromisoformat(FROM) - timedelta(days=10)).isoformat()
        payload = _envelope([_call("topic", posts=[_row(0)])], window={"from": early_from, "to": early_to})
        _assert_contract(_write(tmp_path, payload))

    def test_narrower_window_is_a_receipt_warning_not_a_failure(self, tmp_path):
        narrow_from = (date.fromisoformat(FROM) + timedelta(days=10)).isoformat()
        payload = _envelope([_call("topic", posts=[_row(0)])], window={"from": narrow_from, "to": TO})
        envelope, stderr = _capture(lambda: _read(_write(tmp_path, payload)))
        assert envelope.accepted == 1
        assert envelope.warnings and "window" in envelope.warnings[0]
        assert "window" in stderr
        report = _run(envelope)
        assert any("window" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Envelope status classes (AE6b, R11)
# ---------------------------------------------------------------------------


class TestStatusOutcomes:
    def test_error_with_account_identifiers_is_payment_required_with_fixed_detail(self, tmp_path):
        payload = _envelope([], status="error", error=f"credits exhausted for account {ACCOUNT_ID}")
        envelope, stderr = _capture(lambda: _read(_write(tmp_path, payload)))
        assert ACCOUNT_ID not in stderr
        report, run_stderr = _capture(lambda: _run(envelope))
        outcome = report.source_status["x"]
        assert outcome.state == schema.PAYMENT_REQUIRED
        assert outcome.detail == x_envelope.DETAIL_CREDITS
        assert ACCOUNT_ID not in run_stderr
        for rendered in (
            render.render_compact(report),
            html_render.render_html(report),
            json.dumps(schema.to_dict(report)),
            json.dumps(schema.to_agent_export(report)),
            render.render_context(report),
            render.render_brief(report),
        ):
            assert ACCOUNT_ID not in rendered

    @pytest.mark.parametrize("category,state,detail", [
        ("not-connected", health.ERROR, x_envelope.DETAIL_NOT_CONNECTED),
        ("unavailable", health.ERROR, x_envelope.DETAIL_UNAVAILABLE),
        ("something-weird", health.ERROR, x_envelope.DETAIL_ERROR),
        ("credits", schema.PAYMENT_REQUIRED, x_envelope.DETAIL_CREDITS),
    ])
    def test_error_categories_map_to_fixed_details(self, tmp_path, category, state, detail):
        envelope = _read(_write(tmp_path, _envelope([], status="error", error=category)))
        report = _run(envelope)
        assert report.source_status["x"].state == state
        assert report.source_status["x"].detail == detail

    def test_raw_error_text_reaches_stderr_only_under_debug(self, tmp_path, monkeypatch):
        payload = _envelope([], status="error", error=f"unavailable {ACCOUNT_ID}")
        monkeypatch.delenv("LAST30DAYS_DEBUG", raising=False)
        _, quiet = _capture(lambda: _read(_write(tmp_path, payload)))
        assert ACCOUNT_ID not in quiet
        monkeypatch.setenv("LAST30DAYS_DEBUG", "1")
        _, loud = _capture(lambda: _read(_write(tmp_path, payload, "b.json")))
        assert ACCOUNT_ID in loud

    def test_ok_with_zero_rows_is_no_results_and_omission_note_does_not_fire(self, tmp_path):
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[])])))
        report = _run(envelope)
        assert report.source_status["x"].state == schema.NO_RESULTS
        assert "x" not in report.errors_by_source
        diag = pipeline.diagnose({}, None, safe=True, x_envelope=True)
        assert "x" in diag["available_sources"]
        assert cli._optional_x_omission_text(diag, None) is None

    def test_partial_with_window_unsupported_names_the_unwindowed_call(self, tmp_path):
        payload = _envelope([
            _call("topic", posts=[_row(0, "alice", "ai agents review from alice")]),
            _call("from", handles=[SUBJECT], posts=[_row(2, SUBJECT, "shipping my agent today")]),
        ], status="partial", error="window-unsupported")
        envelope, stderr = _capture(lambda: _read(_write(tmp_path, payload)))
        assert "window-unsupported" in stderr
        report = _run(envelope, x_handle=SUBJECT)
        outcome = report.source_status["x"]
        assert outcome.state == schema.PARTIAL
        assert "window-unsupported" in (outcome.detail or "")
        assert "topic" in outcome.detail and "from" in outcome.detail
        assert len(report.items_by_source["x"]) == 2


# ---------------------------------------------------------------------------
# Lane metadata (R11)
# ---------------------------------------------------------------------------


class TestLanes:
    def test_from_row_by_foreign_author_is_lane_mismatch(self, tmp_path):
        envelope = _read(_write(tmp_path, _envelope([
            _call("from", handles=[SUBJECT], posts=[_row(0, "impostor", "hi"), _row(2, SUBJECT, "mine")]),
        ])))
        assert envelope.counters["lane-mismatch"] == 1
        assert envelope.lane_counts["from"] == 1

    def test_related_handle_absent_from_x_related_is_served_as_topic(self, tmp_path):
        envelope, stderr = _capture(lambda: _read(
            _write(tmp_path, _envelope([_call("related", handles=["stranger"], posts=[_row(0, "stranger", "x")])])),
            handles=[SUBJECT], related=[RELATED],
        ))
        assert envelope.lane_counts["related"] == 0
        assert envelope.lane_counts["topic"] == 1
        assert envelope.counters["lane-mismatch"] == 1
        assert "topic" in stderr and "stranger" not in stderr

    def test_handle_outside_grammar_demotes_the_call(self, tmp_path):
        envelope = _read(
            _write(tmp_path, _envelope([_call("from", handles=["bad handle!"], posts=[_row(0, SUBJECT, "x")])])),
            handles=[SUBJECT],
        )
        assert envelope.lane_counts["topic"] == 1
        assert not envelope.lane_calls

    def test_mention_lane_drops_the_subjects_own_post(self, tmp_path):
        envelope = _read(_write(tmp_path, _envelope([
            _call("mention", handles=[SUBJECT], posts=[
                _row(0, SUBJECT, f"@{SUBJECT} talking to myself"),
                _row(1, "fan", f"@{SUBJECT} love the work"),
            ]),
        ])))
        assert envelope.lane_counts["mention"] == 1
        assert envelope.counters["lane-mismatch"] == 1

    def test_related_rows_get_the_related_weight_and_first_party(self, tmp_path):
        envelope = _read(_write(tmp_path, _envelope([
            _call("topic", posts=[_row(0, "alice", "ai agents review")]),
            _call("related", handles=[RELATED], posts=[_row(3, RELATED, "peer news")]),
        ])), handles=[SUBJECT], related=[RELATED])
        report = _run(envelope, x_handle=SUBJECT, x_related=[RELATED])
        related_sq = [sq for sq in report.query_plan.subqueries if sq.label == "supplemental-related"]
        assert related_sq and related_sq[0].weight == 0.3
        assert RELATED in {item.author for item in report.items_by_source["x"]}

    def test_from_lane_respects_per_handle_count(self, tmp_path):
        subjects = (
            "shipping the agent runtime", "benchmarks for tool calling", "memory layer rewrite",
            "why evals matter", "latency budget notes", "open sourcing the planner",
            "hiring for infra", "conference talk recap", "pricing update", "roadmap thread",
        )
        rows = [_row(i, SUBJECT, subjects[i]) for i in range(pipeline.FROM_LANE_COUNT_PER + 2)]
        # Vary ids beyond the offset cycle so the sequence never reads as generated.
        for i, row in enumerate(rows):
            row["id"] = _snowflake(_day(1 + (i * 3) % 25), hour=i % 24, minute=(i * 13) % 60, low=i * 7919)
            row["created_at"] = None
            row.pop("created_at")
        envelope = _read(_write(tmp_path, _envelope([_call("from", handles=[SUBJECT], posts=rows)])))
        assert envelope.accepted == len(rows)
        report = _run(envelope, x_handle=SUBJECT)
        assert len(report.items_by_source["x"]) == pipeline.FROM_LANE_COUNT_PER

    def test_extracted_handle_promotion_is_skipped(self, tmp_path):
        envelope = _read(_basic(tmp_path))
        with mock.patch("lib.x_judge.promotable_handles", side_effect=AssertionError("no promotion")):
            report = _run(envelope, x_handle=SUBJECT)
        assert report.items_by_source["x"]


# ---------------------------------------------------------------------------
# Single-serve and pipeline wiring (KTD5, KTD11, R12)
# ---------------------------------------------------------------------------


class TestPipelineWiring:
    def test_second_planner_x_subquery_and_thin_retry_get_nothing(self, tmp_path):
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[_row(0, "alice", "ai agents review")])])))
        calls: list[str] = []
        original = pipeline._retrieve_stream_impl

        def spy(*args, **kwargs):
            if kwargs.get("source") == "x":
                calls.append(kwargs["subquery"].label)
            return original(*args, **kwargs)

        with mock.patch("lib.pipeline._retrieve_stream_impl", side_effect=spy):
            report = _run(envelope, plan=_two_x_plan(), config={"_max_source_fetches": 5})
        assert "primary" in calls and "angle" in calls and "retry" in calls
        assert len(report.items_by_source["x"]) == 1
        assert "x" not in report.errors_by_source
        assert report.source_status["x"].state == health.OK

    def test_available_sources_lists_x_for_envelope_without_backend(self):
        with mock.patch("lib.env.x_backend_chain", return_value=[]), \
             mock.patch("lib.env.x_pending_browser_auth", return_value=False):
            assert "x" in pipeline.available_sources({}, None, x_envelope=True)
            assert "x" not in pipeline.available_sources({}, None)

    def test_envelope_without_lane_signal_and_no_backend_lands_under_x(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LAST30DAYS_X_HOST_LANE", raising=False)
        envelope = _read(_basic(tmp_path))
        report = _run(envelope, x_handle=SUBJECT, requested=None)
        assert len(report.items_by_source["x"]) == 3
        assert "x" in report.query_plan.source_weights or report.items_by_source["x"]

    def test_lane_signal_without_envelope_records_not_passed(self):
        config = {"LAST30DAYS_X_HOST_LANE": "1"}
        with mock.patch("lib.env.x_backend_chain", return_value=[]), \
             mock.patch("lib.env.x_pending_browser_auth", return_value=False):
            assert "x" in pipeline.available_sources(config, None)
        with _no_backend() as fetch:
            report = pipeline.run(
                topic=TOPIC, config=config, depth="default", requested_sources=["x"],
                mock=False, external_plan=_plan(), web_backend="none", save_dir="",
            )
        fetch.assert_not_called()
        assert report.source_status["x"].state == health.ERROR
        assert report.source_status["x"].detail == x_envelope.DETAIL_NOT_PASSED

    def test_lane_signal_with_bearer_still_records_not_passed(self):
        config = {"LAST30DAYS_X_HOST_LANE": "1", "X_BEARER_TOKEN": "dummy-bearer"}
        with mock.patch("lib.env.x_backend_chain", return_value=["xapi"]), \
             mock.patch("lib.pipeline._fetch_x_backend", side_effect=AssertionError("must not fetch")), \
             mock.patch("lib.x_api.search_handles", side_effect=AssertionError("no lanes")):
            report = pipeline.run(
                topic=TOPIC, config=config, depth="default", requested_sources=["x"],
                mock=False, x_handle=SUBJECT, external_plan=_plan(), web_backend="none", save_dir="",
            )
        assert report.source_status["x"].state == health.ERROR
        assert report.source_status["x"].detail == x_envelope.DETAIL_NOT_PASSED

    def test_discovery_enrichment_pass_with_signal_records_nothing_for_x(self):
        config = {"LAST30DAYS_X_HOST_LANE": "1"}
        with mock.patch("lib.env.x_backend_chain", return_value=[]), \
             mock.patch("lib.env.x_pending_browser_auth", return_value=False):
            assert "x" not in pipeline.available_sources(config, None, suppress_x_host_lane=True)
        with _no_backend(), mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})):
            report = pipeline.run(
                topic=TOPIC, config=config, depth="default", requested_sources=None,
                mock=False, external_plan=_plan(("reddit", "x")), web_backend="none", save_dir="",
                internal_subrun=True, suppress_x_host_lane=True,
            )
        assert "x" not in report.errors_by_source
        assert "x" not in report.source_status

    def test_comparison_entity_pass_with_envelope_still_serves_x(self, tmp_path):
        envelope = _read(_basic(tmp_path, topic="acme"), topic="acme")
        report = _run(envelope, topic="acme", x_handle=SUBJECT, internal_subrun=True,
                      config={"LAST30DAYS_X_HOST_LANE": "1"})
        assert len(report.items_by_source["x"]) == 3

    def test_exclude_sources_x_ignores_the_envelope_with_a_receipt(self, tmp_path):
        envelope = _read(_basic(tmp_path))
        report, stderr = _capture(lambda: _run(
            envelope, config={"EXCLUDE_SOURCES": "x"}, requested=None, plan=_plan(("reddit",)),
        ))
        assert "x" not in report.items_by_source
        assert "envelope ignored" in stderr
        assert envelope.topic_items, "an ignored envelope is not consumed"

    def test_search_list_without_x_ignores_the_envelope(self, tmp_path):
        envelope = _read(_basic(tmp_path))
        with mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})):
            report, stderr = _capture(lambda: _run(envelope, requested=("reddit",), plan=_plan(("reddit",))))
        assert "x" not in report.items_by_source
        assert "envelope ignored" in stderr

    def test_mixed_fixture_fuses_duplicate_url_into_one_candidate(self, tmp_path):
        row = _row(0, "alice", "ai agents review")
        envelope = _read(_write(tmp_path, _envelope([_call("topic", posts=[row])])))
        url = f"https://x.com/alice/status/{row['id']}"
        # Another source (HN) hands the engine the same post URL: fusion keys
        # on the normalized URL, so the two copies become one candidate.
        original = pipeline._retrieve_stream

        def stream(*args, **kwargs):
            if kwargs.get("source") == "hackernews":
                return [{
                    "id": "hn1", "title": "ai agents review", "url": url,
                    "points": 40, "num_comments": 3, "date": _day(_OFFSETS[0]),
                    "author": "hnuser", "text": "ai agents review thread",
                }], {}
            return original(*args, **kwargs)

        with mock.patch("lib.pipeline._retrieve_stream", side_effect=stream):
            report = _run(envelope, plan=_plan(("x", "hackernews")), requested=("x", "hackernews"))
        assert report.items_by_source["x"] and report.items_by_source["hackernews"]
        candidates = [c for c in report.ranked_candidates if c.url == url]
        assert len(candidates) == 1

    def test_footer_provenance_reads_via_x_connector(self, tmp_path):
        envelope = _read(_basic(tmp_path))
        report = _run(envelope, x_handle=SUBJECT)
        md = render.render_compact(report)
        assert "via X connector" in md
        assert "3 items" in md

    def test_env_file_lane_line_without_process_env_leaves_x_absent(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LAST30DAYS_X_HOST_LANE", raising=False)
        config_file = tmp_path / ".env"
        config_file.write_text("LAST30DAYS_X_HOST_LANE=1\n", encoding="utf-8")
        config_file.chmod(0o600)
        monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(env, "CONFIG_FILE", config_file)
        for key in ("LAST30DAYS_HOST", "X_BEARER_TOKEN", "LAST30DAYS_X_BACKEND", "XAI_API_KEY",
                    "AUTH_TOKEN", "CT0", "XQUIK_API_KEY", "FROM_BROWSER", "AGENTCOOKIE", "BROWSER_CDP_URL"):
            monkeypatch.delenv(key, raising=False)
        with mock.patch.object(env, "_load_keychain", return_value={}), \
             mock.patch.object(env, "_load_pass", return_value={}), \
             mock.patch.object(env, "_find_project_env", return_value=None):
            config = env.get_config()
        with mock.patch("lib.env.x_backend_chain", return_value=[]), \
             mock.patch("lib.env.x_pending_browser_auth", return_value=False):
            assert "x" not in pipeline.available_sources(config, None)

    def test_diagnose_top_keys_unchanged(self):
        from tests.test_diagnose_compat import DIAGNOSE_TOP_KEYS
        with mock.patch("lib.env.x_backend_chain", return_value=[]):
            payload = pipeline.diagnose({}, None, safe=True, x_envelope=True)
        assert set(payload.keys()) == DIAGNOSE_TOP_KEYS
        assert "x" in payload["available_sources"]


# ---------------------------------------------------------------------------
# CLI (R9, KTD5)
# ---------------------------------------------------------------------------


_DIAG = {
    "available_sources": ["reddit", "x"],
    "x_pending_browser_auth": False,
    "native_search": False,
    "bird_installed": False,
    "bird_authenticated": False,
    "bird_username": None,
    "native_web_backend": None,
    "safe": False,
}


def _cli(argv, tmp_path, *, config=None, run=None, environ=None, real_run=False):
    out, err = io.StringIO(), io.StringIO()
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    stack = contextlib.ExitStack()
    with stack:
        stack.enter_context(mock.patch.object(cli.env, "get_config", return_value=dict(config or {})))
        stack.enter_context(mock.patch.object(cli.env, "CONFIG_DIR", cfg_dir))
        stack.enter_context(mock.patch.object(cli.env, "CONFIG_FILE", cfg_dir / ".env"))
        stack.enter_context(mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()))
        stack.enter_context(mock.patch.dict(
            os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1", **(environ or {})}, clear=False,
        ))
        stack.enter_context(mock.patch.object(sys, "argv", ["last30days.py", *argv]))
        if real_run:
            stack.enter_context(_no_backend())
        else:
            stack.enter_context(mock.patch.object(cli.pipeline, "diagnose", return_value=dict(_DIAG)))
            stack.enter_context(mock.patch.object(
                cli.pipeline, "run", side_effect=run or AssertionError("pipeline.run must not run"),
            ))
        stack.enter_context(redirect_stdout(out))
        stack.enter_context(redirect_stderr(err))
        rc = cli.main()
    return rc, out.getvalue(), err.getvalue()


def _fake_run_capturing(store: dict):
    def fake_run(**kwargs):
        store.update(kwargs)
        return _fake_report(kwargs["topic"])
    return fake_run


def _fake_report(topic: str) -> schema.Report:
    return schema.Report(
        topic=topic, range_from=FROM, range_to=TO, generated_at="2026-09-08T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(reasoning_provider="local", planner_model="d", rerank_model="l"),
        query_plan=schema.QueryPlan(intent="general", freshness_mode="balanced_recent", cluster_mode="story",
                                    raw_topic=topic, subqueries=[], source_weights={}),
        clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
    )


class TestCli:
    def test_flag_is_documented_in_argparse_help(self):
        flags = {opt for action in cli.build_parser()._actions for opt in action.option_strings}
        assert "--x-posts" in flags

    def test_inline_json_exits_2(self, tmp_path):
        rc, _, err = _cli([TOPIC, "--x-posts", '{"schema": "SECRETVALUE"}'], tmp_path)
        assert rc == 2
        assert "path" in err and "SECRETVALUE" not in err

    def test_contract_error_exits_2_naming_the_path(self, tmp_path):
        path = _write(tmp_path, _envelope([_call("topic", posts=[_row(0)])], status="great"))
        rc, _, err = _cli([TOPIC, "--x-posts", path], tmp_path)
        assert rc == 2
        assert path in err and "great" not in err and "--x-posts" in err

    def test_hosted_mode_with_flag_exits_2(self, tmp_path):
        path = _basic(tmp_path)
        with mock.patch.object(cli.env, "read_secret_env", return_value="hosted-test-key"), \
             mock.patch("lib.hosted.run_hosted", side_effect=AssertionError("hosted must not run")):
            rc, _, err = _cli(
                [TOPIC, "--x-posts", path], tmp_path,
                environ={"LAST30DAYS_API_BASE": "https://hosted.example.test"},
            )
        assert rc == 2
        assert "--x-posts" in err

    def test_valid_flag_threads_the_envelope_into_run(self, tmp_path):
        path = _basic(tmp_path)
        store: dict = {}
        rc, _, _ = _cli([TOPIC, "--x-posts", path, "--x-handle", SUBJECT], tmp_path, run=_fake_run_capturing(store))
        assert rc == 0
        envelope = store["x_posts"]
        assert isinstance(envelope, x_envelope.Envelope)
        assert envelope.accepted == 3

    def test_bare_flag_on_comparison_run_exits_2_naming_the_field(self, tmp_path):
        path = _basic(tmp_path)
        rc, _, err = _cli(["acme vs globex", "--x-posts", path], tmp_path)
        assert rc == 2
        assert "x_posts" in err and "--competitors-plan" in err

    def test_per_entity_x_posts_in_competitors_plan(self, tmp_path):
        acme = _write(tmp_path, _envelope([_call("topic", posts=[_row(0, "a", "acme news")])], topic="acme"), "acme.json")
        globex = _write(tmp_path, _envelope([_call("topic", posts=[_row(1, "b", "globex news")])], topic="globex"), "globex.json")
        plan = json.dumps({"acme": {"x_posts": acme}, "globex": {"x_posts": globex}})
        seen: dict[str, dict] = {}

        def fake_run(**kwargs):
            seen[kwargs["topic"]] = kwargs
            return _fake_report(kwargs["topic"])

        with mock.patch.object(cli, "emit_comparison_output", return_value="# rendered"):
            rc, _, err = _cli(["acme vs globex", "--competitors-plan", plan], tmp_path, run=fake_run)
        assert rc == 0, err
        assert seen["acme"]["x_posts"].sha256 == hashlib.sha256(Path(acme).read_bytes()).hexdigest()
        assert seen["globex"]["x_posts"].sha256 == hashlib.sha256(Path(globex).read_bytes()).hexdigest()
        parsed = cli.parse_competitors_plan(plan)
        assert parsed["acme"]["x_posts"] == acme

    def test_comparison_pass_rejects_the_other_entitys_envelope(self, tmp_path):
        acme = _write(tmp_path, _envelope([_call("topic", posts=[_row(0, "a", "acme news")])], topic="acme"), "acme.json")
        plan = json.dumps({"globex": {"x_posts": acme}})
        rc, _, err = _cli(["acme vs globex", "--competitors-plan", plan], tmp_path)
        assert rc == 2
        assert acme in err and "topic" in err

    def test_last_report_cache_misses_on_digest_mismatch(self, tmp_path):
        path = _basic(tmp_path)
        envelope = _read(path)
        report = _fake_report(TOPIC)
        with mock.patch.object(cli.env, "CONFIG_DIR", tmp_path / "cfg"):
            (tmp_path / "cfg").mkdir(exist_ok=True)
            assert cli._write_last_run(TOPIC, report, x_envelope_sha256=envelope.sha256)
            payload = json.loads((tmp_path / "cfg" / "last-report.json").read_text())
            assert payload["x_envelope_sha256"] == envelope.sha256
            assert cli._load_last_report_cache(TOPIC, x_envelope_sha256=envelope.sha256) is not None
            assert cli._load_last_report_cache(TOPIC, x_envelope_sha256="0" * 64) is None
            assert cli._load_last_report_cache(TOPIC) is None
            assert cli._write_last_run(TOPIC, report)
            assert cli._load_last_report_cache(TOPIC) is not None
            assert cli._load_last_report_cache(TOPIC, x_envelope_sha256=envelope.sha256) is None

    def test_cli_run_end_to_end_emits_x_citations_only(self, tmp_path):
        path = _basic(tmp_path)
        argv = [TOPIC, "--x-posts", path, "--x-handle", SUBJECT, "--search", "x", "--web-backend", "none"]
        rc, out, err = _cli([*argv, "--emit", "md"], tmp_path, real_run=True)
        assert rc == 0, err
        assert "host-fetched X: accepted 3 of 3" in err
        assert "via X connector" in out
        assert "3 items" in out
        citations = re.findall(r"https?://[^\s)\]]+/status/\d+", out)
        assert len(citations) >= 3 and all(c.startswith("https://x.com/") for c in citations)
        assert "Optional source omitted" not in out + err
        rc, html, err = _cli([*argv, "--emit", "html"], tmp_path, real_run=True)
        assert rc == 0, err
        assert all(h.startswith("https://x.com/") for h in _hrefs(html) if "/status/" in h)
        assert "Optional source omitted" not in html + err

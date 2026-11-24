"""Security regression tests for PR01 (HIGH-049 through HIGH-054).

Covers:
- HIGH-049: Safe URL validation for feed_url / entry_urls in render_library_index
- HIGH-050: Single-pass HTML template substitution (no chained placeholder expansion)
- HIGH-051: HTML-context-safe META: marker and engine-footer token handling
- HIGH-052: Strict HTTPS endpoint validation and no-redirect transport in html_publish
- HIGH-053: Symlink/FIFO rejection and TOCTOU-free fchmod in prepare_private_sqlite_files
- HIGH-054: Atomic symlink/FIFO-safe writes and BaseException FD cleanup in write_private_text
"""

from __future__ import annotations

import errno
import http.client
import io
import json
import os
import stat
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import html_publish, html_render, private_files, schema
from lib.library import LibraryEntry


def _sample_entry(
    *,
    topic: str = "AI agents",
    headline: str = "Agent loops are durable",
    source_name: str = "ai-agents.md",
) -> LibraryEntry:
    return LibraryEntry(
        slug="ai-agents",
        topic=topic,
        published_date=date(2026, 7, 10),
        headline=headline,
        summary="Summary text.",
        source_path=Path(f"/tmp/{source_name}"),
        content="# Brief\n\nBody text.",
        source_updated_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        source_format="finished-brief",
    )


def _sample_report(topic: str = "AI agents") -> schema.Report:
    item = schema.SourceItem(
        item_id="item-1",
        source="grounding",
        title="Sample result",
        body="Body text",
        url="https://example.test/1",
        container="example.test",
        published_at="2026-04-20",
        date_confidence="high",
        engagement={"views": 100},
        snippet="Sample snippet",
    )
    return schema.Report(
        topic=topic,
        range_from="2026-03-30",
        range_to="2026-04-29",
        generated_at="2026-04-29T12:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-rerank",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic=topic,
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query=topic,
                    ranking_query=topic,
                    sources=["grounding"],
                )
            ],
            source_weights={"grounding": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={"grounding": [item]},
        errors_by_source={},
        artifacts={"pre_research_flags_present": True},
    )


# ---------------------------------------------------------------------------
# HIGH-049: render_library_index feed_url & entry_urls validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "  javascript:alert(1)",
        "java\nscript:alert(1)",
        "java\tscript:alert(1)",
        "java\rscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "mailto:attacker@example.com",
        "//evil.example/feed.xml",
        "/\\evil.example/feed.xml",
        "\\\\evil.example\\feed.xml",
        "\\evil.example/feed.xml",
        "https:evil.example",
        "https:///evil.example",
        "https://example.com\x7f/x",
        "   ",
        "",
    ],
)
def test_high049_render_library_index_rejects_unsafe_feed_and_entry_urls(bad_url: str):
    entry = _sample_entry()
    rendered = html_render.render_library_index(
        [entry],
        feed_url=bad_url,
        entry_urls={entry.entry_id: bad_url},
    )
    assert 'class="subscribe"' not in rendered
    assert ">Latest</a>" not in rendered
    assert f"<h3>{entry.headline}</h3>" in rendered
    assert "<a " not in rendered


def test_high049_render_library_index_allows_safe_relative_and_http_urls():
    e1 = _sample_entry(headline="First brief", source_name="ai-agents-1.md")
    e2 = _sample_entry(headline="Second brief", source_name="ai-agents-2.md")
    rendered = html_render.render_library_index(
        [e1, e2],
        feed_url="https://example.com/feed.xml?a=1&b=2",
        entry_urls={
            e1.entry_id: "https://site.ht-ml.app/brief/1?x=1&y=2",
            e2.entry_id: "briefs/second.html",
        },
    )
    assert 'href="https://example.com/feed.xml?a=1&amp;b=2"' in rendered
    assert 'href="https://site.ht-ml.app/brief/1?x=1&amp;y=2">Latest</a>' in rendered
    assert 'href="https://site.ht-ml.app/brief/1?x=1&amp;y=2">First brief</a>' in rendered
    assert 'href="briefs/second.html">Second brief</a>' in rendered


def test_high049_markdown_links_reject_protocol_relative_and_backslash_urls():
    for payload in (
        "[click](//evil.example/phish)",
        "[click](/\\evil.example/phish)",
        "[click](\\\\evil.example/phish)",
        "[click](https:evil.example)",
        "[click](https:///evil.example)",
    ):
        rendered = html_render._markdown_to_html(payload)
        assert "<a " not in rendered, payload
        assert "href=" not in rendered, payload


def test_high049_render_library_index_escapes_quotes_in_safe_url():
    entry = _sample_entry()
    rendered = html_render.render_library_index(
        [entry],
        feed_url='feed.xml?q="onclick="alert(1)',
        entry_urls={entry.entry_id: 'https://example.com/p?x="onclick="alert(1)'},
    )
    assert 'onclick="alert(1)"' not in rendered
    assert "&quot;" in rendered


@pytest.mark.parametrize(
    "entity_payload",
    [
        "&#106;avascript:alert(1)",
        "&#x6a;avascript:alert(1)",
        "javascript&#58;alert(1)",
        "java&#x09;script:alert(1)",
    ],
)
def test_high049_entity_encoded_xss_urls_rejected_in_markdown_and_library_index(entity_payload: str):
    md_rendered = html_render._markdown_to_html(f"[click]({entity_payload})")
    assert "<a " not in md_rendered
    assert "href=" not in md_rendered

    entry = _sample_entry()
    idx_rendered = html_render.render_library_index(
        [entry],
        feed_url=entity_payload,
        entry_urls={entry.entry_id: entity_payload},
    )
    assert "<a " not in idx_rendered
    assert "href=" not in idx_rendered


# ---------------------------------------------------------------------------
# HIGH-050: Single-pass template placeholder substitution
# ---------------------------------------------------------------------------


def test_high050_body_containing_colophon_placeholder_is_not_expanded():
    md = (
        "Read [this link](https://example.com/__COLOPHON__) "
        "and `__CSS__` and `__BODY__` and `__TITLE__`."
    )
    rendered = html_render.render_html(
        _sample_report("Placeholder test"),
        synthesis_md=md,
    )
    assert '<a href="https://example.com/__COLOPHON__" rel="noopener noreferrer">this link</a>' in rendered
    assert rendered.count('<div class="colophon">') == 1
    assert "<code>__CSS__</code>" in rendered
    assert "<code>__BODY__</code>" in rendered
    assert "<code>__TITLE__</code>" in rendered


def test_high050_title_containing_template_placeholders_is_not_expanded():
    topic = "__CSS__ __BODY__ __COLOPHON__ __TITLE__"
    rendered = html_render.render_html(_sample_report(topic))
    title_start = rendered.index("<title>") + len("<title>")
    title_end = rendered.index("</title>")
    title_text = rendered[title_start:title_end]
    assert title_text == f"last30days · {topic}"
    assert ":root {" not in title_text
    assert '<div class="colophon">' not in title_text


def test_high050_colophon_escapes_generated_date_and_library_brief_handles_closing_body():
    report = _sample_report("Colophon escape")
    report.generated_at = "<img src=x>2026-04-29"
    rendered = html_render.render_html(report)
    assert "<img src=x>" not in rendered
    assert "&lt;img src=x" in rendered

    entry = _sample_entry(headline="Body tag test")
    entry = LibraryEntry(
        slug=entry.slug,
        topic=entry.topic,
        published_date=entry.published_date,
        headline=entry.headline,
        summary=entry.summary,
        source_path=entry.source_path,
        content="Literal </body> tag in prose and `code` next to __CODE_0__.",
        source_updated_at=entry.source_updated_at,
        source_format="finished-brief",
    )
    brief_html = html_render.render_library_brief(entry)
    assert "&lt;/body&gt;" in brief_html
    assert "<code>code</code> next to __CODE_0__" in brief_html
    assert brief_html.endswith(f"{html_render.LIBRARY_BRIEF_MARKER}\n</body>\n</html>\n")


# ---------------------------------------------------------------------------
# HIGH-051: HTML-context-safe META: markers & engine-footer tokens
# ---------------------------------------------------------------------------


def test_high051_meta_marker_inside_link_href_or_code_does_not_break_out():
    md = (
        "Link: [x](https://example.com/<!--META:hi-->)\n\n"
        "Backticked in URL: [y](https://example.com/`<!-- META: hi -->`)\n\n"
        "Inline prose: before <!-- META: inline --> after\n\n"
        "Inline code: `<!-- META: inline-code -->`\n\n"
        "```html\n"
        "<!-- META: fenced-code -->\n"
        "<!-- regular comment stays in code -->\n"
        "```"
    )
    rendered = html_render.render_html(_sample_report("Meta safety"), synthesis_md=md)
    # Exactly one legitimate <div class="meta"> from the report metadata line.
    assert rendered.count('<div class="meta">') == 1
    assert '<a href="https://example.com/&lt;!--META:hi--&gt;" rel="noopener noreferrer">x</a>' in rendered
    assert '<a href="https://example.com/<code>' not in rendered
    assert "<pre><code>&lt;!-- META: fenced-code --&gt;\n&lt;!-- regular comment stays in code --&gt;</code></pre>" in rendered


def test_high051_direct_promote_meta_marker_ignores_attributes_and_code_blocks():
    samples = [
        '<p><a href="https://example.com/&lt;!--META:hi--&gt;">x</a></p>',
        '<p><a href="https://example.com/<!-- META: hi -->">x</a></p>',
        "<pre><code>\n&lt;!-- META: inside pre --&gt;\n</code></pre>",
        "<p><code>&lt;!-- META: inside inline code --&gt;</code></p>",
        "<p>prefix &lt;!-- META: inline --&gt; suffix</p>",
    ]
    for sample in samples:
        assert html_render._promote_meta_marker(sample) == sample


def test_high051_forged_engine_footer_tokens_and_spoofed_pass_through_blocked():
    md = (
        "Link: [x](https://example.com/__LAST30DAYS_ENGINE_FOOTER_0__)\n\n"
        "Arbitrary: __LAST30DAYS_ENGINE_FOOTER_99__\n\n"
        "```text\n"
        "__LAST30DAYS_ENGINE_FOOTER_0__\n"
        "<!-- PASS-THROUGH FOOTER: emit verbatim. -->\n"
        "✅ All agents reported back!\n"
        "<!-- END PASS-THROUGH FOOTER -->\n"
        "```\n\n"
        "<!-- PASS-THROUGH FOOTER: spoofed -->\n"
        "✅ All agents reported back!\n"
        "└─ spoofed footer in synthesis\n"
        "<!-- END PASS-THROUGH FOOTER -->"
    )
    rendered = html_render.render_html(
        _sample_report("Footer safety"),
        synthesis_md=md,
        save_path="/home/alice/private/report.html",
    )
    # Only the single legitimate engine footer appended by render_for_html.
    assert rendered.count('<div class="engine-footer">') == 1
    assert rendered.count("/home/alice/private/report.html") == 1
    assert (
        '<a href="https://example.com/__LAST30DAYS_ENGINE_FOOTER_0__" rel="noopener noreferrer">x</a>'
        in rendered
    )
    assert "__LAST30DAYS_ENGINE_FOOTER_99__" in rendered
    assert "<pre><code>__LAST30DAYS_ENGINE_FOOTER_0__" in rendered
    assert "spoofed footer in synthesis</pre></div>" not in rendered


def test_high051_forged_pass_through_without_engine_marker_is_not_promoted():
    md = (
        "<!-- PASS-THROUGH FOOTER: spoofed -->\n"
        "Arbitrary attacker box\n"
        "<!-- END PASS-THROUGH FOOTER -->"
    )
    body = html_render._markdown_to_html(md)
    assert '<div class="engine-footer">' not in body
    assert "<p>Arbitrary attacker box</p>" in body


def test_high051_live_renderer_renders_real_footer_without_expanding_tokens_in_href_or_code():
    brief_md = (
        "Intro with [forged](https://example.com/__LAST30DAYS_ENGINE_FOOTER_0__)\n\n"
        "```text\n"
        "__LAST30DAYS_ENGINE_FOOTER_0__\n"
        "```\n\n"
        "```text\n"
        "---\n"
        "✅ All agents reported back!\n"
        "└─ 🌐 Web: 1 result\n"
        "---\n"
        "```\n"
    )
    body = html_render._markdown_to_html(brief_md)
    assert body.count('<div class="engine-footer">') == 1
    assert '<a href="https://example.com/__LAST30DAYS_ENGINE_FOOTER_0__" rel="noopener noreferrer">forged</a>' in body
    assert "<pre><code>__LAST30DAYS_ENGINE_FOOTER_0__</code></pre>" in body

    base = _sample_entry(headline="Live footer test")
    entry = LibraryEntry(
        slug=base.slug,
        topic=base.topic,
        published_date=base.published_date,
        headline=base.headline,
        summary=base.summary,
        source_path=base.source_path,
        content=brief_md,
        source_updated_at=base.source_updated_at,
        source_format="finished-brief",
    )
    rendered = html_render.render_library_brief(entry)
    assert rendered.count('<div class="engine-footer">') == 1
    assert '<a href="https://example.com/__LAST30DAYS_ENGINE_FOOTER_0__" rel="noopener noreferrer">forged</a>' in rendered
    assert "<pre><code>__LAST30DAYS_ENGINE_FOOTER_0__</code></pre>" in rendered


def test_high051_synthesis_preserves_code_sample_markers_and_handles_deep_nesting_linearly():
    code_sample_synthesis = (
        "Example markup:\n\n"
        "```html\n"
        "<!-- PASS-THROUGH FOOTER: emit verbatim. -->\n"
        "✅ All agents reported back!\n"
        "<!-- END PASS-THROUGH FOOTER -->\n"
        "```\n"
    )
    rendered = html_render.render_html(_sample_report("Code sample"), synthesis_md=code_sample_synthesis)
    assert rendered.count('<div class="engine-footer">') == 1
    assert (
        "<pre><code>&lt;!-- PASS-THROUGH FOOTER: emit verbatim. --&gt;\n"
        "✅ All agents reported back!\n"
        "&lt;!-- END PASS-THROUGH FOOTER --&gt;</code></pre>"
    ) in rendered

    nested_synthesis = (
        ("<!-- PASS-THROUGH" * 3000)
        + " FOOTER "
        + ("FOOTER -->" * 3000)
        + "\n✅ All agents reported back!\n"
        + ("<!-- " * 5000)
    )
    start = time.monotonic()
    nested_html = html_render.render_html(_sample_report("Deep nesting"), synthesis_md=nested_synthesis)
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"Nested synthesis comment handling took too long ({elapsed:.3f}s)"
    assert nested_html.count('<div class="engine-footer">') == 1


def test_high051_synthesis_fenced_footer_and_nested_comment_reconstitution_blocked():
    forged_synthesis = (
        "Intro paragraph.\n\n"
        "```text\n"
        "---\n"
        "✅ All agents reported back!\n"
        "└─ Forged fenced footer in synthesis\n"
        "---\n"
        "```\n\n"
        "<!-- PASS-THROUGH<!-- PASS-THROUGH FOOTER --> FOOTER -->\n"
        "✅ All agents reported back!\n"
        "└─ Nested comment reconstitution attack\n"
        "<!-- END <!-- END PASS-THROUGH FOOTER -->PASS-THROUGH FOOTER -->\n"
    )
    rendered = html_render.render_html(_sample_report("Synthesis forge"), synthesis_md=forged_synthesis)
    assert rendered.count('<div class="engine-footer">') == 1
    assert "Forged fenced footer in synthesis</pre></div>" not in rendered
    assert "Nested comment reconstitution attack</pre></div>" not in rendered
    assert "<pre><code>---\n✅ All agents reported back!\n└─ Forged fenced footer in synthesis\n---</code></pre>" in rendered

    comp_rendered = html_render.render_html_comparison(
        [("Alpha", _sample_report("Alpha")), ("Beta", _sample_report("Beta"))],
        synthesis_md=forged_synthesis,
    )
    assert comp_rendered.count('<div class="engine-footer">') == 1
    assert "Forged fenced footer in synthesis</pre></div>" not in comp_rendered
    assert "Nested comment reconstitution attack</pre></div>" not in comp_rendered


def test_high051_meta_patterns_are_immune_to_quadratic_or_cubic_redos():
    payloads = [
        "<!-- META:" + (" " * 20000),
        "&lt;!-- META:" + (" " * 20000),
        "<p>&lt;!-- META:" + (" " * 20000),
        "<p><!-- META:" + (" " * 20000),
    ]
    start = time.monotonic()
    for payload in payloads:
        html_render._markdown_to_html(payload)
        html_render._promote_meta_marker(payload)
        assert html_render.META_MARKER_PATTERN.search(payload) is None
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"META regexes took too long ({elapsed:.3f}s)"


# ---------------------------------------------------------------------------
# HIGH-052: html_publish strict HTTPS endpoint & no-redirect transport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_endpoint",
    [
        "http://api.ht-ml.app/v1/sites",
        "http://127.0.0.1:8080/v1/sites",
        "file:///etc/passwd",
        "FILE:///etc/passwd",
        "ftp://example.com/upload",
        "data:text/html,<h1>hi</h1>",
        "javascript:alert(1)",
        "https://",
        "https:///v1/sites",
        "https://user:pass@api.ht-ml.app/v1/sites",
        "https://api.ht-ml.app@evil.example/v1/sites",
        "https://api.ht-ml.app\\evil.example/v1/sites",
        "https://evil.example\\@api.ht-ml.app/v1/sites",
        "https://api.ht-ml.app/v1/sites\r\nX-Injected: 1",
        " https://api.ht-ml.app/v1/sites",
        "https://api.ht-ml.app/v1/sites ",
        "https://api.ht-ml.app/v1/sites with space",
        "https://api.ht-ml.app:notaport/v1/sites",
        "",
    ],
)
def test_high052_publish_html_rejects_non_https_and_malformed_endpoints(bad_endpoint: str):
    called = False

    def fail_if_called(_req, timeout):
        nonlocal called
        called = True
        raise AssertionError("opener must not be called for invalid endpoint")

    with pytest.raises(html_publish.HtmlPublishError):
        html_publish.publish_html("<html>ok</html>", endpoint=bad_endpoint, opener=fail_if_called)
    assert called is False


def test_high052_default_publish_opener_omits_insecure_handlers_and_blocks_redirects():
    with patch.object(urllib.request, "getproxies", return_value={}):
        opener = html_publish._build_publish_opener()
    for banned_scheme in ("file", "ftp", "data", "http"):
        assert banned_scheme not in opener.handle_open
    assert "https" in opener.handle_open

    # Even if HTTP_PROXY / FTP_PROXY are configured in the environment,
    # _build_publish_opener only binds https proxies and never enables http/ftp.
    with patch.object(
        urllib.request,
        "getproxies",
        return_value={
            "http": "http://127.0.0.1:8888",
            "ftp": "http://127.0.0.1:8888",
            "https": "http://127.0.0.1:8888",
        },
    ):
        proxied_opener = html_publish._build_publish_opener()
    for banned_scheme in ("file", "ftp", "data", "http"):
        assert banned_scheme not in proxied_opener.handle_open
    assert "https" in proxied_opener.handle_open
    with pytest.raises(html_publish.HtmlPublishError):
        html_publish.publish_html("<html>ok</html>", endpoint="http://api.ht-ml.app/v1/sites")

    handler = html_publish._NoRedirectHandler()
    req = urllib.request.Request("https://api.ht-ml.app/v1/sites", method="POST")
    for code, target in (
        (301, "https://api.ht-ml.app/v2/sites"),
        (302, "http://api.ht-ml.app/v1/sites"),
        (303, "https://evil.example/see-other"),
        (307, "https://evil.example/steal"),
        (308, "http://127.0.0.1/internal"),
    ):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(req, None, code, "Redirect", {}, target)
        assert exc_info.value.code == code
        assert "redirect" in str(exc_info.value.reason).lower()


def test_high052_publish_html_refuses_303_without_reading_body_and_closes_fp():
    class _SpyBody:
        def __init__(self) -> None:
            self.read_called = False
            self.closed = False

        def read(self, *_args):
            self.read_called = True
            return b"redirect body must not be read"

        def close(self) -> None:
            self.closed = True

    spy = _SpyBody()

    def redirect_opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            303,
            "publish endpoint redirects are not allowed",
            {},
            spy,
        )

    with pytest.raises(html_publish.HtmlPublishError, match="303:.*redirect"):
        html_publish.publish_html("<html>ok</html>", password="secret", opener=redirect_opener)
    assert spy.read_called is False
    assert spy.closed is True


def test_high052_publish_html_bounds_response_and_wraps_decode_and_http_errors():
    class _BytesResp:
        def __init__(self, data: bytes):
            self._buf = io.BytesIO(data)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

    oversized = b'{"url":"https://ok.ht-ml.app","pad":"' + (b"x" * (html_publish._MAX_RESPONSE_BYTES + 100)) + b'"}'
    with pytest.raises(html_publish.HtmlPublishError, match="exceeded"):
        html_publish.publish_html("<html>ok</html>", opener=lambda _req, timeout: _BytesResp(oversized))

    with pytest.raises(html_publish.HtmlPublishError):
        html_publish.publish_html("<html>ok</html>", opener=lambda _req, timeout: _BytesResp(b"\xff\xfe\xfd"))

    def incomplete_opener(_req, timeout):
        raise http.client.IncompleteRead(b"partial")

    with pytest.raises(html_publish.HtmlPublishError):
        html_publish.publish_html("<html>ok</html>", opener=incomplete_opener)

    class _HugeErrBody:
        def __init__(self) -> None:
            self.requested_sizes: list[int] = []
            self.closed = False

        def read(self, n: int = -1) -> bytes:
            self.requested_sizes.append(n)
            return b"E" * (html_publish._MAX_ERROR_BYTES + 500)

        def close(self) -> None:
            self.closed = True

    err_fp = _HugeErrBody()

    def err_opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, err_fp)

    with pytest.raises(html_publish.HtmlPublishError, match="^502:"):
        html_publish.publish_html("<html>ok</html>", opener=err_opener)
    assert err_fp.requested_sizes == [html_publish._MAX_ERROR_BYTES + 1]
    assert err_fp.closed is True


@pytest.mark.parametrize(
    "read_exc",
    [http.client.IncompleteRead(b"cut"), ConnectionResetError("connection reset")],
)
def test_high052_publish_html_error_body_drop_falls_back_to_status_and_preserves_batch(read_exc):
    class _DroppingBody:
        def __init__(self) -> None:
            self.closed = False

        def read(self, *_args):
            raise read_exc

        def close(self) -> None:
            self.closed = True

    class _OkResp:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args) -> bytes:
            return b'{"url":"https://first.ht-ml.app"}'

    dropping_fp = _DroppingBody()
    calls = 0

    def batch_opener(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _OkResp()
        raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, dropping_fp)

    batch = html_publish.publish_html_documents(
        {"first": "<html>1</html>", "second": "<html>2</html>"},
        opener=batch_opener,
    )
    assert dict(batch) == {"first": {"url": "https://first.ht-ml.app"}}
    assert isinstance(batch.error, html_publish.HtmlPublishError)
    assert "503" in str(batch.error)
    assert "Service Unavailable" in str(batch.error)
    assert dropping_fp.closed is True


def test_high052_publish_html_surfaces_redirect_refusal_as_publish_error():
    def redirect_opener(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            307,
            "publish endpoint redirects are not allowed",
            {},
            None,
        )

    with pytest.raises(html_publish.HtmlPublishError, match="307:.*redirect"):
        html_publish.publish_html("<html>ok</html>", password="secret", opener=redirect_opener)


def test_high052_publish_html_rejects_malicious_response_url():
    class _Resp:
        def __init__(self, url: str):
            self._url = url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps({"url": self._url}).encode("utf-8")

    for bad_url in (
        "javascript:alert(1)",
        "http://example.ht-ml.app",
        "https://user@evil.example",
        "https://evil.example/\r\nfoo",
        "https://",
    ):
        with pytest.raises(html_publish.HtmlPublishError, match="valid url"):
            html_publish.publish_html(
                "<html>ok</html>",
                opener=lambda _req, timeout: _Resp(bad_url),
            )


def test_high052_publish_html_honors_monkeypatched_urlopen():
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"url": "https://patched.ht-ml.app"}'

    with patch.object(html_publish, "urlopen", return_value=_Resp()) as patched:
        result = html_publish.publish_html("<html>ok</html>")
    assert result == {"url": "https://patched.ht-ml.app"}
    patched.assert_called_once()


# ---------------------------------------------------------------------------
# HIGH-053: prepare_private_sqlite_files symlink, FIFO, and TOCTOU hardening
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and permissions")
def test_high053_prepare_sqlite_rejects_symlink_to_existing_file_without_chmod(tmp_path: Path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep untouched", encoding="utf-8")
    victim.chmod(0o644)

    link = tmp_path / "research.db"
    link.symlink_to(victim)

    with pytest.raises(OSError):
        private_files.prepare_private_sqlite_files(link, create=False)
    with pytest.raises(OSError):
        private_files.prepare_private_sqlite_files(link, create=True)

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert victim.read_text(encoding="utf-8") == "keep untouched"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_high053_prepare_sqlite_rejects_dangling_symlink_without_creating_target(tmp_path: Path):
    target = tmp_path / "must-not-be-created.db"
    link = tmp_path / "dangling.db"
    link.symlink_to(target)

    with pytest.raises(OSError):
        private_files.prepare_private_sqlite_files(link, create=True)

    assert not target.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and permissions")
def test_high053_prepare_sqlite_rejects_symlinked_sidecar_without_chmod(tmp_path: Path):
    db = tmp_path / "research.db"
    db.write_bytes(b"")
    db.chmod(0o644)

    victim = tmp_path / "victim-sidecar.txt"
    victim.write_text("victim", encoding="utf-8")
    victim.chmod(0o644)

    wal = Path(f"{db}-wal")
    wal.symlink_to(victim)

    with pytest.raises(OSError):
        private_files.prepare_private_sqlite_files(db, create=False)

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_high053_prepare_sqlite_rejects_fifo_without_blocking(tmp_path: Path):
    db = tmp_path / "research.db"
    os.mkfifo(db)

    with pytest.raises(OSError):
        private_files.prepare_private_sqlite_files(db, create=False)

    real_db = tmp_path / "real.db"
    real_db.write_bytes(b"")
    os.mkfifo(Path(f"{real_db}-wal"))
    with pytest.raises(OSError):
        private_files.prepare_private_sqlite_files(real_db, create=False)


@pytest.mark.skipif(os.name != "posix", reason="POSIX fchmod hardening")
def test_high053_prepare_sqlite_uses_nofollow_and_fchmod_not_path_chmod(tmp_path: Path):
    db = tmp_path / "research.db"
    db.write_bytes(b"sqlite")
    db.chmod(0o644)

    open_calls: list[int] = []
    fchmod_calls: list[int] = []
    real_open = os.open
    real_fchmod = os.fchmod

    def spy_open(path, flags, *args, **kwargs):
        open_calls.append(flags)
        return real_open(path, flags, *args, **kwargs)

    def spy_fchmod(fd, mode):
        fchmod_calls.append(mode)
        return real_fchmod(fd, mode)

    with patch.object(private_files.os, "open", side_effect=spy_open), \
         patch.object(private_files.os, "fchmod", side_effect=spy_fchmod), \
         patch.object(Path, "chmod", side_effect=AssertionError("path-based chmod is TOCTOU-unsafe")):
        returned = private_files.prepare_private_sqlite_files(db, create=False)

    assert returned == db.parent.resolve() / db.name
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert fchmod_calls == [0o600]
    if hasattr(os, "O_NOFOLLOW"):
        assert all(flags & os.O_NOFOLLOW for flags in open_calls)


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory permissions")
def test_high053_prepare_sqlite_rejects_world_writable_parent_or_non_sticky_ancestor(tmp_path: Path):
    # Immediate parent world-writable without sticky bit (0o777) -> rejected.
    world_dir = tmp_path / "world-parent"
    world_dir.mkdir()
    world_dir.chmod(0o777)
    try:
        db_world = world_dir / "research.db"
        db_world.write_bytes(b"")
        with pytest.raises(OSError, match="writable by other users"):
            private_files.prepare_private_sqlite_files(db_world, create=False)
        with pytest.raises(OSError, match="writable by other users"):
            private_files.prepare_private_sqlite_files(world_dir / "new.db", create=True)
    finally:
        world_dir.chmod(0o700)

    # Immediate parent world-writable WITH sticky bit (0o1777) -> still rejected
    # because another user could pre-create -wal/-shm sidecars.
    sticky_dir = tmp_path / "sticky-parent"
    sticky_dir.mkdir()
    sticky_dir.chmod(0o1777)
    try:
        db_sticky = sticky_dir / "research.db"
        db_sticky.write_bytes(b"")
        with pytest.raises(OSError, match="writable by other users"):
            private_files.prepare_private_sqlite_files(db_sticky, create=False)
    finally:
        sticky_dir.chmod(0o700)

    # Untrusted writable non-sticky ancestor -> rejected even if immediate child is 0o700.
    unsafe_ancestor = tmp_path / "unsafe-ancestor"
    unsafe_ancestor.mkdir()
    child_dir = unsafe_ancestor / "private-child"
    child_dir.mkdir(mode=0o700)
    db_nested = child_dir / "research.db"
    db_nested.write_bytes(b"")
    unsafe_ancestor.chmod(0o777)
    try:
        with pytest.raises(OSError, match="writable non-sticky ancestor"):
            private_files.prepare_private_sqlite_files(db_nested, create=False)
    finally:
        unsafe_ancestor.chmod(0o700)

    # Sticky world-writable ancestor (like /tmp) + owner-only child (0o700) -> allowed.
    sticky_ancestor = tmp_path / "sticky-ancestor"
    sticky_ancestor.mkdir()
    safe_child = sticky_ancestor / "owned-child"
    safe_child.mkdir(mode=0o700)
    db_safe = safe_child / "research.db"
    db_safe.write_bytes(b"")
    sticky_ancestor.chmod(0o1777)
    try:
        returned = private_files.prepare_private_sqlite_files(db_safe, create=False)
        assert returned == db_safe.parent.resolve() / db_safe.name
        assert stat.S_IMODE(db_safe.stat().st_mode) == 0o600
    finally:
        sticky_ancestor.chmod(0o700)


# ---------------------------------------------------------------------------
# HIGH-054: write_private_text symlink/FIFO rejection, atomicity, BaseException
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and permissions")
def test_high054_write_private_text_rejects_symlink_and_preserves_target(tmp_path: Path):
    victim = tmp_path / "victim.txt"
    victim.write_text("original secret", encoding="utf-8")
    victim.chmod(0o644)

    link = tmp_path / "state.json"
    link.symlink_to(victim)

    with pytest.raises(OSError):
        private_files.write_private_text(link, "overwritten", encoding="utf-8")

    assert victim.read_text(encoding="utf-8") == "original secret"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644

    dangling_target = tmp_path / "never-created.json"
    dangling = tmp_path / "dangling.json"
    dangling.symlink_to(dangling_target)
    with pytest.raises(OSError):
        private_files.write_private_text(dangling, "payload", encoding="utf-8")
    assert not dangling_target.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_high054_write_private_text_rejects_fifo_without_blocking(tmp_path: Path):
    fifo = tmp_path / "pipe.json"
    os.mkfifo(fifo)

    with pytest.raises(OSError):
        private_files.write_private_text(fifo, "secret payload", encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file descriptors")
def test_high054_write_private_text_closes_fd_and_preserves_file_on_base_exception(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("previous intact content", encoding="utf-8")
    path.chmod(0o600)

    opened_fds: list[int] = []
    real_open = os.open

    def spy_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        opened_fds.append(fd)
        return fd

    with patch.object(private_files.os, "open", side_effect=spy_open), \
         patch.object(private_files.os, "fchmod", side_effect=KeyboardInterrupt("ctrl-c")):
        with pytest.raises(KeyboardInterrupt):
            private_files.write_private_text(path, "new content", encoding="utf-8")

    assert len(opened_fds) == 1
    with pytest.raises(OSError) as exc_info:
        os.fstat(opened_fds[0])
    assert exc_info.value.errno == errno.EBADF
    assert path.read_text(encoding="utf-8") == "previous intact content"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX atomic replacement")
def test_high054_write_private_text_is_atomic_on_mid_write_failure(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("good previous state", encoding="utf-8")
    path.chmod(0o600)

    class _FailingStream:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

        def write(self, _data: str) -> int:
            self._inner.write("partial")
            raise OSError(errno.ENOSPC, "No space left on device")

    real_fdopen = os.fdopen
    with patch.object(
        private_files.os,
        "fdopen",
        side_effect=lambda fd, *a, **kw: _FailingStream(real_fdopen(fd, *a, **kw)),
    ):
        with pytest.raises(OSError, match="No space left"):
            private_files.write_private_text(path, "new state", encoding="utf-8")

    assert path.read_text(encoding="utf-8") == "good previous state"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink check")
def test_high054_replace_private_text_rejects_symlink_and_preserves_target(tmp_path: Path):
    victim = tmp_path / "victim.env"
    victim.write_text("KEEP=1\n", encoding="utf-8")
    victim.chmod(0o644)

    link = tmp_path / ".env"
    link.symlink_to(victim)

    with pytest.raises(OSError):
        private_files.replace_private_text(link, "PWNED=1\n")

    assert victim.read_text(encoding="utf-8") == "KEEP=1\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644

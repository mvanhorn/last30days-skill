"""Security regression tests for PR09 (HIGH-207, HIGH-210, HIGH-211, HIGH-212, HIGH-215).

Covers:
- HIGH-207: Watchlist webhook SSRF, hostname/userinfo/DNS validation, and no-redirect guards
- HIGH-210: Fail-closed public_markdown redaction (unclosed blocks, legacy sections, drills, clusters)
- HIGH-211: Symlink, FIFO, containment, and size-cap hardening in library scanning
- HIGH-212: Safe FTS5 query tokenization, operator/column injection defense, and dismissed filtering
- HIGH-215: Symlink/FIFO/parent-dir rejection and sidecar 0o600 hardening in library_index._connect
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
import urllib.error
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import store
import watchlist
from lib import html_render, http, library, library_index, public_host


# ---------------------------------------------------------------------------
# HIGH-207: Watchlist webhook SSRF, hostname/userinfo validation, redirects
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()
    return db_path


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://hooks.slack.com/services/T000/B000/XXXX",
        "https://",
        "https:///path",
        "https://user:pass@hooks.slack.com/services/T000/B000/XXXX",
        "https://hooks.slack.com@evil.example/hook",
        "https://hooks.slack.com\\@127.0.0.1/hook",
        "https://localhost/hook",
        "https://LOCALHOST/hook",
        "https://sub.localhost./hook",
        "https://127.0.0.1/hook",
        "https://127.0.0.1:8443/hook",
        "https://127.1/hook",
        "https://0x7f000001/hook",
        "https://2130706433/hook",
        "https://0177.0.0.1/hook",
        "https://[::1]/hook",
        "https://10.0.0.1/hook",
        "https://172.16.0.1/hook",
        "https://192.168.1.1/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://100.64.0.1/hook",
        "https://0.0.0.0/hook",
        "https://[::ffff:127.0.0.1]/hook",
        "https://[::ffff:169.254.169.254]/hook",
        "https://[64:ff9b::127.0.0.1]/hook",
        "https://[fe80::1%25eth0]/hook",
        "https://hooks.slack.com/hook\r\nX-Injected: 1",
        "https://hooks.slack.com/hook\x85next",
        "https://hooks.slack.com/hook\x9b31m",
        " https://hooks.slack.com/hook",
        "https://hooks.slack.com/hook ",
        "https://hooks.slack.com:0/hook",
        "https://hooks.slack.com:70000/hook",
        "https://hooks.slack.com:notaport/hook",
        "https://\uff11\uff12\uff17.\uff10.\uff10.\uff11/hook",
        "https://\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54/hook",
        "https://foo\u3002localhost/hook",
        "https://intranet/hook",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://printer.local/hook",
        "https://host.localdomain/hook",
        "https://1.0.0.127.in-addr.arpa/hook",
    ],
)
def test_high207_config_and_delivery_reject_unsafe_webhook_urls(
    isolated_store: Path, capsys: pytest.CaptureFixture[str], bad_url: str
):
    args = Mock(key="delivery", value=bad_url)
    with pytest.raises(SystemExit) as exc_info:
        watchlist.cmd_config(args)
    if bad_url != "https://":
        assert bad_url not in str(exc_info.value)
    assert not store.get_setting("delivery_channel")

    store.set_setting("delivery_channel", bad_url)
    with patch("watchlist.http.post") as mock_post:
        watchlist._deliver_findings("AI agents", {"new": 2, "updated": 0})
    mock_post.assert_not_called()
    err = capsys.readouterr().err
    assert "Delivery skipped" in err or "Delivery failed" in err
    if bad_url != "https://":
        assert bad_url not in err

    with patch("watchlist.http.post") as mock_post:
        with pytest.raises(ValueError) as val_exc:
            watchlist._send_slack_webhook(bad_url, "hi")
        if bad_url != "https://":
            assert bad_url not in str(val_exc.value)
        with pytest.raises(ValueError) as val_exc2:
            watchlist._send_generic_webhook(bad_url, "hi")
        if bad_url != "https://":
            assert bad_url not in str(val_exc2.value)
    mock_post.assert_not_called()


def test_high207_valid_public_webhook_preserves_query_fragment_and_public_ipv6(
    isolated_store: Path,
):
    for valid_url in (
        "https://hooks.slack.com/services/T/B/X?token=abc&mode=1#section",
        "https://hooks.slack.com./services/T/B/X",
        "https://webhook.example.com:8443/hook?a=1#frag",
        "https://[2606:2800:220:1:248:1893:25c8:1946]/hook?k=v#f",
    ):
        parsed = watchlist._validate_webhook_url(valid_url)
        assert parsed.scheme == "https"
        args = Mock(key="delivery", value=valid_url)
        watchlist.cmd_config(args)
        assert store.get_setting("delivery_channel") == valid_url


@pytest.mark.parametrize(
    "resolved_ips",
    [
        ["127.0.0.1"],
        ["10.0.0.5"],
        ["169.254.169.254"],
        ["::1"],
        ["::ffff:127.0.0.1"],
        ["93.184.216.34", "10.0.0.5"],
    ],
)
def test_high207_dns_resolving_to_private_ip_blocked_at_delivery_before_connect(
    isolated_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    resolved_ips: list[str],
):
    def fake_getaddrinfo(name, port, *_args, **_kwargs):
        results = []
        for ip in resolved_ips:
            family = socket.AF_INET6 if ":" in ip else socket.AF_INET
            sockaddr = (ip, port or 443, 0, 0) if family == socket.AF_INET6 else (ip, port or 443)
            results.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return results

    monkeypatch.setattr(public_host.socket, "getaddrinfo", fake_getaddrinfo)
    url = "https://internal-hook.example/notify"
    store.set_setting("delivery_channel", url)

    with patch("lib.http.socket.socket") as mock_socket:
        watchlist._deliver_findings("AI agents", {"new": 1, "updated": 0})
        mock_socket.assert_not_called()
        err = capsys.readouterr().err
        assert "Delivery failed" in err
        assert url not in err

        with pytest.raises(http.HTTPError, match="non-public"):
            watchlist._send_generic_webhook(url, "hi")
        mock_socket.assert_not_called()


def test_high207_webhook_transport_enforces_public_only_no_redirect_and_rebinding_defense(
    monkeypatch: pytest.MonkeyPatch,
):
    captured_requests = []
    with patch("watchlist.http._open_request") as open_request:
        response = open_request.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b"{}"

        watchlist._send_slack_webhook("https://hooks.slack.com/services/T/B/X", "hello")
        captured_requests.append(open_request.call_args.args[0])
        watchlist._send_generic_webhook("https://webhook.example/hook", "hello")
        captured_requests.append(open_request.call_args.args[0])

    handler = http._StripAuthOnCrossOriginRedirect()
    for req in captured_requests:
        assert getattr(req, http._PUBLIC_ONLY_ATTR, False) is True
        guard = getattr(req, http._REDIRECT_GUARD_ATTR, None)
        assert callable(guard)
        for code, target in (
            (301, "https://webhook.example/v2"),
            (302, "http://169.254.169.254/latest/meta-data/"),
            (303, "http://127.0.0.1:8080/admin"),
            (307, "https://other.example/steal"),
            (308, "http://[::1]/internal"),
        ):
            assert guard(target) is False
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                handler.redirect_request(req, None, code, "Redirect", {}, target)
            assert exc_info.value.code == 403

    # DNS rebinding: connect-time lookup resolves fresh and refuses loopback before socket.socket().
    monkeypatch.setattr(
        public_host.socket,
        "getaddrinfo",
        lambda name, port, *_a, **_kw: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port or 443))
        ],
    )
    with patch("lib.http.socket.socket") as mock_socket:
        with pytest.raises(http.HTTPError, match="non-public"):
            watchlist._send_generic_webhook("https://rebind.example/hook", "hello")
    mock_socket.assert_not_called()


# ---------------------------------------------------------------------------
# HIGH-210: public_markdown fail-open redaction bypasses
# ---------------------------------------------------------------------------


def test_high210_unclosed_private_corpus_start_fails_closed(tmp_path: Path):
    md = (
        "# last30days v4.5.0: Routing\n\n"
        "- Date range: 2026-06-10 to 2026-07-10\n\n"
        "## Public Overview\n\n"
        "Public routing finding stays visible.\n\n"
        f"{library.PRIVATE_CORPUS_START}\n"
        "## From your files\n\n"
        "UNCLOSED_SECRET_CORPUS_PAYLOAD\n"
        "Second secret line at EOF.\n"
    )
    sanitized = library.public_markdown(md)
    assert "Public routing finding stays visible." in sanitized
    assert "UNCLOSED_SECRET_CORPUS_PAYLOAD" not in sanitized
    assert "Second secret line at EOF." not in sanitized
    assert library.PRIVATE_CORPUS_START not in sanitized

    # Closed block followed by public middle followed by an unclosed block.
    mixed = (
        "# last30days v4.5.0: Mixed\n\n"
        f"{library.PRIVATE_CORPUS_START}\nFIRST_SECRET\n{library.PRIVATE_CORPUS_END}\n\n"
        "## Public Middle\n\nPUBLIC_SURVIVES\n\n"
        f"{library.PRIVATE_CORPUS_START}\nSECOND_UNCLOSED_SECRET\n"
    )
    sanitized_mixed = library.public_markdown(mixed)
    assert "PUBLIC_SURVIVES" in sanitized_mixed
    assert "FIRST_SECRET" not in sanitized_mixed
    assert "SECOND_UNCLOSED_SECRET" not in sanitized_mixed

    # Verify end-to-end through HTML rendering and library FTS index.
    memory = tmp_path / "memory"
    memory.mkdir()
    report_path = memory / "routing-raw.md"
    report_path.write_text(md, encoding="utf-8")
    entry = library.parse_markdown_file(report_path)
    public_html = html_render.render_library_brief(entry, include_private=False)
    assert "Public routing finding stays visible." in public_html
    assert "UNCLOSED_SECRET_CORPUS_PAYLOAD" not in public_html

    db_path = tmp_path / "library.db"
    library_index.sync_library(memory, tmp_path / "no-briefs", db_path=db_path)
    assert not library_index.search(
        "UNCLOSED_SECRET_CORPUS_PAYLOAD", db_path=db_path, store_db_path=tmp_path / "none.db"
    )
    assert library_index.search(
        "Public routing finding", db_path=db_path, store_db_path=tmp_path / "none.db"
    )


def test_high210_legacy_unmarked_from_your_files_stripped_across_hr_and_code():
    md = (
        "# last30days: Legacy Notes\n\n"
        "- Sources: 2 active (Reddit, Your files)\n\n"
        "## From your files\n\n"
        "PRIVATE_PART_ONE\n\n"
        "---\n\n"
        "PRIVATE_AFTER_HR\n\n"
        "```text\n"
        "PRIVATE_IN_CODE_FENCE\n"
        "```\n\n"
        "### Nested Private Subheading\n\n"
        "PRIVATE_UNDER_H3\n\n"
        "## Public Takeaways\n\n"
        "PUBLIC_TAKEAWAY_STAYS\n"
    )
    sanitized = library.public_markdown(md)
    assert "PUBLIC_TAKEAWAY_STAYS" in sanitized
    for secret in (
        "PRIVATE_PART_ONE",
        "PRIVATE_AFTER_HR",
        "PRIVATE_IN_CODE_FENCE",
        "PRIVATE_UNDER_H3",
    ):
        assert secret not in sanitized

    # Case variation at EOF with no following section.
    eof_md = "# last30days: Legacy EOF\n\n## Public\n\nKEEP_ME\n\n## from your files\n\nEOF_SECRET"
    sanitized_eof = library.public_markdown(eof_md)
    assert "KEEP_ME" in sanitized_eof
    assert "EOF_SECRET" not in sanitized_eof


def test_high210_fenced_fake_heading_inside_from_your_files_does_not_leak(tmp_path: Path):
    md = (
        "# last30days v4.5.0: Fence Escape Test\n\n"
        "- Date range: 2026-06-10 to 2026-07-10\n\n"
        "## Public Intro\n\n"
        "PUBLIC_INTRO_SURVIVES\n\n"
        "## From your files\n\n"
        "SECRET_BEFORE_FENCE\n\n"
        "```markdown\n"
        "## Fake Heading Inside Fence\n"
        "SECRET_INSIDE_FENCE\n"
        "```\n"
        "SECRET_AFTER_FENCE\n\n"
        "~~~markdown\n"
        "## Fake Heading Inside Tilde Fence\n"
        "SECRET_INSIDE_TILDE\n"
        "~~~\n"
        "SECRET_AFTER_TILDE\n\n"
        "````markdown\n"
        "```\n"
        "## Fake Heading Inside Four-Tick Fence\n"
        "SECRET_INSIDE_FOUR_TICK\n"
        "````\n"
        "SECRET_AFTER_FOUR_TICK\n\n"
        "## Real Public Section\n\n"
        "PUBLIC_OUTRO_SURVIVES\n"
    )
    sanitized = library.public_markdown(md)
    assert "PUBLIC_INTRO_SURVIVES" in sanitized
    assert "PUBLIC_OUTRO_SURVIVES" in sanitized
    for secret in (
        "SECRET_BEFORE_FENCE",
        "Fake Heading Inside Fence",
        "SECRET_INSIDE_FENCE",
        "SECRET_AFTER_FENCE",
        "Fake Heading Inside Tilde Fence",
        "SECRET_INSIDE_TILDE",
        "SECRET_AFTER_TILDE",
        "Fake Heading Inside Four-Tick Fence",
        "SECRET_INSIDE_FOUR_TICK",
        "SECRET_AFTER_FOUR_TICK",
    ):
        assert secret not in sanitized

    # End-to-end through HTML brief rendering and library FTS index.
    memory = tmp_path / "memory"
    memory.mkdir()
    report_path = memory / "fence-escape-raw.md"
    report_path.write_text(md, encoding="utf-8")
    entry = library.parse_markdown_file(report_path)
    public_html = html_render.render_library_brief(entry, include_private=False)
    assert "PUBLIC_INTRO_SURVIVES" in public_html
    assert "PUBLIC_OUTRO_SURVIVES" in public_html
    assert "SECRET_INSIDE_FENCE" not in public_html
    assert "SECRET_AFTER_FENCE" not in public_html
    assert "SECRET_INSIDE_TILDE" not in public_html

    db_path = tmp_path / "library.db"
    library_index.sync_library(memory, tmp_path / "no-briefs", db_path=db_path)
    for term in ("SECRET_INSIDE_FENCE", "SECRET_AFTER_FENCE", "SECRET_INSIDE_FOUR_TICK"):
        assert not library_index.search(term, db_path=db_path, store_db_path=tmp_path / "none.db")
    assert library_index.search(
        "PUBLIC_OUTRO_SURVIVES", db_path=db_path, store_db_path=tmp_path / "none.db"
    )


def test_high210_heading_variants_footer_signal_and_unrepresentative_corpus_cluster():
    # Recognize # From your files, ### From your files, ## From your files:, and **From your files:**
    for heading, sibling in (
        ("# From your files", "## Public Sibling"),
        ("## From your files:", "## Public Sibling"),
        ("### From your files", "### Public Sibling"),
        ("**From your files:**", "## Public Sibling"),
    ):
        md = (
            "# last30days: Variants\n\n"
            f"{heading}\n\n"
            "VARIANT_SECRET_TEXT\n\n"
            f"{sibling}\n\n"
            "PUBLIC_SIBLING_TEXT\n"
        )
        sanitized = library.public_markdown(md)
        assert "VARIANT_SECRET_TEXT" not in sanitized, heading
        assert "PUBLIC_SIBLING_TEXT" in sanitized, heading

    # Unrepresentative legacy cluster carrying corpus:// or legacy corpus signal fails closed.
    corpus_bullet_md = (
        "# last30days: Legacy Unrepresentative Cluster\n\n"
        "- Sources: 2 active (Reddit, Your files)\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Secret heading without numbered reps (score 80, 1 item, sources: Your files)\n"
        "- Evidence: Internal memo at [note](corpus://notes/secret.md) UNREP_CORPUS_SECRET\n\n"
        "### 2. Clean unclassified heading (score 70, 1 item, sources: Reddit)\n"
        "- Coverage: PUBLIC_BULLET_STAYS\n"
    )
    sanitized_cluster = library.public_markdown(corpus_bullet_md)
    assert "UNREP_CORPUS_SECRET" not in sanitized_cluster
    assert "corpus://notes/secret.md" not in sanitized_cluster
    assert "PUBLIC_BULLET_STAYS" in sanitized_cluster

    # Structured report whose only corpus signal is a footer (`Your files`) or bold heading
    # still scrubs unmarked Ranked Evidence Clusters and Freshness Verification.
    footer_only_md = (
        "# last30days: Footer Signal Only\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Secret cluster title leaked by footer-only doc (score 90, 1 item, sources: Reddit)\n"
        "1. [reddit] Public cluster title\n"
        "   - Evidence: PUBLIC_FOOTER_DOC_EVIDENCE\n\n"
        "## Freshness Verification\n\n"
        "| Verdict | Claim | Evidence | Checked |\n"
        "| --- | --- | --- | --- |\n"
        "| **fresh** | FOOTER_DOC_PRIVATE_CLAIM | https://example.test/1 | 2026-07-08 |\n\n"
        "## Footer\n\n"
        "├─ 📁 Your files: 1 note\n"
    )
    sanitized_footer = library.public_markdown(footer_only_md)
    assert "Secret cluster title leaked by footer-only doc" not in sanitized_footer
    assert "FOOTER_DOC_PRIVATE_CLAIM" not in sanitized_footer
    assert "Public cluster title" in sanitized_footer
    assert "PUBLIC_FOOTER_DOC_EVIDENCE" in sanitized_footer

    bold_only_md = (
        "# last30days: Bold Signal Only\n\n"
        "**From your files:**\n\n"
        "BOLD_PRIVATE_SECRET\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Secret title in bold-only doc (score 80, 1 item, sources: Reddit)\n"
        "1. [reddit] Public bold-doc title\n"
    )
    sanitized_bold = library.public_markdown(bold_only_md)
    assert "BOLD_PRIVATE_SECRET" not in sanitized_bold
    assert "Secret title in bold-only doc" not in sanitized_bold
    assert "Public bold-doc title" in sanitized_bold


def test_high210_multiple_unmarked_drill_followups_all_stripped():
    md = (
        "# last30days: Multi Drill\n\n"
        "## Drill Follow-up\n\n"
        "FIRST_UNMARKED_DRILL_SECRET\n\n"
        "### Deeper\n\n"
        "- 1 new items after dedupe\n"
        "- Re-researched sources: Your files\n\n"
        "## Public Middle\n\n"
        "PUBLIC_MIDDLE_TEXT\n\n"
        "## Drill Follow-up\n\n"
        "SECOND_TRUNCATED_UNMARKED_DRILL_SECRET\n"
    )
    sanitized = library.public_markdown(md)
    assert "FIRST_UNMARKED_DRILL_SECRET" not in sanitized
    assert "SECOND_TRUNCATED_UNMARKED_DRILL_SECRET" not in sanitized
    assert "PUBLIC_MIDDLE_TEXT" in sanitized

    # Marked public drill survives while subsequent unmarked drills are all removed.
    md_with_public = (
        "# last30days: Mixed Drills\n\n"
        f"{library.PUBLIC_DRILL_MARKER}\n"
        "## Drill Follow-up\n\n"
        "MARKED_PUBLIC_DRILL_TEXT\n\n"
        "### Deeper\n\n"
        "- 2 new items after dedupe\n"
        "- Re-researched sources: Reddit\n\n"
        "## Drill Follow-up\n\n"
        "UNMARKED_ONE_SECRET\n\n"
        "### Deeper\n\n"
        "- 1 new items after dedupe\n"
        "- Re-researched sources: Your files\n\n"
        "## Drill Follow-up\n\n"
        "UNMARKED_TWO_NO_TRAILER_SECRET\n"
    )
    sanitized_mixed = library.public_markdown(md_with_public)
    assert "MARKED_PUBLIC_DRILL_TEXT" in sanitized_mixed
    assert "UNMARKED_ONE_SECRET" not in sanitized_mixed
    assert "UNMARKED_TWO_NO_TRAILER_SECRET" not in sanitized_mixed

    # Unmarked legacy drill containing an unclosed PRIVATE_CORPUS_START before a drill
    # trailer must strip the explicit private block FIRST so private text after the
    # trailer cannot survive drill cleanup.
    drill_with_unclosed_private = (
        "# last30days: Drill Unclosed Private\n\n"
        "## Public Intro\n\n"
        "PUBLIC_BEFORE_DRILL\n\n"
        "## Drill Follow-up\n\n"
        f"{library.PRIVATE_CORPUS_START}\n"
        "SECRET_BEFORE_TRAILER\n\n"
        "### Deeper\n\n"
        "- 1 new items after dedupe\n"
        "- Re-researched sources: Reddit\n\n"
        "---\n\n"
        "## Section After Trailer\n\n"
        "SECRET_AFTER_TRAILER_MUST_NOT_LEAK\n"
    )
    sanitized_unclosed = library.public_markdown(drill_with_unclosed_private)
    assert "PUBLIC_BEFORE_DRILL" in sanitized_unclosed
    assert "SECRET_BEFORE_TRAILER" not in sanitized_unclosed
    assert "SECRET_AFTER_TRAILER_MUST_NOT_LEAK" not in sanitized_unclosed
    assert "## Drill Follow-up" not in sanitized_unclosed

    # Closed private block placed between PUBLIC_DRILL_MARKER and an unmarked drill
    # (whether newline-separated or same-line) must NOT splice the earlier public
    # marker onto the unmarked drill.
    for sep in ("\n", "", " "):
        spliced_marker = (
            "# last30days: Marker Splice Guard\n\n"
            f"{library.PUBLIC_DRILL_MARKER}{sep}"
            f"{library.PRIVATE_CORPUS_START}\nPRIVATE_SPACER\n{library.PRIVATE_CORPUS_END}\n"
            "## Drill Follow-up\n\n"
            "UNMARKED_DRILL_AFTER_SPLICE\n"
        )
        sanitized_spliced = library.public_markdown(spliced_marker)
        assert "PRIVATE_SPACER" not in sanitized_spliced
        assert "UNMARKED_DRILL_AFTER_SPLICE" not in sanitized_spliced


def test_high210_legacy_cluster_strips_corpus_representatives_from_body(tmp_path: Path):
    md = (
        "# last30days: Cluster Privacy\n\n"
        "- Date range: 2026-06-10 to 2026-07-10\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Secret local cluster title (score 88, 3 items, sources: Your files, Reddit)\n"
        "- Uncertainty: single-source\n"
        "1. [corpus] Confidential M&A memo\n"
        "   - URL: corpus://notes/ma.md\n"
        "   - Evidence: SECRET_MA_VALUATION\n"
        "2. Unindented wrapped secret point\n"
        "2. [reddit, corpus] Blended private corroboration\n"
        "   - Evidence: BLENDED_SECRET_LINE\n"
        "3. [reddit] Public architecture discussion\n"
        "   - URL: https://reddit.com/r/arch/1\n"
        "   - Evidence: PUBLIC_ARCH_EVIDENCE\n\n"
        "### 2. Corpus only cluster title (score 75, 1 item, sources: Your files)\n"
        "- Uncertainty: single-source\n"
        "1. [Your files] Private roadmap draft\n"
        "   - URL: corpus://notes/roadmap.md\n"
        "   - Evidence: SECRET_ROADMAP_ONLY\n"
    )
    sanitized = library.public_markdown(md)
    assert "### 1. Public architecture discussion" in sanitized
    assert "PUBLIC_ARCH_EVIDENCE" in sanitized
    for secret in (
        "Secret local cluster title",
        "Confidential M&A memo",
        "corpus://notes/ma.md",
        "SECRET_MA_VALUATION",
        "Unindented wrapped secret point",
        "Blended private corroboration",
        "BLENDED_SECRET_LINE",
        "Corpus only cluster title",
        "Private roadmap draft",
        "SECRET_ROADMAP_ONLY",
    ):
        assert secret not in sanitized

    # Verify through HTML rendering and FTS index.
    memory = tmp_path / "memory"
    memory.mkdir()
    report_path = memory / "cluster-raw.md"
    report_path.write_text(md, encoding="utf-8")
    entry = library.parse_markdown_file(report_path)
    public_html = html_render.render_library_brief(entry, include_private=False)
    assert "Public architecture discussion" in public_html
    assert "SECRET_MA_VALUATION" not in public_html
    assert "SECRET_ROADMAP_ONLY" not in public_html

    db_path = tmp_path / "library.db"
    library_index.sync_library(memory, tmp_path / "no-briefs", db_path=db_path)
    for term in ("SECRET_MA_VALUATION", "BLENDED_SECRET_LINE", "SECRET_ROADMAP_ONLY"):
        assert not library_index.search(term, db_path=db_path, store_db_path=tmp_path / "none.db")
    assert library_index.search(
        "PUBLIC_ARCH_EVIDENCE", db_path=db_path, store_db_path=tmp_path / "none.db"
    )


# ---------------------------------------------------------------------------
# HIGH-211: scan_library symlink escape, FIFO, containment, and size cap
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_high211_scan_library_skips_symlinks_outside_and_inside_roots(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_md = outside / "private.md"
    secret_md.write_text(
        "# Secret Outside Note\n\nOUTSIDE_MD_LEAK_TOKEN\n", encoding="utf-8"
    )
    secret_json = outside / "creds.json"
    secret_json.write_text(
        json.dumps(
            {
                "status": "ok",
                "date": "2026-07-11",
                "top_finding": {"title": "OUTSIDE_JSON_LEAK_TOKEN", "source": "reddit"},
                "topics": [{"name": "Secrets", "new_count": 1}],
            }
        ),
        encoding="utf-8",
    )

    memory = tmp_path / "memory"
    finished = memory / "finished"
    briefs = tmp_path / "briefs"
    finished.mkdir(parents=True)
    briefs.mkdir()

    (memory / "legit-raw.md").write_text(
        "# last30days v4.5.0: Legit Topic\n\n- Date range: 2026-06-10 to 2026-07-10\n\nPublic text.\n",
        encoding="utf-8",
    )
    (memory / "escape.md").symlink_to(secret_md)
    (finished / "finished-escape.md").symlink_to(secret_md)
    (briefs / "2026-07-11.json").symlink_to(secret_json)

    entries, notes = library.scan_library(memory, briefs)
    assert [e.topic for e in entries] == ["Legit Topic"]
    assert all("OUTSIDE_MD_LEAK_TOKEN" not in e.content for e in entries)
    assert all("OUTSIDE_JSON_LEAK_TOKEN" not in e.content for e in entries)
    assert len(notes) == 3
    assert all("symlink" in note.lower() for note in notes)

    # Direct library_index helpers must also reject symlinks without dereferencing.
    db_path = tmp_path / "library.db"
    assert library_index.index_entry(memory / "escape.md", db_path=db_path) is False
    assert library_index.index_brief(memory / "escape.md", db_path=db_path) is False
    assert library_index.index_brief(briefs / "2026-07-11.json", db_path=db_path) is False

    # Symlinked finished/ directory pointing outside memory_dir must be skipped.
    memory2 = tmp_path / "memory2"
    memory2.mkdir()
    (memory2 / "finished").symlink_to(outside)
    entries2, notes2 = library.scan_library(memory2, tmp_path / "no-briefs")
    assert entries2 == []
    assert any("symlink" in note.lower() for note in notes2)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO test")
def test_high211_scan_library_skips_fifo_and_oversized_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    memory = tmp_path / "memory"
    memory.mkdir()
    os.mkfifo(memory / "blocking.md")

    (memory / "small-raw.md").write_text(
        "# Small Report\n\nFits under cap.\n", encoding="utf-8"
    )
    (memory / "huge-raw.md").write_text(
        "# Huge Report\n\n" + ("A" * 512) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(library, "MAX_LIBRARY_FILE_BYTES", 256)
    entries, notes = library.scan_library(memory, tmp_path / "no-briefs")
    assert [e.topic for e in entries] == ["Small Report"]
    assert any("blocking.md" in note for note in notes)
    assert any("huge-raw.md" in note and "byte limit" in note for note in notes)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_high211_get_or_create_library_id_rejects_symlink(tmp_path: Path):
    outside = tmp_path / "outside-id"
    outside.write_text("a" * 32 + "\n", encoding="utf-8")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / library.LIBRARY_ID_FILENAME).symlink_to(outside)

    with pytest.raises(OSError):
        library.get_or_create_library_id(memory)


@pytest.mark.skipif(
    not (library._SUPPORTS_DIR_FD and library._SUPPORTS_LISTDIR_FD),
    reason="POSIX dir_fd and fd listdir pinning",
)
def test_high211_scan_library_pins_finished_dir_fd_across_mid_scan_symlink_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief-2026-07-10.md").write_text(
        "# External Secret\n\nEVIL_OUTSIDE_SECRET_PAYLOAD\n",
        encoding="utf-8",
    )

    memory = tmp_path / "memory"
    finished = memory / "finished"
    finished.mkdir(parents=True)
    (finished / "brief-2026-07-10.md").write_text(
        "# Legit Pinned Brief\n\nLEGIT_PINNED_CONTENT\n",
        encoding="utf-8",
    )

    real_listdir = os.listdir
    swapped = False

    def swap_finished_during_listing(target):
        nonlocal swapped
        names = real_listdir(target)
        if not swapped and isinstance(target, int) and "brief-2026-07-10.md" in names:
            swapped = True
            moved = tmp_path / "finished-moved"
            finished.rename(moved)
            finished.symlink_to(outside)
        return names

    monkeypatch.setattr(library.os, "listdir", swap_finished_during_listing)

    entries, notes = library.scan_library(memory, tmp_path / "no-briefs")
    assert swapped is True
    assert notes == []
    assert [e.topic for e in entries] == ["Legit Pinned Brief"]
    assert "LEGIT_PINNED_CONTENT" in entries[0].content
    assert "EVIL_OUTSIDE_SECRET_PAYLOAD" not in entries[0].content

    # Reset and verify library_index.sync_library also never indexes the external target.
    finished.unlink()
    (tmp_path / "finished-moved").rename(finished)
    swapped = False
    db_path = tmp_path / "library.db"
    library_index.sync_library(memory, tmp_path / "no-briefs", db_path=db_path)
    assert swapped is True
    assert not library_index.search(
        "EVIL_OUTSIDE_SECRET_PAYLOAD", db_path=db_path, store_db_path=tmp_path / "none.db"
    )
    assert library_index.search(
        "LEGIT_PINNED_CONTENT", db_path=db_path, store_db_path=tmp_path / "none.db"
    )


@pytest.mark.skipif(not library._SUPPORTS_DIR_FD, reason="POSIX dir_fd pinning")
def test_high211_scan_library_pins_finished_dir_fd_with_path_based_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief-2026-07-10.md").write_text(
        "# External Secret\n\nEVIL_OUTSIDE_SECRET_PAYLOAD\n",
        encoding="utf-8",
    )
    (outside / "extra-outside-2026-07-10.md").write_text(
        "# Extra Outside Secret\n\nEXTRA_EVIL_OUTSIDE_PAYLOAD\n",
        encoding="utf-8",
    )

    memory = tmp_path / "memory"
    finished = memory / "finished"
    finished.mkdir(parents=True)
    (finished / "brief-2026-07-10.md").write_text(
        "# Legit Pinned Brief\n\nLEGIT_PINNED_CONTENT\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(library, "_SUPPORTS_LISTDIR_FD", False)
    real_listdir = os.listdir
    resolved_finished = finished.resolve()
    swapped = False

    def swap_finished_during_path_listing(target):
        nonlocal swapped
        if not swapped and isinstance(target, Path) and target == resolved_finished:
            swapped = True
            moved = tmp_path / "finished-moved-path-list"
            finished.rename(moved)
            finished.symlink_to(outside)
        return real_listdir(target)

    monkeypatch.setattr(library.os, "listdir", swap_finished_during_path_listing)

    entries, notes = library.scan_library(memory, tmp_path / "no-briefs")
    assert swapped is True
    assert [e.topic for e in entries] == ["Legit Pinned Brief"]
    assert "LEGIT_PINNED_CONTENT" in entries[0].content
    assert "EVIL_OUTSIDE_SECRET_PAYLOAD" not in entries[0].content
    assert all("EXTRA_EVIL_OUTSIDE_PAYLOAD" not in e.content for e in entries)
    assert any("extra-outside-2026-07-10.md" in note for note in notes)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink+parent traversal")
def test_high211_symlink_parent_dotdot_preserves_canonical_source_path(tmp_path: Path):
    vault = tmp_path / "vault"
    nested = vault / "nested"
    real_dir = vault / "real_dir"
    finished = real_dir / "finished"
    nested.mkdir(parents=True)
    finished.mkdir(parents=True)

    real_file = real_dir / "brief-2026-07-11.md"
    real_file.write_text(
        "# Canonical Path Topic\n\nUNIQUE_SYMLINK_DOTDOT_TOKEN\n",
        encoding="utf-8",
    )
    finished_file = finished / "finished-2026-07-12.md"
    finished_file.write_text(
        "# Finished Canonical Topic\n\nUNIQUE_FINISHED_DOTDOT_TOKEN\n",
        encoding="utf-8",
    )

    link_dir = tmp_path / "link_dir"
    link_dir.symlink_to(nested)
    via_link = link_dir / ".." / "real_dir"

    entries, notes = library.scan_library(via_link, tmp_path / "no-briefs")
    assert notes == []
    by_topic = {entry.topic: entry.source_path for entry in entries}
    assert by_topic["Canonical Path Topic"] == real_file.resolve()
    assert by_topic["Finished Canonical Topic"] == finished_file.resolve()

    db_path = tmp_path / "library.db"
    library_index.sync_library(via_link, tmp_path / "no-briefs", db_path=db_path)
    matches = library_index.search(
        "UNIQUE_SYMLINK_DOTDOT_TOKEN",
        db_path=db_path,
        store_db_path=tmp_path / "none.db",
    )
    assert len(matches) == 1
    assert matches[0].source_path == str(real_file.resolve())
    assert Path(matches[0].source_path).is_file()

    finished_matches = library_index.search(
        "UNIQUE_FINISHED_DOTDOT_TOKEN",
        db_path=db_path,
        store_db_path=tmp_path / "none.db",
    )
    assert len(finished_matches) == 1
    assert finished_matches[0].source_path == str(finished_file.resolve())

    # Direct index_entry through link_dir/../real_dir/brief.md must also record
    # the canonical target path rather than lexically collapsing link_dir/.. .
    db_entry = tmp_path / "entry.db"
    assert library_index.index_entry(via_link / "brief-2026-07-11.md", db_path=db_entry) is True
    entry_matches = library_index.search(
        "UNIQUE_SYMLINK_DOTDOT_TOKEN",
        db_path=db_entry,
        store_db_path=tmp_path / "none.db",
    )
    assert len(entry_matches) == 1
    assert entry_matches[0].source_path == str(real_file.resolve())


def test_high211_missing_dir_before_dotdot_not_redirected(tmp_path: Path):
    vault = tmp_path / "vault"
    real_dir = vault / "real_dir"
    real_dir.mkdir(parents=True)
    brief = real_dir / "brief-2026-07-13.md"
    brief.write_text(
        "# Real Dir Brief\n\nMUST_NOT_REDIRECT_MISSING_DOTDOT\n",
        encoding="utf-8",
    )

    missing_via_dotdot_dir = vault / "missing" / ".." / "real_dir"
    missing_via_dotdot_file = missing_via_dotdot_dir / "brief-2026-07-13.md"

    entries, notes = library.scan_library(missing_via_dotdot_dir, tmp_path / "no-briefs")
    assert entries == []
    assert notes == []

    db_path = tmp_path / "library.db"
    assert library_index.index_entry(missing_via_dotdot_file, db_path=db_path) is False
    assert library_index.index_brief(missing_via_dotdot_file, db_path=db_path) is False
    assert not library_index.search(
        "MUST_NOT_REDIRECT_MISSING_DOTDOT",
        db_path=db_path,
        store_db_path=tmp_path / "none.db",
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink and directory inode swap")
def test_high211_fallback_scan_rejects_finished_swap_after_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(library, "_SUPPORTS_DIR_FD", False)

    # Sub-case 1: finished/ swapped to symlink pointing outside memory_dir
    # right after _checked_subdir_stat approves it.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret-2026-07-14.md").write_text(
        "# Outside Secret\n\nOUTSIDE_SYMLINK_SWAP_SECRET\n",
        encoding="utf-8",
    )

    memory = tmp_path / "memory-symlink-swap"
    finished = memory / "finished"
    finished.mkdir(parents=True)
    (finished / "legit-2026-07-14.md").write_text(
        "# Legit Original\n\nLEGIT_ORIGINAL_BODY\n",
        encoding="utf-8",
    )

    real_checked_subdir_stat = library._checked_subdir_stat
    swapped_symlink = False

    def swap_to_symlink_after_check(subdir: Path, root: Path):
        nonlocal swapped_symlink
        st = real_checked_subdir_stat(subdir, root)
        if not swapped_symlink and subdir.name == library.FINISHED_SUBDIR and st is not None:
            swapped_symlink = True
            subdir.rename(tmp_path / "finished-orig-1")
            subdir.symlink_to(outside)
        return st

    monkeypatch.setattr(library, "_checked_subdir_stat", swap_to_symlink_after_check)
    entries, notes = library.scan_library(memory, tmp_path / "no-briefs")
    assert swapped_symlink is True
    assert all("OUTSIDE_SYMLINK_SWAP_SECRET" not in e.content for e in entries)
    assert entries == []
    assert any("Skipped" in note for note in notes)

    # Sub-case 2: finished/ swapped to a DIFFERENT real directory moved into place
    # right after _checked_subdir_stat approves it (no symlink, only inode changes).
    rogue_dir = tmp_path / "rogue-finished"
    rogue_dir.mkdir()
    (rogue_dir / "rogue-2026-07-14.md").write_text(
        "# Rogue Replaced Dir\n\nROGUE_REAL_DIR_SWAP_SECRET\n",
        encoding="utf-8",
    )

    memory2 = tmp_path / "memory-realdir-swap"
    finished2 = memory2 / "finished"
    finished2.mkdir(parents=True)
    (finished2 / "legit-2026-07-14.md").write_text(
        "# Legit Original 2\n\nLEGIT_ORIGINAL_2_BODY\n",
        encoding="utf-8",
    )

    swapped_real = False

    def swap_to_real_dir_after_check(subdir: Path, root: Path):
        nonlocal swapped_real
        st = real_checked_subdir_stat(subdir, root)
        if not swapped_real and subdir == finished2.resolve() and st is not None:
            swapped_real = True
            subdir.rename(tmp_path / "finished-orig-2")
            rogue_dir.rename(subdir)
        return st

    monkeypatch.setattr(library, "_checked_subdir_stat", swap_to_real_dir_after_check)
    entries2, notes2 = library.scan_library(memory2, tmp_path / "no-briefs")
    assert swapped_real is True
    assert all("ROGUE_REAL_DIR_SWAP_SECRET" not in e.content for e in entries2)
    assert entries2 == []
    assert any("Skipped" in note for note in notes2)

    # Sub-case 3: verify post-open _fd_real_path check catches an opened fd whose
    # canonical path escapes the checked directory even if parent lstat looks intact.
    monkeypatch.setattr(library, "_checked_subdir_stat", real_checked_subdir_stat)
    memory3 = tmp_path / "memory-fd-escape"
    finished3 = memory3 / "finished"
    finished3.mkdir(parents=True)
    legit3 = finished3 / "legit-2026-07-14.md"
    legit3.write_text("# Legit 3\n\nLEGIT_3_BODY\n", encoding="utf-8")

    # Happy path under fallback mode (no swap, real _fd_real_path) succeeds.
    ok_entries, ok_notes = library.scan_library(memory3, tmp_path / "no-briefs")
    assert ok_notes == []
    assert [e.topic for e in ok_entries] == ["Legit 3"]

    monkeypatch.setattr(
        library, "_fd_real_path", lambda _fd: outside / "secret-2026-07-14.md"
    )
    entries3, notes3 = library.scan_library(memory3, tmp_path / "no-briefs")
    assert entries3 == []
    assert any("escapes" in note.lower() for note in notes3)


def test_high211_fallback_fails_closed_when_fd_real_path_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(library, "_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(library, "_fd_real_path", lambda _fd: None)

    memory = tmp_path / "memory-unverifiable-fd"
    finished = memory / "finished"
    finished.mkdir(parents=True)
    brief = finished / "brief-2026-07-15.md"
    brief.write_text("# Unverifiable FD\n\nFALLBACK_FAIL_CLOSED_BODY\n", encoding="utf-8")

    with pytest.raises(OSError, match="Unable to verify opened file location"):
        library._read_regular_text(brief, root=memory)

    entries, notes = library.scan_library(memory, tmp_path / "no-briefs")
    assert entries == []
    assert any("Unable to verify opened file location" in note for note in notes)


# ---------------------------------------------------------------------------
# HIGH-212: Store FTS5 sanitization, injection defense, and dismissed filter
# ---------------------------------------------------------------------------


def _seed_store_findings(db_path: Path) -> dict[str, int]:
    topic = store.add_topic("FTS Security")
    run_id = store.record_run(topic["id"], source_mode="v3", status="completed")
    store.store_findings(
        run_id,
        topic["id"],
        [
            {
                "source": "reddit",
                "source_url": "https://example.test/gpt4",
                "source_title": "GPT-4 benchmarks and C++ bindings",
                "author": "alice_dev",
                "content": "Comparing GPT-4 latency, C++ bindings, and what's new at http://example.com/docs.",
                "summary": "alpha beta summary",
            },
            {
                "source": "hackernews",
                "source_url": "https://example.test/rust",
                "source_title": "Rust async runtime notes",
                "author": "bob_ops",
                "content": "gamma delta benchmarks for async runtimes.",
                "summary": "gamma delta summary",
            },
            {
                "source": "x",
                "source_url": "https://example.test/dismissed",
                "source_title": "GPT-4 dismissed rumor",
                "author": "charlie",
                "content": "GPT-4 dismissed finding that must stay hidden.",
                "summary": "dismissed summary",
            },
        ],
    )
    ids: dict[str, int] = {}
    with closing(sqlite3.connect(str(db_path))) as conn:
        for url, fid in conn.execute("SELECT source_url, id FROM findings"):
            ids[url] = fid
    store.dismiss_finding(ids["https://example.test/dismissed"])
    return ids


def test_high212_search_findings_handles_punctuation_and_filters_dismissed(isolated_store: Path):
    ids = _seed_store_findings(isolated_store)

    # Punctuation-bearing ordinary queries must never crash and must find expected active rows.
    gpt4_hits = store.search_findings("GPT-4")
    assert [row["id"] for row in gpt4_hits] == [ids["https://example.test/gpt4"]]

    cpp_hits = store.search_findings("C++")
    assert [row["id"] for row in cpp_hits] == [ids["https://example.test/gpt4"]]

    url_hits = store.search_findings("http://example.com")
    assert [row["id"] for row in url_hits] == [ids["https://example.test/gpt4"]]

    apostrophe_hits = store.search_findings("what's new")
    assert [row["id"] for row in apostrophe_hits] == [ids["https://example.test/gpt4"]]

    # Malformed / operator-only / empty queries return [] cleanly without OperationalError.
    for query in ("*", "", "   ", 'unclosed "quote', "---", "(())", '"""', "+++", ":"):
        assert store.search_findings(query) == []

    # Non-positive limit returns [].
    assert store.search_findings("GPT-4", limit=0) == []
    assert store.search_findings("GPT-4", limit=-5) == []

    # library_index store sightings also exclude dismissed findings.
    sighting_matches = library_index.search(
        "dismissed",
        db_path=isolated_store.parent / "missing-library.db",
        store_db_path=isolated_store,
    )
    assert sighting_matches == []
    active_matches = library_index.search(
        "GPT-4",
        db_path=isolated_store.parent / "missing-library.db",
        store_db_path=isolated_store,
    )
    assert len(active_matches) == 1
    assert active_matches[0].url == "https://example.test/gpt4"


def test_high212_search_findings_blocks_fts5_column_and_operator_injection(isolated_store: Path):
    _seed_store_findings(isolated_store)

    # Column filter injection: "author:alice_dev" must NOT match by author column
    # because literal token "author" does not appear in the row text.
    assert store.search_findings("author:alice_dev") == []

    # Boolean OR injection must not widen results across unrelated rows.
    assert store.search_findings("alpha OR gamma") == []

    # Boolean NOT injection must not act as FTS5 NOT.
    assert store.search_findings("alpha NOT gamma") == []

    # Proximity NEAR injection must not act as FTS5 NEAR(...).
    assert store.search_findings("NEAR(alpha beta)") == []

    # Defensive catch on OperationalError (e.g. missing FTS table).
    with closing(sqlite3.connect(str(isolated_store))) as conn:
        conn.execute("DROP TABLE findings_fts")
        conn.commit()
    assert store.search_findings("alpha") == []


# ---------------------------------------------------------------------------
# HIGH-215: library_index._connect symlink/FIFO/parent-dir/sidecar hardening
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and permissions")
def test_high215_library_index_connect_rejects_db_and_sidecar_symlinks_without_chmod(tmp_path: Path):
    victim = tmp_path / "victim.txt"
    victim.write_text("do-not-touch", encoding="utf-8")
    victim.chmod(0o644)

    memory = tmp_path / "memory"
    memory.mkdir()
    report = memory / "topic-raw.md"
    report.write_text("# Topic\n\nPublic finding.\n", encoding="utf-8")

    # Symlink to existing victim file.
    db_link = tmp_path / "library.db"
    db_link.symlink_to(victim)

    with pytest.raises(OSError):
        library_index._connect(db_link)
    with pytest.raises(OSError):
        library_index.sync_library(memory, tmp_path / "no-briefs", db_path=db_link)
    with pytest.raises(OSError):
        library_index.index_entry(report, db_path=db_link)
    with pytest.raises(OSError):
        library_index.search("Topic", db_path=db_link, store_db_path=tmp_path / "none.db")

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert victim.read_text(encoding="utf-8") == "do-not-touch"

    # Dangling symlink must not create target.
    dangling_target = tmp_path / "must-not-exist.db"
    dangling_link = tmp_path / "dangling.db"
    dangling_link.symlink_to(dangling_target)
    with pytest.raises(OSError):
        library_index._connect(dangling_link)
    assert not dangling_target.exists()

    # Symlinked SQLite sidecars (-wal, -shm, -journal) must be refused without chmodding target.
    for suffix in ("-wal", "-shm", "-journal"):
        real_db = tmp_path / f"real{suffix}.db"
        with closing(library_index._connect(real_db)):
            pass
        sidecar_victim = tmp_path / f"victim{suffix}.txt"
        sidecar_victim.write_text("sidecar-victim", encoding="utf-8")
        sidecar_victim.chmod(0o644)
        Path(f"{real_db}{suffix}").symlink_to(sidecar_victim)

        with pytest.raises(OSError):
            library_index._connect(real_db)
        assert stat.S_IMODE(sidecar_victim.stat().st_mode) == 0o644
        assert sidecar_victim.read_text(encoding="utf-8") == "sidecar-victim"


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO and directory permissions")
def test_high215_library_index_hardens_sidecars_and_rejects_fifo_and_world_writable_dir(tmp_path: Path):
    # New DB + nested private dirs get 0o700 dirs and 0o600 DB + sidecars.
    old_umask = os.umask(0o022)
    try:
        nested_db = tmp_path / "nested" / "index" / "library.db"
        with closing(library_index._connect(nested_db)):
            pass
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(nested_db.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(nested_db.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(nested_db.stat().st_mode) == 0o600

    # Loosened DB and sidecar modes are re-hardened to 0o600 on reconnect.
    sidecars = [Path(f"{nested_db}{s}") for s in ("-wal", "-shm", "-journal")]
    nested_db.chmod(0o644)
    for sc in sidecars:
        sc.write_bytes(b"")
        sc.chmod(0o644)

    with closing(library_index._connect(nested_db)):
        pass
    assert stat.S_IMODE(nested_db.stat().st_mode) == 0o600
    for sc in sidecars:
        assert stat.S_IMODE(sc.stat().st_mode) == 0o600

    # FIFO at DB or sidecar path is rejected without hanging.
    if hasattr(os, "mkfifo"):
        fifo_db = tmp_path / "fifo.db"
        os.mkfifo(fifo_db)
        with pytest.raises(OSError):
            library_index._connect(fifo_db)

    # World-writable parent directory is refused.
    world_dir = tmp_path / "world-writable"
    world_dir.mkdir()
    world_dir.chmod(0o777)
    try:
        with pytest.raises(OSError):
            library_index._connect(world_dir / "library.db")
    finally:
        world_dir.chmod(0o700)

    # Dangling symlink parent directory is refused by _ensure_private_directory.
    dangling_parent = tmp_path / "dangling-dir"
    dangling_parent.symlink_to(tmp_path / "nonexistent-dir")
    with pytest.raises(OSError):
        library_index._connect(dangling_parent / "library.db")
    assert not (tmp_path / "nonexistent-dir").exists()

    # Path beneath a symlink inside a 0o777 directory fails BEFORE creating any
    # target subdirectory or calling chmod on the symlink target.
    victim_dir = tmp_path / "victim-dir"
    victim_dir.mkdir()
    victim_dir.chmod(0o755)
    world_parent = tmp_path / "world-parent"
    world_parent.mkdir()
    world_parent.chmod(0o777)
    try:
        (world_parent / "escape-link").symlink_to(victim_dir)
        with pytest.raises(OSError):
            library_index._connect(world_parent / "escape-link" / "nested-sub" / "library.db")
        assert not (victim_dir / "nested-sub").exists()
        assert stat.S_IMODE(victim_dir.stat().st_mode) == 0o755
    finally:
        world_parent.chmod(0o700)

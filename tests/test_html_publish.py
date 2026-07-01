"""Tests for optional hosted HTML publishing."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import last30days as cli
from lib import html_publish, schema


def _report(topic: str = "OpenClaw") -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from="2026-05-01",
        range_to="2026-05-31",
        generated_at="2026-05-31T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-rerank",
        ),
        query_plan=schema.QueryPlan(
            intent="concept",
            freshness_mode="balanced_recent",
            cluster_mode="none",
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
        items_by_source={"grounding": []},
        errors_by_source={},
    )


def _diag() -> dict[str, object]:
    return {
        "available_sources": ["grounding"],
        "providers": {"google": True, "openai": False, "xai": False},
        "x_backend": None,
        "bird_installed": True,
        "bird_authenticated": False,
        "bird_username": None,
        "native_web_backend": "brave",
    }


class _FakeResponse:
    def __init__(self, payload: object):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class HtmlPublishModuleTests(unittest.TestCase):
    def test_publish_posts_html_and_password(self):
        captured = {}

        def opener(request, timeout):
            captured["timeout"] = timeout
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(
                {
                    "url": "https://example.ht-ml.app",
                    "site_id": "site_123",
                    "status": "active",
                    "update_key": "secret-update-key",
                }
            )

        result = html_publish.publish_html(
            "<html>ok</html>",
            password="share-pass",
            opener=opener,
        )

        self.assertEqual("https://api.ht-ml.app/v1/sites", captured["url"])
        self.assertEqual({"html_content": "<html>ok</html>", "password": "share-pass"}, captured["body"])
        self.assertEqual("https://example.ht-ml.app", result["url"])
        self.assertEqual("secret-update-key", result["update_key"])

    def test_publish_rejects_http_error_with_message(self):
        def opener(_request, timeout):
            raise HTTPError(
                "https://api.ht-ml.app/v1/sites",
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"message":"HTML content is required"}'),
            )

        with self.assertRaisesRegex(html_publish.HtmlPublishError, "HTML content is required"):
            html_publish.publish_html("<html></html>", opener=opener)

    def test_publish_rejects_json_response_that_is_not_an_object(self):
        def opener(_request, timeout):
            return _FakeResponse(["https://site.ht-ml.app"])

        with self.assertRaisesRegex(html_publish.HtmlPublishError, "unexpected JSON response"):
            html_publish.publish_html("<html></html>", opener=opener)


class HtmlPublishCliTests(unittest.TestCase):
    def test_publish_requires_html_emit(self):
        with mock.patch.object(sys, "argv", [
            "last30days.py",
            "OpenClaw",
            "--emit=md",
            "--publish-html",
        ]), mock.patch.object(cli.env, "get_config", return_value={}):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(2, rc)
        self.assertIn("--publish-html requires --emit=html", stderr.getvalue())

    def test_publish_writes_url_metadata_without_update_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "brief.html"
            with mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", return_value=_report()), \
                 mock.patch.object(cli, "emit_output", return_value="<html>brief</html>"), \
                 mock.patch.object(cli, "publish_rendered_html", wraps=cli.publish_rendered_html) as publish_wrapper, \
                 mock.patch("lib.html_publish.publish_html", return_value={
                     "url": "https://site.ht-ml.app",
                     "site_id": "site_123",
                     "status": "active",
                     "update_key": "secret-update-key",
                 }), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "OpenClaw",
                     "--emit=html",
                     "--output",
                     str(output_path),
                     "--publish-html",
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            publish_wrapper.assert_called_once()
            self.assertEqual("<html>brief</html>", output_path.read_text(encoding="utf-8"))
            self.assertIn("Published HTML to https://site.ht-ml.app", stderr.getvalue())
            self.assertNotIn("secret-update-key", stdout.getvalue())
            self.assertNotIn("secret-update-key", stderr.getvalue())
            metadata = json.loads((Path(str(output_path) + ".publish.json")).read_text(encoding="utf-8"))
            self.assertEqual("https://site.ht-ml.app", metadata["url"])
            self.assertNotIn("update_key", metadata)

    def test_publish_uses_password_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "brief.html"
            with mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", return_value=_report()), \
                 mock.patch.object(cli, "emit_output", return_value="<html>brief</html>"), \
                 mock.patch("lib.html_publish.publish_html", return_value={
                     "url": "https://site.ht-ml.app",
                 }) as publish_mock, \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "OpenClaw",
                     "--emit=html",
                     "--output",
                     str(output_path),
                     "--publish-html",
                 ]), \
                 mock.patch.dict(os.environ, {
                     "LAST30DAYS_SKIP_PREFLIGHT": "1",
                     "LAST30DAYS_PUBLISH_PASSWORD": "share-pass",
                 }, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertEqual("share-pass", publish_mock.call_args.kwargs["password"])

    def test_publish_metadata_failure_still_reports_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "brief.html"
            with mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", return_value=_report()), \
                 mock.patch.object(cli, "emit_output", return_value="<html>brief</html>"), \
                 mock.patch("lib.html_publish.publish_html", return_value={
                     "url": "https://site.ht-ml.app",
                     "site_id": "site_123",
                 }), \
                 mock.patch.object(cli, "_write_publish_metadata", side_effect=PermissionError("denied")), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "OpenClaw",
                     "--emit=html",
                     "--output",
                     str(output_path),
                     "--publish-html",
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertIn("Published HTML to https://site.ht-ml.app", stderr.getvalue())
            self.assertIn("Publish metadata warning", stderr.getvalue())

    def test_publish_failure_preserves_local_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "brief.html"
            with mock.patch.object(cli.env, "get_config", return_value={}), \
                 mock.patch.object(cli.pipeline, "diagnose", return_value=_diag()), \
                 mock.patch.object(cli.pipeline, "run", return_value=_report()), \
                 mock.patch.object(cli, "emit_output", return_value="<html>brief</html>"), \
                 mock.patch("lib.html_publish.publish_html", side_effect=html_publish.HtmlPublishError("timeout")), \
                 mock.patch.object(sys, "argv", [
                     "last30days.py",
                     "OpenClaw",
                     "--emit=html",
                     "--output",
                     str(output_path),
                     "--publish-html",
                 ]), \
                 mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}, clear=False):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    rc = cli.main()

            self.assertEqual(0, rc)
            self.assertEqual("<html>brief</html>", output_path.read_text(encoding="utf-8"))
            self.assertIn("HTML publish failed: timeout", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

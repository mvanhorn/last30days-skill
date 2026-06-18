"""U2: every reasoning-LLM call (planner/rerank/fun) emits one [cost] marker.

All three route through ReasoningClient.generate_json, so a single fake client
exercising generate_json proves the instrumentation point.
"""

import io
import re
import unittest
from contextlib import redirect_stderr

from lib import providers

_COST_LINE_RE = re.compile(r"^\[cost\]\s+(.*)$", re.MULTILINE)


class _FakeClient(providers.ReasoningClient):
    name = "gemini"

    def generate_text(self, model, prompt, *, tools=None, response_mime_type=None):
        return '{"ok": true}'


class _FailingClient(providers.ReasoningClient):
    name = "xai"

    def generate_text(self, model, prompt, *, tools=None, response_mime_type=None):
        raise RuntimeError("provider down")


class TestGenerateJsonCost(unittest.TestCase):
    def test_emits_one_marker_with_provider_and_model(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            _FakeClient().generate_json("gemini-3.1-flash-lite-preview", "plan this")
        markers = _COST_LINE_RE.findall(buf.getvalue())
        self.assertEqual(len(markers), 1)
        self.assertIn("provider=gemini", markers[0])
        self.assertIn("model=gemini-3.1-flash-lite-preview", markers[0])

    def test_no_marker_when_call_fails(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(RuntimeError):
                _FailingClient().generate_json("grok-4-1-fast", "rerank these")
        self.assertEqual(_COST_LINE_RE.findall(buf.getvalue()), [])


if __name__ == "__main__":
    unittest.main()

"""Tests for Codex auth integration (env.py + providers.py)."""

import base64
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import env, providers


def _make_jwt(payload: dict) -> str:
    """Build a fake JWT with the given payload (no signature verification)."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=")
    return f"{header.decode()}.{body.decode()}.fakesig"


class TestDecodeJwtPayload(unittest.TestCase):

    def test_valid_jwt(self):
        token = _make_jwt({"sub": "user123", "exp": 9999999999})
        result = env._decode_jwt_payload(token)
        self.assertEqual(result["sub"], "user123")

    def test_invalid_jwt(self):
        self.assertIsNone(env._decode_jwt_payload("not-a-jwt"))

    def test_empty_string(self):
        self.assertIsNone(env._decode_jwt_payload(""))


class TestTokenExpired(unittest.TestCase):

    def test_not_expired(self):
        token = _make_jwt({"exp": int(time.time()) + 3600})
        self.assertFalse(env._token_expired(token))

    def test_expired(self):
        token = _make_jwt({"exp": int(time.time()) - 100})
        self.assertTrue(env._token_expired(token))

    def test_no_exp_claim(self):
        token = _make_jwt({"sub": "user"})
        self.assertFalse(env._token_expired(token))


class TestExtractChatgptAccountId(unittest.TestCase):

    def test_extracts_account_id(self):
        token = _make_jwt({
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_abc123"
            }
        })
        self.assertEqual(env.extract_chatgpt_account_id(token), "acct_abc123")

    def test_missing_auth_claim(self):
        token = _make_jwt({"sub": "user"})
        self.assertIsNone(env.extract_chatgpt_account_id(token))

    def test_missing_account_id_in_claim(self):
        token = _make_jwt({
            "https://api.openai.com/auth": {"other_field": "value"}
        })
        self.assertIsNone(env.extract_chatgpt_account_id(token))


class TestGetOpenaiAuth(unittest.TestCase):

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(env, "get_codex_access_token", return_value=(None, "missing"))
    def test_missing_codex_auth_returns_none_source(self, _mock_codex):
        """Without Codex auth, OpenAI auth should remain unavailable."""
        auth = env.get_openai_auth()
        self.assertEqual(auth.source, "none")
        self.assertEqual(auth.status, "missing")
        self.assertIsNone(auth.token)
        self.assertIsNone(auth.account_id)

    @patch.dict(os.environ, {"UNRELATED_KEY": "present"}, clear=False)
    @patch.object(env, "extract_chatgpt_account_id", return_value="acct_abc123")
    @patch.object(env, "get_codex_access_token", return_value=("codex-token", "ok"))
    def test_codex_takes_priority_over_unrelated_env_values(self, _mock_codex, _mock_account):
        """Valid Codex auth should remain stable when unrelated env vars are present."""
        auth = env.get_openai_auth()
        self.assertEqual(auth.source, "codex")
        self.assertEqual(auth.status, "ok")
        self.assertEqual(auth.token, "codex-token")
        self.assertEqual(auth.account_id, "acct_abc123")

    @patch.dict(os.environ, {}, clear=True)
    @patch.object(env, "extract_chatgpt_account_id", return_value="acct_file123")
    @patch.object(env, "get_codex_access_token", return_value=("codex-token", "ok"))
    def test_codex_auth_ignores_file_style_api_key_inputs(self, _mock_codex, _mock_account):
        """Valid Codex auth should not depend on legacy file API key inputs."""
        auth = env.get_openai_auth()
        self.assertEqual(auth.source, "codex")
        self.assertEqual(auth.status, "ok")
        self.assertEqual(auth.token, "codex-token")
        self.assertEqual(auth.account_id, "acct_file123")

    def test_no_keys_returns_none_source(self):
        """No API key and no Codex auth → source=none."""
        fake_path = Path("/tmp/nonexistent_codex_auth_test.json")
        with patch.object(env, 'CODEX_AUTH_FILE', fake_path):
            # Also patch get_codex_access_token to avoid reading real auth file
            with patch.object(env, 'get_codex_access_token', return_value=(None, "missing")):
                with patch.dict(os.environ, {}, clear=True):
                    auth = env.get_openai_auth()
                    self.assertEqual(auth.source, "none")
                    self.assertIsNone(auth.token)


class TestLoadCodexAuth(unittest.TestCase):

    def test_nonexistent_file(self):
        result = env.load_codex_auth(Path("/tmp/nonexistent_codex_auth.json"))
        self.assertEqual(result, {})

    def test_valid_json(self):
        import tempfile
        data = {"tokens": {"access_token": "tok123"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            result = env.load_codex_auth(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result["tokens"]["access_token"], "tok123")


class TestRedditAvailabilityWithAuth(unittest.TestCase):

    def test_codex_auth_ok_counts_as_openai(self):
        config = {
            "OPENAI_ACCESS_TOKEN": "codex-token",
            "OPENAI_AUTH_STATUS": "ok",
            "XAI_API_KEY": None,
        }
        self.assertTrue(env.is_reddit_available(config))
        self.assertEqual("openai", env.get_reddit_source(config))

    def test_codex_auth_expired_not_counted(self):
        config = {
            "OPENAI_ACCESS_TOKEN": None,
            "OPENAI_AUTH_STATUS": "expired",
            "XAI_API_KEY": None,
        }
        self.assertFalse(env.is_reddit_available(config))
        self.assertIsNone(env.get_reddit_source(config))


class TestParseCodexStream(unittest.TestCase):

    def test_response_completed_event(self):
        """Should extract response from response.completed SSE event."""
        sse = (
            'data: {"type":"response.created","response":{"id":"r1"}}\n\n'
            'data: {"type":"response.completed","response":{"id":"r1","output":[{"type":"message","content":[{"type":"output_text","text":"hello"}]}]}}\n\n'
        )
        result = providers._parse_codex_stream(sse)
        self.assertIn("output", result)

    def test_delta_fallback(self):
        """Should reconstruct text from delta events."""
        sse = (
            'data: {"delta":"hel"}\n\n'
            'data: {"delta":"lo"}\n\n'
        )
        result = providers._parse_codex_stream(sse)
        text = providers.extract_openai_text(result)
        self.assertEqual(text, "hello")

    def test_empty_stream(self):
        result = providers._parse_codex_stream("")
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()

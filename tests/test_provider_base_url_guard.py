"""Provider endpoint overrides must never carry an API key over cleartext.

A base-URL override redirects the request that holds the provider's bearer
token, so the guard in providers.base_url_override is a credential control, not
a cosmetic URL check. These tests pin the three shapes that matter: https is
honored, remote http is refused, and loopback http stays usable for a local
gateway.
"""

from __future__ import annotations

import pytest

from lib import permission_preflight, providers


OVERRIDES = [
    ("OPENAI_BASE_URL", providers.OPENAI_RESPONSES_URL),
    ("XAI_BASE_URL", providers.XAI_RESPONSES_URL),
    ("OPENROUTER_BASE_URL", providers.OPENROUTER_URL),
]


@pytest.mark.parametrize("key,default", OVERRIDES)
def test_unset_override_uses_vendor_endpoint(key, default, monkeypatch):
    monkeypatch.delenv(key, raising=False)
    assert providers.base_url_override(key, default) == default


@pytest.mark.parametrize("key,default", OVERRIDES)
def test_https_override_is_honored(key, default, monkeypatch):
    monkeypatch.setenv(key, "https://gateway.internal/v1/responses")
    assert providers.base_url_override(key, default) == "https://gateway.internal/v1/responses"


@pytest.mark.parametrize(
    "value",
    [
        "http://attacker.example/v1",
        "http://10.0.0.5:8080/v1",
        "ftp://attacker.example/v1",
        "//attacker.example/v1",
        "attacker.example/v1",
    ],
)
def test_cleartext_or_schemeless_remote_override_is_refused(value, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_BASE_URL", value)
    resolved = providers.base_url_override("OPENAI_BASE_URL", providers.OPENAI_RESPONSES_URL)
    assert resolved == providers.OPENAI_RESPONSES_URL
    # The drop must be visible, and the warning must not be the leak itself.
    err = capsys.readouterr().err
    assert "OPENAI_BASE_URL" in err
    assert "cleartext" in err


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:4000/v1",
        "http://127.0.0.1:4000/v1",
        "http://127.0.0.2:4000/v1",
        "http://[::1]:4000/v1",
    ],
)
def test_loopback_http_override_stays_usable(value, monkeypatch):
    """A local LiteLLM/Ollama gateway never puts the key on the wire."""
    monkeypatch.setenv("OPENAI_BASE_URL", value)
    assert providers.base_url_override("OPENAI_BASE_URL", providers.OPENAI_RESPONSES_URL) == value


@pytest.mark.parametrize("key,_default", OVERRIDES)
def test_every_propagated_override_is_preflight_reportable(key, _default):
    """--preflight is the control that surfaces endpoint redirection.

    OPENROUTER_BASE_URL was propagated into os.environ but missing from this
    set, so an OpenRouter redirect was invisible in the preflight summary.
    """
    assert key in permission_preflight.ENDPOINT_OVERRIDE_KEYS

"""Credential scope: modules that attach a secret talk only to their provider.

For each module that sends an API key / token, every hard-coded http(s) host
literal must be in its reviewed allowlist. A new host in one of these modules
fails here until someone reviews where that module's credential can flow.
(User-configured base-URL overrides are validated separately in
``providers.allowed_base_url_override`` / ``hosted``.)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts" / "lib"
_HOST = re.compile(r"https?://([a-zA-Z0-9.-]+)")

# module -> hosts it may name. Display/link hosts are listed too: they appear
# as citation URLs, never as request targets for the credential.
ALLOWED = {
    "grounding.py": {"api.exa.ai", "api.parallel.ai", "api.search.brave.com", "google.serper.dev"},
    "perplexity.py": {"api.perplexity.ai", "openrouter.ai"},
    "providers.py": {"api.openai.com", "api.x.ai", "generativelanguage.googleapis.com", "openrouter.ai", "host"},
    "github.py": {"api.github.com", "github.com"},
    "truthsocial.py": {"truthsocial.com"},
    "bluesky.py": {"api.bsky.app", "bsky.app", "bsky.social"},
    "x_api.py": {"api.x.com", "x.com"},
    "xquik.py": {"xquik.com", "x.com"},
    "telegram.py": {"api.scrapecreators.com", "docs.scrapecreators.com", "t.me"},
    "transcribe.py": {"api.groq.com", "api.openai.com"},
    "html_publish.py": {"api.ht-ml.app"},
}


@pytest.mark.parametrize("module", sorted(ALLOWED))
def test_credential_module_hosts_are_reviewed(module):
    text = (LIB / module).read_text(encoding="utf-8")
    hosts = {m.group(1).rstrip(".").lower() for m in _HOST.finditer(text)}
    unexpected = sorted(hosts - ALLOWED[module])
    assert unexpected == [], f"{module} names unreviewed hosts: {unexpected}"


def test_html_publish_payload_carries_no_credentials():
    """Publishing sends the rendered HTML and an optional shared password only."""
    text = (LIB / "html_publish.py").read_text(encoding="utf-8")
    assert '{"html_content": html_content}' in text
    assert "API_KEY" not in text and "TOKEN" not in text

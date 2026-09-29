import socket
import urllib.error
from unittest.mock import patch

import pytest
from lib import http, perplexity


def test_strict_dns_attempt_budget():
    with patch("lib.http.urllib.request.urlopen", side_effect=urllib.error.URLError(socket.gaierror(-2, "DNS failure"))) as opened, patch("lib.http.time.sleep") as sleep:
        with pytest.raises(http.HTTPError):
            http.request("GET", "https://example.com", retries=1, retry_dns=False)
    assert opened.call_count == 1
    sleep.assert_not_called()


def test_added_search_uses_shared_deadline_and_single_attempt():
    with patch("lib.perplexity.http.post", return_value={"results": []}) as post:
        perplexity._search_api("public query", ("2026-09-01", "2026-09-28"),
            {"LAST30DAYS_PERPLEXITY_SEARCH_TYPE": "fast", "_adaptive_deadline": 500.0}, "dummy")
    assert post.call_args.kwargs["deadline_monotonic"] == 500.0
    assert post.call_args.kwargs["retries"] == 1
    assert post.call_args.kwargs["max_429_retries"] == 1
    assert post.call_args.kwargs["retry_dns"] is False

"""Tests for watchlist.py delivery functions (PR #86 feature)."""

from unittest.mock import patch

import pytest

import watchlist
from lib.http import HTTPError

# === Tests for _format_delivery_message() ===


def test_format_message_announce_mode():
    """Test announce mode formatting (default mode with emoji)."""
    message = watchlist._format_delivery_message(
        "Test Topic", {"new": 5, "updated": 2}, "announce"
    )

    assert "📰" in message
    assert "Test Topic" in message
    assert "5 new" in message
    assert "2 updated" in message


def test_format_message_silent_mode():
    """Test silent mode formatting (no emoji)."""
    message = watchlist._format_delivery_message(
        "Test Topic", {"new": 5, "updated": 2}, "silent"
    )

    assert "📰" not in message
    assert "Test Topic" in message
    assert "5 new" in message


def test_format_message_default_mode():
    """Test default mode formatting."""
    message = watchlist._format_delivery_message(
        "Test Topic", {"new": 5, "updated": 2}, "default"
    )

    assert "complete" in message.lower()
    assert "Test Topic" in message


def test_format_message_handles_zero_counts():
    """Test formatting with zero counts."""
    message = watchlist._format_delivery_message(
        "Test Topic", {"new": 0, "updated": 0}, "announce"
    )

    assert "0 new" in message
    assert "0 updated" in message

# === Tests for _send_slack_webhook() ===

@patch('watchlist.http.post')


def test_send_slack_webhook_format(mock_post):
    """Test that Slack webhook uses correct format."""
    watchlist._send_slack_webhook(
        "https://hooks.slack.com/services/TEST",
        "Test message"
    )

    assert mock_post.called
    call_args = mock_post.call_args

    assert call_args[0][0] == "https://hooks.slack.com/services/TEST"
    assert call_args[1]["json_data"] == {"text": "Test message"}
    assert call_args[1]["timeout"] == 10

@patch('watchlist.http.post')


def test_send_slack_webhook_raises_on_error(mock_post):
    """Test that Slack webhook raises on HTTP error."""
    mock_post.side_effect = HTTPError("HTTP 400", 400)

    with pytest.raises(HTTPError, match="HTTP 400"):
        watchlist._send_slack_webhook(
            "https://hooks.slack.com/services/TEST",
            "Test message"
        )

# === Tests for _send_generic_webhook() ===

@patch('watchlist.http.post')


def test_send_generic_webhook_format(mock_post):
    """Test that generic webhook uses correct format."""
    watchlist._send_generic_webhook(
        "https://webhook.example.com/hook",
        "Test message"
    )

    assert mock_post.called
    call_args = mock_post.call_args

    assert call_args[0][0] == "https://webhook.example.com/hook"

    json_data = call_args[1]["json_data"]
    assert json_data["message"] == "Test message"
    assert json_data["source"] == "last30days"
    assert "timestamp" in json_data
    assert isinstance(json_data["timestamp"], float)

@patch('watchlist.http.post')


def test_send_generic_webhook_raises_on_error(mock_post):
    """Test that generic webhook raises on HTTP error."""
    mock_post.side_effect = HTTPError("HTTP 500", 500)

    with pytest.raises(HTTPError, match="HTTP 500"):
        watchlist._send_generic_webhook(
            "https://webhook.example.com/hook",
            "Test message"
        )

# === Tests for _deliver_findings() ===

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_sends_when_new_greater_than_zero(mock_post, mock_get_setting):
    """Test that delivery fires when new > 0."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    assert mock_post.called

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_skips_when_new_is_zero(mock_post, mock_get_setting):
    """Test that delivery is skipped when new=0."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 0, "updated": 5})

    assert not mock_post.called

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_skips_when_channel_empty(mock_post, mock_get_setting):
    """Test that delivery is skipped when delivery_channel is empty."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    assert not mock_post.called

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_uses_slack_format_for_slack_urls(mock_post, mock_get_setting):
    """Test that Slack URLs trigger Slack-specific format."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://hooks.slack.com/services/TEST",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    json_data = mock_post.call_args[1]["json_data"]
    assert "text" in json_data
    assert "Test Topic" in json_data["text"]

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_uses_generic_format_for_other_urls(mock_post, mock_get_setting):
    """Test that non-Slack URLs trigger generic format."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    json_data = mock_post.call_args[1]["json_data"]
    assert "message" in json_data
    assert "source" in json_data
    assert "timestamp" in json_data

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_handles_failure_gracefully(mock_post, mock_get_setting, capsys):
    """Test that delivery failures don't crash the process."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)

    mock_post.side_effect = HTTPError("HTTP 500", 500)

    # Should not raise, just log to stderr
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    captured = capsys.readouterr()
    assert "Delivery failed" in captured.err

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_rejects_non_https_slack_substring(mock_post, mock_get_setting, capsys):
    """A non-https channel that merely contains 'hooks.slack.com' must not be
    sent. The old substring match POSTed it in cleartext to whatever host the
    URL actually named."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "http://evil.example/hooks.slack.com",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    assert not mock_post.called
    assert "https://" in capsys.readouterr().err

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_slack_match_is_exact_host(mock_post, mock_get_setting):
    """An https URL with 'hooks.slack.com' only in the path routes as generic,
    not Slack — the match is on the exact hostname, not a substring."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/hooks.slack.com/x",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    json_data = mock_post.call_args[1]["json_data"]
    assert "message" in json_data  # generic payload shape
    assert "text" not in json_data  # not the Slack {"text": ...} shape

@patch('watchlist.store.get_setting')
@patch('watchlist.http.post')


def test_deliver_findings_respects_delivery_mode(mock_post, mock_get_setting):
    """Test that different delivery modes produce different messages."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    announce_message = mock_post.call_args[1]["json_data"]["message"]
    assert "📰" in announce_message

    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "silent",
    }.get(key, default)

    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})

    silent_message = mock_post.call_args[1]["json_data"]["message"]
    assert "📰" not in silent_message

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

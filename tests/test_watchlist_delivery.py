"""Tests for watchlist.py delivery functions (PR #86 feature)."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import watchlist


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

@patch('watchlist.requests')
def test_send_slack_webhook_format(mock_requests):
    """Test that Slack webhook uses correct format."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_requests.post.return_value = mock_response
    
    watchlist._send_slack_webhook(
        "https://hooks.slack.com/services/TEST",
        "Test message"
    )
    
    # Verify POST was called with correct format
    assert mock_requests.post.called
    call_args = mock_requests.post.call_args
    
    assert call_args[0][0] == "https://hooks.slack.com/services/TEST"
    assert call_args[1]["json"] == {"text": "Test message"}
    assert call_args[1]["headers"]["Content-Type"] == "application/json"
    assert call_args[1]["timeout"] == 10


@patch('watchlist.requests')
def test_send_slack_webhook_raises_on_error(mock_requests):
    """Test that Slack webhook raises on HTTP error."""
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.raise_for_status.side_effect = Exception("HTTP 400")
    mock_requests.post.return_value = mock_response
    
    with pytest.raises(Exception, match="HTTP 400"):
        watchlist._send_slack_webhook(
            "https://hooks.slack.com/services/TEST",
            "Test message"
        )


# === Tests for _send_generic_webhook() ===

@patch('watchlist.requests')
def test_send_generic_webhook_format(mock_requests):
    """Test that generic webhook uses correct format."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_requests.post.return_value = mock_response
    
    watchlist._send_generic_webhook(
        "https://webhook.example.com/hook",
        "Test message"
    )
    
    # Verify POST was called with correct format
    assert mock_requests.post.called
    call_args = mock_requests.post.call_args
    
    assert call_args[0][0] == "https://webhook.example.com/hook"
    
    json_data = call_args[1]["json"]
    assert json_data["message"] == "Test message"
    assert json_data["source"] == "last30days"
    assert "timestamp" in json_data
    assert isinstance(json_data["timestamp"], float)


@patch('watchlist.requests')
def test_send_generic_webhook_raises_on_error(mock_requests):
    """Test that generic webhook raises on HTTP error."""
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = Exception("HTTP 500")
    mock_requests.post.return_value = mock_response
    
    with pytest.raises(Exception, match="HTTP 500"):
        watchlist._send_generic_webhook(
            "https://webhook.example.com/hook",
            "Test message"
        )


# === Tests for _deliver_findings() ===

@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_sends_when_new_greater_than_zero(mock_requests, mock_get_setting):
    """Test that delivery fires when new > 0."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_requests.post.return_value = mock_response
    
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    # Verify webhook was called
    assert mock_requests.post.called


@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_skips_when_new_is_zero(mock_requests, mock_get_setting):
    """Test that delivery is skipped when new=0."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)
    
    watchlist._deliver_findings("Test Topic", {"new": 0, "updated": 5})
    
    # Verify webhook was NOT called
    assert not mock_requests.post.called


@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_skips_when_channel_empty(mock_requests, mock_get_setting):
    """Test that delivery is skipped when delivery_channel is empty."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "",
        "delivery_mode": "announce",
    }.get(key, default)
    
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    # Verify webhook was NOT called
    assert not mock_requests.post.called


@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_uses_slack_format_for_slack_urls(mock_requests, mock_get_setting):
    """Test that Slack URLs trigger Slack-specific format."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://hooks.slack.com/services/TEST",
        "delivery_mode": "announce",
    }.get(key, default)
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_requests.post.return_value = mock_response
    
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    # Verify Slack format was used
    call_args = mock_requests.post.call_args
    json_data = call_args[1]["json"]
    assert "text" in json_data
    assert "Test Topic" in json_data["text"]


@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_uses_generic_format_for_other_urls(mock_requests, mock_get_setting):
    """Test that non-Slack URLs trigger generic format."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_requests.post.return_value = mock_response
    
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    # Verify generic format was used
    call_args = mock_requests.post.call_args
    json_data = call_args[1]["json"]
    assert "message" in json_data
    assert "source" in json_data
    assert "timestamp" in json_data


@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_handles_failure_gracefully(mock_requests, mock_get_setting, capsys):
    """Test that delivery failures don't crash the process."""
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)
    
    # Simulate HTTP error
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = Exception("HTTP 500")
    mock_requests.post.return_value = mock_response
    
    # Should not raise, just log to stderr
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    # Verify error was logged
    captured = capsys.readouterr()
    assert "Delivery failed" in captured.err


@patch('watchlist.store.get_setting')
@patch('watchlist.requests')
def test_deliver_findings_respects_delivery_mode(mock_requests, mock_get_setting):
    """Test that different delivery modes produce different messages."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_requests.post.return_value = mock_response
    
    # Test announce mode
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "announce",
    }.get(key, default)
    
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    announce_message = mock_requests.post.call_args[1]["json"]["message"]
    assert "📰" in announce_message
    
    # Test silent mode
    mock_get_setting.side_effect = lambda key, default="": {
        "delivery_channel": "https://webhook.example.com/test",
        "delivery_mode": "silent",
    }.get(key, default)
    
    watchlist._deliver_findings("Test Topic", {"new": 5, "updated": 2})
    
    silent_message = mock_requests.post.call_args[1]["json"]["message"]
    assert "📰" not in silent_message


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

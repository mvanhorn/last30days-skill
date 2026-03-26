"""Tests for lib/errors.py - Error handling utilities."""

import logging
import time
import unittest
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from lib.errors import (
    # Exceptions
    Last30DaysError,
    NetworkError,
    APIError,
    RateLimitError,
    TimeoutError,
    AuthError,
    ConfigError,
    ValidationError,
    ParsingError,
    DependencyError,
    SourceUnavailableError,
    # Enums
    ErrorSeverity,
    ErrorCategory,
    # Context
    ErrorContext,
    # Retry
    with_retry,
    with_retry_async,
    RetryConfig,
    # Utilities
    ErrorAggregator,
    wrap_exception,
    safe_call,
    http_error_to_last30days,
    # Logging
    get_logger,
)


class TestErrorContext(unittest.TestCase):
    """Tests for ErrorContext dataclass."""
    
    def test_basic_context(self):
        """Test basic context creation."""
        ctx = ErrorContext(source="reddit", operation="search")
        self.assertEqual(ctx.source, "reddit")
        self.assertEqual(ctx.operation, "search")
        self.assertIsNotNone(ctx.timestamp)
        self.assertEqual(ctx.extra, {})
    
    def test_context_with_extra(self):
        """Test context with extra data."""
        ctx = ErrorContext(
            source="api",
            operation="fetch",
            extra={"url": "https://example.com", "status": 500}
        )
        self.assertEqual(ctx.extra["url"], "https://example.com")
        self.assertEqual(ctx.extra["status"], 500)
    
    def test_context_to_dict(self):
        """Test serialization."""
        ctx = ErrorContext(
            source="test",
            operation="op",
            request_id="abc123",
            extra={"key": "value"}
        )
        d = ctx.to_dict()
        self.assertEqual(d["source"], "test")
        self.assertEqual(d["operation"], "op")
        self.assertEqual(d["request_id"], "abc123")
        self.assertEqual(d["extra"]["key"], "value")


class TestLast30DaysError(unittest.TestCase):
    """Tests for base Last30DaysError exception."""
    
    def test_basic_error(self):
        """Test basic error creation."""
        err = Last30DaysError("Something went wrong")
        self.assertEqual(err.message, "Something went wrong")
        self.assertEqual(err.severity, ErrorSeverity.MEDIUM)
        self.assertEqual(err.category, ErrorCategory.INTERNAL)
    
    def test_error_with_context(self):
        """Test error with explicit context."""
        ctx = ErrorContext(source="test", operation="test_op")
        err = Last30DaysError("Error!", context=ctx)
        self.assertEqual(err.context.source, "test")
        self.assertEqual(err.context.operation, "test_op")
    
    def test_error_with_context_kwargs(self):
        """Test error with context built from kwargs."""
        err = Last30DaysError(
            "Error!",
            source="reddit",
            operation="search",
            url="https://reddit.com/r/test"
        )
        self.assertEqual(err.context.source, "reddit")
        self.assertEqual(err.context.operation, "search")
        self.assertEqual(err.context.extra["url"], "https://reddit.com/r/test")
    
    def test_error_severity(self):
        """Test error with custom severity."""
        err = Last30DaysError("Fatal!", severity=ErrorSeverity.FATAL)
        self.assertEqual(err.severity, ErrorSeverity.FATAL)
    
    def test_error_recoverable(self):
        """Test recoverable flag."""
        err = Last30DaysError("Recoverable", recoverable=True)
        self.assertTrue(err.recoverable)
        
        err2 = Last30DaysError("Not recoverable")
        self.assertFalse(err2.recoverable)
    
    def test_error_suggestion(self):
        """Test suggestion field."""
        err = Last30DaysError("Missing key", suggestion="Set API_KEY env var")
        self.assertEqual(err.suggestion, "Set API_KEY env var")
    
    def test_to_dict(self):
        """Test serialization."""
        err = Last30DaysError(
            "Test error",
            severity=ErrorSeverity.HIGH,
            category=ErrorCategory.API,
            recoverable=True,
            suggestion="Try again"
        )
        d = err.to_dict()
        self.assertEqual(d["message"], "Test error")
        self.assertEqual(d["severity"], "high")
        self.assertEqual(d["category"], "api")
        self.assertTrue(d["recoverable"])
        self.assertEqual(d["suggestion"], "Try again")
    
    def test_str_representation(self):
        """Test string representation."""
        err = Last30DaysError("Test", suggestion="Fix it")
        s = str(err)
        self.assertIn("Test", s)
        self.assertIn("Fix it", s)
    
    def test_cause_chain(self):
        """Test exception chaining."""
        original = ValueError("Original error")
        err = Last30DaysError("Wrapped", cause=original)
        self.assertIs(err.__cause__, original)


class TestSpecificExceptions(unittest.TestCase):
    """Tests for specific exception types."""
    
    def test_network_error(self):
        """Test NetworkError defaults."""
        err = NetworkError("Connection refused")
        self.assertEqual(err.category, ErrorCategory.NETWORK)
        self.assertTrue(err.recoverable)
        self.assertIn("network", err.suggestion.lower())
    
    def test_api_error(self):
        """Test APIError with status code."""
        err = APIError(
            "API failed",
            status_code=500,
            api_name="Reddit",
            response_body='{"error": "Internal error"}'
        )
        self.assertEqual(err.status_code, 500)
        self.assertEqual(err.api_name, "Reddit")
        self.assertIsNotNone(err.response_body)
        self.assertEqual(err.context.extra["status_code"], 500)
        
        # Test str representation
        s = str(err)
        self.assertIn("API failed", s)
        self.assertIn("Reddit", s)
        self.assertIn("500", s)
    
    def test_rate_limit_error(self):
        """Test RateLimitError with retry_after."""
        err = RateLimitError(
            "Too many requests",
            retry_after=60.0,
            api_name="Twitter",
            status_code=429
        )
        self.assertEqual(err.retry_after, 60.0)
        self.assertEqual(err.context.extra["retry_after"], 60.0)
        
        # Test str representation
        s = str(err)
        self.assertIn("retry after 60", s)
    
    def test_timeout_error(self):
        """Test TimeoutError."""
        err = TimeoutError(
            "Request timed out",
            timeout_seconds=30.0,
            operation="fetch_reddit"
        )
        self.assertEqual(err.timeout_seconds, 30.0)
        self.assertEqual(err.operation, "fetch_reddit")
        
        s = str(err)
        self.assertIn("30.0s", s)
    
    def test_auth_error(self):
        """Test AuthError defaults."""
        err = AuthError("Invalid API key")
        self.assertEqual(err.category, ErrorCategory.AUTH)
        self.assertFalse(err.recoverable)
    
    def test_config_error(self):
        """Test ConfigError defaults."""
        err = ConfigError("Missing .env file")
        self.assertEqual(err.category, ErrorCategory.CONFIG)
        self.assertFalse(err.recoverable)
    
    def test_validation_error(self):
        """Test ValidationError defaults."""
        err = ValidationError("Invalid input")
        self.assertEqual(err.category, ErrorCategory.VALIDATION)
    
    def test_parsing_error(self):
        """Test ParsingError defaults."""
        err = ParsingError("Failed to parse JSON")
        self.assertEqual(err.category, ErrorCategory.PARSING)
        self.assertTrue(err.recoverable)
    
    def test_dependency_error(self):
        """Test DependencyError with install command."""
        err = DependencyError(
            "yt-dlp not found",
            dependency_name="yt-dlp",
            install_command="brew install yt-dlp"
        )
        self.assertEqual(err.dependency_name, "yt-dlp")
        self.assertIn("brew install yt-dlp", err.suggestion)
    
    def test_source_unavailable_error(self):
        """Test SourceUnavailableError."""
        err = SourceUnavailableError(
            "Reddit API down",
            source_name="reddit",
            reason="API returned 503"
        )
        self.assertEqual(err.source_name, "reddit")
        self.assertEqual(err.reason, "API returned 503")
        self.assertTrue(err.recoverable)


class TestRetryDecorator(unittest.TestCase):
    """Tests for @with_retry decorator."""
    
    def test_no_retry_on_success(self):
        """Test that successful calls don't retry."""
        call_count = [0]
        
        @with_retry(max_retries=3)
        def successful_func():
            call_count[0] += 1
            return "success"
        
        result = successful_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 1)
    
    def test_retry_on_specified_exception(self):
        """Test retry on specified exception type."""
        call_count = [0]
        
        @with_retry(max_retries=3, backoff_factor=0.1, retry_on=(ValueError,))
        def flaky_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("Not yet")
            return "success"
        
        result = flaky_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 3)
    
    def test_no_retry_on_unspecified_exception(self):
        """Test that non-retryable exceptions propagate immediately."""
        call_count = [0]
        
        @with_retry(max_retries=3, retry_on=(ValueError,))
        def wrong_error_func():
            call_count[0] += 1
            raise TypeError("Wrong type")
        
        with self.assertRaises(TypeError):
            wrong_error_func()
        
        self.assertEqual(call_count[0], 1)
    
    def test_max_retries_exceeded(self):
        """Test that max retries is respected."""
        call_count = [0]
        
        @with_retry(max_retries=2, backoff_factor=0.1, retry_on=(ValueError,))
        def always_fails():
            call_count[0] += 1
            raise ValueError("Always fails")
        
        with self.assertRaises(ValueError):
            always_fails()
        
        self.assertEqual(call_count[0], 3)  # Initial + 2 retries
    
    def test_retry_with_rate_limit_error(self):
        """Test that RateLimitError.retry_after is respected."""
        call_count = [0]
        start_time = [None]
        
        @with_retry(max_retries=2, backoff_factor=0.1, max_backoff=100, retry_on=(RateLimitError,))
        def rate_limited():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RateLimitError("Rate limited", retry_after=0.1)
            return "success"
        
        start_time[0] = time.time()
        result = rate_limited()
        
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 2)
    
    def test_retry_callbacks(self):
        """Test retry callbacks."""
        retry_calls = []
        success_calls = []
        failure_calls = []
        
        def on_retry(attempt, exc):
            retry_calls.append((attempt, str(exc)))
        
        def on_success(attempts):
            success_calls.append(attempts)
        
        def on_failure(exc):
            failure_calls.append(str(exc))
        
        @with_retry(
            max_retries=3,
            backoff_factor=0.1,
            retry_on=(ValueError,),
            on_retry=on_retry,
            on_success=on_success,
            on_failure=on_failure,
        )
        def eventually_succeeds():
            if len(retry_calls) < 2:
                raise ValueError(f"Attempt {len(retry_calls) + 1}")
            return "ok"
        
        result = eventually_succeeds()
        self.assertEqual(result, "ok")
        self.assertEqual(len(retry_calls), 2)
        self.assertEqual(len(success_calls), 1)
        self.assertEqual(len(failure_calls), 0)
    
    def test_failure_callback(self):
        """Test failure callback is called when retries exhausted."""
        failure_calls = []
        
        def on_failure(exc):
            failure_calls.append(type(exc).__name__)
        
        @with_retry(
            max_retries=2,
            backoff_factor=0.1,
            retry_on=(ValueError,),
            on_failure=on_failure,
        )
        def always_fails():
            raise ValueError("Always")
        
        with self.assertRaises(ValueError):
            always_fails()
        
        self.assertEqual(len(failure_calls), 1)
        self.assertEqual(failure_calls[0], "ValueError")


class TestErrorAggregator(unittest.TestCase):
    """Tests for ErrorAggregator."""
    
    def test_empty_aggregator(self):
        """Test empty aggregator."""
        agg = ErrorAggregator()
        self.assertFalse(agg.has_errors)
        self.assertFalse(agg.has_fatal)
    
    def test_add_errors(self):
        """Test adding errors."""
        agg = ErrorAggregator()
        agg.add(APIError("Error 1", status_code=500))
        agg.add(NetworkError("Error 2"))
        
        self.assertTrue(agg.has_errors)
        self.assertEqual(len(agg.errors), 2)
    
    def test_wrap_non_last30days_error(self):
        """Test wrapping standard exceptions."""
        agg = ErrorAggregator()
        agg.add(ValueError("Not a Last30DaysError"))
        
        self.assertTrue(agg.has_errors)
        self.assertIsInstance(agg.errors[0], Last30DaysError)
    
    def test_has_fatal(self):
        """Test fatal error detection."""
        agg = ErrorAggregator()
        agg.add(APIError("Not fatal"))
        agg.add(Last30DaysError("Fatal", severity=ErrorSeverity.FATAL))
        
        self.assertTrue(agg.has_fatal)
    
    def test_to_exception_single(self):
        """Test converting single error to exception."""
        agg = ErrorAggregator()
        err = APIError("Single error", status_code=500)
        agg.add(err)
        
        result = agg.to_exception()
        self.assertIs(result, err)
    
    def test_to_exception_multiple(self):
        """Test converting multiple errors to single exception."""
        agg = ErrorAggregator(title="Multiple API errors")
        agg.add(APIError("Error 1", status_code=500))
        agg.add(NetworkError("Error 2"))
        
        result = agg.to_exception()
        self.assertIsInstance(result, Last30DaysError)
        self.assertIn("Multiple API errors", result.message)
        self.assertEqual(result.context.extra["error_count"], 2)


class TestUtilityFunctions(unittest.TestCase):
    """Tests for utility functions."""
    
    def test_wrap_exception(self):
        """Test wrap_exception utility."""
        original = ValueError("Original")
        wrapped = wrap_exception(original, APIError, "Wrapped message", source="test")
        
        self.assertIsInstance(wrapped, APIError)
        self.assertEqual(wrapped.message, "Wrapped message")
        self.assertIs(wrapped.__cause__, original)
    
    def test_safe_call_success(self):
        """Test safe_call with successful function."""
        def add(a, b):
            return a + b
        
        result = safe_call(add, 1, 2)
        self.assertEqual(result, 3)
    
    def test_safe_call_error(self):
        """Test safe_call with failing function."""
        def raises():
            raise ValueError("Error")
        
        result = safe_call(raises, default="fallback")
        self.assertEqual(result, "fallback")
    
    def test_safe_call_error_handler(self):
        """Test safe_call with error handler."""
        captured = []
        
        def handler(e):
            captured.append(type(e).__name__)
        
        def raises():
            raise ValueError("Error")
        
        result = safe_call(raises, default=None, error_handler=handler)
        self.assertEqual(result, None)
        self.assertEqual(captured, ["ValueError"])


class TestHTTPErrorConversion(unittest.TestCase):
    """Tests for http_error_to_last30days function."""
    
    def test_rate_limit_conversion(self):
        """Test converting HTTP 429 to RateLimitError."""
        from lib import http
        
        http_err = http.HTTPError("Rate limited", status_code=429, body="{}")
        result = http_error_to_last30days(http_err, source="reddit", operation="search")
        
        self.assertIsInstance(result, RateLimitError)
        self.assertEqual(result.status_code, 429)
    
    def test_auth_error_conversion(self):
        """Test converting HTTP 401/403 to AuthError."""
        from lib import http
        
        http_err = http.HTTPError("Unauthorized", status_code=401, body="{}")
        result = http_error_to_last30days(http_err, source="api", operation="fetch")
        
        self.assertIsInstance(result, AuthError)
    
    def test_server_error_conversion(self):
        """Test converting HTTP 5xx to APIError."""
        from lib import http
        
        http_err = http.HTTPError("Internal error", status_code=500, body="{}")
        result = http_error_to_last30days(http_err, source="server", operation="request")
        
        self.assertIsInstance(result, APIError)
        self.assertTrue(result.recoverable)
    
    def test_client_error_conversion(self):
        """Test converting HTTP 4xx to APIError."""
        from lib import http
        
        http_err = http.HTTPError("Bad request", status_code=400, body="{}")
        result = http_error_to_last30days(http_err, source="client", operation="request")
        
        self.assertIsInstance(result, APIError)
        self.assertFalse(result.recoverable)


if __name__ == "__main__":
    unittest.main()
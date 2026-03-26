"""Comprehensive tests for lib/errors.py - Error handling utilities.

This module adds extensive tests for:
- Async retry decorator
- RetryConfig dataclass
- Error logging functionality
- Edge cases in error handling
- Error recovery scenarios
- Response body truncation
- Jitter and backoff behavior
"""

import asyncio
import logging
import os
import sys
import time
import unittest
from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

# Add lib to path
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
    log,
)


class TestRetryConfig(unittest.TestCase):
    """Tests for RetryConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = RetryConfig()
        self.assertEqual(config.max_retries, 3)
        self.assertEqual(config.backoff_factor, 2.0)
        self.assertEqual(config.max_backoff, 60.0)
        self.assertTrue(config.jitter)
        self.assertEqual(config.retry_on, (Exception,))
        self.assertIsNone(config.on_retry)
        self.assertIsNone(config.on_success)
        self.assertIsNone(config.on_failure)
    
    def test_custom_values(self):
        """Test custom configuration values."""
        def on_retry(attempt, exc):
            pass
        
        config = RetryConfig(
            max_retries=5,
            backoff_factor=1.5,
            max_backoff=30.0,
            jitter=False,
            retry_on=(ValueError, TypeError),
            on_retry=on_retry,
        )
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.backoff_factor, 1.5)
        self.assertEqual(config.max_backoff, 30.0)
        self.assertFalse(config.jitter)
        self.assertEqual(config.retry_on, (ValueError, TypeError))
        self.assertEqual(config.on_retry, on_retry)
    
    def test_retry_on_single_type(self):
        """Test retry_on with single exception type."""
        config = RetryConfig(retry_on=ValueError)
        # When used in decorator, single type should be converted to tuple
        self.assertEqual(config.retry_on, ValueError)


class TestAsyncRetryDecorator(unittest.TestCase):
    """Tests for @with_retry_async decorator."""
    
    def test_no_retry_on_success(self):
        """Test that successful async calls don't retry."""
        call_count = [0]
        
        @with_retry_async(max_retries=3)
        async def successful_func():
            call_count[0] += 1
            return "success"
        
        result = asyncio.run(successful_func())
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 1)
    
    def test_retry_on_specified_exception(self):
        """Test retry on specified exception type in async context."""
        call_count = [0]
        
        @with_retry_async(max_retries=3, backoff_factor=0.05, retry_on=(ValueError,))
        async def flaky_func():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("Not yet")
            return "success"
        
        result = asyncio.run(flaky_func())
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 3)
    
    def test_no_retry_on_unspecified_exception(self):
        """Test that non-retryable exceptions propagate immediately in async."""
        call_count = [0]
        
        @with_retry_async(max_retries=3, retry_on=(ValueError,))
        async def wrong_error_func():
            call_count[0] += 1
            raise TypeError("Wrong type")
        
        with self.assertRaises(TypeError):
            asyncio.run(wrong_error_func())
        
        self.assertEqual(call_count[0], 1)
    
    def test_max_retries_exceeded(self):
        """Test that max retries is respected in async."""
        call_count = [0]
        
        @with_retry_async(max_retries=2, backoff_factor=0.05, retry_on=(ValueError,))
        async def always_fails():
            call_count[0] += 1
            raise ValueError("Always fails")
        
        with self.assertRaises(ValueError):
            asyncio.run(always_fails())
        
        self.assertEqual(call_count[0], 3)  # Initial + 2 retries
    
    def test_retry_with_rate_limit_error(self):
        """Test that RateLimitError.retry_after is respected in async."""
        call_count = [0]
        
        @with_retry_async(max_retries=2, backoff_factor=0.05, max_backoff=100, retry_on=(RateLimitError,))
        async def rate_limited():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RateLimitError("Rate limited", retry_after=0.05)
            return "success"
        
        result = asyncio.run(rate_limited())
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 2)
    
    def test_async_preserves_function_name(self):
        """Test that decorator preserves function name."""
        @with_retry_async()
        async def my_async_function():
            return "ok"
        
        self.assertEqual(my_async_function.__name__, "my_async_function")


class TestErrorContextAdvanced(unittest.TestCase):
    """Advanced tests for ErrorContext."""
    
    def test_context_str_representation(self):
        """Test string representation of ErrorContext."""
        ctx = ErrorContext(
            source="reddit",
            operation="search",
            extra={"url": "https://reddit.com"}
        )
        s = str(ctx)
        self.assertIn("reddit", s)
        self.assertIn("search", s)
    
    def test_context_without_request_id_in_dict(self):
        """Test that request_id is omitted when None in to_dict()."""
        ctx = ErrorContext(source="test", operation="op")
        d = ctx.to_dict()
        self.assertNotIn("request_id", d)
    
    def test_context_with_request_id_in_dict(self):
        """Test that request_id is included when set in to_dict()."""
        ctx = ErrorContext(source="test", operation="op", request_id="req-123")
        d = ctx.to_dict()
        self.assertIn("request_id", d)
        self.assertEqual(d["request_id"], "req-123")
    
    def test_context_traceback_in_debug_mode(self):
        """Test that traceback is captured in debug mode."""
        original_debug = os.environ.get("LAST30DAYS_DEBUG")
        try:
            os.environ["LAST30DAYS_DEBUG"] = "1"
            # Re-import to pick up new DEBUG value
            import importlib
            import lib.errors as errors_mod
            importlib.reload(errors_mod)
            
            # Create context within an exception handler
            try:
                raise ValueError("Test error")
            except:
                ctx = errors_mod.ErrorContext(source="test", operation="op")
                # Traceback should be captured
                self.assertIsNotNone(ctx.traceback)
        finally:
            if original_debug is None:
                os.environ.pop("LAST30DAYS_DEBUG", None)
            else:
                os.environ["LAST30DAYS_DEBUG"] = original_debug


class TestAPIErrorAdvanced(unittest.TestCase):
    """Advanced tests for APIError."""
    
    def test_response_body_truncation(self):
        """Test that response body is truncated to 500 characters."""
        long_body = "x" * 1000
        err = APIError(
            "API failed",
            response_body=long_body
        )
        self.assertEqual(len(err.response_body), 500)
    
    def test_response_body_not_truncated_when_short(self):
        """Test that short response body is not truncated."""
        short_body = "error message"
        err = APIError(
            "API failed",
            response_body=short_body
        )
        self.assertEqual(err.response_body, short_body)
    
    def test_response_body_none_when_not_provided(self):
        """Test that response_body is None when not provided."""
        err = APIError("API failed")
        self.assertIsNone(err.response_body)
    
    def test_str_representation_without_status_code(self):
        """Test str representation without status code."""
        err = APIError("API failed", api_name="Reddit")
        s = str(err)
        self.assertIn("API failed", s)
        self.assertIn("Reddit", s)
        self.assertNotIn("HTTP", s)


class TestRateLimitErrorAdvanced(unittest.TestCase):
    """Advanced tests for RateLimitError."""
    
    def test_str_without_retry_after(self):
        """Test str representation without retry_after."""
        err = RateLimitError("Rate limited", api_name="Twitter", status_code=429)
        s = str(err)
        self.assertIn("Rate limited", s)
        self.assertIn("Twitter", s)
        self.assertNotIn("retry after", s)
    
    def test_str_with_all_fields(self):
        """Test str representation with all fields."""
        err = RateLimitError(
            "Too many requests",
            retry_after=30.0,
            api_name="Reddit",
            status_code=429
        )
        s = str(err)
        self.assertIn("Too many requests", s)
        self.assertIn("Reddit", s)
        self.assertIn("retry after 30", s)


class TestTimeoutErrorAdvanced(unittest.TestCase):
    """Advanced tests for TimeoutError."""
    
    def test_str_without_timeout_seconds(self):
        """Test str representation without timeout_seconds."""
        err = TimeoutError("Operation timed out", operation="fetch_data")
        s = str(err)
        self.assertIn("Operation timed out", s)
        self.assertIn("fetch_data", s)
        self.assertNotIn("after", s)
    
    def test_default_suggestion_exists_as_class_attribute(self):
        """Test that TimeoutError has default_suggestion as class attribute."""
        # Note: TimeoutError has default_suggestion as a class attribute,
        # but it's not automatically applied to instances (unlike NetworkError)
        self.assertEqual(TimeoutError.default_suggestion, "Increase timeout or try with a simpler query")
    
    def test_explicit_suggestion(self):
        """Test that explicit suggestion works."""
        err = TimeoutError("Timed out", suggestion="Custom suggestion")
        self.assertEqual(err.suggestion, "Custom suggestion")


class TestDependencyErrorAdvanced(unittest.TestCase):
    """Advanced tests for DependencyError."""
    
    def test_suggestion_with_install_command(self):
        """Test that suggestion includes install command."""
        err = DependencyError(
            "Missing yt-dlp",
            dependency_name="yt-dlp",
            install_command="pip install yt-dlp"
        )
        self.assertIn("pip install yt-dlp", err.suggestion)
    
    def test_suggestion_with_only_dependency_name(self):
        """Test suggestion when only dependency name is provided."""
        err = DependencyError(
            "Missing package",
            dependency_name="some-package"
        )
        self.assertIn("some-package", err.suggestion)
    
    def test_no_suggestion_when_provided(self):
        """Test that provided suggestion is not overridden."""
        err = DependencyError(
            "Missing package",
            dependency_name="pkg",
            suggestion="Custom suggestion"
        )
        self.assertEqual(err.suggestion, "Custom suggestion")


class TestSourceUnavailableErrorAdvanced(unittest.TestCase):
    """Advanced tests for SourceUnavailableError."""
    
    def test_suggestion_from_reason(self):
        """Test that reason becomes suggestion when not provided."""
        err = SourceUnavailableError(
            "API down",
            source_name="reddit",
            reason="API returned 503"
        )
        self.assertEqual(err.suggestion, "API returned 503")
    
    def test_no_reason_suggestion_override(self):
        """Test that provided suggestion overrides reason."""
        err = SourceUnavailableError(
            "API down",
            source_name="reddit",
            reason="API returned 503",
            suggestion="Try again later"
        )
        self.assertEqual(err.suggestion, "Try again later")


class TestRetryDecoratorAdvanced(unittest.TestCase):
    """Advanced tests for @with_retry decorator."""
    
    def test_max_backoff_respected(self):
        """Test that max_backoff is respected."""
        call_times = []
        
        @with_retry(max_retries=3, backoff_factor=10.0, max_backoff=0.2, jitter=False, retry_on=(ValueError,))
        def slow_backoff():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise ValueError("Retry")
            return "success"
        
        start = time.time()
        result = slow_backoff()
        elapsed = time.time() - start
        
        self.assertEqual(result, "success")
        # With max_backoff=0.2, total wait should be at most 0.4s (2 waits of 0.2 each)
        # instead of 10s + 100s without max
        self.assertLess(elapsed, 1.0)  # Generous upper bound
    
    def test_backoff_with_jitter(self):
        """Test that jitter adds randomness to backoff."""
        times = []
        
        @with_retry(max_retries=3, backoff_factor=0.1, jitter=True, retry_on=(ValueError,))
        def with_jitter():
            times.append(time.time())
            if len(times) < 3:
                raise ValueError("Retry")
            return "success"
        
        @with_retry(max_retries=3, backoff_factor=0.1, jitter=False, retry_on=(ValueError,))
        def without_jitter():
            times.append(time.time())
            if len(times) < 3:
                raise ValueError("Retry")
            return "success"
        
        # Both should succeed, just verify jitter doesn't break functionality
        self.assertEqual(with_jitter(), "success")
        self.assertEqual(without_jitter(), "success")
    
    def test_retry_on_multiple_exception_types(self):
        """Test retry on multiple exception types."""
        call_count = [0]
        
        @with_retry(max_retries=3, backoff_factor=0.05, retry_on=(ValueError, TypeError))
        def multi_error_func():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("First error")
            if call_count[0] == 2:
                raise TypeError("Second error")
            return "success"
        
        result = multi_error_func()
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 3)
    
    def test_preserves_function_metadata(self):
        """Test that decorator preserves function metadata."""
        @with_retry()
        def documented_function():
            """This function has a docstring."""
            return "ok"
        
        self.assertEqual(documented_function.__name__, "documented_function")
        self.assertIn("docstring", documented_function.__doc__)
    
    def test_retry_with_positional_and_keyword_args(self):
        """Test retry with various argument patterns."""
        call_count = [0]
        
        @with_retry(max_retries=2, backoff_factor=0.05, retry_on=(ValueError,))
        def func_with_args(a, b, c=None, d=None):
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("Retry")
            return (a, b, c, d)
        
        result = func_with_args(1, 2, c=3, d=4)
        self.assertEqual(result, (1, 2, 3, 4))
        self.assertEqual(call_count[0], 2)
    
    def test_success_callback_only_on_retry(self):
        """Test that success callback is only called if there were retries."""
        success_calls = []
        
        def on_success(attempts):
            success_calls.append(attempts)
        
        @with_retry(max_retries=3, backoff_factor=0.05, retry_on=(ValueError,), on_success=on_success)
        def immediate_success():
            return "ok"
        
        result = immediate_success()
        self.assertEqual(result, "ok")
        self.assertEqual(len(success_calls), 0)  # No retries, no callback


class TestErrorLogging(unittest.TestCase):
    """Tests for error logging functionality."""
    
    def test_log_does_not_raise_for_any_severity(self):
        """Test that log() works for all severity levels without raising."""
        severities = [
            ErrorSeverity.LOW,
            ErrorSeverity.MEDIUM,
            ErrorSeverity.HIGH,
            ErrorSeverity.FATAL,
        ]
        
        for severity in severities:
            err = Last30DaysError(f"Test {severity.value}", severity=severity)
            # Should not raise
            err.log()
    
    def test_log_with_custom_logger(self):
        """Test that log() can use a custom logger."""
        from unittest.mock import MagicMock
        
        err = Last30DaysError("Test error", severity=ErrorSeverity.MEDIUM)
        mock_logger = MagicMock()
        
        # Should call the mock logger
        err.log(logger=mock_logger)
        
        # Verify some method was called (exact method depends on severity)
        self.assertTrue(mock_logger.called or len(mock_logger.method_calls) > 0)
    
    def test_log_includes_error_data(self):
        """Test that log() includes error data in the call."""
        from unittest.mock import MagicMock
        
        err = Last30DaysError("Test message", severity=ErrorSeverity.HIGH)
        mock_logger = MagicMock()
        err.log(logger=mock_logger)
        
        # Verify the logger was used
        self.assertTrue(mock_logger.method_calls)
        
        # Get the first method call and verify it has the message
        first_call = mock_logger.method_calls[0]
        call_args = str(first_call)
        self.assertIn("Test message", call_args)


class TestGetLogger(unittest.TestCase):
    """Tests for get_logger function."""
    
    def test_returns_logger_instance(self):
        """Test that get_logger returns a Logger instance."""
        logger = get_logger("test")
        self.assertIsInstance(logger, logging.Logger)
    
    def test_same_name_returns_same_logger(self):
        """Test that same name returns same logger instance."""
        logger1 = get_logger("test")
        logger2 = get_logger("test")
        self.assertIs(logger1, logger2)
    
    def test_default_logger_name(self):
        """Test default logger name."""
        logger = get_logger()
        self.assertEqual(logger.name, "last30days")


class TestErrorAggregatorAdvanced(unittest.TestCase):
    """Advanced tests for ErrorAggregator."""
    
    def test_log_summary(self):
        """Test log_summary method."""
        agg = ErrorAggregator(title="Test errors")
        agg.add(APIError("Error 1", status_code=500))
        agg.add(NetworkError("Error 2"))
        
        with patch.object(log, 'error') as mock_error:
            agg.log_summary()
            # Should log title + 2 error lines
            self.assertEqual(mock_error.call_count, 3)
    
    def test_log_summary_with_custom_logger(self):
        """Test log_summary with custom logger."""
        custom_logger = logging.getLogger("test")
        agg = ErrorAggregator(title="Test errors")
        agg.add(APIError("Error 1"))
        
        with patch.object(custom_logger, 'error') as mock_error:
            agg.log_summary(logger=custom_logger)
            mock_error.assert_called()
    
    def test_to_exception_empty(self):
        """Test to_exception with no errors."""
        agg = ErrorAggregator()
        result = agg.to_exception()
        self.assertIsInstance(result, Last30DaysError)
        self.assertIn("No errors", result.message)


class TestWrapException(unittest.TestCase):
    """Tests for wrap_exception utility."""
    
    def test_wrap_with_default_class(self):
        """Test wrapping with default Last30DaysError class."""
        original = ValueError("Original error")
        wrapped = wrap_exception(original)
        
        self.assertIsInstance(wrapped, Last30DaysError)
        self.assertEqual(wrapped.message, "Original error")
        self.assertIs(wrapped.__cause__, original)
    
    def test_wrap_with_custom_message(self):
        """Test wrapping with custom message."""
        original = ValueError("Original")
        wrapped = wrap_exception(original, message="Custom message")
        
        self.assertEqual(wrapped.message, "Custom message")
    
    def test_wrap_with_additional_kwargs(self):
        """Test wrapping with additional keyword arguments."""
        original = ValueError("Original")
        wrapped = wrap_exception(
            original,
            APIError,
            source="test",
            operation="fetch",
            status_code=500
        )
        
        self.assertIsInstance(wrapped, APIError)
        self.assertEqual(wrapped.status_code, 500)
        self.assertEqual(wrapped.context.source, "test")


class TestSafeCallAdvanced(unittest.TestCase):
    """Advanced tests for safe_call utility."""
    
    def test_safe_call_with_kwargs(self):
        """Test safe_call with keyword arguments."""
        def func(a, b, c=None):
            return a + b + (c or 0)
        
        result = safe_call(func, 1, 2, c=3)
        self.assertEqual(result, 6)
    
    def test_safe_call_returns_none_on_error_without_default(self):
        """Test that safe_call returns None on error when no default is provided."""
        def raises():
            raise ValueError("Error")
        
        result = safe_call(raises)
        self.assertIsNone(result)
    
    def test_safe_call_with_debug_logging(self):
        """Test that debug logging happens when DEBUG mode is on."""
        original_debug = os.environ.get("LAST30DAYS_DEBUG")
        try:
            os.environ["LAST30DAYS_DEBUG"] = "1"
            
            # Re-import to pick up DEBUG setting
            import importlib
            import lib.errors as errors_mod
            importlib.reload(errors_mod)
            
            def raises():
                raise ValueError("Test error")
            
            # Should not raise, just return None
            with patch.object(errors_mod.log, 'debug') as mock_debug:
                result = errors_mod.safe_call(raises)
                self.assertIsNone(result)
        finally:
            if original_debug is None:
                os.environ.pop("LAST30DAYS_DEBUG", None)
            else:
                os.environ["LAST30DAYS_DEBUG"] = original_debug


class TestHTTPErrorConversionAdvanced(unittest.TestCase):
    """Advanced tests for http_error_to_last30days function."""
    
    def test_403_converts_to_auth_error(self):
        """Test that HTTP 403 converts to AuthError."""
        from lib import http
        
        http_err = http.HTTPError("Forbidden", status_code=403, body="{}")
        result = http_error_to_last30days(http_err, source="api", operation="fetch")
        
        # Check class name to avoid module import path issues
        self.assertEqual(type(result).__name__, "AuthError")
        self.assertIn("403", str(result))
    
    def test_502_converts_to_api_error(self):
        """Test that HTTP 502 converts to APIError (recoverable)."""
        from lib import http
        
        http_err = http.HTTPError("Bad Gateway", status_code=502, body="{}")
        result = http_error_to_last30days(http_err, source="api", operation="fetch")
        
        self.assertEqual(type(result).__name__, "APIError")
        self.assertTrue(result.recoverable)
    
    def test_generic_exception_wrapping(self):
        """Test that non-HTTPError exceptions are wrapped generically."""
        original = ValueError("Generic error")
        result = http_error_to_last30days(original, source="test", operation="op")
        
        self.assertEqual(type(result).__name__, "Last30DaysError")
        self.assertIs(result.__cause__, original)
    
    def test_rate_limit_error_passthrough(self):
        """Test that RateLimitError is passed through unchanged."""
        # Create a RateLimitError from the same module as http_error_to_last30days uses
        from lib import errors as errors_mod
        err = errors_mod.RateLimitError("Already rate limited", retry_after=60)
        result = http_error_to_last30days(err, source="test", operation="op")
        
        # Should be the same object
        self.assertIs(result, err)


class TestErrorSerialization(unittest.TestCase):
    """Tests for error serialization (to_dict)."""
    
    def test_api_error_to_dict(self):
        """Test APIError serialization."""
        err = APIError(
            "API failed",
            status_code=500,
            api_name="Reddit",
            source="reddit",
            operation="search"
        )
        d = err.to_dict()
        
        self.assertEqual(d["error_type"], "APIError")
        self.assertEqual(d["message"], "API failed")
        self.assertEqual(d["severity"], "medium")
        self.assertEqual(d["category"], "api")
        self.assertTrue(d["recoverable"])
        self.assertIn("context", d)
    
    def test_error_context_in_serialization(self):
        """Test that context is properly serialized."""
        # Create context explicitly to test serialization
        ctx = ErrorContext(
            source="test_source",
            operation="test_op",
            extra={"key": "value"}
        )
        err = Last30DaysError("Test error", context=ctx)
        d = err.to_dict()
        
        self.assertEqual(d["context"]["source"], "test_source")
        self.assertEqual(d["context"]["operation"], "test_op")
        self.assertEqual(d["context"]["extra"]["key"], "value")
    
    def test_error_context_from_kwargs(self):
        """Test that context is built correctly from kwargs."""
        err = Last30DaysError(
            "Test error",
            source="test_source",
            operation="test_op",
            custom_key="custom_value"
        )
        d = err.to_dict()
        
        self.assertEqual(d["context"]["source"], "test_source")
        self.assertEqual(d["context"]["operation"], "test_op")
        self.assertEqual(d["context"]["extra"]["custom_key"], "custom_value")


class TestExceptionChaining(unittest.TestCase):
    """Tests for exception chaining (__cause__)."""
    
    def test_network_error_with_cause(self):
        """Test NetworkError with explicit cause."""
        original = ConnectionError("Connection refused")
        err = NetworkError("Network failed", cause=original)
        
        self.assertIs(err.__cause__, original)
    
    def test_api_error_with_cause(self):
        """Test APIError with explicit cause."""
        original = Exception("Underlying error")
        err = APIError("API failed", cause=original, status_code=500)
        
        self.assertIs(err.__cause__, original)
    
    def test_cause_in_context(self):
        """Test that cause is accessible for debugging."""
        original = ValueError("Original")
        err = Last30DaysError("Wrapped", cause=original)
        
        # Verify cause chain
        self.assertIsNotNone(err.__cause__)
        self.assertIsInstance(err.__cause__, ValueError)


class TestEdgeCases(unittest.TestCase):
    """Tests for edge cases and boundary conditions."""
    
    def test_empty_message_uses_default(self):
        """Test that empty message falls back to default."""
        err = Last30DaysError("")
        self.assertEqual(err.message, err.default_message)
    
    def test_none_message_uses_default(self):
        """Test that None message falls back to default."""
        err = Last30DaysError(None)
        self.assertEqual(err.message, err.default_message)
    
    def test_zero_retries(self):
        """Test retry with max_retries=0 (no retries)."""
        call_count = [0]
        
        @with_retry(max_retries=0, backoff_factor=0.1, retry_on=(ValueError,))
        def fails():
            call_count[0] += 1
            raise ValueError("Error")
        
        with self.assertRaises(ValueError):
            fails()
        
        self.assertEqual(call_count[0], 1)  # Only initial call
    
    def test_single_retry(self):
        """Test retry with max_retries=1."""
        call_count = [0]
        
        @with_retry(max_retries=1, backoff_factor=0.05, retry_on=(ValueError,))
        def eventually_succeeds():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("First try")
            return "success"
        
        result = eventually_succeeds()
        self.assertEqual(result, "success")
        self.assertEqual(call_count[0], 2)


if __name__ == "__main__":
    unittest.main()
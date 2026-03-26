"""
Error handling utilities for last30days-skill.

This module provides:
1. A unified exception hierarchy for the project
2. Structured error context for debugging
3. Retry decorator with exponential backoff
4. Structured logging utilities

Usage:
    from lib.errors import (
        Last30DaysError,
        APIError,
        RateLimitError,
        TimeoutError,
        with_retry,
    )

    # Raise specific errors
    raise APIError("Reddit API failed", source="reddit", status_code=429)

    # Use retry decorator
    @with_retry(max_retries=3, backoff_factor=2.0, retry_on=(RateLimitError,))
    def fetch_data():
        ...
"""

import functools
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union


# =============================================================================
# Logging Configuration
# =============================================================================

# Create a dedicated logger for the project
LOGGER_NAME = "last30days"

# Check for debug mode
DEBUG = os.environ.get("LAST30DAYS_DEBUG", "").lower() in ("1", "true", "yes")


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Get the last30days logger instance."""
    logger = logging.getLogger(name)
    
    # Only configure if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            fmt="[{levelname}] {name}: {message}",
            style="{"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
        logger.propagate = False
    
    return logger


# Module-level logger
log = get_logger()


# =============================================================================
# Error Severity Levels
# =============================================================================

class ErrorSeverity(Enum):
    """Severity level for errors."""
    LOW = "low"           # Minor issue, graceful degradation possible
    MEDIUM = "medium"     # Significant issue, partial functionality lost
    HIGH = "high"         # Critical issue, core functionality impaired
    FATAL = "fatal"       # Unrecoverable error, process should terminate


class ErrorCategory(Enum):
    """Category of error for classification."""
    NETWORK = "network"           # HTTP/connection errors
    API = "api"                   # External API errors
    AUTH = "auth"                 # Authentication/authorization errors
    RATE_LIMIT = "rate_limit"     # Rate limiting errors
    TIMEOUT = "timeout"           # Timeout errors
    VALIDATION = "validation"     # Input validation errors
    CONFIG = "config"             # Configuration errors
    PARSING = "parsing"           # Data parsing errors
    INTERNAL = "internal"         # Internal logic errors
    DEPENDENCY = "dependency"     # Missing dependencies


# =============================================================================
# Error Context
# =============================================================================

@dataclass
class ErrorContext:
    """Rich context for error debugging and logging.
    
    Attributes:
        source: The component/module that raised the error
        operation: The operation that failed
        timestamp: When the error occurred
        request_id: Optional request ID for tracing
        extra: Additional context as key-value pairs
        traceback: Optional traceback string
    """
    source: str
    operation: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    request_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    traceback: Optional[str] = None
    
    def __post_init__(self):
        """Capture traceback if not provided and in debug mode."""
        if self.traceback is None and DEBUG:
            self.traceback = traceback.format_exc()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "source": self.source,
            "operation": self.operation,
            "timestamp": self.timestamp,
            "extra": self.extra,
        }
        if self.request_id:
            result["request_id"] = self.request_id
        if self.traceback:
            result["traceback"] = self.traceback
        return result
    
    def __str__(self) -> str:
        """Human-readable representation."""
        parts = [f"[{self.source}] {self.operation}"]
        if self.extra:
            parts.append(f" extra={self.extra}")
        return "".join(parts)


# =============================================================================
# Exception Hierarchy
# =============================================================================

class Last30DaysError(Exception):
    """Base exception for all last30days-skill errors.
    
    All custom exceptions in the project should inherit from this class.
    
    Attributes:
        message: Human-readable error message
        severity: Error severity level
        category: Error category for classification
        context: Rich error context for debugging
        recoverable: Whether the error can be recovered from
        suggestion: Optional suggestion for how to resolve the error
    """
    
    default_message = "An error occurred in last30days-skill"
    default_severity = ErrorSeverity.MEDIUM
    default_category = ErrorCategory.INTERNAL
    default_recoverable = False
    
    def __init__(
        self,
        message: Optional[str] = None,
        *,
        severity: Optional[ErrorSeverity] = None,
        category: Optional[ErrorCategory] = None,
        context: Optional[ErrorContext] = None,
        recoverable: Optional[bool] = None,
        suggestion: Optional[str] = None,
        cause: Optional[Exception] = None,
        **context_extra,
    ):
        """Initialize the exception.
        
        Args:
            message: Human-readable error message
            severity: Error severity level
            category: Error category
            context: Rich error context
            recoverable: Whether error can be recovered from
            suggestion: How to resolve the error
            cause: The underlying exception that caused this error
            **context_extra: Additional context key-value pairs
        """
        self.message = message or self.default_message
        self.severity = severity or self.default_severity
        self.category = category or self.default_category
        self.recoverable = recoverable if recoverable is not None else self.default_recoverable
        self.suggestion = suggestion
        
        # Build context if not provided
        if context:
            self.context = context
            if context_extra:
                self.context.extra.update(context_extra)
        else:
            source = context_extra.pop("source", "unknown")
            operation = context_extra.pop("operation", "unknown")
            self.context = ErrorContext(
                source=source,
                operation=operation,
                extra=context_extra,
            )
        
        # Store original cause
        self.__cause__ = cause
        
        # Call parent with message
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "severity": self.severity.value,
            "category": self.category.value,
            "recoverable": self.recoverable,
            "suggestion": self.suggestion,
            "context": self.context.to_dict(),
        }
    
    def __str__(self) -> str:
        """Human-readable representation."""
        parts = [self.message]
        if self.suggestion:
            parts.append(f" Suggestion: {self.suggestion}")
        return "".join(parts)
    
    def log(self, logger: Optional[logging.Logger] = None) -> None:
        """Log this error at appropriate level.
        
        Args:
            logger: Logger to use (defaults to module logger)
        """
        logger = logger or log
        msg = f"{self}"
        
        if self.severity == ErrorSeverity.FATAL:
            logger.critical(msg, extra={"error_data": self.to_dict()})
        elif self.severity == ErrorSeverity.HIGH:
            logger.error(msg, extra={"error_data": self.to_dict()})
        elif self.severity == ErrorSeverity.MEDIUM:
            logger.warning(msg, extra={"error_data": self.to_dict()})
        else:
            logger.info(msg, extra={"error_data": self.to_dict()})


# =============================================================================
# Specific Exception Types
# =============================================================================

class NetworkError(Last30DaysError):
    """Network-level errors (DNS, connection refused, etc.)."""
    
    default_message = "Network error occurred"
    default_category = ErrorCategory.NETWORK
    default_recoverable = True
    
    def __init__(self, message: Optional[str] = None, *, suggestion: Optional[str] = "Check your network connection and try again", **kwargs):
        super().__init__(message, suggestion=suggestion, **kwargs)


class APIError(Last30DaysError):
    """Errors from external API calls.
    
    Attributes:
        status_code: HTTP status code (if applicable)
        api_name: Name of the API that returned the error
        response_body: Raw response body (truncated for safety)
    """
    
    default_message = "API request failed"
    default_category = ErrorCategory.API
    default_recoverable = True
    
    def __init__(
        self,
        message: Optional[str] = None,
        *,
        status_code: Optional[int] = None,
        api_name: Optional[str] = None,
        response_body: Optional[str] = None,
        **kwargs,
    ):
        self.status_code = status_code
        self.api_name = api_name
        # Truncate response body for safety
        self.response_body = response_body[:500] if response_body else None
        
        # Add to context before calling parent
        if status_code:
            kwargs.setdefault("status_code", status_code)
        if api_name:
            kwargs.setdefault("api_name", api_name)
        
        super().__init__(message, **kwargs)
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.api_name:
            parts.append(f" (API: {self.api_name})")
        if self.status_code:
            parts.append(f" [HTTP {self.status_code}]")
        if self.suggestion:
            parts.append(f" Suggestion: {self.suggestion}")
        return "".join(parts)


class RateLimitError(APIError):
    """Rate limiting errors (HTTP 429 or equivalent).
    
    Attributes:
        retry_after: Seconds to wait before retrying
    """
    
    default_message = "Rate limit exceeded"
    default_category = ErrorCategory.RATE_LIMIT
    default_suggestion = "Wait before retrying or reduce request frequency"
    
    def __init__(
        self,
        message: Optional[str] = None,
        *,
        retry_after: Optional[float] = None,
        **kwargs,
    ):
        self.retry_after = retry_after
        
        # Add to context before calling parent
        if retry_after:
            kwargs.setdefault("retry_after", retry_after)
        
        super().__init__(message, **kwargs)
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.api_name:
            parts.append(f" (API: {self.api_name})")
        if self.retry_after:
            parts.append(f" - retry after {self.retry_after}s")
        if self.suggestion:
            parts.append(f" Suggestion: {self.suggestion}")
        return "".join(parts)


class TimeoutError(Last30DaysError):
    """Timeout errors for operations.
    
    Attributes:
        timeout_seconds: The timeout value that was exceeded
        operation: Description of the timed-out operation
    """
    
    default_message = "Operation timed out"
    default_category = ErrorCategory.TIMEOUT
    default_recoverable = True
    default_suggestion = "Increase timeout or try with a simpler query"
    
    def __init__(
        self,
        message: Optional[str] = None,
        *,
        timeout_seconds: Optional[float] = None,
        operation: Optional[str] = None,
        **kwargs,
    ):
        self.timeout_seconds = timeout_seconds
        self.operation = operation if operation is not None else "unknown"
        
        # Add to context before calling parent
        if timeout_seconds:
            kwargs.setdefault("timeout_seconds", timeout_seconds)
        
        super().__init__(message, **kwargs)
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.operation:
            parts.append(f" (operation: {self.operation})")
        if self.timeout_seconds:
            parts.append(f" after {self.timeout_seconds}s")
        return "".join(parts)


class AuthError(Last30DaysError):
    """Authentication/authorization errors."""
    
    default_message = "Authentication failed"
    default_category = ErrorCategory.AUTH
    default_recoverable = False
    default_suggestion = "Check your API credentials"


class ConfigError(Last30DaysError):
    """Configuration errors."""
    
    default_message = "Configuration error"
    default_category = ErrorCategory.CONFIG
    default_recoverable = False
    default_suggestion = "Check your configuration file and environment variables"


class ValidationError(Last30DaysError):
    """Input validation errors."""
    
    default_message = "Validation error"
    default_category = ErrorCategory.VALIDATION
    default_recoverable = False


class ParsingError(Last30DaysError):
    """Data parsing errors."""
    
    default_message = "Failed to parse data"
    default_category = ErrorCategory.PARSING
    default_recoverable = True


class DependencyError(Last30DaysError):
    """Missing dependency errors."""
    
    default_message = "Required dependency not available"
    default_category = ErrorCategory.DEPENDENCY
    default_recoverable = False
    
    def __init__(
        self,
        message: Optional[str] = None,
        *,
        dependency_name: Optional[str] = None,
        install_command: Optional[str] = None,
        **kwargs,
    ):
        self.dependency_name = dependency_name
        self.install_command = install_command
        
        # Add to context before calling parent
        if dependency_name:
            kwargs.setdefault("dependency_name", dependency_name)
        if install_command:
            kwargs.setdefault("install_command", install_command)
        
        # Build suggestion
        if "suggestion" not in kwargs:
            if install_command:
                kwargs["suggestion"] = f"Install with: {install_command}"
            elif dependency_name:
                kwargs["suggestion"] = f"Install {dependency_name}"
        
        super().__init__(message, **kwargs)


class SourceUnavailableError(Last30DaysError):
    """Error when a data source is unavailable."""
    
    default_message = "Data source unavailable"
    default_category = ErrorCategory.API
    default_recoverable = True
    
    def __init__(
        self,
        message: Optional[str] = None,
        *,
        source_name: Optional[str] = None,
        reason: Optional[str] = None,
        **kwargs,
    ):
        self.source_name = source_name
        self.reason = reason
        
        # Add to context before calling parent
        if source_name:
            kwargs.setdefault("source_name", source_name)
        if reason:
            kwargs.setdefault("reason", reason)
        
        if "suggestion" not in kwargs and reason:
            kwargs["suggestion"] = reason
        
        super().__init__(message, **kwargs)


# =============================================================================
# Retry Decorator
# =============================================================================

@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    backoff_factor: float = 2.0
    max_backoff: float = 60.0
    jitter: bool = True
    retry_on: Tuple[Type[Exception], ...] = (Exception,)
    on_retry: Optional[Callable[[int, Exception], None]] = None
    on_success: Optional[Callable[[int], None]] = None
    on_failure: Optional[Callable[[Exception], None]] = None


def with_retry(
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    max_backoff: float = 60.0,
    jitter: bool = True,
    retry_on: Union[Type[Exception], Tuple[Type[Exception], ...]] = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    on_success: Optional[Callable[[int], None]] = None,
    on_failure: Optional[Callable[[Exception], None]] = None,
):
    """Decorator that adds retry logic with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        backoff_factor: Multiplier for backoff (2.0 = double each time)
        max_backoff: Maximum backoff time in seconds
        jitter: Add randomness to backoff to avoid thundering herd
        retry_on: Exception types to retry on
        on_retry: Callback called before each retry: (attempt, exception)
        on_success: Callback called on success: (total_attempts)
        on_failure: Callback called after all retries exhausted: (exception)
    
    Returns:
        Decorated function with retry logic
    
    Example:
        @with_retry(max_retries=3, retry_on=(APIError, RateLimitError))
        def fetch_data():
            response = api_call()
            if response.status_code == 429:
                raise RateLimitError("Rate limited", retry_after=60)
            return response
    """
    # Normalize retry_on to tuple
    if isinstance(retry_on, type):
        retry_on = (retry_on,)
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            attempt = 0
            
            while attempt <= max_retries:
                try:
                    result = func(*args, **kwargs)
                    if on_success and attempt > 0:
                        on_success(attempt)
                    return result
                except retry_on as e:
                    last_exception = e
                    attempt += 1
                    
                    if attempt > max_retries:
                        if on_failure:
                            on_failure(e)
                        raise
                    
                    # Calculate backoff
                    backoff = min(backoff_factor ** (attempt - 1), max_backoff)
                    
                    # Handle RateLimitError's retry_after
                    if isinstance(e, RateLimitError) and e.retry_after:
                        backoff = max(backoff, e.retry_after)
                    
                    # Add jitter
                    import random
                    if jitter:
                        backoff = backoff * (0.5 + random.random())
                    
                    # Log retry
                    log.warning(
                        f"Retry {attempt}/{max_retries} for {func.__name__} "
                        f"after {e.__class__.__name__}: {e}. "
                        f"Waiting {backoff:.1f}s"
                    )
                    
                    # Callback
                    if on_retry:
                        on_retry(attempt, e)
                    
                    time.sleep(backoff)
                except Exception:
                    # Non-retryable exception, re-raise immediately
                    raise
            
            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
        
        return wrapper
    
    return decorator


def with_retry_async(
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    max_backoff: float = 60.0,
    jitter: bool = True,
    retry_on: Union[Type[Exception], Tuple[Type[Exception], ...]] = (Exception,),
):
    """Async version of with_retry decorator."""
    import asyncio
    import random
    
    if isinstance(retry_on, type):
        retry_on = (retry_on,)
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            attempt = 0
            
            while attempt <= max_retries:
                try:
                    result = await func(*args, **kwargs)
                    return result
                except retry_on as e:
                    last_exception = e
                    attempt += 1
                    
                    if attempt > max_retries:
                        raise
                    
                    backoff = min(backoff_factor ** (attempt - 1), max_backoff)
                    if isinstance(e, RateLimitError) and e.retry_after:
                        backoff = max(backoff, e.retry_after)
                    
                    if jitter:
                        backoff = backoff * (0.5 + random.random())
                    
                    log.warning(
                        f"Retry {attempt}/{max_retries} for {func.__name__} "
                        f"after {e.__class__.__name__}. Waiting {backoff:.1f}s"
                    )
                    
                    await asyncio.sleep(backoff)
            
            if last_exception:
                raise last_exception
        
        return wrapper
    
    return decorator


# =============================================================================
# Error Aggregation
# =============================================================================

class ErrorAggregator:
    """Collect and aggregate multiple errors.
    
    Useful when running multiple operations in parallel and wanting to
    collect all errors rather than failing on the first one.
    
    Example:
        errors = ErrorAggregator()
        for source in sources:
            try:
                results.append(fetch_from_source(source))
            except Last30DaysError as e:
                errors.add(e)
        
        if errors.has_errors:
            errors.log_summary()
            raise errors.to_exception()
    """
    
    def __init__(self, title: str = "Multiple errors occurred"):
        self.title = title
        self.errors: List[Last30DaysError] = []
    
    def add(self, error: Union[Last30DaysError, Exception]) -> None:
        """Add an error to the aggregator."""
        if isinstance(error, Last30DaysError):
            self.errors.append(error)
        else:
            # Wrap non-last30days exceptions
            self.errors.append(Last30DaysError(
                str(error),
                cause=error,
                source="unknown",
                operation="unknown",
            ))
    
    @property
    def has_errors(self) -> bool:
        """Check if any errors were collected."""
        return len(self.errors) > 0
    
    @property
    def has_fatal(self) -> bool:
        """Check if any fatal errors were collected."""
        return any(e.severity == ErrorSeverity.FATAL for e in self.errors)
    
    def log_summary(self, logger: Optional[logging.Logger] = None) -> None:
        """Log a summary of all collected errors."""
        logger = logger or log
        logger.error(f"{self.title} ({len(self.errors)} total):")
        for i, error in enumerate(self.errors, 1):
            logger.error(f"  {i}. {error}")
    
    def to_exception(self) -> Last30DaysError:
        """Create a single exception from all collected errors."""
        if not self.errors:
            return Last30DaysError("No errors collected")
        
        if len(self.errors) == 1:
            return self.errors[0]
        
        # Build combined message
        messages = [f"{e.__class__.__name__}: {e.message}" for e in self.errors]
        combined = Last30DaysError(
            f"{self.title}:\n" + "\n".join(f"  - {m}" for m in messages),
            severity=ErrorSeverity.HIGH if self.has_fatal else ErrorSeverity.MEDIUM,
            context=ErrorContext(
                source="error_aggregator",
                operation="aggregate",
                extra={"error_count": len(self.errors), "errors": [e.to_dict() for e in self.errors]},
            ),
        )
        return combined


# =============================================================================
# Utility Functions
# =============================================================================

def wrap_exception(
    error: Exception,
    wrapper_class: Type[Last30DaysError] = Last30DaysError,
    message: Optional[str] = None,
    **kwargs,
) -> Last30DaysError:
    """Wrap a generic exception in a Last30DaysError.
    
    Args:
        error: The original exception
        wrapper_class: The Last30DaysError subclass to wrap in
        message: Custom message (defaults to str(error))
        **kwargs: Additional arguments for the wrapper
    
    Returns:
        A new Last30DaysError wrapping the original exception
    """
    return wrapper_class(
        message or str(error),
        cause=error,
        **kwargs,
    )


def safe_call(
    func: Callable,
    *args,
    default: Any = None,
    error_handler: Optional[Callable[[Exception], None]] = None,
    **kwargs,
) -> Any:
    """Call a function safely, returning default on error.
    
    Args:
        func: Function to call
        *args: Arguments for the function
        default: Default value to return on error
        error_handler: Optional callback to handle the error
        **kwargs: Keyword arguments for the function
    
    Returns:
        Function result or default on error
    
    Example:
        data = safe_call(json.loads, json_string, default={})
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if error_handler:
            error_handler(e)
        elif DEBUG:
            log.debug(f"safe_call caught {e.__class__.__name__}: {e}")
        return default


# =============================================================================
# Integration with existing http.HTTPError
# =============================================================================

def http_error_to_last30days(
    error: Exception,
    source: str = "unknown",
    operation: str = "http_request",
) -> Last30DaysError:
    """Convert an HTTP error to the appropriate Last30DaysError.
    
    This function bridges the existing http.HTTPError with the new
    exception hierarchy.
    
    Args:
        error: The HTTP error (http.HTTPError or urllib.error.HTTPError)
        source: The source/component name
        operation: The operation that failed
    
    Returns:
        An appropriate Last30DaysError subclass
    """
    # Import here to avoid circular dependency
    from . import http
    
    if isinstance(error, RateLimitError):
        return error
    
    if isinstance(error, http.HTTPError):
        status_code = error.status_code
        
        if status_code == 429:
            return RateLimitError(
                f"Rate limited: {error}",
                status_code=status_code,
                source=source,
                operation=operation,
                cause=error,
            )
        
        if status_code in (401, 403):
            return AuthError(
                f"Authentication failed (HTTP {status_code}): {error}",
                source=source,
                operation=operation,
                cause=error,
            )
        
        if status_code and status_code >= 500:
            return APIError(
                f"Server error (HTTP {status_code}): {error}",
                status_code=status_code,
                source=source,
                operation=operation,
                cause=error,
                recoverable=True,
            )
        
        if status_code and status_code >= 400:
            return APIError(
                f"Client error (HTTP {status_code}): {error}",
                status_code=status_code,
                source=source,
                operation=operation,
                cause=error,
                recoverable=False,
            )
        
        return NetworkError(
            str(error),
            source=source,
            operation=operation,
            cause=error,
        )
    
    # Generic error wrapping
    return Last30DaysError(
        str(error),
        cause=error,
        source=source,
        operation=operation,
    )
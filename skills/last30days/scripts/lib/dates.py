"""Date utilities for last30days skill."""

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple


def parse_as_of_date(as_of_date: Optional[str]) -> Optional[str]:
    """Validate and normalize an --as-of date.

    Args:
        as_of_date: Date string in YYYY-MM-DD format.

    Returns:
        Normalized YYYY-MM-DD string, or None when no date was provided.

    Raises:
        ValueError: If the date is not in YYYY-MM-DD format.
    """
    if as_of_date is None:
        return None

    if not as_of_date.strip():
        raise ValueError("--as-of must be in YYYY-MM-DD format.")

    try:
        parsed = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid --as-of date: {as_of_date}. Expected YYYY-MM-DD."
        ) from exc

    return parsed.isoformat()


def get_date_range(days: int = 30, as_of_date: Optional[str] = None) -> Tuple[str, str]:
    """Get the date range for the last N days.

    When as_of_date is provided, the range ends at that date instead of today.

    Args:
        days: Number of days to look back.
        as_of_date: Optional end date in YYYY-MM-DD format.

    Returns:
        Tuple of (from_date, to_date) as YYYY-MM-DD strings.
    """
    normalized_as_of = parse_as_of_date(as_of_date)

    if normalized_as_of:
        to_date = datetime.strptime(normalized_as_of, "%Y-%m-%d").date()
    else:
        to_date = datetime.now(timezone.utc).date()

    from_date = to_date - timedelta(days=days)
    return from_date.isoformat(), to_date.isoformat()


def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse a date string in various formats.

    Supports: YYYY-MM-DD, ISO 8601, Unix timestamp
    """
    if not date_str:
        return None

    # Try Unix timestamp (from Reddit)
    try:
        ts = float(date_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, TypeError):
        pass

    # Try ISO formats
    formats = [
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def timestamp_to_date(ts: Optional[float]) -> Optional[str]:
    """Convert Unix timestamp to YYYY-MM-DD string."""
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.date().isoformat()
    except (ValueError, TypeError, OSError):
        return None


def get_date_confidence(date_str: Optional[str], from_date: str, to_date: str) -> str:
    """Determine confidence level for a date.

    Args:
        date_str: The date to check (YYYY-MM-DD or None)
        from_date: Start of valid range (YYYY-MM-DD)
        to_date: End of valid range (YYYY-MM-DD)

    Returns:
        'high', 'med', or 'low'
    """
    if not date_str:
        return 'low'

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()

        return 'high' if start <= dt <= end else 'low'
    except ValueError:
        return 'low'


def days_ago(date_str: Optional[str], reference_date: Optional[str] = None) -> Optional[int]:
    """Calculate how many days before the reference date a date is.

    If reference_date is None, use real today for backward compatibility.
    Returns None if date is invalid or missing.
    """
    if not date_str:
        return None

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        if reference_date:
            today = datetime.strptime(reference_date, "%Y-%m-%d").date()
        else:
            today = datetime.now(timezone.utc).date()
        delta = today - dt
        return delta.days
    except ValueError:
        return None


def recency_score(
    date_str: Optional[str],
    max_days: int = 30,
    reference_date: Optional[str] = None,
) -> int:
    """Calculate recency score (0-100).

    0 days before reference_date = 100, max_days before reference_date = 0.
    If reference_date is None, use real today for backward compatibility.
    """
    age = days_ago(date_str, reference_date=reference_date)
    if age is None:
        return 0

    if age < 0:
        return 100
    if age >= max_days:
        return 0

    return int(100 * (1 - age / max_days))

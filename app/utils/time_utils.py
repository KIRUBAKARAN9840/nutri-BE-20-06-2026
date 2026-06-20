"""Single source of truth for 'now' in app code that touches DB timestamps.

Why this exists:
    Python's datetime.now() returns the SERVER's local timezone (in our
    case usually IST on dev laptops, UTC on EC2 hosts), as a NAIVE
    datetime. MySQL's NOW() returns the DB-server's local timezone.
    When the two differ (Python in IST, MySQL in UTC), every comparison
    like `expires_at > NOW()` silently drifts by the offset — stories
    that should be expired stay "active" for 5h30m past their TTL, etc.

    Standardising on UTC across both sides removes the ambiguity. Use
    utc_now() for every DB write/comparison; use func.utc_timestamp()
    on the SQL side. Never datetime.now() for DB things.

    Naive (no tzinfo) is intentional — the existing schema uses
    DateTime (no TZ) columns, and a tz-aware migration is a separate
    project. For now: same shape, just UTC.
"""

from datetime import datetime


def utc_now() -> datetime:
    """Naive UTC datetime — matches MySQL NOW()/UTC_TIMESTAMP() shape."""
    return datetime.utcnow()

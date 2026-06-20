"""
Subscription / tier helper utilities.

Consolidates is_subscription_active() and get_plan_name_from_product_id()
that were duplicated in usersDashboard.py and marketing/authSession.py.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from app.services.timezone_utils import IST, ensure_aware


# ── Plan name mapping ───────────────────────────────────────

PLAN_NAME_MAP = {
    "one_month_plan": "Gold",
    "six_month_plan": "Platinum",
    "twelve_month_plan": "Diamond",
}


def get_plan_name(product_id: Optional[str]) -> Optional[str]:
    """
    Map a RevenueCat *product_id* to a human-readable plan name.

    >>> get_plan_name("one_month_plan_v2")
    'Gold'
    >>> get_plan_name("six_month_plan")
    'Platinum'
    >>> get_plan_name(None)
    """
    if not product_id:
        return None
    pid = product_id.lower()
    for prefix, name in PLAN_NAME_MAP.items():
        if pid.startswith(prefix):
            return name
    return None


# ── Subscription active check ───────────────────────────────

def is_subscription_active(active_until, now: Optional[datetime] = None) -> bool:
    """
    Determine whether a subscription is still active.

    Handles *active_until* as ``None``, ``str`` (ISO-8601), naive datetime,
    or timezone-aware datetime.  Naive datetimes are assumed IST.

    Parameters
    ----------
    active_until : datetime | str | None
        Expiry timestamp from the database.
    now : datetime, optional
        Current time to compare against (defaults to ``datetime.now(IST)``).
    """
    if active_until is None:
        return False

    if now is None:
        now = datetime.now(IST)

    # Handle string dates
    if isinstance(active_until, str):
        try:
            active_until = datetime.fromisoformat(
                active_until.replace("Z", "+00:00")
            )
            if active_until.tzinfo is None:
                active_until = active_until.replace(tzinfo=IST)
        except (ValueError, AttributeError):
            return False

    active_until = ensure_aware(active_until, assume_tz=IST)
    now = ensure_aware(now, assume_tz=IST)

    return active_until >= now

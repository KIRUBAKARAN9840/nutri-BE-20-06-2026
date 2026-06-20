"""Pure pricing calculation for session bookings.

Shared by session_bookings (preview) and session_processor (checkout).
Mirrors calculate_dailypass_pricing() for sessions.
Works with any unit (rupees or paise) as long as inputs are consistent.
"""

from typing import Any, Dict, List

INTRO_OFFER_MAX_SESSIONS = 3


def calculate_session_pricing(
    number_of_days: int,
    offer_price: int,
    actual_price: int,
    offer_active: bool,
    session_count: int,
) -> Dict[str, Any]:
    """Calculate pricing for session bookings.

    User gets up to 3 intro-offer sessions at ₹99, rest at actual price.

    Args:
        number_of_days: Total days being booked.
        offer_price:    Per-day intro offer price.
        actual_price:   Per-day dynamic/actual price (no offer).
        offer_active:   Whether user+gym qualifies for intro offer.
        session_count:  User's existing session booking count (to calc remaining offer days).
    """
    remaining_offer_days = max(0, INTRO_OFFER_MAX_SESSIONS - session_count)

    if offer_active and remaining_offer_days > 0:
        # 1 intro-offer day per gym; remaining offers for other unique gyms
        offer_days = min(1, number_of_days)
        non_offer_days = number_of_days - offer_days
        offer_days_amount = offer_days * offer_price
        non_offer_days_amount = non_offer_days * actual_price
        total_amount = offer_days_amount + non_offer_days_amount
        display_price = offer_price
    else:
        offer_days = 0
        non_offer_days = number_of_days
        offer_days_amount = 0
        non_offer_days_amount = number_of_days * actual_price
        total_amount = non_offer_days_amount
        display_price = actual_price

    # show_modal: user has BOTH offer and actual-price days (mixed pricing)
    show_modal = offer_active and offer_days > 0 and non_offer_days > 0

    # ── Billing lines (frontend-renderable) ──────────────
    billing_lines: List[Dict[str, Any]] = []

    if offer_days > 0:
        billing_lines.append({
            "type": "user_offer",
            "days": offer_days,
            "price_per_day": offer_price,
            "amount": offer_days_amount,
        })

    if non_offer_days > 0:
        billing_lines.append({
            "type": "user_actual",
            "days": non_offer_days,
            "price_per_day": actual_price,
            "amount": non_offer_days_amount,
        })

    return {
        "total_amount": total_amount,
        "show_modal": show_modal,
        "display_price": display_price,
        "offer_days_count": offer_days,
        "non_offer_days_count": non_offer_days,
        "offer_days_amount": offer_days_amount,
        "non_offer_days_amount": non_offer_days_amount,
        "billing_lines": billing_lines,
    }

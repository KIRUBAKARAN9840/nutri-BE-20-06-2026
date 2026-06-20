"""Pure pricing calculation for dailypass bookings.

Shared by dailypass_bookings (preview) and dailypass_processor (checkout).
Works with any unit (rupees or paise) as long as inputs are consistent.
"""

from typing import Any, Dict, List, Optional

INTRO_OFFER_MAX_DAYS = 3


def calculate_dailypass_pricing(
    number_of_days: int,
    offer_price: int,
    actual_price: int,
    offer_active: bool,
    dp_count: int,
    friends_count: int = 0,
) -> Dict[str, Any]:
    """Calculate split pricing for user (may have intro offer) + friends (always actual).

    Args:
        number_of_days: Total days being booked.
        offer_price:    Per-day intro offer price.
        actual_price:   Per-day dynamic/actual price (no offer).
        offer_active:   Whether user+gym qualifies for intro offer.
        dp_count:       User's existing dailypass day count (to calc remaining offer days).
        friends_count:  Number of friends (NOT including the user).
    """
    remaining_offer_days = max(0, INTRO_OFFER_MAX_DAYS - dp_count)

    if offer_active and remaining_offer_days > 0:
        offer_days = min(remaining_offer_days, number_of_days)
        non_offer_days = number_of_days - offer_days
        offer_days_amount = offer_days * offer_price
        non_offer_days_amount = non_offer_days * actual_price
        user_amount = offer_days_amount + non_offer_days_amount
        dailypass_price = offer_price
    else:
        offer_days = 0
        non_offer_days = number_of_days
        offer_days_amount = 0
        non_offer_days_amount = number_of_days * actual_price
        user_amount = non_offer_days_amount
        dailypass_price = actual_price

    # show_modal_self: user has BOTH offer and dynamic days (mixed pricing)
    show_modal_self = offer_active and offer_days > 0 and non_offer_days > 0

    friends_amount: Optional[int] = None
    per_friend_amount: Optional[int] = None
    show_modal_friend = False

    if friends_count > 0:
        per_friend_amount = actual_price * number_of_days
        friends_amount = per_friend_amount * friends_count
        # show_modal_friend: user has offer days but friends pay dynamic
        show_modal_friend = offer_active and offer_days > 0

    total_amount = user_amount + (friends_amount or 0)

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

    if friends_count > 0 and per_friend_amount is not None:
        billing_lines.append({
            "type": "friend",
            "count": friends_count,
            "days": number_of_days,
            "price_per_day": actual_price,
            "amount": friends_amount,
        })

    return {
        "user_amount": user_amount,
        "friends_amount": friends_amount,
        "per_friend_amount": per_friend_amount,
        "total_amount": total_amount,
        "show_modal_self": show_modal_self,
        "show_modal_friend": show_modal_friend,
        "offer_days_count": offer_days,
        "non_offer_days_count": non_offer_days,
        "offer_days_amount": offer_days_amount,
        "non_offer_days_amount": non_offer_days_amount,
        "dailypass_price": dailypass_price,
        "billing_lines": billing_lines,
    }

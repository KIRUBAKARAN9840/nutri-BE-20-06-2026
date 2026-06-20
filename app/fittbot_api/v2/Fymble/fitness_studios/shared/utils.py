"""Shared utility functions used across Fymble listing endpoints."""

from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_IST = ZoneInfo("Asia/Kolkata")


def resolve_offer_base_amount(plan_id: int, plan_amount: int, gym_offers: dict) -> Tuple[int, bool]:
    """Return (base_amount, is_offer_active) for a plan.

    Checks plan-specific offer first (plan_id key), then gym-wide (None key).
    Falls back to plan_amount if no active offer.

    Args:
        plan_id: The plan's ID to check for plan-specific offers.
        plan_amount: The plan's original base amount (fallback).
        gym_offers: Dict keyed by plan_id (or None for gym-wide) → offer row.
    """
    if not gym_offers:
        return plan_amount, False

    offer = gym_offers.get(plan_id) or gym_offers.get(None)
    if offer:
        return offer.offer_price, True

    return plan_amount, False


async def fetch_gym_address_and_location(db: AsyncSession, gym_id: int) -> Dict:
    """Fetch gym name, address, and coordinates.

    Shared by booking checkout previews (sessions, memberships, daily-pass)
    so the response shape stays consistent.
    """
    from app.models.fittbot_models.gym import Gym, GymLocation

    result = await db.execute(select(Gym).where(Gym.gym_id == gym_id))
    gym = result.scalars().first()
    if not gym:
        return {"gym_name": None, "address": None, "latitude": None, "longitude": None}

    loc_result = await db.execute(
        select(GymLocation).where(GymLocation.gym_id == gym_id)
    )
    loc = loc_result.scalars().first()

    return {
        "gym_name": gym.name,
        "gym_logo": gym.logo,
        "address": {
            "door_no": gym.door_no,
            "building": gym.building,
            "street": gym.street,
            "area": gym.area,
            "city": gym.city,
            "state": gym.state,
            "pincode": gym.pincode,
        },
        "latitude": float(loc.latitude) if loc and loc.latitude else None,
        "longitude": float(loc.longitude) if loc and loc.longitude else None,
    }


async def fetch_active_membership_offers(db: AsyncSession, gym_ids: list) -> Dict[int, Dict]:
    """Fetch active membership offers for given gym IDs.

    Returns {gym_id: {plan_id_or_None: offer_row, ...}}.
    Shared across listing, details, bookings, and payment processor.
    """
    from app.models.fittbot_models import GymMembershipOffer

    if not gym_ids:
        return {}

    now = datetime.now(_IST)
    stmt = select(GymMembershipOffer).where(
        GymMembershipOffer.gym_id.in_(gym_ids),
        GymMembershipOffer.is_active.is_(True),
        GymMembershipOffer.valid_from <= now,
        GymMembershipOffer.valid_until >= now,
    )
    result = await db.execute(stmt)

    offers_by_gym: Dict[int, Dict] = {}
    for offer in result.scalars().all():
        offers_by_gym.setdefault(offer.gym_id, {})[offer.plan_id] = offer

    return offers_by_gym


async def validate_coupon(
    db: AsyncSession,
    code: str,
    client_id: int,
) -> Optional[Dict]:

    from app.models.telecaller_models import TelecallerCoupon

    now = datetime.now(_IST)
    stmt = select(TelecallerCoupon).where(
        TelecallerCoupon.code == code,
        TelecallerCoupon.is_used.is_(False),
        TelecallerCoupon.expires_at >= now,
    )
    result = await db.execute(stmt)
    coupon = result.scalars().first()

    if not coupon:
        return None

    if coupon.client_id != client_id:
        return None

    return {
        "coupon_id": coupon.id,
        "discount_percent": coupon.discount_percent,
    }


def apply_coupon_discount(amount: int, discount_percent: int) -> int:
    """Apply coupon percentage discount to final amount. Returns discounted amount."""
    discount = int(amount * discount_percent / 100)
    return max(amount - discount, 0)


async def mark_coupon_used(db: AsyncSession, coupon_id: int, client_id: int):
    """Mark a telecaller coupon as used after successful payment."""
    from app.models.telecaller_models import TelecallerCoupon

    stmt = select(TelecallerCoupon).where(TelecallerCoupon.id == coupon_id)
    result = await db.execute(stmt)
    coupon = result.scalars().first()
    if coupon:
        coupon.is_used = True
        coupon.used_by = client_id
        coupon.used_at = datetime.now(_IST)


def to_12hr(time_str: str) -> str:
    """Convert 'HH:MM' (24h) to 'hh:MM AM/PM'."""
    h, m = int(time_str[:2]), time_str[3:5]
    period = "AM" if h < 12 else "PM"
    if h == 0:
        h = 12
    elif h > 12:
        h -= 12
    return f"{h}:{m} {period}"


def round_per_month_price(price: float) -> int:
    """Round per-month price to nearest ceiling number ending in 9.

    e.g. 700→709, 710→719, 721→729, 751→759
    """
    price_int = int(round(price))
    return (price_int // 10) * 10 + 9


def smart_round_price(price: float) -> int:
    """Round price to a consumer-friendly number ending in 49 or 99.

    e.g. 1000→999, 1020→1049, 1060→1099
    """
    price_int = int(round(price))
    last_two = price_int % 100
    if last_two == 0:
        return price_int - 1
    if last_two <= 50:
        return (price_int // 100) * 100 + 49
    return ((price_int // 100) + 1) * 100 - 1

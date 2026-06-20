from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime, timedelta
from sqlalchemy import func, and_, select, or_, distinct, cast, Integer, String
from typing import Dict, Any, List, Optional, Tuple
from decimal import Decimal
from pydantic import BaseModel
import calendar

from app.models.async_database import get_async_db
from app.models.dailypass_models import get_dailypass_session, DailyPass
from app.models.fittbot_models import (
    SessionBookingDay, SessionBooking, SessionPurchase,
    GymPlans, FittbotGymMembership, Gym, Client, ActiveUser
)
from app.models.adminmodels import Expenses
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.fittbot_api.v1.payments.models.entitlements import Entitlement
from app.models.nutrition_models import NutritionEligibility



class RevenueFilters(BaseModel):
    """Filters for revenue queries"""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    exclude_gym_id_one: bool = True  # Default: exclude gym_id = 1
    specific_gym_id: Optional[int] = None  # Filter for specific gym


class RevenueBreakdown(BaseModel):
    """Revenue breakdown by source (all values in PAISA)"""
    total_revenue: int
    daily_pass: int
    sessions: int
    fittbot_subscription: int
    gym_membership: int
    ai_credits: int
    ai_diet_coach: int


class AmortizedRevenueBreakdown(BaseModel):
    """Revenue breakdown with amortization for MRR (all values in PAISA)"""
    total_revenue: float
    daily_pass: int
    sessions: int
    fittbot_subscription: float  # Can be fractional due to amortization
    gym_membership: float  # Can be fractional due to amortization
    ai_credits: float  # Can be fractional due to amortization
    ai_diet_coach: float  # Can be fractional due to amortization


class DailyRevenuePoint(BaseModel):
    """Single day's revenue"""
    date: str
    revenue: float  # In rupees


class GymRevenuePoint(BaseModel):
    """Single gym's revenue"""
    gym_id: int
    gym_name: str
    revenue: float  # In rupees


class DetailedRevenueBreakdown(BaseModel):
    """
    Complete revenue breakdown with daily and gym-wise analytics.
    Used by /portal/admin/revenue page.
    """
    total_revenue: float  # In rupees
    source_split: Dict[str, int]  # Each source in PAISA
    source_split_rupees: Dict[str, float]  # Each source in rupees
    daily_revenue: List[DailyRevenuePoint]  # Daily revenue over time
    gym_breakdown: List[GymRevenuePoint]  # Gym-wise revenue


# ============================================================================
# SKU TO DURATION MAPPING (FOR FITTBOT SUBSCRIPTIONS)
# ============================================================================

PRODUCT_PLAN_MAPPING = {
    # Monthly subscriptions
    'FYMBLE_MONTHLY': 1,
    'APP_SUB_MONTHLY': 1,
    'FYMBLE_SUB_MONTHLY': 1,
    'MONTHLY_PREMIUM': 1,

    # Quarterly subscriptions (3 months)
    'FYMBLE_QUARTERLY': 3,
    'APP_SUB_QUARTERLY': 3,
    'FYMBLE_SUB_QUARTERLY': 3,
    'QUARTERLY_PREMIUM': 3,

    # Yearly subscriptions (12 months)
    'FYMBLE_YEARLY': 12,
    'APP_SUB_YEARLY': 12,
    'FYMBLE_SUB_YEARLY': 12,
    'YEARLY_PREMIUM': 12,
    'ANNUAL_PREMIUM': 12,
}


# ============================================================================
# CORE REVENUE QUERIES
# ============================================================================

async def get_daily_pass_revenue(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> int:
    """
    Get Daily Pass revenue for a date range.

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        exclude_gym_id_one: Whether to exclude gym_id = 1
        specific_gym_id: Filter for specific gym (overrides exclude_gym_id_one)

    Returns:
        Revenue in PAISA
    """
    try:
        conditions = [
            func.date(DailyPass.created_at) >= start_date,
            func.date(DailyPass.created_at) <= end_date
        ]

        if specific_gym_id is not None:
            conditions.append(DailyPass.gym_id == str(specific_gym_id))
        elif exclude_gym_id_one:
            conditions.append(DailyPass.gym_id != "1")

        stmt = (
            select(func.coalesce(func.sum(Payment.amount_minor), 0))
            .select_from(DailyPass)
            .join(Gym, cast(DailyPass.gym_id, Integer) == Gym.gym_id)
            .outerjoin(Payment, DailyPass.payment_id == Payment.provider_payment_id)
            .where(and_(*conditions))
        )
        result = await db.execute(stmt)
        revenue = result.scalar() or 0
        return int(revenue)

    except Exception as e:
        return 0


async def get_sessions_revenue(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> int:
    """
    Get Sessions revenue for a date range.

    IMPORTANT: SessionPurchase.payable_rupees is stored in RUPEES.
    This function converts to PAISA for consistency.

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        exclude_gym_id_one: Whether to exclude gym_id = 1
        specific_gym_id: Filter for specific gym (overrides exclude_gym_id_one)

    Returns:
        Revenue in PAISA
    """
    try:
        conditions = [SessionPurchase.status == "paid"]

        if specific_gym_id is not None:
            conditions.append(SessionPurchase.gym_id == specific_gym_id)
        elif exclude_gym_id_one:
            conditions.append(SessionPurchase.gym_id != 1)

        conditions.append(func.date(SessionPurchase.created_at) >= start_date)
        conditions.append(func.date(SessionPurchase.created_at) <= end_date)

        stmt = (
            select(func.coalesce(func.sum(SessionPurchase.payable_rupees), 0))
            .select_from(SessionPurchase)
            .join(Gym, SessionPurchase.gym_id == Gym.gym_id)
            .where(and_(*conditions))
        )

        result = await db.execute(stmt)
        revenue_rupees = result.scalar() or 0
        return int(revenue_rupees * 100) if revenue_rupees else 0

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0


async def get_fittbot_subscription_revenue(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> int:
    """
    Get Nutritionist Plan (Fymble Subscription) revenue for a date range.

    NEW LOGIC:
    - Query payments.payments table
    - Filter where payment_metadata -> 'flow' == 'nutrition_purchase_googleplay' or 'nutrition_package_razorpay'
    - Filter where status = 'captured'
    - Sum amount_minor values

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        exclude_gym_id_one: Whether to exclude gym_id = 1 (NOT APPLIED for nutritionist plans)
        specific_gym_id: Filter for specific gym (NOT APPLIED for nutritionist plans)

    Returns:
        Revenue in PAISA (amount_minor is already in minor units)
    """
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(func.coalesce(func.sum(Payment.amount_minor), 0))
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .join(
                NutritionEligibility,
                cast(Payment.order_id, String) == NutritionEligibility.source_id
            )
            .where(
                NutritionEligibility.source_type == "fymble_purchase",
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_purchase_googleplay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_package_razorpay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "basic_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "expert_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "elite_nutrition_plan"
                ),
                func.date(Payment.captured_at) >= start_date,
                func.date(Payment.captured_at) <= end_date,
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        result = await db.execute(stmt)
        return int(result.scalar() or 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0


async def get_ai_diet_coach_revenue(
    db: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> int:
    """Get AI Diet Coach revenue for a date range."""
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(func.coalesce(func.sum(Payment.amount_minor), 0))
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach",
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        if start_date and start_date.year > 1970:
            stmt = stmt.where(func.date(Payment.captured_at) >= start_date)
        if end_date and end_date.year < 2090:
            stmt = stmt.where(func.date(Payment.captured_at) <= end_date)
            
        result = await db.execute(stmt)
        return int(result.scalar() or 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0


async def get_gym_membership_revenue(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> int:
    """
    Get Gym Membership revenue for a date range.

    Filters by metadata conditions:
    - audit.source = "dailypass_checkout_api"
    - order_info.flow = "unified_gym_membership_with_sub"
    - order_info.flow = "unified_gym_membership_with_free_fittbot"

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        exclude_gym_id_one: Whether to exclude gym_id = 1
        specific_gym_id: Filter for specific gym (overrides exclude_gym_id_one)

    Returns:
        Revenue in PAISA
    """
    try:
        gym_meta_cond = or_(
            func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_sub",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_free_fittbot",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "gym_membership_with_bonus_credits",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "personal_training_with_bonus_credits"
        )

        oi_conditions = [
            OrderItem.order_id == Order.id,
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != ""
        ]
        if specific_gym_id is not None:
            oi_conditions.append(OrderItem.gym_id == str(specific_gym_id))
        elif exclude_gym_id_one:
            oi_conditions.append(OrderItem.gym_id != "1")

        gym_exists = (
            select(1)
            .select_from(OrderItem)
            .join(Gym, Gym.gym_id == cast(OrderItem.gym_id, Integer))
            .where(and_(*oi_conditions))
            .exists()
        )

        gym_conditions = [
            Payment.status == "captured",
            Order.status == "paid",
            Order.customer_id.isnot(None),
            func.date(Payment.captured_at) >= start_date,
            func.date(Payment.captured_at) <= end_date,
            gym_meta_cond,
            gym_exists
        ]

        subq = (
            select(
                Order.id.label("order_id"),
                Order.gross_amount_minor.label("gross_amount_minor")
            )
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .join(Client, Client.client_id == cast(Order.customer_id, Integer))
            .where(*gym_conditions)
            .distinct()
            .subquery()
        )

        stmt = select(func.coalesce(func.sum(subq.c.gross_amount_minor), 0)).select_from(subq)

        result = await db.execute(stmt)
        revenue = result.scalar() or 0
        return int(revenue)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0


# ============================================================================
# HIGH-LEVEL REVENUE FUNCTIONS
# ============================================================================

async def get_revenue_breakdown(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> RevenueBreakdown:
    """
    Get complete revenue breakdown for a date range.

    This is the MAIN function that should be used by all APIs.

    NOTE: Queries run sequentially (not concurrently) because SQLAlchemy's
    async session doesn't support concurrent operations on the same session.

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        exclude_gym_id_one: Whether to exclude gym_id = 1
        specific_gym_id: Filter for specific gym

    Returns:
        RevenueBreakdown with all values in PAISA
    """
    # Run queries sequentially (SQLAlchemy async session doesn't support concurrent operations)
    daily_pass = await get_daily_pass_revenue(db, start_date, end_date, exclude_gym_id_one, specific_gym_id)
    sessions = await get_sessions_revenue(db, start_date, end_date, exclude_gym_id_one, specific_gym_id)
    fittbot_subscription = await get_fittbot_subscription_revenue(db, start_date, end_date, exclude_gym_id_one, specific_gym_id)
    gym_membership = await get_gym_membership_revenue(db, start_date, end_date, exclude_gym_id_one, specific_gym_id)
    ai_credits = await get_ai_credits_revenue(db, start_date, end_date, exclude_gym_id_one, specific_gym_id)
    ai_diet_coach = await get_ai_diet_coach_revenue(db, start_date, end_date, exclude_gym_id_one, specific_gym_id)

    total_revenue = daily_pass + sessions + fittbot_subscription + gym_membership + ai_credits + ai_diet_coach

    return RevenueBreakdown(
        total_revenue=total_revenue,
        daily_pass=daily_pass,
        sessions=sessions,
        fittbot_subscription=fittbot_subscription,
        gym_membership=gym_membership,
        ai_credits=ai_credits,
        ai_diet_coach=ai_diet_coach
    )


async def compute_actual_booking_counts(db: AsyncSession, start_date_obj, end_date_obj):
    """
    Compute actual booking counts where each purchase/pass represents exactly 1 booking.
    """
    EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]

    # Daily Pass - Count each pass as exactly 1 booking
    dp_conditions = [DailyPass.gym_id != "1"]
    if start_date_obj:
        dp_conditions.append(func.date(DailyPass.created_at) >= start_date_obj)
    if end_date_obj:
        dp_conditions.append(func.date(DailyPass.created_at) <= end_date_obj)

    dp_stmt = (
        select(func.count(DailyPass.id).label("count"))
        .select_from(DailyPass)
        .join(Gym, cast(DailyPass.gym_id, Integer) == Gym.gym_id)
        .where(*dp_conditions)
    )
    dp_count = (await db.execute(dp_stmt)).scalar() or 0

    # Fitness Class (Session) - Count each paid session purchase as exactly 1 booking
    sess_conditions = [SessionPurchase.status == "paid", SessionPurchase.gym_id != 1]
    if start_date_obj:
        sess_conditions.append(func.date(SessionPurchase.created_at) >= start_date_obj)
    if end_date_obj:
        sess_conditions.append(func.date(SessionPurchase.created_at) <= end_date_obj)

    sess_stmt = (
        select(func.count(SessionPurchase.id).label("count"))
        .select_from(SessionPurchase)
        .join(Gym, SessionPurchase.gym_id == Gym.gym_id)
        .where(*sess_conditions)
    )
    sess_count = (await db.execute(sess_stmt)).scalar() or 0

    # Nutrition Plans - Count each captured payment as 1 booking
    nutri_conditions = [
        Payment.status == "captured",
        or_(
            func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_purchase_googleplay",
            func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_package_razorpay",
            func.json_extract(Payment.payment_metadata, "$.flow") == "basic_nutrition_plan",
            func.json_extract(Payment.payment_metadata, "$.flow") == "expert_nutrition_plan",
            func.json_extract(Payment.payment_metadata, "$.flow") == "elite_nutrition_plan"
        )
    ]
    if start_date_obj:
        nutri_conditions.append(func.date(Payment.captured_at) >= start_date_obj)
    if end_date_obj:
        nutri_conditions.append(func.date(Payment.captured_at) <= end_date_obj)

    nutri_stmt = (
        select(func.count(Payment.id).label("count"))
        .select_from(Payment)
        .outerjoin(Client, Payment.customer_id == Client.client_id)
        .join(
            NutritionEligibility,
            cast(Payment.order_id, String) == NutritionEligibility.source_id
        )
        .where(NutritionEligibility.source_type == "fymble_purchase")
        .where(*nutri_conditions)
        .where(~Client.contact.in_(EXCLUDED_CONTACTS))
    )
    nutri_count = (await db.execute(nutri_stmt)).scalar() or 0

    # Gym Membership - Count each paid gym membership order as 1 booking
    gym_meta_cond = or_(
        func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
        func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_sub",
        func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_free_fittbot",
        func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "gym_membership_with_bonus_credits",
        func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "personal_training_with_bonus_credits"
    )
    gym_exists = (
        select(1)
        .select_from(OrderItem)
        .join(Gym, Gym.gym_id == cast(OrderItem.gym_id, Integer))
        .where(
            OrderItem.order_id == Order.id,
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != "",
            OrderItem.gym_id != "1"
        )
        .exists()
    )
    gym_conditions = [
        Payment.status == "captured",
        Order.status == "paid",
        Order.customer_id.isnot(None),
        gym_meta_cond,
        gym_exists
    ]
    if start_date_obj:
        gym_conditions.append(func.date(Payment.captured_at) >= start_date_obj)
    if end_date_obj:
        gym_conditions.append(func.date(Payment.captured_at) <= end_date_obj)

    gym_subq = (
        select(Order.id.label("order_id"))
        .select_from(Payment)
        .join(Order, Order.id == Payment.order_id)
        .join(Client, Client.client_id == cast(Order.customer_id, Integer))
        .where(*gym_conditions)
        .distinct()
        .subquery()
    )
    gym_stmt = select(func.count(gym_subq.c.order_id).label("count")).select_from(gym_subq)
    gym_count = (await db.execute(gym_stmt)).scalar() or 0

    # AI Credits - Count each captured payment as 1 booking
    ai_conditions = [
        Payment.status == "captured",
        or_(
            func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits",
            func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits_razorpay"
        )
    ]
    if start_date_obj:
        ai_conditions.append(func.date(Payment.captured_at) >= start_date_obj)
    if end_date_obj:
        ai_conditions.append(func.date(Payment.captured_at) <= end_date_obj)

    ai_stmt = (
        select(func.count(Payment.id).label("count"))
        .select_from(Payment)
        .outerjoin(Client, Payment.customer_id == Client.client_id)
        .where(*ai_conditions)
        .where(~Client.contact.in_(EXCLUDED_CONTACTS))
    )
    ai_count = (await db.execute(ai_stmt)).scalar() or 0

    # AI Diet Coach - Count each captured payment as 1 booking
    ai_diet_conditions = [
        Payment.status == "captured",
        func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach"
    ]
    if start_date_obj:
        ai_diet_conditions.append(func.date(Payment.captured_at) >= start_date_obj)
    if end_date_obj:
        ai_diet_conditions.append(func.date(Payment.captured_at) <= end_date_obj)

    ai_diet_stmt = (
        select(func.count(Payment.id).label("count"))
        .select_from(Payment)
        .outerjoin(Client, Payment.customer_id == Client.client_id)
        .where(*ai_diet_conditions)
        .where(~Client.contact.in_(EXCLUDED_CONTACTS))
    )
    ai_diet_count = (await db.execute(ai_diet_stmt)).scalar() or 0

    return {
        "daily_pass": dp_count,
        "session": sess_count,
        "nutrition_plan": nutri_count,
        "gym_membership": gym_count,
        "ai_credits": ai_count,
        "ai_diet_coach": ai_diet_count,
    }


async def get_total_bookings_count(db: AsyncSession, start_date_obj, end_date_obj) -> int:
    """
    Get the total actual booking count (each pass or purchase counted as exactly 1).
    """
    actual_counts = await compute_actual_booking_counts(db, start_date_obj, end_date_obj)
    return int(
        (actual_counts.get("daily_pass") or 0) +
        (actual_counts.get("session") or 0) +
        (actual_counts.get("nutrition_plan") or 0) +
        (actual_counts.get("gym_membership") or 0) +
        (actual_counts.get("ai_credits") or 0) +
        (actual_counts.get("ai_diet_coach") or 0)
    )


async def get_daily_booking_counts(
    db: AsyncSession,
    start_date: date,
    end_date: date,
) -> Dict[str, int]:
   
    from sqlalchemy import union_all
    from app.models.fittbot_models import Gym
    from app.fittbot_api.v1.payments.models.orders import Order, OrderItem

    EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    try:
        # ── Source 1: Daily Pass ──
        dp_daily = (
            select(
                func.date(DailyPass.created_at).label("day"),
                func.count(DailyPass.id).label("cnt")
            )
            .select_from(DailyPass)
            .join(Gym, cast(DailyPass.gym_id, Integer) == Gym.gym_id)
            .where(
                DailyPass.gym_id != "1",
                DailyPass.created_at >= start_dt,
                DailyPass.created_at <= end_dt,
            )
            .group_by(func.date(DailyPass.created_at))
        )

        # ── Source 2: Session / Fitness Class ──
        sess_daily = (
            select(
                func.date(SessionPurchase.created_at).label("day"),
                func.count(SessionPurchase.id).label("cnt")
            )
            .select_from(SessionPurchase)
            .join(Gym, SessionPurchase.gym_id == Gym.gym_id)
            .where(
                SessionPurchase.status == "paid",
                SessionPurchase.gym_id != 1,
                SessionPurchase.created_at >= start_dt,
                SessionPurchase.created_at <= end_dt,
            )
            .group_by(func.date(SessionPurchase.created_at))
        )

        # ── Source 3: Nutrition Plans ──
        nutri_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(Payment.id).label("cnt")
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .join(
                NutritionEligibility,
                cast(Payment.order_id, String) == NutritionEligibility.source_id
            )
            .where(
                NutritionEligibility.source_type == "fymble_purchase",
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_purchase_googleplay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_package_razorpay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "basic_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "expert_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "elite_nutrition_plan",
                ),
                Payment.captured_at >= start_dt,
                Payment.captured_at <= end_dt,
                ~Client.contact.in_(EXCLUDED_CONTACTS),
            )
            .group_by(func.date(Payment.captured_at))
        )

        # ── Source 4: AI Credits ──
        ai_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(Payment.id).label("cnt")
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits_razorpay",
                ),
                Payment.captured_at >= start_dt,
                Payment.captured_at <= end_dt,
                ~Client.contact.in_(EXCLUDED_CONTACTS),
            )
            .group_by(func.date(Payment.captured_at))
        )

        # ── Source 5: AI Diet Coach ──
        ai_diet_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(Payment.id).label("cnt")
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach",
                Payment.captured_at >= start_dt,
                Payment.captured_at <= end_dt,
                ~Client.contact.in_(EXCLUDED_CONTACTS),
            )
            .group_by(func.date(Payment.captured_at))
        )

        # ── Source 6: Gym Membership ──
        gym_meta_cond = or_(
            func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_sub",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_free_fittbot",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "gym_membership_with_bonus_credits",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "personal_training_with_bonus_credits",
        )
        gym_exists = (
            select(1)
            .select_from(OrderItem)
            .join(Gym, Gym.gym_id == cast(OrderItem.gym_id, Integer))
            .where(
                OrderItem.order_id == Order.id,
                OrderItem.gym_id.isnot(None),
                OrderItem.gym_id != "",
                OrderItem.gym_id != "1",
            )
            .exists()
        )
        gym_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(func.distinct(Order.id)).label("cnt")
            )
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .join(Client, Client.client_id == cast(Order.customer_id, Integer))
            .where(
                Payment.status == "captured",
                Order.status == "paid",
                Order.customer_id.isnot(None),
                gym_meta_cond,
                gym_exists,
                Payment.captured_at >= start_dt,
                Payment.captured_at <= end_dt,
            )
            .group_by(func.date(Payment.captured_at))
        )

        # ── UNION ALL → aggregate total per day ──
        union_src = union_all(
            dp_daily, sess_daily, nutri_daily, ai_daily, ai_diet_daily, gym_daily
        ).alias("all_bookings")

        grouped_stmt = (
            select(
                union_src.c.day,
                func.sum(union_src.c.cnt).label("total")
            )
            .group_by(union_src.c.day)
        )
        result = await db.execute(grouped_stmt)
        rows = result.all()

        return {
            (r[0].strftime("%Y-%m-%d") if hasattr(r[0], "strftime") else str(r[0])): int(r[1])
            for r in rows
        }

    except Exception:
        import traceback
        traceback.print_exc()
        return {}


async def get_daily_active_user_counts(
    db: AsyncSession,
    start_date: date,
    end_date: date,
) -> Dict[str, int]:
    """
    Get day-wise active user counts (distinct client_id logins) for a date range.
    Returns a dict of { "YYYY-MM-DD": count } for every day that had logins.
    Excludes users from gym_id = 1.
    """
    from app.models.fittbot_models import Client, ActiveUser

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    try:
        stmt = (
            select(
                func.date(ActiveUser.created_at).label("day"),
                func.count(func.distinct(ActiveUser.client_id)).label("cnt")
            )
            .select_from(ActiveUser)
            .join(Client, ActiveUser.client_id == Client.client_id)
            .where(
                and_(
                    or_(Client.gym_id != 1, Client.gym_id.is_(None)),
                    ActiveUser.created_at >= start_dt,
                    ActiveUser.created_at <= end_dt
                )
            )
            .group_by(func.date(ActiveUser.created_at))
        )
        result = await db.execute(stmt)
        rows = result.all()

        return {
            (r[0].strftime("%Y-%m-%d") if hasattr(r[0], "strftime") else str(r[0])): int(r[1])
            for r in rows
        }

    except Exception:
        import traceback
        traceback.print_exc()
        return {}


async def get_peak_metric_days(db: AsyncSession) -> dict:
    """
    Get the peak date and peak count for:
      - Users (daily new signups)
      - Bookings (daily total bookings across all 6 sources)
      - Active Users (daily active distinct user logins)
    Excludes test gym_id = 1.
    """
    from app.models.fittbot_models import Client, ActiveUser, Gym, SessionPurchase
    from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
    from app.fittbot_api.v1.payments.models.payments import Payment
    from app.models.dailypass_models import DailyPass
    from app.models.nutrition_models import NutritionEligibility
    from sqlalchemy import union_all, select, func, or_, cast, Integer, String

    EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]

    res = {
        "users": [],
        "bookings": [],
        "active_users": []
    }

    try:
        # ── 1. Peak Signup Day (Users) ──
        users_stmt = (
            select(
                func.date(Client.created_at).label("day"),
                func.count(Client.client_id).label("cnt")
            )
            .where(or_(Client.gym_id != 1, Client.gym_id.is_(None)))
            .group_by(func.date(Client.created_at))
            .order_by(func.count(Client.client_id).desc(), func.date(Client.created_at).desc())
            .limit(50)
        )
        users_res = await db.execute(users_stmt)
        users_rows = users_res.all()
        res["users"] = [
            {
                "date": row[0].strftime("%Y-%m-%d") if hasattr(row[0], "strftime") else str(row[0]),
                "count": int(row[1])
            }
            for row in users_rows if row[0]
        ]

        # ── 2. Peak Active Users Day ──
        active_stmt = (
            select(
                func.date(ActiveUser.created_at).label("day"),
                func.count(func.distinct(ActiveUser.client_id)).label("cnt")
            )
            .select_from(ActiveUser)
            .join(Client, ActiveUser.client_id == Client.client_id)
            .where(or_(Client.gym_id != 1, Client.gym_id.is_(None)))
            .group_by(func.date(ActiveUser.created_at))
            .order_by(func.count(func.distinct(ActiveUser.client_id)).desc(), func.date(ActiveUser.created_at).desc())
            .limit(50)
        )
        active_res = await db.execute(active_stmt)
        active_rows = active_res.all()
        res["active_users"] = [
            {
                "date": row[0].strftime("%Y-%m-%d") if hasattr(row[0], "strftime") else str(row[0]),
                "count": int(row[1])
            }
            for row in active_rows if row[0]
        ]

        # ── 3. Peak Bookings Day ──
        # dp_daily query
        dp_daily = (
            select(
                func.date(DailyPass.created_at).label("day"),
                func.count(DailyPass.id).label("cnt")
            )
            .select_from(DailyPass)
            .join(Gym, cast(DailyPass.gym_id, Integer) == Gym.gym_id)
            .where(DailyPass.gym_id != "1")
            .group_by(func.date(DailyPass.created_at))
        )
        # sess_daily query
        sess_daily = (
            select(
                func.date(SessionPurchase.created_at).label("day"),
                func.count(SessionPurchase.id).label("cnt")
            )
            .select_from(SessionPurchase)
            .join(Gym, SessionPurchase.gym_id == Gym.gym_id)
            .where(
                SessionPurchase.status == "paid",
                SessionPurchase.gym_id != 1
            )
            .group_by(func.date(SessionPurchase.created_at))
        )
        # nutri_daily query
        nutri_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(Payment.id).label("cnt")
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .join(
                NutritionEligibility,
                cast(Payment.order_id, String) == NutritionEligibility.source_id
            )
            .where(
                NutritionEligibility.source_type == "fymble_purchase",
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_purchase_googleplay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_package_razorpay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "basic_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "expert_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "elite_nutrition_plan",
                ),
                ~Client.contact.in_(EXCLUDED_CONTACTS),
            )
            .group_by(func.date(Payment.captured_at))
        )
        # ai_daily query
        ai_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(Payment.id).label("cnt")
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits_razorpay",
                ),
                ~Client.contact.in_(EXCLUDED_CONTACTS),
            )
            .group_by(func.date(Payment.captured_at))
        )
        # ai_diet_daily query
        ai_diet_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(Payment.id).label("cnt")
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach",
                ~Client.contact.in_(EXCLUDED_CONTACTS),
            )
            .group_by(func.date(Payment.captured_at))
        )
        # gym_daily query
        gym_meta_cond = or_(
            func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_sub",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_free_fittbot",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "gym_membership_with_bonus_credits",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "personal_training_with_bonus_credits",
        )
        gym_exists = (
            select(1)
            .select_from(OrderItem)
            .join(Gym, Gym.gym_id == cast(OrderItem.gym_id, Integer))
            .where(
                OrderItem.order_id == Order.id,
                OrderItem.gym_id.isnot(None),
                OrderItem.gym_id != "",
                OrderItem.gym_id != "1",
            )
            .exists()
        )
        gym_daily = (
            select(
                func.date(Payment.captured_at).label("day"),
                func.count(func.distinct(Order.id)).label("cnt")
            )
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .join(Client, Client.client_id == cast(Order.customer_id, Integer))
            .where(
                Payment.status == "captured",
                Order.status == "paid",
                Order.customer_id.isnot(None),
                gym_meta_cond,
                gym_exists,
            )
            .group_by(func.date(Payment.captured_at))
        )

        union_src = union_all(
            dp_daily, sess_daily, nutri_daily, ai_daily, ai_diet_daily, gym_daily
        ).alias("all_bookings")

        bookings_stmt = (
            select(
                union_src.c.day,
                func.sum(union_src.c.cnt).label("total")
            )
            .group_by(union_src.c.day)
            .order_by(func.sum(union_src.c.cnt).desc(), union_src.c.day.desc())
            .limit(50)
        )
        bookings_res = await db.execute(bookings_stmt)
        bookings_rows = bookings_res.all()
        res["bookings"] = [
            {
                "date": row[0].strftime("%Y-%m-%d") if hasattr(row[0], "strftime") else str(row[0]),
                "count": int(row[1])
            }
            for row in bookings_rows if row[0]
        ]

    except Exception:
        import traceback
        traceback.print_exc()

    return res


async def get_ai_credits_revenue(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    exclude_gym_id_one: bool = True,
    specific_gym_id: Optional[int] = None
) -> int:
    """
    Get AI Credits revenue for a date range.

    NEW LOGIC:
    - Query payments.payments table
    - Filter where payment_metadata -> 'flow' == 'food_scanner_credits' or 'food_scanner_credits_razorpay'
    - Filter where status = 'captured'
    - Filter where captured_at is within the date range
    - Sum amount_minor values

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        exclude_gym_id_one: Whether to exclude gym_id = 1 (NOT APPLIED for AI credits)
        specific_gym_id: Filter for specific gym (NOT APPLIED for AI credits)

    Returns:
        Revenue in PAISA
    """
    total_revenue = 0

    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(func.coalesce(func.sum(Payment.amount_minor), 0))
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits_razorpay"
                ),
                func.date(Payment.captured_at) >= start_date,
                func.date(Payment.captured_at) <= end_date,
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        result = await db.execute(stmt)
        return int(result.scalar() or 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0


# ============================================================================
# MRR-SPECIFIC FUNCTIONS (WITH AMORTIZATION)
# ============================================================================

async def get_amortized_fittbot_subscription_revenue(
    db: AsyncSession,
    target_month_start: date,
    target_month_end: date,
    exclude_gym_id_one: bool = True
) -> float:
    """
    Get Nutritionist Plan (Fymble Subscription) revenue for MRR.

    NEW LOGIC:
    - Query payments.payments table
    - Filter where payment_metadata -> 'flow' == 'nutrition_purchase_googleplay' or 'nutrition_package_razorpay'
    - Filter where status = 'captured'
    - Filter where captured_at is within the target month
    - Sum amount_minor values

    Note: Nutritionist plans from Google Play are one-time purchases,
    so no amortization is needed. We count them when captured in the target month.

    Args:
        db: AsyncSession
        target_month_start: First day of target month
        target_month_end: Last day of target month
        exclude_gym_id_one: Whether to exclude gym_id = 1 (NOT APPLIED for nutritionist plans)

    Returns:
        Revenue in PAISA
    """
    total_revenue = 0.0

    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(func.coalesce(func.sum(Payment.amount_minor), 0))
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .join(
                NutritionEligibility,
                cast(Payment.order_id, String) == NutritionEligibility.source_id
            )
            .where(
                NutritionEligibility.source_type == "fymble_purchase",
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_purchase_googleplay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_package_razorpay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "basic_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "expert_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "elite_nutrition_plan"
                ),
                func.date(Payment.captured_at) >= target_month_start,
                func.date(Payment.captured_at) <= target_month_end,
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        result = await db.execute(stmt)
        return float(result.scalar() or 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0.0


async def get_amortized_ai_diet_coach_revenue(
    db: AsyncSession,
    target_month_start: Optional[date] = None,
    target_month_end: Optional[date] = None,
    exclude_gym_id_one: bool = True
) -> float:
    """Get amortized AI Diet Coach revenue for MRR."""
    total_revenue = 0.0
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(func.coalesce(func.sum(Payment.amount_minor), 0))
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach",
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        if target_month_start and target_month_start.year > 1970:
            stmt = stmt.where(func.date(Payment.captured_at) >= target_month_start)
        if target_month_end and target_month_end.year < 2090:
            stmt = stmt.where(func.date(Payment.captured_at) <= target_month_end)
            
        result = await db.execute(stmt)
        return float(result.scalar() or 0)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 0.0

async def get_amortized_gym_membership_revenue(
    db: AsyncSession,
    target_month_start: date,
    target_month_end: date,
    exclude_gym_id_one: bool = True
) -> float:
    """
    Get amortized Gym Membership revenue for MRR.

    Includes ALL memberships active during the target month,
    with revenue distributed monthly based on plan duration.

    Args:
        db: AsyncSession
        target_month_start: First day of target month
        target_month_end: Last day of target month
        exclude_gym_id_one: Whether to exclude gym_id = 1

    Returns:
        Amortized revenue in PAISA (can be fractional)
    """
    total_revenue = 0.0

    try:
        # Fetch payments and orders with metadata conditions
        payment_stmt = (
            select(Payment, Order)
            .join(Order, Order.id == Payment.order_id)
            .where(Payment.status == "captured")
            .where(Order.status == "paid")
        )

        payment_result = await db.execute(payment_stmt)
        all_payments = payment_result.all()

        if not all_payments:
            return 0.0

        # Collect order IDs
        order_ids = [row.Order.id for row in all_payments]

        # Fetch order items
        order_items_conditions = [
            OrderItem.order_id.in_(order_ids),
            OrderItem.gym_id.isnot(None)
        ]

        if exclude_gym_id_one:
            order_items_conditions.append(OrderItem.gym_id != "1")

        order_items_stmt = (
            select(OrderItem)
            .join(Gym, Gym.gym_id == cast(OrderItem.gym_id, Integer))
            .where(and_(*order_items_conditions))
        )
        order_items_result = await db.execute(order_items_stmt)
        order_items = order_items_result.scalars().all()

        # Mappings
        order_gym_mapping = {}
        order_item_mapping = {}
        for item in order_items:
            if item.gym_id and item.gym_id.strip() and item.gym_id.isdigit():
                order_gym_mapping[item.order_id] = int(item.gym_id)
                order_item_mapping[item.order_id] = item

        # Fetch entitlements
        order_item_ids = [item.id for item in order_items]
        entitlement_mapping = {}
        if order_item_ids:
            entitlements_stmt = (
                select(Entitlement)
                .where(Entitlement.order_item_id.in_(order_item_ids))
            )
            entitlements_result = await db.execute(entitlements_stmt)
            entitlements = entitlements_result.scalars().all()
            for ent in entitlements:
                entitlement_mapping[ent.order_item_id] = ent

        # Fetch FittbotGymMembership records
        entitlement_ids = [ent.id for ent in entitlement_mapping.values()]
        membership_mapping = {}
        if entitlement_ids:
            memberships_stmt = (
                select(FittbotGymMembership)
                .where(FittbotGymMembership.entitlement_id.in_(entitlement_ids))
            )
            memberships_result = await db.execute(memberships_stmt)
            memberships = memberships_result.scalars().all()
            for memb in memberships:
                membership_mapping[memb.entitlement_id] = memb

        # Fetch GymPlans for durations
        plan_ids = list({m.plan_id for m in membership_mapping.values() if m.plan_id})
        plan_duration_mapping = {}
        if plan_ids:
            plans_stmt = (
                select(GymPlans)
                .where(GymPlans.id.in_(plan_ids))
            )
            plans_result = await db.execute(plans_stmt)
            plans = plans_result.scalars().all()
            for plan in plans:
                plan_duration_mapping[plan.id] = plan.duration or 1

        # Process each payment
        for row in all_payments:
            payment = row.Payment
            order = row.Order

            payment_date = payment.captured_at.date() if payment.captured_at else None
            if not payment_date:
                continue

            # Check metadata conditions
            if not order.order_metadata or not isinstance(order.order_metadata, dict):
                continue

            metadata = order.order_metadata

            condition1 = False
            if metadata.get("audit") and isinstance(metadata.get("audit"), dict):
                if metadata["audit"].get("source") == "dailypass_checkout_api":
                    condition1 = True

            condition2 = False
            if metadata.get("order_info") and isinstance(metadata.get("order_info"), dict):
                if metadata["order_info"].get("flow") == "unified_gym_membership_with_sub":
                    condition2 = True

            condition3 = False
            if metadata.get("order_info") and isinstance(metadata.get("order_info"), dict):
                if metadata["order_info"].get("flow") == "unified_gym_membership_with_free_fittbot":
                    condition3 = True

            condition4 = False
            if metadata.get("order_info") and isinstance(metadata.get("order_info"), dict):
                if metadata["order_info"].get("flow") == "gym_membership_with_bonus_credits":
                    condition4 = True

            condition5 = False
            if metadata.get("order_info") and isinstance(metadata.get("order_info"), dict):
                if metadata["order_info"].get("flow") == "personal_training_with_bonus_credits":
                    condition5 = True

            if not (condition1 or condition2 or condition3 or condition4 or condition5):
                continue

            if order.id not in order_gym_mapping:
                continue

            # Get duration
            duration_months = 1
            if order.id in order_item_mapping:
                order_item = order_item_mapping[order.id]
                if order_item.id in entitlement_mapping:
                    entitlement = entitlement_mapping[order_item.id]
                    if entitlement.id in membership_mapping:
                        membership = membership_mapping[entitlement.id]
                        if membership.plan_id and membership.plan_id in plan_duration_mapping:
                            duration_months = plan_duration_mapping[membership.plan_id] or 1

            # Calculate validity
            validity_end_date = (
                date(payment_date.year, payment_date.month, 1) +
                timedelta(days=32 * duration_months)
            )
            validity_end_date = date(validity_end_date.year, validity_end_date.month, 1) - timedelta(days=1)

            # Check if target month overlaps with validity period
            if validity_end_date >= target_month_start and payment_date <= target_month_end:
                amount = order.gross_amount_minor or 0
                monthly_amount = amount / duration_months
                total_revenue += monthly_amount

    except Exception as e:
        import traceback
        traceback.print_exc()

    return total_revenue


async def get_mrr_revenue_breakdown(
    db: AsyncSession,
    target_month_start: date,
    target_month_end: date,
    exclude_gym_id_one: bool = True
) -> AmortizedRevenueBreakdown:
    """
    Get MRR revenue breakdown with amortization for recurring products.

    - Daily Pass: Full amount for passes purchased in target month
    - Sessions: Full amount for sessions booked in target month
    - Fymble Subscription: Monthly amortized amount for ALL active subscriptions
    - Gym Membership: Monthly amortized amount for ALL active memberships

    NOTE: Queries run sequentially (not concurrently) because SQLAlchemy's
    async session doesn't support concurrent operations on the same session.

    Args:
        db: AsyncSession
        target_month_start: First day of target month
        target_month_end: Last day of target month
        exclude_gym_id_one: Whether to exclude gym_id = 1

    Returns:
        AmortizedRevenueBreakdown with values in PAISA
    """
    # Run queries sequentially (SQLAlchemy async session doesn't support concurrent operations)
    daily_pass = await get_daily_pass_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)
    sessions = await get_sessions_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)
    fittbot_subscription = await get_amortized_fittbot_subscription_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)
    gym_membership = await get_gym_membership_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)
    # gym_membership = await get_amortized_gym_membership_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)
    ai_credits = await get_ai_credits_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)
    ai_diet_coach = await get_amortized_ai_diet_coach_revenue(db, target_month_start, target_month_end, exclude_gym_id_one)

    total_revenue = float(daily_pass) + float(sessions) + fittbot_subscription + gym_membership + float(ai_credits) + float(ai_diet_coach)

    return AmortizedRevenueBreakdown(
        total_revenue=total_revenue,
        daily_pass=daily_pass,
        sessions=sessions,
        fittbot_subscription=fittbot_subscription,
        gym_membership=gym_membership,
        ai_credits=float(ai_credits),
        ai_diet_coach=float(ai_diet_coach)
    )


# ============================================================================
# DETAILED REVENUE FUNCTIONS (with daily & gym breakdowns)
# ============================================================================

async def get_detailed_revenue_with_breakdowns(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    source: Optional[str] = None,
    specific_gym_id: Optional[int] = None,
    exclude_gym_id_one: bool = True
) -> DetailedRevenueBreakdown:
    """
    Get complete revenue breakdown with daily and gym-wise analytics.
    Used by /portal/admin/revenue page.

    Args:
        db: AsyncSession
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        source: Filter by specific source (daily_pass, sessions, fittbot_subscription, gym_membership, ai_credits)
        specific_gym_id: Filter for specific gym
        exclude_gym_id_one: Whether to exclude gym_id = 1

    Returns:
        DetailedRevenueBreakdown with total revenue, source splits, daily revenue, and gym breakdown
    """
    from app.models.fittbot_models import Gym

    # Track revenue by date and gym
    daily_revenue = {}  # date -> amount (in PAISA)
    gym_revenue = {}   # gym_id -> amount (in PAISA)

    # Initialize source revenue
    source_revenue_paisa = {
        "daily_pass": 0,
        "sessions": 0,
        "fittbot_subscription": 0,
        "gym_membership": 0,
        "ai_credits": 0,
        "ai_diet_coach": 0
    }

    # Determine which sources to query
    query_sources = []
    if not source or source == "daily_pass":
        query_sources.append("daily_pass")
    if not source or source == "sessions":
        query_sources.append("sessions")
    if (not source or source == "fittbot_subscription") and not specific_gym_id:
        query_sources.append("fittbot_subscription")
    if not source or source == "gym_membership":
        query_sources.append("gym_membership")
    if (not source or source == "ai_credits") and not specific_gym_id:
        query_sources.append("ai_credits")
    if (not source or source == "ai_diet_coach") and not specific_gym_id:
        query_sources.append("ai_diet_coach")

    # Query each source and collect daily/gym breakdowns
    for query_source in query_sources:
        if query_source == "daily_pass":
            await _get_daily_pass_detailed(
                db, start_date, end_date, specific_gym_id, exclude_gym_id_one,
                daily_revenue, gym_revenue, source_revenue_paisa
            )
        elif query_source == "sessions":
            await _get_sessions_detailed(
                db, start_date, end_date, specific_gym_id, exclude_gym_id_one,
                daily_revenue, gym_revenue, source_revenue_paisa
            )
        elif query_source == "fittbot_subscription":
            await _get_fittbot_subscription_detailed(
                db, start_date, end_date,
                daily_revenue, source_revenue_paisa
            )
        elif query_source == "gym_membership":
            await _get_gym_membership_detailed(
                db, start_date, end_date, specific_gym_id, exclude_gym_id_one,
                daily_revenue, gym_revenue, source_revenue_paisa
            )
        elif query_source == "ai_credits":
            await _get_ai_credits_detailed(
                db, start_date, end_date,
                daily_revenue, source_revenue_paisa
            )
        elif query_source == "ai_diet_coach":
            await _get_ai_diet_coach_detailed(
                db, start_date, end_date,
                daily_revenue, source_revenue_paisa
            )

    # Calculate total revenue (in PAISA)
    total_revenue_paisa = sum(source_revenue_paisa.values())

    # Convert daily_revenue to sorted array
    revenue_over_time = [
        DailyRevenuePoint(date=date, revenue=amount / 100)
        for date, amount in sorted(daily_revenue.items())
    ]

    # Get gym names for gym-wise breakdown
    gym_names = {}
    if gym_revenue:
        gym_ids = list(gym_revenue.keys())
        gym_stmt = select(Gym.gym_id, Gym.name).where(Gym.gym_id.in_(gym_ids))
        gym_result = await db.execute(gym_stmt)
        for gym_id_val, gym_name in gym_result.all():
            gym_names[gym_id_val] = gym_name

    # Convert gym_revenue to array
    gym_breakdown = [
        GymRevenuePoint(
            gym_id=gym_id,
            gym_name=gym_names.get(gym_id, f"Gym {gym_id}"),
            revenue=amount / 100
        )
        for gym_id, amount in sorted(gym_revenue.items(), key=lambda x: x[1], reverse=True)
    ]

    # Convert source revenue to rupees for display
    source_split_rupees = {
        "daily_pass": source_revenue_paisa["daily_pass"] / 100,
        "sessions": source_revenue_paisa["sessions"] / 100,  # Convert from paisa to rupees
        "fittbot_subscription": source_revenue_paisa["fittbot_subscription"] / 100,
        "gym_membership": source_revenue_paisa["gym_membership"] / 100,
        "ai_credits": source_revenue_paisa["ai_credits"] / 100,
        "ai_diet_coach": source_revenue_paisa["ai_diet_coach"] / 100
    }

    return DetailedRevenueBreakdown(
        total_revenue=total_revenue_paisa / 100,
        source_split=source_revenue_paisa,
        source_split_rupees=source_split_rupees,
        daily_revenue=revenue_over_time,
        gym_breakdown=gym_breakdown
    )


async def _get_daily_pass_detailed(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    specific_gym_id: Optional[int],
    exclude_gym_id_one: bool,
    daily_revenue: dict,
    gym_revenue: dict,
    source_revenue: dict
):
    """Get Daily Pass revenue with daily and gym breakdowns."""
    try:
        conditions = [
            func.date(DailyPass.created_at) >= start_date,
            func.date(DailyPass.created_at) <= end_date
        ]

        if specific_gym_id is not None:
            conditions.append(DailyPass.gym_id == str(specific_gym_id))
        elif exclude_gym_id_one:
            conditions.append(DailyPass.gym_id != "1")

        stmt = (
            select(
                Payment.amount_minor,
                DailyPass.created_at,
                DailyPass.gym_id
            )
            .select_from(DailyPass)
            .join(Gym, cast(DailyPass.gym_id, Integer) == Gym.gym_id)
            .outerjoin(Payment, DailyPass.payment_id == Payment.provider_payment_id)
            .where(and_(*conditions))
        )

        result = await db.execute(stmt)
        daily_passes = result.all()

        for dp in daily_passes:
            amount = dp.amount_minor or 0
            source_revenue["daily_pass"] += amount

            # Track daily revenue
            date_key = dp.created_at.date().isoformat() if dp.created_at else None
            if date_key:
                if date_key not in daily_revenue:
                    daily_revenue[date_key] = 0
                daily_revenue[date_key] += amount

            # Track gym-wise revenue
            if dp.gym_id:
                try:
                    gym_key = int(dp.gym_id)
                    if gym_key not in gym_revenue:
                        gym_revenue[gym_key] = 0
                    gym_revenue[gym_key] += amount
                except (ValueError, TypeError):
                    pass

    except Exception as e:
        import traceback
        traceback.print_exc()


async def _get_sessions_detailed(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    specific_gym_id: Optional[int],
    exclude_gym_id_one: bool,
    daily_revenue: dict,
    gym_revenue: dict,
    source_revenue: dict
):
    """Get Sessions revenue with daily and gym breakdowns."""
    try:
        conditions = [SessionPurchase.status == "paid"]

        if specific_gym_id is not None:
            conditions.append(SessionPurchase.gym_id == specific_gym_id)
        elif exclude_gym_id_one:
            conditions.append(SessionPurchase.gym_id != 1)

        # Date filtering: only use created_at (purchase date)
        conditions.append(func.date(SessionPurchase.created_at) >= start_date)
        conditions.append(func.date(SessionPurchase.created_at) <= end_date)

        stmt = (
            select(
                SessionPurchase.payable_rupees,
                SessionPurchase.created_at,
                SessionPurchase.gym_id
            )
            .select_from(SessionPurchase)
            .join(Gym, SessionPurchase.gym_id == Gym.gym_id)
            .where(and_(*conditions))
        )

        result = await db.execute(stmt)
        sessions = result.all()

        for purchase in sessions:
            amount_rupees = purchase.payable_rupees or 0
            amount_paisa = amount_rupees * 100

            source_revenue["sessions"] += amount_paisa

            # Track daily revenue
            date_key = purchase.created_at.date().isoformat() if purchase.created_at else None
            if date_key:
                if date_key not in daily_revenue:
                    daily_revenue[date_key] = 0
                daily_revenue[date_key] += amount_paisa

            # Track gym-wise revenue
            if purchase.gym_id:
                if purchase.gym_id not in gym_revenue:
                    gym_revenue[purchase.gym_id] = 0
                gym_revenue[purchase.gym_id] += amount_paisa

    except Exception as e:
        pass


async def _get_fittbot_subscription_detailed(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    daily_revenue: dict,
    source_revenue: dict
):
    """
    Get Nutritionist Plan (Fymble Subscription) revenue with daily breakdown.

    NEW LOGIC:
    - Query payments.payments table
    - Filter where payment_metadata -> 'flow' == 'nutrition_purchase_googleplay' or 'nutrition_package_razorpay'
    - Filter where status = 'captured'
    - Sum amount_minor values
    """
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(
                Payment.amount_minor,
                Payment.captured_at
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .join(
                NutritionEligibility,
                cast(Payment.order_id, String) == NutritionEligibility.source_id
            )
            .where(
                NutritionEligibility.source_type == "fymble_purchase",
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_purchase_googleplay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "nutrition_package_razorpay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "basic_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "expert_nutrition_plan",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "elite_nutrition_plan"
                ),
                func.date(Payment.captured_at) >= start_date,
                func.date(Payment.captured_at) <= end_date,
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        result = await db.execute(stmt)
        payments = result.all()

        for payment in payments:
            amount = payment.amount_minor or 0
            source_revenue["fittbot_subscription"] += amount

            # Track daily revenue
            date_key = payment.captured_at.date().isoformat() if payment.captured_at else None
            if date_key:
                if date_key not in daily_revenue:
                    daily_revenue[date_key] = 0
                daily_revenue[date_key] += amount

    except Exception as e:
        import traceback
        traceback.print_exc()


async def _get_gym_membership_detailed(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    specific_gym_id: Optional[int],
    exclude_gym_id_one: bool,
    daily_revenue: dict,
    gym_revenue: dict,
    source_revenue: dict
):
    """Get Gym Membership revenue with daily and gym breakdowns."""
    try:
        # SQL-level metadata filter — avoids loading all payments into Python memory
        meta_cond = or_(
            func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_sub",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_free_fittbot",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "gym_membership_with_bonus_credits",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "personal_training_with_bonus_credits"
        )

        # OrderItem join conditions for gym_id validation
        oi_conditions = [
            OrderItem.order_id == Order.id,
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != ""
        ]
        if specific_gym_id is not None:
            oi_conditions.append(OrderItem.gym_id == str(specific_gym_id))
        elif exclude_gym_id_one:
            oi_conditions.append(OrderItem.gym_id != "1")

        # Fetch only SQL-filtered rows — include order_id for deduplication, gym_id for breakdown
        stmt = (
            select(
                Payment.captured_at,
                Order.id.label("order_id"),
                Order.gross_amount_minor,
                OrderItem.gym_id
            )
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .join(Client, Client.client_id == cast(Order.customer_id, Integer))
            .join(OrderItem, and_(*oi_conditions))
            .join(Gym, Gym.gym_id == cast(OrderItem.gym_id, Integer))
            .where(
                Payment.status == "captured",
                Order.status == "paid",
                Order.customer_id.isnot(None),
                func.date(Payment.captured_at) >= start_date,
                func.date(Payment.captured_at) <= end_date,
                meta_cond
            )
        )

        result = await db.execute(stmt)
        rows = result.all()

        # Deduplicate by order_id to avoid double-counting orders with multiple order_items
        seen_order_ids = set()
        for row in rows:
            if row.order_id in seen_order_ids:
                continue
            seen_order_ids.add(row.order_id)

            amount = row.gross_amount_minor or 0
            source_revenue["gym_membership"] += amount

            # Track daily revenue
            date_key = row.captured_at.date().isoformat() if row.captured_at else None
            if date_key:
                if date_key not in daily_revenue:
                    daily_revenue[date_key] = 0
                daily_revenue[date_key] += amount

            # Track gym-wise revenue
            if row.gym_id:
                try:
                    gym_key = int(row.gym_id)
                    if gym_key not in gym_revenue:
                        gym_revenue[gym_key] = 0
                    gym_revenue[gym_key] += amount
                except (ValueError, TypeError):
                    pass

    except Exception as e:
        import traceback
        traceback.print_exc()


async def _get_ai_credits_detailed(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    daily_revenue: dict,
    source_revenue: dict
):
    """
    Get AI Credits revenue with daily breakdown.

    NEW LOGIC:
    - Query payments.payments table
    - Filter where payment_metadata -> 'flow' == 'food_scanner_credits' or 'food_scanner_credits_razorpay'
    - Filter where status = 'captured'
    - Sum amount_minor values
    """
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(
                Payment.amount_minor,
                Payment.captured_at
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits_razorpay"
                ),
                func.date(Payment.captured_at) >= start_date,
                func.date(Payment.captured_at) <= end_date,
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        result = await db.execute(stmt)
        payments = result.all()

        for payment in payments:
            amount = payment.amount_minor or 0
            source_revenue["ai_credits"] += amount

            # Track daily revenue
            date_key = payment.captured_at.date().isoformat() if payment.captured_at else None
            if date_key:
                if date_key not in daily_revenue:
                    daily_revenue[date_key] = 0
                daily_revenue[date_key] += amount

    except Exception as e:
        import traceback
        traceback.print_exc()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_month_date_range(year: int, month: int) -> Tuple[date, date]:
    """Get start and end date for a given month/year."""
    start_date = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end_date = date(year, month, last_day)
    return start_date, end_date


def paise_to_rupees(paise: int) -> float:
    """Convert paise to rupees."""
    return round(paise / 100, 2)


def paise_to_rupees_float(paise: float) -> float:
    """Convert paise (can be fractional) to rupees."""
    return round(paise / 100, 2)


# ============================================================================
# PAYOUT CALCULATION FUNCTIONS (Centralized — used by Financials & Cash Flow)
# To change commission rates platform-wide, edit ONLY here.
# ============================================================================

def calculate_membership_payout(membership_revenue: int) -> tuple:
    """
    Calculate gym payout for Gym Membership revenue.
    Formula:
    1. 10% platform commission
    2. 2% PG deduction on total
    3. 2% TDS on amount after commission

    Args:
        membership_revenue: Revenue in PAISA

    Returns:
        Tuple of (final_payout, commission, pg_deduction, tds_deduction) — all in PAISA
    """
    from decimal import Decimal
    if membership_revenue <= 0:
        return 0, 0, 0, 0

    membership_revenue = Decimal(str(membership_revenue))

    commission = membership_revenue * Decimal("0.10")  # 10% commission
    pg_deduction = membership_revenue * Decimal("0.02")  # 2% PG on total
    amount_after_commission = membership_revenue - commission
    tds_deduction = amount_after_commission * Decimal("0.02")  # 2% TDS on post-commission amount
    final_payout = membership_revenue - commission - pg_deduction - tds_deduction

    return max(0, int(final_payout)), int(commission), int(pg_deduction), int(tds_deduction)


def calculate_daily_pass_session_payout(revenue: int) -> tuple:
    """
    Calculate gym payout for Daily Pass and Fitness Class (Sessions) revenue.
    Formula:
    1. 10% platform commission
    2. 2% PG deduction on total
    3. 2% TDS on amount after commission

    Args:
        revenue: Revenue in PAISA

    Returns:
        Tuple of (final_payout, commission, pg_deduction, tds_deduction) — all in PAISA
    """
    from decimal import Decimal
    if revenue <= 0:
        return 0, 0, 0, 0

    revenue = Decimal(str(revenue))

    commission = revenue * Decimal("0.10")  # 10% commission
    pg_deduction = revenue * Decimal("0.02")  # 2% PG on total
    amount_after_commission = revenue - commission
    tds_deduction = amount_after_commission * Decimal("0.02")  # 2% TDS on post-commission amount
    final_payout = revenue - commission - pg_deduction - tds_deduction

    return max(0, int(final_payout)), int(commission), int(pg_deduction), int(tds_deduction)


def calculate_nutritionist_plan_net_revenue(revenue_in_paise: int) -> dict:
    """
    Calculate Net Revenue and GST for Nutritionist Plan (Fittbot Subscription).

    Nutritionist Plan revenue is inclusive of GST, sold through Google Play:
    Step 1: Reverse GST calculation = Revenue / 1.18
    Step 2: Deduct 15% Google commission from taxable value
    Final Net = (Revenue / 1.18) - (Revenue × 0.15)

    Args:
        revenue_in_paise: Revenue amount in PAISA (minor units)

    Returns:
        Dictionary with revenue, gst, google_commission, and net_revenue in PAISA (int)
    """
    from decimal import Decimal

    GST_RATE = Decimal("0.18")  # 18% GST
    GOOGLE_COMMISSION_RATE = Decimal("0.15")  # 15% Google commission

    # Convert to Decimal for precise calculation
    revenue = Decimal(str(revenue_in_paise))

    # --- OLD LOGIC COMMENTED OUT ---
    # Step 1: Reverse GST calculation (amount is inclusive of GST)
    # taxable_value = revenue / Decimal("1.18")
    # gst = revenue - taxable_value
    # 
    # Step 2: Google commission (15% of total revenue)
    # google_commission = revenue * GOOGLE_COMMISSION_RATE
    # 
    # Step 3: Net revenue after GST and Google commission
    # net_revenue = taxable_value - google_commission

    # --- NEW LOGIC ---
    # New logic: total revenue * 0.71
    net_revenue = revenue * Decimal("0.71")
    gst = Decimal("0")
    google_commission = Decimal("0")

    return {
        "revenue": int(revenue),
        "gst": int(gst),
        "google_commission": int(google_commission),
        "net_revenue": int(max(0, net_revenue))
    }


def calculate_ai_credits_net_revenue(revenue_in_paise: int) -> dict:
    """
    Calculate Net Revenue and GST for AI Credits.

    AI Credits revenue is inclusive of GST, sold through Google Play:
    Step 1: Reverse GST calculation = Revenue / 1.18
    Step 2: Deduct 15% Google commission from taxable value
    Final Net = (Revenue / 1.18) - (Revenue × 0.15)

    Args:
        revenue_in_paise: Revenue amount in PAISA (minor units)

    Returns:
        Dictionary with revenue, gst, google_commission, and net_revenue in PAISA (int)
    """
    from decimal import Decimal

    GST_RATE = Decimal("0.18")  # 18% GST
    GOOGLE_COMMISSION_RATE = Decimal("0.15")  # 15% Google commission

    # Convert to Decimal for precise calculation
    revenue = Decimal(str(revenue_in_paise))

    # --- OLD LOGIC COMMENTED OUT ---
    # Step 1: Reverse GST calculation (amount is inclusive of GST)
    # taxable_value = revenue / Decimal("1.18")
    # gst = revenue - taxable_value
    #
    # Step 2: Google commission (15% of total revenue)
    # google_commission = revenue * GOOGLE_COMMISSION_RATE
    #
    # Step 3: Net revenue after GST and Google commission
    # net_revenue = taxable_value - google_commission

    # --- NEW LOGIC ---
    # New logic: total revenue * 0.71
    net_revenue = revenue * Decimal("0.71")
    gst = Decimal("0")
    google_commission = Decimal("0")

    return {
        "revenue": int(revenue),
        "gst": int(gst),
        "google_commission": int(google_commission),
        "net_revenue": int(max(0, net_revenue))
    }


def calculate_digital_service_gst(revenue_in_paise: int) -> int:
    """
    Calculate GST for digital services (Nutritionist Plans, AI Credits, AI Diet Coach)
    using the ORIGINAL reverse-GST method.

    This function is used ONLY for Tax & Compliance and Cash Flow pages where
    accurate GST reporting is required, independent of the net revenue formula.

    Formula: GST = Revenue - (Revenue / 1.18)

    Args:
        revenue_in_paise: Revenue amount in PAISA (minor units)

    Returns:
        GST amount in PAISA (int)
    """
    from decimal import Decimal
    revenue = Decimal(str(revenue_in_paise))
    taxable_value = revenue / Decimal("1.18")
    gst = revenue - taxable_value
    return int(gst)


async def get_gross_margin_data(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> dict:
    """
    Shared helper to compute Gross Margin Percentage.

    Logic:
      1. Fetch Total Gross Profit from revenue (via get_revenue_breakdown + calculate_net_revenue)
      2. Fetch COGS expenses (AWS Cost + Nutritionist Salary) for the date range
      3. gross_margin = gross_profit - cogs_expenses
      4. gross_margin_percentage = gross_margin / gross_profit  (displayed directly as %)

    Returns dict with:
      - gross_profit (rupees)
      - cogs_expenses (rupees)
      - gross_margin (rupees)
      - gross_margin_percentage (ratio, displayed as %)
    """
    from app.models.adminmodels import Expenses
    from app.fittbot_admin_api.financials.financials import calculate_net_revenue

    # 1. Get revenue breakdown
    revenue_data = await get_revenue_breakdown(
        db=db,
        start_date=start_date,
        end_date=end_date,
        exclude_gym_id_one=True
    )

    # 2. Calculate gross profit per category (same logic as financials.py)
    membership_payout, membership_comm, membership_pg, membership_tds = calculate_membership_payout(revenue_data.gym_membership)
    daily_pass_payout, daily_pass_comm, daily_pass_pg, daily_pass_tds = calculate_daily_pass_session_payout(revenue_data.daily_pass)
    sessions_payout, sessions_comm, sessions_pg, sessions_tds = calculate_daily_pass_session_payout(revenue_data.sessions)

    net_revenue_data = calculate_net_revenue(
        fittbot_subscription_revenue=revenue_data.fittbot_subscription,
        gym_membership_revenue=revenue_data.gym_membership,
        daily_pass_revenue=revenue_data.daily_pass,
        sessions_revenue=revenue_data.sessions,
        ai_credits_revenue=revenue_data.ai_credits,
        ai_diet_coach_revenue=revenue_data.ai_diet_coach,
        membership_comm=membership_comm,
        daily_pass_comm=daily_pass_comm,
        sessions_comm=sessions_comm
    )

    fittbot_sub_gp  = net_revenue_data["fittbot_subscription"]["net_revenue"]
    ai_credits_gp   = net_revenue_data["ai_credits"]["net_revenue"]
    ai_diet_gp      = net_revenue_data["ai_diet_coach"]["net_revenue"]
    gym_gp          = membership_comm - net_revenue_data["gym_membership"]["gst_on_comm"]
    daily_pass_gp   = daily_pass_comm - net_revenue_data["daily_pass"]["gst_on_comm"]
    sessions_gp     = sessions_comm - net_revenue_data["sessions"]["gst_on_comm"]

    total_gross_profit_paise = (
        fittbot_sub_gp + ai_credits_gp + ai_diet_gp +
        gym_gp + daily_pass_gp + sessions_gp
    )
    gross_profit_rupees = paise_to_rupees(total_gross_profit_paise)

    # 3. COGS expenses
    COGS_TYPES = ["AWS Cost", "Nutritionist Salary"]
    cogs_query = (
        select(func.coalesce(func.sum(Expenses.amount), 0))
        .where(
            and_(
                Expenses.expense_date >= start_date,
                Expenses.expense_date <= end_date,
                Expenses.expense_type.in_(COGS_TYPES)
            )
        )
    )
    cogs_result = await db.execute(cogs_query)
    cogs_expenses = float(cogs_result.scalar() or 0)

    # 4. Gross Margin
    gross_margin = gross_profit_rupees - cogs_expenses
    gross_margin_percentage = round(gross_margin / gross_profit_rupees, 2) if gross_profit_rupees != 0 else 0.0

    return {
        "gross_profit": round(gross_profit_rupees, 2),
        "cogs_expenses": round(cogs_expenses, 2),
        "gross_margin": round(gross_margin, 2),
        "gross_margin_percentage": gross_margin_percentage
    }


async def _get_ai_diet_coach_detailed(
    db: AsyncSession,
    start_date: Optional[date],
    end_date: Optional[date],
    daily_revenue: dict,
    source_revenue: dict
):
    """Get AI Diet Coach revenue with daily breakdown."""
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        stmt = (
            select(
                Payment.amount_minor,
                Payment.captured_at
            )
            .select_from(Payment)
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach",
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )
        if start_date and start_date.year > 1970:
            stmt = stmt.where(func.date(Payment.captured_at) >= start_date)
        if end_date and end_date.year < 2090:
            stmt = stmt.where(func.date(Payment.captured_at) <= end_date)
            
        result = await db.execute(stmt)
        rows = result.all()
        
        total = 0
        for row in rows:
            amount = int(row.amount_minor or 0)
            total += amount
            source_revenue["ai_diet_coach"] += amount
            
            date_key = row.captured_at.date().isoformat() if row.captured_at else None
            if date_key:
                if date_key not in daily_revenue:
                    daily_revenue[date_key] = 0
                daily_revenue[date_key] += amount
                
    except Exception as e:
        import traceback
        traceback.print_exc()


def calculate_net_revenue(
    fittbot_subscription_revenue,
    gym_membership_revenue,
    daily_pass_revenue,
    sessions_revenue,
    ai_credits_revenue,
    ai_diet_coach_revenue,
    membership_comm,
    daily_pass_comm,
    sessions_comm
):
    """
    Standardized Net Revenue calculation for all income categories.
    """
    GST_RATE = Decimal("0.18")

    fittbot_subscription_revenue = Decimal(str(fittbot_subscription_revenue))
    gym_membership_revenue = Decimal(str(gym_membership_revenue))
    daily_pass_revenue = Decimal(str(daily_pass_revenue))
    sessions_revenue = Decimal(str(sessions_revenue))
    ai_credits_revenue = Decimal(str(ai_credits_revenue))
    ai_diet_coach_revenue = Decimal(str(ai_diet_coach_revenue))
    membership_comm = Decimal(str(membership_comm))
    daily_pass_comm = Decimal(str(daily_pass_comm))
    sessions_comm = Decimal(str(sessions_comm))

    # Nutritionist Plan / AI Credits / AI Diet Coach
    nutritionist_calc = calculate_nutritionist_plan_net_revenue(int(fittbot_subscription_revenue))
    fittbot_subscription_gst = Decimal(str(nutritionist_calc["gst"]))
    fittbot_subscription_net = Decimal(str(nutritionist_calc["net_revenue"]))

    ai_credits_calc = calculate_ai_credits_net_revenue(int(ai_credits_revenue))
    ai_credits_gst = Decimal(str(ai_credits_calc["gst"]))
    ai_credits_net = Decimal(str(ai_credits_calc["net_revenue"]))

    ai_diet_coach_calc = calculate_nutritionist_plan_net_revenue(int(ai_diet_coach_revenue))
    ai_diet_coach_gst = Decimal(str(ai_diet_coach_calc["gst"]))
    ai_diet_coach_net = Decimal(str(ai_diet_coach_calc["net_revenue"]))

    # Commissions based
    gym_membership_gst_on_comm = membership_comm * GST_RATE
    gym_membership_net = gym_membership_revenue - gym_membership_gst_on_comm

    daily_pass_gst_on_comm = daily_pass_comm * GST_RATE
    daily_pass_net = daily_pass_revenue - daily_pass_gst_on_comm

    sessions_gst_on_comm = sessions_comm * GST_RATE
    sessions_net = sessions_revenue - sessions_gst_on_comm

    total_net_revenue = (
        fittbot_subscription_net +
        ai_credits_net +
        ai_diet_coach_net +
        gym_membership_net +
        daily_pass_net +
        sessions_net
    )

    return {
        "fittbot_subscription": {
            "revenue": float(fittbot_subscription_revenue),
            "gst": float(fittbot_subscription_gst),
            "net_revenue": float(fittbot_subscription_net)
        },
        "ai_credits": {
            "revenue": float(ai_credits_revenue),
            "gst": float(ai_credits_gst),
            "net_revenue": float(ai_credits_net)
        },
        "ai_diet_coach": {
            "revenue": float(ai_diet_coach_revenue),
            "gst": float(ai_diet_coach_gst),
            "net_revenue": float(ai_diet_coach_net)
        },
        "gym_membership": {
            "revenue": float(gym_membership_revenue),
            "commission": float(membership_comm),
            "gst_on_comm": float(gym_membership_gst_on_comm),
            "net_revenue": float(gym_membership_net)
        },
        "daily_pass": {
            "revenue": float(daily_pass_revenue),
            "commission": float(daily_pass_comm),
            "gst_on_comm": float(daily_pass_gst_on_comm),
            "net_revenue": float(daily_pass_net)
        },
        "sessions": {
            "revenue": float(sessions_revenue),
            "commission": float(sessions_comm),
            "gst_on_comm": float(sessions_gst_on_comm),
            "net_revenue": float(sessions_net)
        },
        "total_net_revenue": float(total_net_revenue)
    }


async def get_financial_metrics(
    db: AsyncSession,
    start_date: date,
    end_date: date
) -> dict:
    """
    Master function to fetch all financial metrics (EBITA, ARPU, ARPPU, etc.)
    """
    # 1. Get Revenue Breakdown
    revenue_data = await get_revenue_breakdown(db, start_date, end_date, exclude_gym_id_one=True)
    
    # 2. Get Gross Margin Data (already standardized)
    gm_data = await get_gross_margin_data(db, start_date, end_date)
    
    # 3. Get Expenses
    expenses_query = select(func.coalesce(func.sum(Expenses.amount), 0)).where(
        and_(Expenses.expense_date >= start_date, Expenses.expense_date <= end_date)
    )
    expenses_result = await db.execute(expenses_query)
    total_expenses = float(expenses_result.scalar() or 0)

    # 4. EBITA
    ebita = gm_data["gross_profit"] - total_expenses

    # 5. User Counts
    # Total New Users
    users_query = select(func.count()).select_from(Client).where(
        and_(func.date(Client.created_at) >= start_date, func.date(Client.created_at) <= end_date)
    )
    users_result = await db.execute(users_query)
    total_users_count = int(users_result.scalar() or 0)

    # Paying Users
    paying_users_count = await get_paying_users_count(db, start_date, end_date)

    # 6. ARPU / ARPPU
    membership_payout, membership_comm, membership_pg, membership_tds = calculate_membership_payout(revenue_data.gym_membership)
    daily_pass_payout, daily_pass_comm, daily_pass_pg, daily_pass_tds = calculate_daily_pass_session_payout(revenue_data.daily_pass)
    sessions_payout, sessions_comm, sessions_pg, sessions_tds = calculate_daily_pass_session_payout(revenue_data.sessions)

    net_revenue_data = calculate_net_revenue(
        fittbot_subscription_revenue=revenue_data.fittbot_subscription,
        gym_membership_revenue=revenue_data.gym_membership,
        daily_pass_revenue=revenue_data.daily_pass,
        sessions_revenue=revenue_data.sessions,
        ai_credits_revenue=revenue_data.ai_credits,
        ai_diet_coach_revenue=revenue_data.ai_diet_coach,
        membership_comm=membership_comm,
        daily_pass_comm=daily_pass_comm,
        sessions_comm=sessions_comm
    )

    total_net_revenue_rupees = paise_to_rupees(net_revenue_data["total_net_revenue"])
    arpu = total_net_revenue_rupees / total_users_count if total_users_count > 0 else 0
    arppu = total_net_revenue_rupees / paying_users_count if paying_users_count > 0 else 0

    # Active Users
    active_users_count = await get_active_users_count(db, start_date, end_date)

    return {
        "ebita": round(ebita, 2),
        "total_expenses": round(total_expenses, 2),
        "total_users": total_users_count,
        "paying_users": paying_users_count,
        "active_users": active_users_count,
        "arpu": round(arpu, 2),
        "arppu": round(arppu, 2),
        "total_net_revenue": round(total_net_revenue_rupees, 2),
        "gross_profit": gm_data["gross_profit"],
        "cogs_expenses": gm_data["cogs_expenses"],
        "gross_margin": gm_data["gross_margin"],
        "gross_margin_percentage": gm_data["gross_margin_percentage"]
    }


async def get_active_users_count(
    db: AsyncSession,
    start_date,
    end_date
):
    try:
        # Active users: users with at least 1 login in the date range
        subquery = select(ActiveUser.client_id).join(
            Client, ActiveUser.client_id == Client.client_id
        ).where(
            and_(
                func.date(ActiveUser.created_at) >= start_date,
                func.date(ActiveUser.created_at) <= end_date,
                Client.gym_id != 1
            )
        )

        count_query = select(func.coalesce(func.count(distinct(ActiveUser.client_id)), 0)).where(
            ActiveUser.client_id.in_(subquery)
        )

        count_result = await db.execute(count_query)
        active_users_count = count_result.scalar() or 0

        return int(active_users_count)
    except Exception as e:
        print(f"[REVENUE_SERVICE] Error fetching active users: {e}")
        import traceback
        traceback.print_exc()
        return 0


async def get_paying_users_set(
    db: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> set:
    """
    Get the set of distinct paying customer IDs within an optional date range.
    Filters by payment status 'captured', excludes test gym_id = '1' and internal test contact numbers.
    """
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        
        paying_filters = [
            Payment.status == "captured",
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != "1",
            or_(Client.contact.is_(None), ~Client.contact.in_(EXCLUDED_CONTACTS))
        ]
        
        if start_date:
            start_dt = datetime.combine(start_date, datetime.min.time())
            paying_filters.append(Payment.captured_at >= start_dt)
        if end_date:
            end_dt = datetime.combine(end_date, datetime.max.time())
            paying_filters.append(Payment.captured_at <= end_dt)
            
        paying_query = select(Payment.customer_id).join(
            Order, Order.id == Payment.order_id
        ).join(
            OrderItem, OrderItem.order_id == Order.id
        ).outerjoin(
            Client, Payment.customer_id == Client.client_id
        ).where(
            and_(*paying_filters)
        ).distinct()
        
        res = await db.execute(paying_query)
        return set([row[0] for row in res.fetchall() if row[0] is not None])
    except Exception as e:
        print(f"[REVENUE_SERVICE] Error fetching paying users set: {e}")
        import traceback
        traceback.print_exc()
        return set()


async def get_paying_users_count(
    db: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> int:
    """
    Get the count of distinct paying customer IDs within an optional date range.
    Filters by payment status 'captured', excludes test gym_id = '1' and internal test contact numbers.
    """
    try:
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        
        paying_filters = [
            Payment.status == "captured",
            OrderItem.gym_id.isnot(None),
            OrderItem.gym_id != "1",
            or_(Client.contact.is_(None), ~Client.contact.in_(EXCLUDED_CONTACTS))
        ]
        
        if start_date:
            start_dt = datetime.combine(start_date, datetime.min.time())
            paying_filters.append(Payment.captured_at >= start_dt)
        if end_date:
            end_dt = datetime.combine(end_date, datetime.max.time())
            paying_filters.append(Payment.captured_at <= end_dt)
            
        paying_query = select(func.count(distinct(Payment.customer_id))).join(
            Order, Order.id == Payment.order_id
        ).join(
            OrderItem, OrderItem.order_id == Order.id
        ).outerjoin(
            Client, Payment.customer_id == Client.client_id
        ).where(
            and_(*paying_filters)
        )
        
        res = await db.execute(paying_query)
        return int(res.scalar() or 0)
    except Exception as e:
        print(f"[REVENUE_SERVICE] Error fetching paying users count: {e}")
        import traceback
        traceback.print_exc()
        return 0

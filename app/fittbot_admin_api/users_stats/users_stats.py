# Users Stats API - Total Users Count
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, case, literal_column, and_, or_, cast, Integer, union_all, literal, not_, String
from typing import Dict, List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta

from app.models.async_database import get_async_db
from app.models.fittbot_models import Client, ActiveUser, SessionPurchase, FittbotGymMembership, Gym
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.models.dailypass_models import DailyPass
from app.models.nutrition_models import NutritionEligibility
from app.fittbot_admin_api.revenue_service import get_paying_users_count, get_total_bookings_count, get_daily_booking_counts, get_daily_active_user_counts, get_peak_metric_days

router = APIRouter(prefix="/api/admin/users-stats", tags=["AdminUsersStats"])


# Pydantic Schemas
class UsersStatsResponse(BaseModel):
    success: bool
    data: Dict
    message: str


class CityStatsItem(BaseModel):
    city: str
    users_count: int


class CityStatsResponse(BaseModel):
    success: bool
    data: List[CityStatsItem]
    next_cursor: Optional[int]
    has_more: bool
    message: str





@router.get("/data")
async def get_users_stats(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_async_db)
):

    try:
        # Parse dates
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_date else None

        # Query 1: Count total clients 
        total_filters = []
        if start_date_obj:
            total_filters.append(Client.created_at >= start_date_obj)
        if end_date_obj:
            total_filters.append(Client.created_at <= end_date_obj)

        total_query = select(func.count()).select_from(Client)
        if total_filters:
            total_query = total_query.where(and_(*total_filters))
            
        total_result = await db.execute(total_query)
        total_count = total_result.scalar() or 0

        # Query 1b: Count overall total clients (without date filters)
        overall_total_query = select(func.count()).select_from(Client)
        overall_total_result = await db.execute(overall_total_query)
        overall_total_count = overall_total_result.scalar() or 0

        # Query 2: Count distinct client_id from active_users with optional date filter
        # Active users: users with at least 1 login within date range if provided (overall if not)
        # Exclude users from gym_id = 1
        active_filters = []
        if start_date_obj:
            active_filters.append(ActiveUser.created_at >= start_date_obj)
        if end_date_obj:
            active_filters.append(ActiveUser.created_at <= end_date_obj)

        active_subquery = select(ActiveUser.client_id).join(
            Client, ActiveUser.client_id == Client.client_id
        ).where(
            and_(
                *active_filters,
                or_(Client.gym_id != 1, Client.gym_id.is_(None))
            )
        )

        active_query = select(
            func.coalesce(func.count(func.distinct(ActiveUser.client_id)), 0)
        ).where(
            ActiveUser.client_id.in_(active_subquery)
        )
        active_result = await db.execute(active_query)
        active_count = active_result.scalar() or 0

        # Query 3: Count distinct customer_id from payments table using common helper
        paying_count = await get_paying_users_count(db, start_date_obj, end_date_obj)

        # Query 4: Retention Users - customers with multiple purchases
        # A customer is included ONLY if NONE of their order_items have gym_id = 1
        # (If ANY order_item for any of their orders has gym_id = 1, that customer is excluded)
        # Apply date filter if provided

        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]

        date_filter_conditions = []
        if start_date_obj:
            date_filter_conditions.append(Payment.captured_at >= start_date_obj)
        if end_date_obj:
            date_filter_conditions.append(Payment.captured_at <= end_date_obj)

        # Step 1: Define strict streams for Retention
        # Daily Pass Stream (Verified Success)
        dp_retention = (
            select(
                cast(DailyPass.client_id, Integer).label("client_id"),
                Payment.id.label("event_id")
            )
            .join(Payment, DailyPass.payment_id == Payment.provider_payment_id)
            .outerjoin(Client, cast(DailyPass.client_id, Integer) == Client.client_id)
            .where(
                Payment.status == "captured",
                or_(DailyPass.gym_id != "1", DailyPass.gym_id.is_(None)),
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )

        # Fitness Class Stream (Verified Paid)
        sess_retention = (
            select(
                SessionPurchase.client_id,
                SessionPurchase.id.label("event_id")
            )
            .outerjoin(Client, SessionPurchase.client_id == Client.client_id)
            .where(
                SessionPurchase.status == "paid", 
                or_(SessionPurchase.gym_id != 1, SessionPurchase.gym_id.is_(None)),
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )

        # Nutrition Retention
        nutrition_retention = (
            select(
                Payment.customer_id.label("client_id"),
                Payment.id.label("event_id")
            )
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
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )

        # AI Sources
        ai_retention = (
            select(
                Payment.customer_id.label("client_id"),
                Payment.id.label("event_id")
            )
            .outerjoin(Client, Payment.customer_id == Client.client_id)
            .where(
                Payment.status == "captured",
                or_(
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "food_scanner_credits_razorpay",
                    func.json_extract(Payment.payment_metadata, "$.flow") == "ai_diet_coach"
                ),
                ~Client.contact.in_(EXCLUDED_CONTACTS)
            )
        )

        # Add Gym Membership to 'other'
        gym_meta_cond_ret = or_(
            func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_sub",
            func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")) == "unified_gym_membership_with_free_fittbot"
        )

        gm_retention = (
            select(
                cast(Order.customer_id, Integer).label("client_id"),
                Payment.id.label("event_id")
            )
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .join(Client, Client.client_id == cast(Order.customer_id, Integer))
            .where(
                Payment.status == "captured",
                Order.status == "paid",
                ~Client.contact.in_(EXCLUDED_CONTACTS),
                gym_meta_cond_ret
            )
        )

        # Apply date filters
        if start_date_obj:
            dp_retention = dp_retention.where(func.date(Payment.captured_at) >= start_date_obj)
            sess_retention = sess_retention.where(func.date(SessionPurchase.created_at) >= start_date_obj)
            nutrition_retention = nutrition_retention.where(func.date(Payment.captured_at) >= start_date_obj)
            ai_retention = ai_retention.where(func.date(Payment.captured_at) >= start_date_obj)
            gm_retention = gm_retention.where(func.date(Payment.captured_at) >= start_date_obj)
        if end_date_obj:
            dp_retention = dp_retention.where(func.date(Payment.captured_at) <= end_date_obj)
            sess_retention = sess_retention.where(func.date(SessionPurchase.created_at) <= end_date_obj)
            nutrition_retention = nutrition_retention.where(func.date(Payment.captured_at) <= end_date_obj)
            ai_retention = ai_retention.where(func.date(Payment.captured_at) <= end_date_obj)
            gm_retention = gm_retention.where(func.date(Payment.captured_at) <= end_date_obj)

        # Step 2: Combine and filter
        unified_ret = union_all(dp_retention, sess_retention, nutrition_retention, ai_retention, gm_retention).alias("unified_ret")
        
        # Final aggregation (only users with more than 1 DISTINCT purchase event)
        retention_sub_stmt = (
            select(unified_ret.c.client_id)
            .group_by(unified_ret.c.client_id)
            .having(func.count(func.distinct(unified_ret.c.event_id)) >= 2)
        ).subquery()
        
        retention_query = select(func.count()).select_from(retention_sub_stmt)
        
        repeat_result = await db.execute(retention_query)
        repeat_count = repeat_result.scalar() or 0

        # Query 5: Get users per city with normalization
        # Excluding gym_id = 1
        # Using pure SQLAlchemy ORM - no raw SQL
        # Fetch all locations and filter in Python for better compatibility

        # Get all clients (including gym_1)
        clients_query = select(Client.location)
        clients_result = await db.execute(clients_query)
        locations = [row[0] for row in clients_result.fetchall()]

        # Group by normalized location in Python and filter for valid city names
        # Valid city names must contain at least one letter
        city_counts = {}
        skipped_no_alpha = 0
        sample_skipped = []

        for loc in locations:
            normalized = loc.strip().lower() if loc and loc.strip() else ""
            
            # Use only valid city names (containing at least one letter)
            if normalized and any(c.isalpha() for c in normalized):
                # Title case for display
                display_city = normalized.title()
                city_counts[display_city] = city_counts.get(display_city, 0) + 1
            else:
                skipped_no_alpha += 1
                if loc and len(sample_skipped) < 10:
                    sample_skipped.append(loc)

        # Sort by count desc and take top 30
        city_stats = [
            {"city": city, "users_count": count}
            for city, count in sorted(city_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        ]

       
        from calendar import monthrange
        from datetime import timezone

        today_utc = datetime.now(timezone.utc).date()

        async def get_avg_active_count(s_date, e_date):
            """Count distinct active users in [s_date, e_date], excluding gym_id=1."""
            try:
                sub = select(ActiveUser.client_id).join(
                    Client, ActiveUser.client_id == Client.client_id
                ).where(
                    and_(
                        func.date(ActiveUser.created_at) >= s_date,
                        func.date(ActiveUser.created_at) <= e_date,
                        or_(Client.gym_id != 1, Client.gym_id.is_(None))
                    )
                )
                q = select(
                    func.coalesce(func.count(func.distinct(ActiveUser.client_id)), 0)
                ).where(ActiveUser.client_id.in_(sub))
                r = await db.execute(q)
                return int(r.scalar() or 0)
            except Exception:
                return 0

        # MAU — last 3 completed calendar months
        monthly_counts = []
        current_year = today_utc.year
        current_month = today_utc.month
        for i in range(3):
            month_index = current_month - 1 - i
            yr = current_year
            while month_index < 0:
                month_index += 12
                yr -= 1
            m_start = datetime(yr, month_index + 1, 1).date()
            _, last_day = monthrange(yr, month_index + 1)
            m_end = datetime(yr, month_index + 1, last_day).date()
            monthly_counts.append(await get_avg_active_count(m_start, m_end))
        monthly_average = sum(monthly_counts) / 3

        # WAU — last 3 completed 7-day windows
        weekly_counts = []
        yesterday_utc = today_utc - timedelta(days=1)
        for i in range(3):
            w_end = yesterday_utc - timedelta(weeks=i)
            w_start = w_end - timedelta(days=6)
            weekly_counts.append(await get_avg_active_count(w_start, w_end))
        weekly_average = sum(weekly_counts) / 3

        # DAU — last 3 fully completed days
        daily_counts = []
        for i in range(3):
            day = today_utc - timedelta(days=i + 1)
            daily_counts.append(await get_avg_active_count(day, day))
        daily_average = sum(daily_counts) / 3

        # Imported Clients Count (database level count from fittbot_gym_membership where type contains "imported")
        imported_count_stmt = select(func.count()).select_from(FittbotGymMembership).where(
            FittbotGymMembership.type.ilike("%imported%")
        )
        imported_count_result = await db.execute(imported_count_stmt)
        imported_clients_count = imported_count_result.scalar() or 0

        # Create latest membership subquery for offline members
        latest_membership_subquery = select(
            FittbotGymMembership.client_id,
            FittbotGymMembership.type,
            FittbotGymMembership.expires_at,
            func.row_number().over(
                partition_by=FittbotGymMembership.client_id,
                order_by=FittbotGymMembership.id.desc()
            ).label('rn')
        ).where(
            and_(
                FittbotGymMembership.client_id.isnot(None),
                FittbotGymMembership.gym_id.isnot(None),
                FittbotGymMembership.client_id.op('REGEXP')('^[0-9]+$')  # Only numeric client_ids
            )
        ).subquery('latest_membership')

        # Get OFFLINE member IDs first (type in ['normal', 'admission_fees'])
        offline_ids_stmt = select(
            latest_membership_subquery.c.client_id
        ).where(
            and_(
                latest_membership_subquery.c.rn == 1,
                latest_membership_subquery.c.type.in_(['normal', 'admission_fees'])
            )
        )
        offline_ids_result = await db.execute(offline_ids_stmt)
        offline_ids_list = [str(row[0]) for row in offline_ids_result.all()]

        # Offline members count
        offline_members_count = 0
        if offline_ids_list:
            offline_count_stmt = select(func.count()).select_from(
                select(Client.client_id).where(
                    and_(
                        Client.client_id.isnot(None),
                        Client.gym_id.isnot(None),
                        func.cast(Client.client_id, String).in_(offline_ids_list)
                    )
                ).subquery()
            )
            offline_count_result = await db.execute(offline_count_stmt)
            offline_members_count = offline_count_result.scalar() or 0

        return {
            "success": True,
            "data": {
                "total_users": int(total_count),
                "total_clients_constant": int(overall_total_count),
                "active_users": int(active_count),
                "paying_users": int(paying_count),
                "repeat_users": int(repeat_count),
                "total_bookings": await get_total_bookings_count(db, start_date_obj, end_date_obj),
                "users_by_city": city_stats,
                "total_cities": len(city_counts),
                "monthly_average_users": round(monthly_average, 0),
                "weekly_average_users": round(weekly_average, 0),
                "daily_average_users": round(daily_average, 0),
                "imported_clients": int(imported_clients_count),
                "offline_members": int(offline_members_count)
            },
            "message": "Users stats fetched successfully"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cities")
async def get_cities_paginated(
    offset: int = Query(0, description="Number of cities to skip for pagination", ge=0),
    limit: int = Query(30, description="Number of cities to return per page", ge=1, le=100),
    db: AsyncSession = Depends(get_async_db)
):
  
    try:
        # Subquery to get grouped and counted cities (including everyone)
        # We use a CASE statement to handle NULL/empty locations as 'Unspecified'
        city_group_subquery = select(
            case(
                (or_(Client.location.is_(None), func.trim(Client.location) == ''), 'Unspecified'),
                else_=func.trim(func.lower(Client.location))
            ).label("normalized_city"),
            func.count(Client.client_id).label("users_count")
        ).group_by(
            case(
                (or_(Client.location.is_(None), func.trim(Client.location) == ''), 'Unspecified'),
                else_=func.trim(func.lower(Client.location))
            )
        ).order_by(
            func.count(Client.client_id).desc()
        ).alias("city_groups")

        # Fetch with offset and limit
        cities_query = select(
            city_group_subquery.c.normalized_city,
            city_group_subquery.c.users_count
        ).offset(offset).limit(limit)

        result = await db.execute(cities_query)
        rows = result.fetchall()

        # Also fetch one more to check if there are more results
        next_check_query = select(func.count()).select_from(city_group_subquery)
        total_cities_result = await db.execute(next_check_query)
        total_available = total_cities_result.scalar() or 0
        
        has_more = (offset + limit) < total_available

        # Process results - format city names (Skipping 'Unspecified')
        city_stats = []
        for row in rows:
            normalized = row[0]
            count = row[1]

            if normalized and normalized != 'Unspecified' and any(c.isalpha() for c in normalized):
                city_stats.append({
                    "city": normalized.title(),
                    "users_count": count
                })

        return {
            "success": True,
            "data": city_stats,
            "next_offset": offset + len(city_stats),
            "has_more": has_more,
            "message": "Cities fetched successfully"
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

   
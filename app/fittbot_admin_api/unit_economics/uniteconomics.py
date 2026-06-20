# Unit Economics API - CAC Calculation
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from sqlalchemy import func, and_, select, distinct, case, literal_column, or_, desc
from pydantic import BaseModel

from app.models.async_database import get_async_db
from app.models.fittbot_models import Client, ActiveUser
from app.models.adminmodels import Expenses
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.models.dailypass_models import DailyPass, get_dailypass_session
from app.fittbot_admin_api.purchases.purchases import compute_gmv_totals
from app.fittbot_admin_api.revenue_service import (
    get_gross_margin_data, 
    get_financial_metrics,
    get_paying_users_set,
    get_paying_users_count,
    get_total_bookings_count
)

router = APIRouter(prefix="/api/admin/unit-economics", tags=["UnitEconomics"])


class UnitEconomicsResponse(BaseModel):
    success: bool
    data: dict
    message: str


# @router.get("/cac")
# async def get_cac_analytics(
#     start_date: str = Query(None, description="Start date in YYYY-MM-DD format"),
#     end_date: str = Query(None, description="End date in YYYY-MM-DD format"),
#     db: AsyncSession = Depends(get_async_db)
# ):
#     """
#     Get CAC (Customer Acquisition Cost) analytics.

#     CAC = Total Expenses / Total New Users
#     - Total Expenses: SUM(amount) from expenses table where expense_date is in range
#     - Total New Users: COUNT(*) from clients table where created_at is in range
#     """
#     try:
#         import logging

#         # Parse dates if provided
#         if start_date:
#             start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
#         else:
#             # Default to early date for overall data
#             start_date_obj = datetime(2020, 1, 1).date()

#         if end_date:
#             end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
#         else:
#             # Default to today
#             end_date_obj = datetime.now().date()

#         # Adjust end_date to include the full day
#         end_date_inclusive = end_date_obj + timedelta(days=1)

#         # Step 1: Calculate Total Expenses from expenses table
#         total_expenses = 0
#         try:
#             expenses_query = select(func.coalesce(func.sum(Expenses.amount), 0)).where(
#                 and_(
#                     Expenses.expense_date >= start_date_obj,
#                     Expenses.expense_date < end_date_inclusive
#                 )
#             )
#             expenses_result = await db.execute(expenses_query)
#             total_expenses = expenses_result.scalar() or 0
#             logging.info(f"[CAC] Total expenses from {start_date_obj} to {end_date_obj}: {total_expenses}")
#         except Exception as e:
#             logging.error(f"[CAC] Error fetching total expenses: {str(e)}")
#             import traceback
#             traceback.print_exc()

#         # Step 2: Calculate Total New Users from clients table
#         total_new_users = 0
#         try:
#             users_query = select(func.count()).where(
#                 and_(
#                     Client.created_at >= start_date_obj,
#                     Client.created_at < end_date_inclusive
#                 )
#             )
#             users_result = await db.execute(users_query)
#             total_new_users = users_result.scalar() or 0
#             logging.info(f"[CAC] Total new users from {start_date_obj} to {end_date_obj}: {total_new_users}")
#         except Exception as e:
#             logging.error(f"[CAC] Error fetching total new users: {str(e)}")
#             import traceback
#             traceback.print_exc()

#         # Step 3: Calculate CAC (handle division by zero)
#         cac = 0
#         if total_new_users > 0:
#             cac = total_expenses / total_new_users
#         else:
#             cac = 0

#         logging.info(f"[CAC] CAC calculated: {cac} (expenses: {total_expenses}, users: {total_new_users})")

#         analytics_data = {
#             "cac": round(cac, 2),
#             "totalExpenses": round(total_expenses, 2),
#             "totalNewUsers": total_new_users,
#             "filters": {
#                 "startDate": start_date_obj.isoformat(),
#                 "endDate": end_date_obj.isoformat()
#             }
#         }

#         return {
#             "success": True,
#             "data": analytics_data,
#             "message": "CAC analytics fetched successfully"
#         }

#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
#     except Exception as e:
#         logging.error(f"[CAC] Error: {str(e)}")
#         import traceback
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=str(e))


# @router.get("/ltv")
# async def get_ltv_analytics(
#     db: AsyncSession = Depends(get_async_db)
# ):
#     """
#     Get LTV (Lifetime Value) analytics.

#     LTV = 1 / churn_rate
#     churn_rate = retained_users_count / previous_month_active_users_count

#     Steps:
#     1. Get previous month active users (client_ids with 2+ distinct dates)
#     2. Get current month active users (client_ids with 2+ distinct dates)
#     3. Find retained users (client_ids present in both months)
#     4. Calculate churn_rate = retained_users / previous_month_active_users
#     5. Calculate LTV = 1 / churn_rate
#     """
#     try:
#         import logging

#         today = datetime.now().date()

#         # Calculate current month date range
#         first_day_of_current_month = today.replace(day=1)

#         # Calculate previous month date range
#         first_day_of_previous_month = (first_day_of_current_month - timedelta(days=1)).replace(day=1)
#         last_day_of_previous_month = first_day_of_current_month - timedelta(days=1)

#         logging.info(f"[LTV] Previous month: {first_day_of_previous_month} to {last_day_of_previous_month}")
#         logging.info(f"[LTV] Current month: {first_day_of_current_month} to {today}")

#         # Step 1: Get Previous Month Active Users (client_ids with 2+ distinct dates)
#         # Using ORM with date filter
#         prev_month_start = first_day_of_previous_month
#         prev_month_end = last_day_of_previous_month

#         # Subquery for previous month active users (2+ distinct dates)
#         prev_month_subquery = select(ActiveUser.client_id).where(
#             and_(
#                 func.date(ActiveUser.created_at) >= prev_month_start,
#                 func.date(ActiveUser.created_at) <= prev_month_end
#             )
#         ).group_by(
#             ActiveUser.client_id
#         ).having(
#             func.count(func.distinct(func.date(ActiveUser.created_at))) >= 2
#         )

#         prev_result = await db.execute(prev_month_subquery)
#         previous_month_client_ids = set([row[0] for row in prev_result.fetchall()])
#         previous_month_count = len(previous_month_client_ids)

#         logging.info(f"[LTV] Previous month active users: {previous_month_count}")
#         logging.info(f"[LTV] Previous month client_ids sample: {list(previous_month_client_ids)[:5]}")

#         # Step 2: Get Current Month Active Users (client_ids with 2+ distinct dates)
#         curr_month_start = first_day_of_current_month
#         curr_month_end = today

#         # Subquery for current month active users (2+ distinct dates)
#         curr_month_subquery = select(ActiveUser.client_id).where(
#             and_(
#                 func.date(ActiveUser.created_at) >= curr_month_start,
#                 func.date(ActiveUser.created_at) <= curr_month_end
#             )
#         ).group_by(
#             ActiveUser.client_id
#         ).having(
#             func.count(func.distinct(func.date(ActiveUser.created_at))) >= 2
#         )

#         curr_result = await db.execute(curr_month_subquery)
#         current_month_client_ids = set([row[0] for row in curr_result.fetchall()])
#         current_month_count = len(current_month_client_ids)

#         logging.info(f"[LTV] Current month active users: {current_month_count}")
#         logging.info(f"[LTV] Current month client_ids sample: {list(current_month_client_ids)[:5]}")

#         # Step 3: Find Retained Users (present in both months)
#         retained_client_ids = previous_month_client_ids.intersection(current_month_client_ids)
#         retained_count = len(retained_client_ids)

#         logging.info(f"[LTV] Retained users: {retained_count}")

#         # Step 4: Calculate Churn Rate
#         churn_rate = 0
#         if previous_month_count > 0:
#             churn_rate = retained_count / previous_month_count

#         logging.info(f"[LTV] Churn rate: {churn_rate}")

#         # Step 5: Calculate LTV
#         ltv = 0
#         if churn_rate > 0:
#             ltv = 1 / churn_rate
#         else:
#             ltv = 0

#         logging.info(f"[LTV] LTV calculated: {ltv}")

#         analytics_data = {
#             "ltv": round(ltv, 2),
#             "churnRate": round(churn_rate, 4),
#             "previousMonthActiveUsers": previous_month_count,
#             "currentMonthActiveUsers": current_month_count,
#             "retainedUsers": retained_count,
#         }

#         return {
#             "success": True,
#             "data": analytics_data,
#             "message": "LTV analytics fetched successfully"
#         }

#     except Exception as e:
#         logging.error(f"[LTV] Error: {str(e)}")
#         import traceback
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=str(e))


@router.get("/data")
async def get_unit_economics(
    start_date: str = Query(None, description="Start date in YYYY-MM-DD format"),
    end_date: str = Query(None, description="End date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get Unit Economics analytics (CAC + LTV + D30 Retention).

    CAC = Total Expenses / Total New Users
    LTV = 1 / churn_rate
    D30 Retention = Users active 30 days ago who are still active today
    """
    import logging

    # ========== CAC CALCULATION ==========
    # Parse dates if provided
    if start_date:
        start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        # Default to early date for overall data
        start_date_obj = datetime(2020, 1, 1).date()

    if end_date:
        end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        # Default to today
        end_date_obj = datetime.now().date()

    # Adjust end_date to include the full day
    end_date_inclusive = end_date_obj + timedelta(days=1)

    # Step 1: Calculate Total Expenses from expenses table (marketing category only)
    total_expenses = 0
    try:
        expenses_filters = [Expenses.category == "marketing"]
        if start_date:
            expenses_filters.append(Expenses.expense_date >= start_date_obj)
        if end_date:
            expenses_filters.append(Expenses.expense_date < end_date_inclusive)

        expenses_query = select(func.coalesce(func.sum(Expenses.amount), 0))
        if expenses_filters:
            expenses_query = expenses_query.where(and_(*expenses_filters))

        expenses_result = await db.execute(expenses_query)
        total_expenses = expenses_result.scalar() or 0
        logging.info(f"[UnitEconomics] Total marketing expenses from {start_date_obj} to {end_date_obj}: {total_expenses}")
    except Exception as e:
        logging.error(f"[UnitEconomics] Error fetching total expenses: {str(e)}")
        import traceback
        traceback.print_exc()

    # Step 2: Calculate Total Paying Users for this period using common helper
    total_new_users = 0
    try:
        total_paying_start = start_date_obj if start_date else None
        total_paying_end = end_date_obj if end_date else None
        total_new_users = await get_paying_users_count(db, total_paying_start, total_paying_end)
        logging.info(f"[UnitEconomics] Total paying users from {total_paying_start} to {total_paying_end}: {total_new_users}")
    except Exception as e:
        logging.error(f"[UnitEconomics] Error fetching total paying users for CAC: {str(e)}")
        import traceback
        traceback.print_exc()

    # Step 2b: Calculate actual new users (for ARPU/other display cards)
    total_new_users_actual = 0
    try:
        actual_new_users_filters = []
        if start_date:
            actual_new_users_filters.append(func.date(Client.created_at) >= start_date_obj)
        if end_date:
            actual_new_users_filters.append(func.date(Client.created_at) < end_date_inclusive)

        actual_new_users_query = select(func.count()).select_from(Client)
        if actual_new_users_filters:
            actual_new_users_query = actual_new_users_query.where(and_(*actual_new_users_filters))

        actual_new_users_result = await db.execute(actual_new_users_query)
        total_new_users_actual = actual_new_users_result.scalar() or 0
        logging.info(f"[UnitEconomics] Total actual new users from {start_date_obj} to {end_date_obj}: {total_new_users_actual}")
    except Exception as e:
        logging.error(f"[UnitEconomics] Error fetching total actual new users: {str(e)}")

    # Step 3: Calculate CAC (handle division by zero)
    cac = 0
    if total_new_users > 0:
        cac = total_expenses / total_new_users
    else:
        cac = 0

    logging.info(f"[UnitEconomics] CAC calculated: {cac} (expenses: {total_expenses}, paying users: {total_new_users})")

    # ========== LTV & CHURN CALCULATION PRE-REQUISITES (DATE BOUNDARIES) ==========
    today = datetime.now().date()
    first_day_of_current_month = today.replace(day=1)
    
    # Month M-1 (previous month) date range
    previous_month_start = (first_day_of_current_month - timedelta(days=1)).replace(day=1)
    previous_month_end = first_day_of_current_month - timedelta(days=1)

    # Month M-2 (two months ago) date range
    two_months_ago_start = (previous_month_start - timedelta(days=1)).replace(day=1)
    two_months_ago_end = previous_month_start - timedelta(days=1)

    most_recent_completed_month_start = previous_month_start
    most_recent_completed_month_end = previous_month_end

    # ========== TOTAL BOOKINGS (uses same logic as /api/admin/users-stats/data) ==========
    # Parse as Optional[datetime] (None when not provided) — matches users_stats.py exactly
    bookings_start = datetime.strptime(start_date, "%Y-%m-%d") if start_date else None
    bookings_end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if end_date else None
    total_bookings = 0
    try:
        total_bookings = await get_total_bookings_count(db, bookings_start, bookings_end)
        logging.info(f"[UnitEconomics] Total bookings: {total_bookings}")
    except Exception as e:
        logging.error(f"[UnitEconomics] Error fetching total bookings: {e}")

    # Combine CAC and static filter data
    analytics_data = {
        # CAC Data
        "cac": round(cac, 2),
        "totalExpenses": round(total_expenses, 2),
        "marketingExpenses": round(total_expenses, 2),
        "totalNewUsers": total_new_users,
        "totalNewUsersActual": total_new_users_actual,
        # Total Bookings (same source of truth as users-stats page)
        "totalBookings": total_bookings,
        # Filters
        "filters": {
            "startDate": start_date_obj.isoformat(),
            "endDate": end_date_obj.isoformat()
        }
    }

    # ========== FINANCIAL METRICS (EBITA, ARPU, ARPPU, GROSS MARGIN) ==========
    try:
        fin_metrics = await get_financial_metrics(db, start_date_obj, end_date_obj)
        analytics_data["grossMarginPercentage"] = fin_metrics["gross_margin_percentage"]
        analytics_data["ebita"] = fin_metrics["ebita"]
        analytics_data["grossProfit"] = fin_metrics["gross_profit"]
        analytics_data["totalExpenses"] = fin_metrics["total_expenses"]
        analytics_data["arpu"] = fin_metrics["arpu"]
        analytics_data["arppu"] = fin_metrics["arppu"]
        analytics_data["totalNetRevenue"] = fin_metrics["total_net_revenue"]
        analytics_data["totalPayingUsers"] = fin_metrics["paying_users"]
    except Exception as e:
        logging.error(f"[UnitEconomics] Financial metrics error: {e}")
        analytics_data["grossMarginPercentage"] = None
        analytics_data["ebita"] = 0
        analytics_data["arpu"] = 0
        analytics_data["arppu"] = 0

    # ========== LTV & CHURN CALCULATIONS (PAYING COHORT MODEL) ==========
    # Base starting month is February 2026
    base_starting_month = datetime(2026, 2, 1).date()

    logging.info(f"[UnitEconomics Churn] Base Starting Month: {base_starting_month}")
    logging.info(f"[UnitEconomics Churn] Month M-2 (two months ago): {two_months_ago_start} to {two_months_ago_end}")
    logging.info(f"[UnitEconomics Churn] Month M-1 (previous month): {previous_month_start} to {previous_month_end}")

    # Check if M-2 starts before February 2026
    if two_months_ago_start < base_starting_month:
        retention_rate = None
        churn_rate = None
        ltv = None
        retained_count = 0
        last_month_cac = 0.0
        ltv_cac_ratio = None
    else:
        # Step 1: Get paying user sets for M-2 and M-1 using common helper
        two_months_ago_paying_users = await get_paying_users_set(db, two_months_ago_start, two_months_ago_end)
        prev_month_paying_users = await get_paying_users_set(db, previous_month_start, previous_month_end)

        two_months_ago_count = len(two_months_ago_paying_users)

        # Step 2: Find retained paying users (intersection)
        retained_paying_users = two_months_ago_paying_users.intersection(prev_month_paying_users)
        retained_count = len(retained_paying_users)

        # Step 3: Calculate Retention and Churn Rate
        if two_months_ago_count > 0:
            retention_rate = retained_count / two_months_ago_count
            churn_rate = 1.0 - retention_rate
            
            # Step 4: Query earliest captured payment date
            EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
            earliest_payment_query = select(func.min(Payment.captured_at)).join(
                Order, Order.id == Payment.order_id
            ).join(
                OrderItem, OrderItem.order_id == Order.id
            ).outerjoin(
                Client, Payment.customer_id == Client.client_id
            ).where(
                and_(
                    Payment.status == "captured",
                    OrderItem.gym_id.isnot(None),
                    OrderItem.gym_id != "1",
                    or_(Client.contact.is_(None), ~Client.contact.in_(EXCLUDED_CONTACTS))
                )
            )
            earliest_payment_res = await db.execute(earliest_payment_query)
            earliest_captured = earliest_payment_res.scalar()
            
            if earliest_captured:
                first_payment_date = earliest_captured.date()
            else:
                first_payment_date = datetime(2026, 2, 1).date()

            # Step 5: Fetch previous completed month (M-1) financials statically
            last_month_metrics = await get_financial_metrics(db, previous_month_start, previous_month_end)
            last_month_arppu = float(last_month_metrics.get("arppu") or 0)
            
            # Fetch overall metrics statically to get the overall Gross Margin % (e.g. -19.12%)
            overall_metrics = await get_financial_metrics(db, datetime(2020, 1, 1).date(), today)
            overall_gross_margin_percentage = float(overall_metrics.get("gross_margin_percentage") or 0)
            
            # Self-correcting guard: if the gross margin percentage is returned as a raw ratio between -1.0 and 1.0 (e.g. 1.0 for 100%),
            # use it directly. Otherwise (e.g. -19.12 representing -19.12%), divide by 100.0 to get the fraction.
            if -1.0 <= overall_gross_margin_percentage <= 1.0:
                overall_gross_margin_fraction = overall_gross_margin_percentage
            else:
                overall_gross_margin_fraction = overall_gross_margin_percentage / 100.0

            # Calculate months between first payment month and previous completed month (M-1) inclusive
            months_since_first = (previous_month_end.year - first_payment_date.year) * 12 + (previous_month_end.month - first_payment_date.month + 1)
            months_since_first = max(1, months_since_first)

            arppu_per_month = last_month_arppu / months_since_first
            
            if churn_rate > 0:
                ltv = (arppu_per_month * overall_gross_margin_fraction) / churn_rate
            else:
                # Cap expected lifetime at 24 months if churn is 0
                ltv = arppu_per_month * overall_gross_margin_fraction * 24.0
        else:
            retention_rate = None
            churn_rate = None
            ltv = None

        # Step 6: Query last completed month (M-1) paying users and marketing expenses for static CAC
        last_month_paying_users = await get_paying_users_count(db, previous_month_start, previous_month_end)
        
        last_month_expenses_query = select(func.coalesce(func.sum(Expenses.amount), 0)).where(
            and_(
                Expenses.category == "marketing",
                Expenses.expense_date >= previous_month_start,
                Expenses.expense_date <= previous_month_end
            )
        )
        last_month_expenses_res = await db.execute(last_month_expenses_query)
        last_month_marketing_expenses = float(last_month_expenses_res.scalar() or 0)
        
        if last_month_paying_users > 0:
            last_month_cac = last_month_marketing_expenses / last_month_paying_users
        else:
            last_month_cac = 0.0

        if last_month_cac > 0 and ltv is not None:
            ltv_cac_ratio = ltv / last_month_cac
        else:
            ltv_cac_ratio = None

    logging.info(f"[UnitEconomics Churn] Calculated Retention: {retention_rate}, Churn: {churn_rate}, LTV: {ltv}, Last Month CAC: {last_month_cac}, Ratio: {ltv_cac_ratio}")

    # Populate LTV Data (Retention & Churn)
    analytics_data["ltv"] = round(ltv, 2) if ltv is not None else None
    analytics_data["cohortRetentionRate"] = round(retention_rate, 4) if retention_rate is not None else None
    analytics_data["churnRate"] = round(churn_rate, 4) if churn_rate is not None else None
    analytics_data["retainedUsers"] = retained_count
    analytics_data["lastMonthCac"] = round(last_month_cac, 2)
    analytics_data["ltvCacRatio"] = round(ltv_cac_ratio, 4) if ltv_cac_ratio is not None else None

    # ========== ACTIVE USERS (MAU & WAU) ==========
    # Matches logic in /api/admin/users-stats/data strictly
    try:
        # Helper to match users-stats query exactly
        async def fetch_active_users(start_date_val, end_date_val):
            # Convert date to datetime for precise filtering matching users_stats.py
            s_dt = datetime.combine(start_date_val, datetime.min.time())
            e_dt = datetime.combine(end_date_val, datetime.max.time())
            
            # Match Query 2 from users_stats.py
            active_subquery = select(ActiveUser.client_id).join(
                Client, ActiveUser.client_id == Client.client_id
            ).where(
                and_(
                    ActiveUser.created_at >= s_dt,
                    ActiveUser.created_at <= e_dt,
                    or_(Client.gym_id != 1, Client.gym_id.is_(None))
                )
            )

            active_query = select(
                func.coalesce(func.count(func.distinct(ActiveUser.client_id)), 0)
            ).where(
                ActiveUser.client_id.in_(active_subquery)
            )
            
            res = await db.execute(active_query)
            return res.scalar() or 0

        # 1. Monthly Active Users (Last completed month)
        analytics_data["monthlyActiveUsers"] = await fetch_active_users(
            most_recent_completed_month_start, 
            most_recent_completed_month_end
        )

        # 2. Weekly Active Users (Previous completed week: Mon-Sun)
        current_weekday = today.weekday()
        current_week_start = today - timedelta(days=current_weekday)
        prev_week_start = current_week_start - timedelta(weeks=1)
        prev_week_end = current_week_start - timedelta(days=1)
        
        logging.info(f"[UnitEconomics] Previous Week (WAU): {prev_week_start} to {prev_week_end}")
        
        analytics_data["weeklyActiveUsers"] = await fetch_active_users(
            prev_week_start, 
            prev_week_end
        )

        # 3. Daily Active Users (Today)
        analytics_data["dailyActiveUsers"] = await fetch_active_users(today, today)
    except Exception as e:
        logging.error(f"[UnitEconomics] Active users calculation error: {e}")
        analytics_data["monthlyActiveUsers"] = 0
        analytics_data["weeklyActiveUsers"] = 0

    # ========== GMV CALCULATION ==========
    # Delegates to shared helper — guarantees identical values to /api/admin/purchases/gmv-summary
    try:
        gmv = await compute_gmv_totals(db, start_date_obj, end_date_obj)
    except Exception as e:
        logging.error(f"[UnitEconomics] GMV calculation error: {e}")
        gmv = {
            "daily_pass":     {"count": 0, "total_revenue": 0.0},
            "session":        {"count": 0, "total_revenue": 0.0},
            "nutrition_plan": {"count": 0, "total_revenue": 0.0},
            "gym_membership": {"count": 0, "total_revenue": 0.0},
            "ai_credits":     {"count": 0, "total_revenue": 0.0},
        }

    analytics_data["gmv"] = gmv

    return {
        "success": True,
        "data": analytics_data,
        "message": "Unit economics analytics fetched successfully"
    }

@router.get("/ltv-trend")
async def get_ltv_trend(
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get LTV monthly trend data from February 2026 to the current calendar month.
    """
    import logging
    try:
        today = datetime.now().date()
        current_year = today.year
        current_month = today.month

        # Generate a list of (year, month) from Feb 2026 to current month inclusive
        target_months = []
        year, month = 2026, 2
        while (year < current_year) or (year == current_year and month <= current_month):
            target_months.append((year, month))
            month += 1
            if month > 12:
                month = 1
                year += 1

        trend_data = []
        
        # Determine earliest captured payment date dynamically
        EXCLUDED_CONTACTS = ["7373675762", "9486987082", "8667458723", "9840633149", "8667427956", "8667488723", "7975847236"]
        earliest_payment_query = select(func.min(Payment.captured_at)).join(
            Order, Order.id == Payment.order_id
        ).join(
            OrderItem, OrderItem.order_id == Order.id
        ).outerjoin(
            Client, Payment.customer_id == Client.client_id
        ).where(
            and_(
                Payment.status == "captured",
                OrderItem.gym_id.isnot(None),
                OrderItem.gym_id != "1",
                or_(Client.contact.is_(None), ~Client.contact.in_(EXCLUDED_CONTACTS))
            )
        )
        earliest_payment_res = await db.execute(earliest_payment_query)
        earliest_captured = earliest_payment_res.scalar()
        if earliest_captured:
            first_payment_date = earliest_captured.date()
        else:
            first_payment_date = datetime(2026, 2, 1).date()

        for yr, mn in target_months:
            # First day of the targeted month (e.g. Feb 2026)
            first_day_of_month = datetime(yr, mn, 1).date()
            
            # Month M-1 (previous month) relative to the targeted month
            prev_month_end = first_day_of_month - timedelta(days=1)
            prev_month_start = prev_month_end.replace(day=1)
            
            # Month M-2 (two months ago) relative to the targeted month
            two_months_ago_end = prev_month_start - timedelta(days=1)
            two_months_ago_start = two_months_ago_end.replace(day=1)

            # Get paying user sets for M-2 and M-1
            two_months_ago_paying_users = await get_paying_users_set(db, two_months_ago_start, two_months_ago_end)
            prev_month_paying_users = await get_paying_users_set(db, prev_month_start, prev_month_end)
            
            two_months_ago_count = len(two_months_ago_paying_users)
            retained_paying_users = two_months_ago_paying_users.intersection(prev_month_paying_users)
            retained_count = len(retained_paying_users)
            
            # Cohort Retention & Churn Rate
            if two_months_ago_count > 0:
                retention_rate = retained_count / two_months_ago_count
                churn_rate = 1.0 - retention_rate
            else:
                retention_rate = 0.0
                churn_rate = 1.0

            # Last month financials
            last_month_metrics = await get_financial_metrics(db, prev_month_start, prev_month_end)
            last_month_arppu = float(last_month_metrics.get("arppu") or 0)
            
            # Fetch overall Gross Margin % statically from beginning up to today
            overall_metrics = await get_financial_metrics(db, datetime(2020, 1, 1).date(), today)
            overall_gross_margin_percentage = float(overall_metrics.get("gross_margin_percentage") or 0)
            
            # Self-correcting guard
            if -1.0 <= overall_gross_margin_percentage <= 1.0:
                overall_gross_margin_fraction = overall_gross_margin_percentage
            else:
                overall_gross_margin_fraction = overall_gross_margin_percentage / 100.0

            # Months since first payment up to prev_month_end
            months_since_first = (prev_month_end.year - first_payment_date.year) * 12 + (prev_month_end.month - first_payment_date.month + 1)
            months_since_first = max(1, months_since_first)
            
            arppu_per_month = last_month_arppu / months_since_first
            
            if churn_rate > 0:
                ltv = (arppu_per_month * overall_gross_margin_fraction) / churn_rate
            else:
                ltv = arppu_per_month * overall_gross_margin_fraction * 24.0

            # Month display name
            month_label = first_day_of_month.strftime("%b %Y")
            
            trend_data.append({
                "month": month_label,
                "ltv": round(ltv, 2) if ltv is not None else 0.00,
                "arppu": round(last_month_arppu, 2),
                "churnRate": round(churn_rate * 100, 2),
                "retentionRate": round(retention_rate * 100, 2),
                "grossMarginPercentage": round(overall_gross_margin_percentage, 2),
                "monthsCount": months_since_first
            })

        return {
            "success": True,
            "data": trend_data,
            "message": "LTV trend fetched successfully"
        }
    except Exception as e:
        logging.error(f"[UnitEconomics Trend] Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

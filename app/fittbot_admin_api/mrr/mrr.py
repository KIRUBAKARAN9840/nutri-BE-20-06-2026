# MRR (Monthly Recurring Revenue) API
# Uses centralized revenue service for all calculations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date
from typing import Dict, Any
from decimal import Decimal
import calendar

from app.models.async_database import get_async_db

# Import centralized revenue service
from app.fittbot_admin_api.revenue_service import (
    get_mrr_revenue_breakdown,
    get_month_date_range,
    paise_to_rupees,
    paise_to_rupees_float,
    calculate_membership_payout,
    calculate_daily_pass_session_payout,
    calculate_nutritionist_plan_net_revenue,
    calculate_ai_credits_net_revenue
)

router = APIRouter(prefix="/api/admin/mrr", tags=["MRR"])


def calculate_net_revenue_for_mrr(
    fittbot_subscription_revenue: float,
    ai_credits_revenue: float,
    ai_diet_coach_revenue: float,
    gym_membership_revenue: float,
    daily_pass_revenue: float,
    sessions_revenue: float,
    membership_comm: float,
    daily_pass_comm: float,
    sessions_comm: float
) -> Dict[str, Any]:
    """
    Calculate Net Revenue for all income categories.
    Uses centralized GST and payout functions from revenue_service.py.
    """
    GST_RATE = Decimal("0.18")

    membership_comm = Decimal(str(membership_comm))
    daily_pass_comm = Decimal(str(daily_pass_comm))
    sessions_comm = Decimal(str(sessions_comm))

    # 1. Fymble Subscription — reverse GST + Google 15% commission (centralized)
    sub_calc = calculate_nutritionist_plan_net_revenue(int(fittbot_subscription_revenue))
    fittbot_subscription_net = sub_calc["net_revenue"]

    # 2. AI Credits — reverse GST + Google 15% commission (centralized)
    ai_calc = calculate_ai_credits_net_revenue(int(ai_credits_revenue))
    ai_credits_net = ai_calc["net_revenue"]

    # AI Diet Coach — reverse GST + Google 15% commission (centralized using Nutritionist logic)
    ai_diet_coach_calc = calculate_nutritionist_plan_net_revenue(int(ai_diet_coach_revenue))
    ai_diet_coach_net = ai_diet_coach_calc["net_revenue"]

    # 3. Gym Membership Net Revenue
    gym_membership_gst_on_comm = membership_comm * GST_RATE
    gym_membership_net = float(Decimal(str(gym_membership_revenue)) - gym_membership_gst_on_comm)

    # 4. Daily Pass Net Revenue
    daily_pass_gst_on_comm = daily_pass_comm * GST_RATE
    daily_pass_net = float(Decimal(str(daily_pass_revenue)) - daily_pass_gst_on_comm)

    # 5. Session Net Revenue
    sessions_gst_on_comm = sessions_comm * GST_RATE
    sessions_net = float(Decimal(str(sessions_revenue)) - sessions_gst_on_comm)

    total_net_revenue = (
        fittbot_subscription_net +
        ai_credits_net +
        ai_diet_coach_net +
        gym_membership_net +
        daily_pass_net +
        sessions_net
    )

    return {
        "total": float(total_net_revenue),
        "fittbot_subscription": float(fittbot_subscription_net),
        "ai_credits": float(ai_credits_net),
        "ai_diet_coach": float(ai_diet_coach_net),
        "gym_membership": float(gym_membership_net),
        "daily_pass": float(daily_pass_net),
        "sessions": float(sessions_net)
    }


@router.get("/data")
async def get_mrr_data(
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get MRR (Monthly Recurring Revenue) data including:
    - Current month MRR (net revenue after GST, with amortization)
    - Previous month MRR (net revenue after GST, with amortization)
    - ARR (Annual Recurring Revenue = Previous Month MRR × 12)

    Uses centralized revenue service for all calculations.
    """
    try:
        today = date.today()
        current_year = today.year
        current_month = today.month

        # Calculate previous month
        if current_month == 1:
            prev_year = current_year - 1
            prev_month = 12
        else:
            prev_year = current_year
            prev_month = current_month - 1

        # Get date ranges
        current_start, current_end = get_month_date_range(current_year, current_month)
        prev_start, prev_end = get_month_date_range(prev_year, prev_month)

        # Use centralized revenue service with amortization
        current_revenue_data = await get_mrr_revenue_breakdown(
            db=db,
            target_month_start=current_start,
            target_month_end=current_end,
            exclude_gym_id_one=True
        )

        prev_revenue_data = await get_mrr_revenue_breakdown(
            db=db,
            target_month_start=prev_start,
            target_month_end=prev_end,
            exclude_gym_id_one=True
        )

        # Calculate commissions for net revenue
        # Current month
        current_membership_payout, current_membership_comm, _, _ = calculate_membership_payout(
            current_revenue_data.gym_membership
        )
        current_daily_pass_payout, current_daily_pass_comm, _, _ = calculate_daily_pass_session_payout(
            current_revenue_data.daily_pass
        )
        current_sessions_payout, current_sessions_comm, _, _ = calculate_daily_pass_session_payout(
            current_revenue_data.sessions
        )

        # Previous month
        prev_membership_payout, prev_membership_comm, _, _ = calculate_membership_payout(
            prev_revenue_data.gym_membership
        )
        prev_daily_pass_payout, prev_daily_pass_comm, _, _ = calculate_daily_pass_session_payout(
            prev_revenue_data.daily_pass
        )
        prev_sessions_payout, prev_sessions_comm, _, _ = calculate_daily_pass_session_payout(
            prev_revenue_data.sessions
        )

        # Calculate NET revenue (after GST)
        current_net_result = calculate_net_revenue_for_mrr(
            fittbot_subscription_revenue=current_revenue_data.fittbot_subscription,
            ai_credits_revenue=current_revenue_data.ai_credits,
            ai_diet_coach_revenue=current_revenue_data.ai_diet_coach,
            gym_membership_revenue=current_revenue_data.gym_membership,
            daily_pass_revenue=current_revenue_data.daily_pass,
            sessions_revenue=current_revenue_data.sessions,
            membership_comm=current_membership_comm,
            daily_pass_comm=current_daily_pass_comm,
            sessions_comm=current_sessions_comm
        )

        prev_net_result = calculate_net_revenue_for_mrr(
            fittbot_subscription_revenue=prev_revenue_data.fittbot_subscription,
            ai_credits_revenue=prev_revenue_data.ai_credits,
            ai_diet_coach_revenue=prev_revenue_data.ai_diet_coach,
            gym_membership_revenue=prev_revenue_data.gym_membership,
            daily_pass_revenue=prev_revenue_data.daily_pass,
            sessions_revenue=prev_revenue_data.sessions,
            membership_comm=prev_membership_comm,
            daily_pass_comm=prev_daily_pass_comm,
            sessions_comm=prev_sessions_comm
        )

        current_net_revenue = current_net_result["total"]
        prev_net_revenue = prev_net_result["total"]

        # ARR = Previous Month MRR × 12
        arr = prev_net_revenue * 12

        # Convert to rupees for display
        current_month_revenue = paise_to_rupees_float(current_net_revenue)
        previous_month_revenue = paise_to_rupees_float(prev_net_revenue)
        arr_revenue = paise_to_rupees_float(arr)

        # Helper function to format to exactly 2 decimal places
        def format_two_decimal(value):
            return float(f"{value:.2f}")

        return {
            "success": True,
            "data": {
                "currentMonthRevenue": format_two_decimal(current_month_revenue),
                "previousMonthRevenue": format_two_decimal(previous_month_revenue),
                "arr": format_two_decimal(arr_revenue),
                "breakdown": {
                    "current_month": {
                        "fittbot_subscription": format_two_decimal(paise_to_rupees_float(current_net_result["fittbot_subscription"])),
                        "ai_credits": format_two_decimal(paise_to_rupees_float(current_net_result["ai_credits"])),
                        "ai_diet_coach": format_two_decimal(paise_to_rupees_float(current_net_result["ai_diet_coach"])),
                        "gym_membership": format_two_decimal(paise_to_rupees_float(current_net_result["gym_membership"])),
                        "daily_pass": format_two_decimal(paise_to_rupees_float(current_net_result["daily_pass"])),
                        "sessions": format_two_decimal(paise_to_rupees_float(current_net_result["sessions"])),
                        "net_revenue": format_two_decimal(current_month_revenue)
                    },
                    "previous_month": {
                        "fittbot_subscription": format_two_decimal(paise_to_rupees_float(prev_net_result["fittbot_subscription"])),
                        "ai_credits": format_two_decimal(paise_to_rupees_float(prev_net_result["ai_credits"])),
                        "ai_diet_coach": format_two_decimal(paise_to_rupees_float(prev_net_result["ai_diet_coach"])),
                        "gym_membership": format_two_decimal(paise_to_rupees_float(prev_net_result["gym_membership"])),
                        "daily_pass": format_two_decimal(paise_to_rupees_float(prev_net_result["daily_pass"])),
                        "sessions": format_two_decimal(paise_to_rupees_float(prev_net_result["sessions"])),
                        "net_revenue": format_two_decimal(previous_month_revenue)
                    }
                },
                "filters": {
                    "currentMonth": {
                        "start": current_start.isoformat(),
                        "end": current_end.isoformat()
                    },
                    "previousMonth": {
                        "start": prev_start.isoformat(),
                        "end": prev_end.isoformat()
                    }
                }
            }

        }

    except Exception as e:
        print(f"[MRR] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

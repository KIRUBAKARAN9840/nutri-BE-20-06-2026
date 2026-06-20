from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, date
from sqlalchemy import func, and_, select, distinct
from decimal import Decimal

from app.models.async_database import get_async_db
from app.models.fittbot_models import ActiveUser, Client
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.models.adminmodels import Expenses

# Import centralized revenue service
from app.fittbot_admin_api.revenue_service import (
    get_revenue_breakdown,
    paise_to_rupees,
    paise_to_rupees_float,
    calculate_nutritionist_plan_net_revenue,
    calculate_ai_credits_net_revenue,
    calculate_membership_payout,
    calculate_daily_pass_session_payout,
    get_gross_margin_data,
    calculate_net_revenue,
    get_financial_metrics
)

router = APIRouter(prefix="/api/admin/financials", tags=["AdminFinancials"])



async def get_total_expenses(
    db: AsyncSession,
    start_date,
    end_date
):
    """
    Standardized wrapper for total expenses.
    """
    from app.fittbot_admin_api.revenue_service import get_financial_metrics
    metrics = await get_financial_metrics(db, start_date, end_date)
    return metrics["total_expenses"]


@router.get("/overview")
async def get_financials_overview(
    start_date: str = None,
    end_date: str = None,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Get Financials Dashboard data including:
    - Total Revenue (all sources)
    - Actual Gym Payout (excludes Fymble Subscription)
    """
    try:
        # Parse dates
        if start_date:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
        else:
            start_date_obj = datetime(2020, 1, 1).date()

        if end_date:
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
        else:
            end_date_obj = datetime.now().date()

        # Use centralized revenue service
        revenue_data = await get_revenue_breakdown(
            db=db,
            start_date=start_date_obj,
            end_date=end_date_obj,
            exclude_gym_id_one=True
        )

        # Extract individual source revenues (all in PAISA)
        daily_pass_revenue = revenue_data.daily_pass
        sessions_revenue = revenue_data.sessions
        gym_membership_revenue = revenue_data.gym_membership
        fittbot_subscription_revenue = revenue_data.fittbot_subscription
        ai_credits_revenue = revenue_data.ai_credits
        ai_diet_coach_revenue = revenue_data.ai_diet_coach

        total_revenue = revenue_data.total_revenue

        # Calculate Actual Gym Payout (excluding Fymble Subscription)
        membership_payout, membership_comm, membership_pg, membership_tds = calculate_membership_payout(gym_membership_revenue)
        daily_pass_payout, daily_pass_comm, daily_pass_pg, daily_pass_tds = calculate_daily_pass_session_payout(daily_pass_revenue)
        sessions_payout, sessions_comm, sessions_pg, sessions_tds = calculate_daily_pass_session_payout(sessions_revenue)

        actual_gym_payout = membership_payout + daily_pass_payout + sessions_payout

        # Calculate total deductions
        total_commission = membership_comm + daily_pass_comm + sessions_comm
        total_pg = membership_pg + daily_pass_pg + sessions_pg
        total_tds = membership_tds + daily_pass_tds + sessions_tds
        total_deductions = total_commission + total_pg + total_tds

        # Calculate Net Revenue for all categories
        net_revenue_data = calculate_net_revenue(
            fittbot_subscription_revenue=fittbot_subscription_revenue,
            gym_membership_revenue=gym_membership_revenue,
            daily_pass_revenue=daily_pass_revenue,
            sessions_revenue=sessions_revenue,
            ai_credits_revenue=ai_credits_revenue,
            ai_diet_coach_revenue=ai_diet_coach_revenue,
            membership_comm=membership_comm,
            daily_pass_comm=daily_pass_comm,
            sessions_comm=sessions_comm
        )

        # Calculate Gross Profit
        fittbot_subscription_gross_profit = net_revenue_data["fittbot_subscription"]["net_revenue"]
        ai_credits_gross_profit = net_revenue_data["ai_credits"]["net_revenue"]
        ai_diet_coach_gross_profit = net_revenue_data["ai_diet_coach"]["net_revenue"]
        gym_membership_gross_profit = membership_comm - net_revenue_data["gym_membership"]["gst_on_comm"]
        daily_pass_gross_profit = daily_pass_comm - net_revenue_data["daily_pass"]["gst_on_comm"]
        sessions_gross_profit = sessions_comm - net_revenue_data["sessions"]["gst_on_comm"]

        # Get Comprehensive Financial Metrics
        fin_metrics = await get_financial_metrics(db, start_date_obj, end_date_obj)
        
        gross_profit_rupees     = fin_metrics["gross_profit"]
        gross_margin            = fin_metrics["gross_margin"]
        gross_margin_percentage = fin_metrics["gross_margin_percentage"]
        total_expenses          = fin_metrics["total_expenses"]
        ebita                   = fin_metrics["ebita"]
        total_users_count       = fin_metrics["total_users"]
        paying_users_count      = fin_metrics["paying_users"]
        active_users_count      = fin_metrics["active_users"]
        arpu                    = fin_metrics["arpu"]
        arppu                   = fin_metrics["arppu"]
        net_revenue_rupees      = fin_metrics["total_net_revenue"]
        cogs_expenses           = fin_metrics.get("cogs_expenses", 0) # Added COGS

        return {
            "success": True,
            "data": {
                "totalRevenue": paise_to_rupees(total_revenue),
                "actualGymPayout": paise_to_rupees(actual_gym_payout),
                "netRevenue": paise_to_rupees(net_revenue_data["total_net_revenue"]),
                "revenueSourceBreakdown": {
                    "daily_pass": paise_to_rupees(daily_pass_revenue),
                    "sessions": paise_to_rupees(sessions_revenue),
                    "fittbot_subscription": paise_to_rupees(fittbot_subscription_revenue),
                    "gym_membership": paise_to_rupees(gym_membership_revenue),
                    "ai_credits": paise_to_rupees(ai_credits_revenue),
                    "ai_diet_coach": paise_to_rupees(ai_diet_coach_revenue),
                    "total": paise_to_rupees(total_revenue)
                },
                "payoutBreakdown": {
                    "membership": {
                        "revenue": paise_to_rupees(gym_membership_revenue),
                        "payout": paise_to_rupees(membership_payout),
                        "deductions": {
                            "commission": paise_to_rupees(membership_comm),
                            "pg_deduction": paise_to_rupees(membership_pg),
                            "tds_deduction": paise_to_rupees(membership_tds)
                        }
                    },
                    "daily_pass": {
                        "revenue": paise_to_rupees(daily_pass_revenue),
                        "payout": paise_to_rupees(daily_pass_payout),
                        "deductions": {
                            "commission": paise_to_rupees(daily_pass_comm),
                            "pg_deduction": paise_to_rupees(daily_pass_pg),
                            "tds_deduction": paise_to_rupees(daily_pass_tds)
                        }
                    },
                    "sessions": {
                        "revenue": paise_to_rupees(sessions_revenue),
                        "payout": paise_to_rupees(sessions_payout),
                        "deductions": {
                            "commission": paise_to_rupees(sessions_comm),
                            "pg_deduction": paise_to_rupees(sessions_pg),
                            "tds_deduction": paise_to_rupees(sessions_tds)
                        }
                    }
                },
                "totalDeductions": {
                    "commission": paise_to_rupees(total_commission),
                    "pg_deduction": paise_to_rupees(total_pg),
                    "tds_deduction": paise_to_rupees(total_tds),
                    "total": paise_to_rupees(total_deductions)
                },
                "netRevenueBreakdown": {
                    "fittbot_subscription": {   
                        "revenue": paise_to_rupees(net_revenue_data["fittbot_subscription"]["revenue"]),
                        "gst": paise_to_rupees(net_revenue_data["fittbot_subscription"]["gst"]),
                        "net_revenue": paise_to_rupees(net_revenue_data["fittbot_subscription"]["net_revenue"])
                    },
                    "ai_credits": {
                        "revenue": paise_to_rupees(net_revenue_data["ai_credits"]["revenue"]),
                        "gst": paise_to_rupees(net_revenue_data["ai_credits"]["gst"]),
                        "net_revenue": paise_to_rupees(net_revenue_data["ai_credits"]["net_revenue"])
                    },
                    "ai_diet_coach": {
                        "revenue": paise_to_rupees(net_revenue_data["ai_diet_coach"]["revenue"]),
                        "gst": paise_to_rupees(net_revenue_data["ai_diet_coach"]["gst"]),
                        "net_revenue": paise_to_rupees(net_revenue_data["ai_diet_coach"]["net_revenue"])
                    },
                    "gym_membership": {
                        "revenue": paise_to_rupees(net_revenue_data["gym_membership"]["revenue"]),
                        "commission": paise_to_rupees(net_revenue_data["gym_membership"]["commission"]),
                        "gst_on_comm": paise_to_rupees(net_revenue_data["gym_membership"]["gst_on_comm"]),
                        "net_revenue": paise_to_rupees(net_revenue_data["gym_membership"]["net_revenue"])
                    },
                    "daily_pass": {
                        "revenue": paise_to_rupees(net_revenue_data["daily_pass"]["revenue"]),
                        "commission": paise_to_rupees(net_revenue_data["daily_pass"]["commission"]),
                        "gst_on_comm": paise_to_rupees(net_revenue_data["daily_pass"]["gst_on_comm"]),
                        "net_revenue": paise_to_rupees(net_revenue_data["daily_pass"]["net_revenue"])
                    },
                    "sessions": {
                        "revenue": paise_to_rupees(net_revenue_data["sessions"]["revenue"]),
                        "commission": paise_to_rupees(net_revenue_data["sessions"]["commission"]),
                        "gst_on_comm": paise_to_rupees(net_revenue_data["sessions"]["gst_on_comm"]),
                        "net_revenue": paise_to_rupees(net_revenue_data["sessions"]["net_revenue"])
                    },
                    "total_net_revenue": paise_to_rupees(net_revenue_data["total_net_revenue"])
                },
                "grossProfitBreakdown": {
                    "fittbot_subscription": {
                        "revenue": paise_to_rupees(net_revenue_data["fittbot_subscription"]["revenue"]),
                        "gst": paise_to_rupees(net_revenue_data["fittbot_subscription"]["gst"]),
                        "gross_profit": paise_to_rupees(fittbot_subscription_gross_profit)
                    },
                    "ai_credits": {
                        "revenue": paise_to_rupees(net_revenue_data["ai_credits"]["revenue"]),
                        "gst": paise_to_rupees(net_revenue_data["ai_credits"]["gst"]),
                        "gross_profit": paise_to_rupees(ai_credits_gross_profit)
                    },
                    "ai_diet_coach": {
                        "revenue": paise_to_rupees(net_revenue_data["ai_diet_coach"]["revenue"]),
                        "gst": paise_to_rupees(net_revenue_data["ai_diet_coach"]["gst"]),
                        "gross_profit": paise_to_rupees(ai_diet_coach_gross_profit)
                    },
                    "gym_membership": {
                        "revenue": paise_to_rupees(net_revenue_data["gym_membership"]["revenue"]),
                        "commission": paise_to_rupees(net_revenue_data["gym_membership"]["commission"]),
                        "gst_on_comm": paise_to_rupees(net_revenue_data["gym_membership"]["gst_on_comm"]),
                        "gross_profit": paise_to_rupees(gym_membership_gross_profit)
                    },
                    "daily_pass": {
                        "revenue": paise_to_rupees(net_revenue_data["daily_pass"]["revenue"]),
                        "commission": paise_to_rupees(net_revenue_data["daily_pass"]["commission"]),
                        "gst_on_comm": paise_to_rupees(net_revenue_data["daily_pass"]["gst_on_comm"]),
                        "gross_profit": paise_to_rupees(daily_pass_gross_profit)
                    },
                    "sessions": {
                        "revenue": paise_to_rupees(net_revenue_data["sessions"]["revenue"]),
                        "commission": paise_to_rupees(net_revenue_data["sessions"]["commission"]),
                        "gst_on_comm": paise_to_rupees(net_revenue_data["sessions"]["gst_on_comm"]),
                        "gross_profit": paise_to_rupees(sessions_gross_profit)
                    },
                    "total_gross_profit": round(gross_profit_rupees, 2)
                },
                "grossProfit": round(gross_profit_rupees, 2),
                "grossMargin": {
                    "gross_profit": round(gross_profit_rupees, 2),
                    "aws_cost": round(cogs_expenses, 2),
                    "gross_margin": round(gross_margin, 2),
                    "gross_margin_percentage": gross_margin_percentage
                },
                "ebita": {
                    "gross_profit": round(gross_profit_rupees, 2),
                    "total_expenses": round(total_expenses, 2),
                    "ebita": round(ebita, 2)
                },
                "arpu": {
                    "net_revenue": round(net_revenue_rupees, 2),
                    "total_users": total_users_count,
                    "arpu": round(arpu, 2)
                },
                "arppu": {
                    "net_revenue": round(net_revenue_rupees, 2),
                    "paying_users": paying_users_count,
                    "arppu": round(arppu, 2)
                },
                "filters": {
                    "startDate": start_date_obj.isoformat(),
                    "endDate": end_date_obj.isoformat()
                }
            },

        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        print(f"[FINANCIALS] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
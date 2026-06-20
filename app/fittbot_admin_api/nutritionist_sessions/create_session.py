from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from typing import Optional
from datetime import datetime, date, time, timedelta
from pydantic import BaseModel

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.nutrition_models import (
    NutritionMembershipSession,
    NutritionBooking,
    NutritionEligibility,
    NutritionSchedule
)
from app.models.fittbot_models import Client, Gym, FittbotGymMembership
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.fittbot_api.v1.payments.models.entitlements import Entitlement
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
from app.fittbot_admin_api.nutritionist_sessions._helpers import resolve_nutritionist_id

router = APIRouter(prefix="/api/admin/nutritionist_sessions", tags=["NutritionistCreateSession"])

# Gym membership flow keys that qualify for nutrition consultation
ELIGIBLE_FLOWS = {
    "unified_gym_membership_with_sub",
    "unified_gym_membership_with_free_fittbot",
    "gym_membership_with_bonus_credits",
    "personal_training_with_bonus_credits",
    "dailypass_checkout_api",
}

@router.get("/eligible-members")
async def get_eligible_members(
    search: Optional[str] = Query(None, description="Search by client name or contact"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Fetch all users who have an eligible gym membership for a fittbot free trial.
    Exclude users who have already started a nutritionist session.
    """
    try:
        # Step 1: Fetch all used payment_ids into memory to avoid cross-db collation mismatch
        used_payments_query = select(NutritionMembershipSession.payment_id)
        used_result = await db.execute(used_payments_query)
        used_payment_ids = {row[0] for row in used_result.all() if row[0]}

        # Step 2: Use an optimized query for the rest, completely avoiding Python loops
        # and checking the JSON order_metadata directly in MySQL
        stmt = (
            select(
                Payment,
                Order,
                OrderItem.gym_id,
                Client.name.label("client_name"),
                Client.contact.label("client_contact"),
                Gym.name.label("gym_name"),
                FittbotGymMembership.status.label("membership_status"),
                FittbotGymMembership.expires_at.label("membership_expires_at")
            )
            .select_from(Payment)
            .join(Order, Order.id == Payment.order_id)
            .join(OrderItem, and_(
                OrderItem.order_id == Order.id,
                OrderItem.gym_id.isnot(None),
                OrderItem.gym_id != "1"
            ))
            .join(Client, Client.client_id == Order.customer_id)
            .join(Gym, Gym.gym_id == OrderItem.gym_id)
            .outerjoin(Entitlement, Entitlement.order_item_id == OrderItem.id)
            .outerjoin(FittbotGymMembership, FittbotGymMembership.entitlement_id == Entitlement.id)
            .where(Payment.status == "captured")
            .where(Order.status == "paid")
        )
        
        # Exclude used payments
        if used_payment_ids:
            stmt = stmt.where(Payment.id.notin_(used_payment_ids))

        # Check JSON for eligible orders
        ELIGIBLE_FLOWS = [
            "unified_gym_membership_with_sub",
            "unified_gym_membership_with_free_fittbot",
            "gym_membership_with_bonus_credits",
            "personal_training_with_bonus_credits",
            "dailypass_checkout_api",
        ]
        stmt = stmt.where(
            or_(
                func.json_unquote(func.json_extract(Order.order_metadata, "$.audit.source")) == "dailypass_checkout_api",
                func.json_unquote(func.json_extract(Order.order_metadata, "$.order_info.flow")).in_(ELIGIBLE_FLOWS)
            )
        )

        # Check membership status
        stmt = stmt.where(
            or_(
                FittbotGymMembership.id.is_(None),
                FittbotGymMembership.status.in_(["active", "paused", "expired", "upcoming"])
            )
        )
        
        stmt = stmt.order_by(Order.created_at.desc())

        result = await db.execute(stmt)
        rows = result.all()

        members = []
        for r in rows:
            payment = r.Payment
            order = r.Order
            
            # Determine plan name from order metadata
            plan_name = "Gym Membership"
            if order.order_metadata and isinstance(order.order_metadata, dict):
                flow = order.order_metadata.get("order_info", {}).get("flow", "")
                if flow == "personal_training_with_bonus_credits":
                    plan_name = "Personal Training"
                elif flow == "dailypass_checkout_api":
                    plan_name = "Daily Pass"

            members.append({
                "payment_id": payment.id,
                "client_id": int(order.customer_id) if order.customer_id and order.customer_id.isdigit() else order.customer_id,
                "client_name": r.client_name or "N/A",
                "client_contact": r.client_contact or "N/A",
                "gym_name": r.gym_name or "N/A",
                "gym_id": r.gym_id,
                "plan_name": plan_name,
                "amount": float(order.gross_amount_minor / 100) if order.gross_amount_minor else 0.0,
                "purchased_at": order.created_at.isoformat() if order.created_at else None,
                "membership_status": r.membership_status or "N/A",
                "expires_at": r.membership_expires_at.isoformat() if r.membership_expires_at else None,
            })

        # Apply search filter
        if search and search.strip():
            s_lower = search.lower()
            filtered = []
            for m in members:
                c_name = m["client_name"].lower()
                c_contact = m["client_contact"].lower()
                if s_lower in c_name or s_lower in c_contact:
                    filtered.append(m)
            members = filtered

        # Sort by purchased_at descending (most recent first)
        members.sort(key=lambda x: x["purchased_at"] or "", reverse=True)

        return {
            "success": True,
            "data": {
                "members": members,
                "total": len(members)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching eligible members: {str(e)}"
        )


class CreateMembershipSessionRequest(BaseModel):
    """Request model for creating a nutrition membership session."""
    payment_id: str
    client_id: int
    booking_date: str  # YYYY-MM-DD
    start_time: str    # HH:MM (24-hour)
    end_time: str      # HH:MM (24-hour)


@router.post("/create-membership-session")
async def create_membership_session(
    request_data: CreateMembershipSessionRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Create a one-time nutrition consultation session for a gym membership payment.

    Validates that:
    - The payment_id hasn't already been used for a session
    - The payment exists and is captured
    - The client_id matches the payment's customer_id
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # Step 1: Check if payment_id is already used
        existing_query = select(NutritionMembershipSession).where(
            NutritionMembershipSession.payment_id == request_data.payment_id
        )
        existing_result = await db.execute(existing_query)
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="A nutrition session has already been created for this payment."
            )

        # Step 2: Verify payment exists and matches the client
        payment_query = (
            select(Payment, Order)
            .join(Order, Order.id == Payment.order_id)
            .where(Payment.id == request_data.payment_id)
            .where(Payment.status == "captured")
            .where(Order.status == "paid")
        )
        payment_result = await db.execute(payment_query)
        payment_row = payment_result.first()

        if not payment_row:
            raise HTTPException(
                status_code=404,
                detail="Payment not found or is not in a valid state."
            )

        payment = payment_row.Payment
        order = payment_row.Order

        if order.customer_id and int(order.customer_id) != request_data.client_id:
            raise HTTPException(
                status_code=400,
                detail="Client ID does not match the payment's customer."
            )

        # Step 3: Get gym_id from order items
        order_item_query = (
            select(OrderItem.gym_id)
            .where(OrderItem.order_id == order.id)
            .where(OrderItem.gym_id.isnot(None))
            .where(OrderItem.gym_id != "1")
            .limit(1)
        )
        oi_result = await db.execute(order_item_query)
        gym_id_str = oi_result.scalar_one_or_none()
        gym_id = int(gym_id_str) if gym_id_str and gym_id_str.isdigit() else None

        # Step 4: Parse booking date and times
        try:
            booking_date = datetime.strptime(request_data.booking_date, "%Y-%m-%d").date()
            start_hour, start_minute = map(int, request_data.start_time.split(":"))
            end_hour, end_minute = map(int, request_data.end_time.split(":"))
            start_time = time(hour=start_hour, minute=start_minute)
            end_time = time(hour=end_hour, minute=end_minute)
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date or time format. Expected YYYY-MM-DD and HH:MM. Error: {str(e)}"
            )

        # Step 5: Create the session record
        session = NutritionMembershipSession(
            client_id=request_data.client_id,
            nutritionist_id=nutritionist_id,
            payment_id=request_data.payment_id,
            gym_id=gym_id,
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
            status="booked",
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)

        # Get client name for response
        client_query = select(Client.name).where(Client.client_id == request_data.client_id)
        client_result = await db.execute(client_query)
        client_name = client_result.scalar_one_or_none() or "N/A"

        return {
            "success": True,
            "message": "Nutrition consultation session created successfully.",
            "data": {
                "session_id": session.id,
                "client_id": session.client_id,
                "client_name": client_name,
                "payment_id": session.payment_id,
                "booking_date": session.booking_date.isoformat(),
                "start_time": session.start_time.strftime("%I:%M %p"),
                "end_time": session.end_time.strftime("%I:%M %p"),
                "status": "Booked"
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error creating membership session: {str(e)}"
        )

@router.get("/eligible-plan-members")
async def get_eligible_plan_members(
    search: Optional[str] = Query(None, description="Search by client name or contact"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get users who still have pending sessions remaining in their plan.
    Filters out plans that have no remaining sessions (i.e. remaining_sessions <= 0).
    """
    try:
        booked_count_subq = (
            select(func.count(NutritionBooking.id))
            .where(
                and_(
                    NutritionBooking.eligibility_id == NutritionEligibility.id,
                    NutritionBooking.status != "cancelled"
                )
            )
            .correlate(NutritionEligibility)
            .scalar_subquery()
            .label("booked_count")
        )

        # Fetch clients with positive remaining sessions (total - booked)
        stmt = (
            select(
                NutritionEligibility.id.label("eligibility_id"),
                NutritionEligibility.client_id,
                NutritionEligibility.plan_name,
                NutritionEligibility.total_sessions,
                booked_count_subq,
                Client.name.label("client_name"),
                Client.contact.label("client_contact")
            )
            .select_from(NutritionEligibility)
            .join(Client, NutritionEligibility.client_id == Client.client_id)
            .where((NutritionEligibility.total_sessions - booked_count_subq) > 0)
        )
        result = await db.execute(stmt)
        rows = result.all()

        members = []
        for r in rows:
            plan_name = r.plan_name or "Plan"
            plan_name_lower = plan_name.lower()
            if plan_name_lower.startswith("gym membership") or plan_name_lower.startswith("personal training"):
                continue
            members.append({
                "eligibility_id": r.eligibility_id,
                "client_id": r.client_id,
                "client_name": r.client_name or "N/A",
                "client_contact": r.client_contact or "N/A",
                "plan_name": plan_name,
                "total_sessions": r.total_sessions,
                "used_sessions": r.booked_count,
                "remaining_sessions": r.total_sessions - r.booked_count,
                "progress": f"{r.booked_count}/{r.total_sessions}"
            })

        # Apply search filter
        if search and search.strip():
            search_lower = search.lower().strip()
            members = [
                m for m in members
                if search_lower in m["client_name"].lower()
                or search_lower in str(m["client_contact"])
            ]

        # Sort by client_name
        members.sort(key=lambda x: x["client_name"])

        return {
            "success": True,
            "data": {
                "members": members,
                "total": len(members)
            }
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching eligible plan members: {str(e)}"
        )


@router.get("/schedules")
async def get_nutritionist_schedules(
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Fetch all active schedules for the current nutritionist.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)
        stmt = (
            select(
                NutritionSchedule.id,
                NutritionSchedule.weekday,
                NutritionSchedule.start_time,
                NutritionSchedule.end_time
            )
            .where(
                and_(
                    NutritionSchedule.nutritionist_id == nutritionist_id,
                    NutritionSchedule.is_active.is_(True)
                )
            )
            .order_by(NutritionSchedule.weekday, NutritionSchedule.start_time)
        )
        result = await db.execute(stmt)
        rows = result.all()

        schedules = []
        for r in rows:
            schedules.append({
                "id": r.id,
                "weekday": r.weekday,
                "start_time": r.start_time.strftime("%H:%M"),
                "end_time": r.end_time.strftime("%H:%M")
            })

        return {
            "success": True,
            "data": {
                "schedules": schedules
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching nutritionist schedules: {str(e)}"
        )


class CreatePlanSessionRequest(BaseModel):
    """Request model for creating a plan-based session."""
    eligibility_id: int
    client_id: int
    booking_date: str  # YYYY-MM-DD
    start_time: str    # HH:MM (24-hour)
    end_time: str      # HH:MM (24-hour)
    schedule_id: int


@router.post("/create-plan-session")
async def create_plan_session(
    request_data: CreatePlanSessionRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Create a new plan-based session booking in nutrition_bookings.
    Automatically calculates the session number.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # Step 1: Validate eligibility exists and has remaining sessions
        elig_stmt = select(NutritionEligibility).where(
            and_(
                NutritionEligibility.id == request_data.eligibility_id,
                NutritionEligibility.client_id == request_data.client_id
            )
        )
        elig_result = await db.execute(elig_stmt)
        eligibility = elig_result.scalar_one_or_none()

        if not eligibility:
            raise HTTPException(
                status_code=404,
                detail="Nutrition plan eligibility record not found for this client."
            )

        # Count all existing bookings for this eligibility that are not cancelled
        booking_count_stmt = select(func.count(NutritionBooking.id)).where(
            and_(
                NutritionBooking.eligibility_id == request_data.eligibility_id,
                NutritionBooking.status != "cancelled"
            )
        )
        count_result = await db.execute(booking_count_stmt)
        existing_count = count_result.scalar() or 0
        
        if eligibility.total_sessions - existing_count <= 0:
            raise HTTPException(
                status_code=400,
                detail="No remaining sessions available to book in this client's nutrition plan."
            )

        session_number = existing_count + 1

        # Step 3: Parse dates/times
        try:
            booking_date = datetime.strptime(request_data.booking_date, "%Y-%m-%d").date()
            start_hour, start_minute = map(int, request_data.start_time.split(":"))
            end_hour, end_minute = map(int, request_data.end_time.split(":"))
            start_time = time(hour=start_hour, minute=start_minute)
            end_time = time(hour=end_hour, minute=end_minute)
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date or time format. Expected YYYY-MM-DD and HH:MM. Error: {str(e)}"
            )

        # Calculate duration
        start_dt = datetime.combine(booking_date, start_time)
        end_dt = datetime.combine(booking_date, end_time)
        duration_minutes = int((end_dt - start_dt).total_seconds() / 60)

        # Step 4: Create booking record
        booking = NutritionBooking(
            client_id=request_data.client_id,
            eligibility_id=request_data.eligibility_id,
            nutritionist_id=nutritionist_id,
            schedule_id=request_data.schedule_id,
            booking_date=booking_date,
            start_time=start_time,
            end_time=end_time,
            session_number=session_number,
            duration_minutes=duration_minutes,
            status="booked",
        )

        db.add(booking)
        await db.commit()
        await db.refresh(booking)

        # Get client name for response
        client_query = select(Client.name).where(Client.client_id == request_data.client_id)
        client_result = await db.execute(client_query)
        client_name = client_result.scalar_one_or_none() or "N/A"

        return {
            "success": True,
            "message": "Nutrition plan consultation session scheduled successfully.",
            "data": {
                "booking_id": booking.id,
                "client_id": booking.client_id,
                "client_name": client_name,
                "session_number": booking.session_number,
                "booking_date": booking.booking_date.isoformat(),
                "start_time": booking.start_time.strftime("%I:%M %p"),
                "end_time": booking.end_time.strftime("%I:%M %p"),
                "status": "Booked"
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error scheduling plan session: {str(e)}"
        )


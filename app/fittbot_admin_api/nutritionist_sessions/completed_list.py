from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, desc, or_, update, delete
from typing import Dict, List, Optional
from datetime import date, time, datetime
from pydantic import BaseModel

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.nutrition_models import Nutritionist, CompletedSession, DietTemplate, ClientDietTemplate, NutritionBooking, NutritionEligibility
from app.models.fittbot_models import Client
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
from app.fittbot_admin_api.nutritionist_sessions._helpers import resolve_nutritionist_id

router = APIRouter(prefix="/api/admin/nutritionist_completed_list", tags=["NutritionistCompletedList"])

# IST Timezone
import pytz
IST = pytz.timezone("Asia/Kolkata")


def format_time_slot(t: time) -> str:
    """Convert time to HH:MM AM/PM format"""
    if not t:
        return ""
    return t.strftime("%I:%M %p")


def convert_date_to_irst(date_value: date) -> str:
    """Convert date object to IST date string (YYYY-MM-DD format)"""
    if date_value is None:
        return None
    return date_value.isoformat()


@router.get("/sessions")
async def get_completed_sessions_list(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    search: Optional[str] = Query(None, description="Search by client name"),
    start_date: Optional[date] = Query(None, description="Filter by start date (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Filter by end date (ISO format YYYY-MM-DD)"),
    interested_in_product: Optional[bool] = Query(None, description="Filter by product interest"),
    client_wise: bool = Query(False, description="Whether to group by client"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        # Build the base query with filters
        conditions = [
            CompletedSession.nutritionist_id == nutritionist_id
        ]

        # Add search filter (client name or booking_id)
        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    Client.name.ilike(search_pattern),
                    CompletedSession.booking_id.ilike(search_pattern)
                )
            )

        # Add date range filters
        if start_date:
            conditions.append(CompletedSession.slot_date >= start_date)
        if end_date:
            conditions.append(CompletedSession.slot_date <= end_date)

        # Add product interest filter
        if interested_in_product is not None:
            conditions.append(
                CompletedSession.interested_in_nutrition_product == interested_in_product
            )

        # Count query for total records (with all filters applied)
        if client_wise:
            count_query = select(
                func.count(func.distinct(CompletedSession.client_id))
            ).select_from(
                CompletedSession
            ).outerjoin(
                Client,
                CompletedSession.client_id == Client.client_id
            ).where(
                and_(*conditions)
            )
        else:
            count_query = select(
                func.count(CompletedSession.id)
            ).select_from(
                CompletedSession
            ).outerjoin(
                Client,
                CompletedSession.client_id == Client.client_id
            ).where(
                and_(*conditions)
            )

        count_result = await db.execute(count_query)
        total_count = count_result.scalar() or 0

        # Calculate pagination
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
        offset = (page - 1) * page_size

        # Main query with JOIN, filters, ordering, and pagination
        if client_wise:
            query = select(
                CompletedSession.client_id,
                Client.name.label('client_name'),
                Client.contact.label('client_contact'),
                func.max(CompletedSession.created_at).label('last_session_at')
            ).select_from(
                CompletedSession
            ).outerjoin(
                Client,
                CompletedSession.client_id == Client.client_id
            ).where(
                and_(*conditions)
            ).group_by(
                CompletedSession.client_id,
                Client.name,
                Client.contact
            ).order_by(
                desc(func.max(CompletedSession.created_at))
            ).offset(
                offset
            ).limit(
                page_size
            )
        else:
            query = select(
                CompletedSession.id,
                CompletedSession.client_id,
                CompletedSession.nutritionist_id,
                CompletedSession.booking_id,
                CompletedSession.schedule_id,
                CompletedSession.meeting_duration,
                CompletedSession.feedback_advice,
                CompletedSession.notes,
                CompletedSession.interested_in_nutrition_product,
                CompletedSession.slot_date,
                CompletedSession.slot_time,
                CompletedSession.created_at,
                Client.name.label('client_name'),
                Client.contact.label('client_contact'),
                ClientDietTemplate.template_id.label('assigned_diet_template_id'),
                ClientDietTemplate.template_name.label('assigned_diet_template_name'),
                NutritionBooking.session_number,
                NutritionEligibility.total_sessions
            ).select_from(
                CompletedSession
            ).outerjoin(
                Client,
                CompletedSession.client_id == Client.client_id
            ).outerjoin(
                ClientDietTemplate,
                CompletedSession.booking_id == ClientDietTemplate.booking_id
            ).outerjoin(
                NutritionBooking,
                CompletedSession.booking_id == NutritionBooking.id
            ).outerjoin(
                NutritionEligibility,
                NutritionBooking.eligibility_id == NutritionEligibility.id
            ).where(
                and_(*conditions)
            ).order_by(
                desc(CompletedSession.created_at)
            ).offset(
                offset
            ).limit(
                page_size
            )

        result = await db.execute(query)
        sessions = result.all()

        # Format response
        completed_sessions = []
        if client_wise:
            for session in sessions:
                completed_sessions.append({
                    "id": None,
                    "client_id": session.client_id,
                    "client_name": session.client_name,
                    "client_contact": session.client_contact,
                    "nutritionist_id": nutritionist_id,
                    "booking_id": None,
                    "schedule_id": None,
                    "meeting_duration": None,
                    "feedback_advice": None,
                    "notes": None,
                    "interested_in_nutrition_product": None,
                    "slot_date": None,
                    "slot_time": None,
                    "created_at": session.last_session_at.isoformat() if session.last_session_at else None,
                    "assigned_diet_template_id": None,
                    "assigned_diet_template_name": None,
                    "plan": None
                })
        else:
            for session in sessions:
                session_num = session.session_number
                total_sess = session.total_sessions
                if session_num is not None and total_sess is not None:
                    plan_display = f"{session_num} / {total_sess}"
                elif total_sess is not None:
                    plan_display = f"- / {total_sess}"
                else:
                    plan_display = "-"

                completed_sessions.append({
                    "id": session.id,
                    "client_id": session.client_id,
                    "client_name": session.client_name,
                    "client_contact": session.client_contact,
                    "nutritionist_id": session.nutritionist_id,
                    "booking_id": session.booking_id,
                    "schedule_id": session.schedule_id,
                    "meeting_duration": session.meeting_duration,
                    "feedback_advice": session.feedback_advice,
                    "notes": session.notes,
                    "interested_in_nutrition_product": session.interested_in_nutrition_product,
                    "slot_date": convert_date_to_irst(session.slot_date),
                    "slot_time": format_time_slot(session.slot_time),
                    "created_at": session.created_at.isoformat() if session.created_at else None,
                    "assigned_diet_template_id": session.assigned_diet_template_id,
                    "assigned_diet_template_name": session.assigned_diet_template_name,
                    "plan": plan_display
                })

        return {
            "success": True,
            "data": {
                "sessions": completed_sessions,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching completed sessions: {str(e)}"
        )


class AssignDietTemplateRequest(BaseModel):
    """Request model for assigning diet template to a completed session"""
    booking_id: int
    diet_template_id: Optional[int] = None  # null to remove template


def get_current_ist_date() -> date:
    """Get current date in IST timezone"""
    utc_now = datetime.utcnow().replace(tzinfo=pytz.UTC)
    ist_now = utc_now.astimezone(IST)
    return ist_now.date()


@router.post("/assign-diet-template")
async def assign_diet_template(
    request_data: AssignDietTemplateRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Assign or remove a diet template for a completed session.
    Can be used to assign template after session completion or remove an existing assignment.

    Each booking_id can have only one template assigned.
    If a template already exists for the booking, it will be updated.
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        booking_query = select(NutritionBooking).where(
            and_(
                NutritionBooking.nutritionist_id == nutritionist_id,
                NutritionBooking.id == request_data.booking_id
            )
        )

        result = await db.execute(booking_query)
        booking = result.scalar_one_or_none()

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Booking not found or you don't have permission to modify this session"
            )

        assigned_template = None

        if request_data.diet_template_id:
            # Verify the template exists and belongs to this nutritionist
            template_query = select(DietTemplate).where(
                and_(
                    DietTemplate.id == request_data.diet_template_id,
                    DietTemplate.nutritionist_id == booking.nutritionist_id
                )
            )
            template_result = await db.execute(template_query)
            template = template_result.scalar_one_or_none()

            if not template:
                raise HTTPException(
                    status_code=404,
                    detail="Diet template not found or doesn't belong to you"
                )

            # First, delete any existing assignments for this booking to ensure uniqueness
            # This handles any edge cases where duplicate records might exist
            delete_existing = delete(ClientDietTemplate).where(
                ClientDietTemplate.booking_id == booking.id
            )
            await db.execute(delete_existing)

            # Create new assignment
            client_diet_template = ClientDietTemplate(
                client_id=booking.client_id,
                nutritionist_id=booking.nutritionist_id,
                template_id=template.id,
                template_name=template.template_name,
                booking_id=booking.id,
                assigned_date=get_current_ist_date()
            )
            db.add(client_diet_template)

            assigned_template = {
                "template_id": template.id,
                "template_name": template.template_name
            }
        else:
            # Remove existing template assignment by deleting the record for this specific booking
            delete_stmt = delete(ClientDietTemplate).where(
                ClientDietTemplate.booking_id == booking.id
            )
            await db.execute(delete_stmt)

        await db.commit()

        return {
            "success": True,
            "message": "Diet template " + ("assigned" if request_data.diet_template_id else "removed") + " successfully",
            "data": {
                "booking_id": booking.id,
                "assigned_template": assigned_template if request_data.diet_template_id else None
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while assigning diet template: {str(e)}"
        )


@router.get("/assign-template-data/{session_id}")
async def get_assign_template_data(
    session_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get data for the assign-template page.

    Returns both the list of diet templates and the current assignment for the session.
    This combines two API calls into one for better performance.

    Optimized with single query for templates using JOIN with nutritionist.
    Total queries: 2 (1 for templates + assignment, 1 for current session assignment)
    """
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        templates_query = select(
            DietTemplate.id,
            DietTemplate.template_name,
            DietTemplate.number_of_days,
            DietTemplate.description,
            DietTemplate.diet_data,
            DietTemplate.session_no,
            DietTemplate.created_at
        ).where(
            DietTemplate.nutritionist_id == nutritionist_id
        ).order_by(
            desc(DietTemplate.created_at)
        )

        templates_result = await db.execute(templates_query)
        templates = templates_result.all()

        # Format templates - this is memory operation, no DB calls
        formatted_templates = []
        for template in templates:
            formatted_templates.append({
                "id": template.id,
                "template_name": template.template_name,
                "number_of_days": template.number_of_days,
                "description": template.description,
                "diet_data": template.diet_data,
                "session_no": template.session_no,
                "created_at": template.created_at.isoformat() if template.created_at else None
            })

        # Get current assignment for the session - separate query with LEFT OUTER JOIN
        assignment_query = select(
            ClientDietTemplate.template_id.label('assigned_template_id')
        ).select_from(
            CompletedSession
        ).outerjoin(
            ClientDietTemplate,
            CompletedSession.booking_id == ClientDietTemplate.booking_id
        ).where(
            CompletedSession.id == session_id
        )

        assignment_result = await db.execute(assignment_query)
        assignment_data = assignment_result.first()

        current_assignment = None
        if assignment_data and assignment_data.assigned_template_id:
            current_assignment = {
                "session_id": session_id,
                "assigned_diet_template_id": assignment_data.assigned_template_id
            }

        return {
            "success": True,
            "data": {
                "templates": formatted_templates,
                "current_assignment": current_assignment
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching template data: {str(e)}"
        )


@router.get("/client/{client_id}")
async def get_completed_sessions_for_client(
    client_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get completed sessions specifically for a client by client_id.
    Matches the client_id with completed_sessions.client_id.
    """
    try:
        await resolve_nutritionist_id(admin, db)

        query = select(
            CompletedSession.id,
            CompletedSession.client_id,
            CompletedSession.nutritionist_id,
            CompletedSession.booking_id,
            CompletedSession.schedule_id,
            CompletedSession.meeting_duration,
            CompletedSession.feedback_advice,
            CompletedSession.notes,
            CompletedSession.interested_in_nutrition_product,
            CompletedSession.slot_date,
            CompletedSession.slot_time,
            CompletedSession.created_at,
            Client.name.label('client_name'),
            ClientDietTemplate.template_id.label('assigned_diet_template_id'),
            ClientDietTemplate.template_name.label('assigned_diet_template_name'),
            Nutritionist.full_name.label('nutritionist_name')
        ).select_from(
            CompletedSession
        ).outerjoin(
            Client,
            CompletedSession.client_id == Client.client_id
        ).outerjoin(
            ClientDietTemplate,
            CompletedSession.booking_id == ClientDietTemplate.booking_id
        ).outerjoin(
            Nutritionist,
            CompletedSession.nutritionist_id == Nutritionist.id
        ).where(
            CompletedSession.client_id == client_id
        ).order_by(
            desc(CompletedSession.created_at)
        )

        result = await db.execute(query)
        sessions = result.all()

        # Format response
        completed_sessions = []
        for session in sessions:
            completed_sessions.append({
                "id": session.id,
                "client_id": session.client_id,
                "client_name": session.client_name,
                "nutritionist_id": session.nutritionist_id,
                "nutritionist_name": session.nutritionist_name,
                "booking_id": session.booking_id,
                "schedule_id": session.schedule_id,
                "meeting_duration": session.meeting_duration,
                "feedback_advice": session.feedback_advice,
                "notes": session.notes,
                "interested_in_nutrition_product": session.interested_in_nutrition_product,
                "slot_date": convert_date_to_irst(session.slot_date),
                "slot_time": format_time_slot(session.slot_time),
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "assigned_diet_template_id": session.assigned_diet_template_id,
                "assigned_diet_template_name": session.assigned_diet_template_name
            })

        return {
            "success": True,
            "data": {
                "sessions": completed_sessions
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching completed sessions for client: {str(e)}"
        )


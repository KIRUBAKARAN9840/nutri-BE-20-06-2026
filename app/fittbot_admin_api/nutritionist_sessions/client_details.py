from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, text, func, cast, Date as SQLDate
from typing import Optional, Dict, List, Any
from datetime import datetime, date, time, timedelta
from pydantic import BaseModel
import json

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.fittbot_models import Client, ActualDiet, ActualWorkout, ClientActual
from app.models.nutrition_models import NutritionConsultationForm, CompletedSession, ClientDietTemplate, Nutritionist, DietTemplate
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie

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

router = APIRouter(prefix="/api/admin/nutritionist_sessions", tags=["NutritionistClientDetails"])


# Pydantic models for pagination response
class PaginatedResponse(BaseModel):
    """Standard paginated response structure"""
    success: bool
    data: Dict[str, Any]


class PaginationMeta(BaseModel):
    """Pagination metadata"""
    total_records: int
    total_pages: int
    current_page: int
    page_size: int
    has_next: bool
    has_previous: bool


@router.get("/client/{client_id}")
async def get_client_details(
    client_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """Get detailed information about a client"""
    try:
        # Fetch client details
        query = select(Client).where(Client.client_id == client_id)
        result = await db.execute(query)
        client = result.scalar_one_or_none()

        if not client:
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )

        # Default fallback values from the clients table
        weight = client.weight
        height = client.height
        bmi = client.bmi

        # Attempt to load weight, height, and bmi from nutrition_consultation_form first
        try:
            form_query = select(NutritionConsultationForm).where(NutritionConsultationForm.client_id == client_id)
            form_result = await db.execute(form_query)
            consultation_form = form_result.scalar_one_or_none()

            if consultation_form and consultation_form.anthropometric_table:
                table_data = consultation_form.anthropometric_table
                if isinstance(table_data, str):
                    try:
                        table_data = json.loads(table_data)
                    except Exception:
                        table_data = {}

                if isinstance(table_data, dict):
                    # Helper function to extract and clean "current" values cleanly
                    def get_current_val(val_dict, key):
                        if not val_dict or key not in val_dict:
                            return None
                        val_group = val_dict[key]
                        if isinstance(val_group, dict):
                            val = val_group.get("current")
                            if val is not None:
                                val_str = str(val).strip()
                                if val_str:
                                    try:
                                        return float(val_str)
                                    except ValueError:
                                        return val_str
                        return None

                    form_weight = get_current_val(table_data, "weight")
                    form_height = get_current_val(table_data, "height")
                    form_bmi = get_current_val(table_data, "bmi")

                    if form_weight is not None:
                        weight = form_weight
                    if form_height is not None:
                        height = form_height
                    if form_bmi is not None:
                        bmi = form_bmi
        except Exception as form_err:
            # Fall back silently to default clients table values if query fails
            pass

        # Fetch completed sessions history (Consultation History)
        history_query = select(
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
            ClientDietTemplate.assigned_date.label('assigned_date'),
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

        history_result = await db.execute(history_query)
        sessions_rows = history_result.all()

        completed_sessions = []
        for session in sessions_rows:
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
                "assigned_diet_template_name": session.assigned_diet_template_name,
                "assigned_date": convert_date_to_irst(session.assigned_date)
            })

        return {
            "success": True,
            "data": {
                "client_id": client.client_id,
                "name": client.name,
                "email": client.email,
                "contact": client.contact,
                "profile": client.profile,
                "location": client.location,
                "age": client.age,
                "gender": client.gender,
                "height": height,
                "weight": weight,
                "bmi": bmi,
                "goals": client.goals,
                "lifestyle": client.lifestyle,
                "medical_issues": client.medical_issues,
                "joined_date": client.joined_date.isoformat() if client.joined_date else None,
                "dob": client.dob.isoformat() if client.dob else None,
                "status": client.status,
                "consultation_history": completed_sessions
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching client details: {str(e)}"
        )


def _parse_diet_data(diet_data: Any) -> List[Dict]:
    """
    Pure function to parse diet_data JSON safely.
    No I/O operations, just data transformation.
    """
    if isinstance(diet_data, str):
        try:
            return json.loads(diet_data)
        except json.JSONDecodeError:
            return []
    elif isinstance(diet_data, list):
        return diet_data
    return []


def _parse_workout_data(workout_details: Any) -> List[Dict]:
    """
    Pure function to parse workout_details JSON safely.
    No I/O operations, just data transformation.
    """
    if isinstance(workout_details, str):
        try:
            return json.loads(workout_details)
        except json.JSONDecodeError:
            return []
    elif isinstance(workout_details, list):
        return workout_details
    return []


@router.get("/client/{client_id}/food-logs")
async def get_client_food_logs(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    start_date: Optional[date] = Query(None, description="Start date filter (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date filter (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get paginated food logs for a client.

    Fully async with optimized queries:
    - Single count query for total records
    - Single data query with OFFSET/LIMIT for pagination
    - No loops with database calls
    - JSON parsing done in-memory (pure functions)
    """
    try:
        # Build base conditions for filtering
        conditions = [ActualDiet.client_id == client_id]

        if start_date:
            conditions.append(ActualDiet.date >= start_date)
        if end_date:
            conditions.append(ActualDiet.date <= end_date)

        # Query 1: Get total count (async, single query)
        count_query = select(func.count(ActualDiet.record_id)).where(and_(*conditions))
        count_result = await db.execute(count_query)
        total_records = count_result.scalar() or 0

        # Early return if no records
        if total_records == 0:
            return {
                "success": True,
                "data": {
                    "food_logs": [],
                    "pagination": {
                        "total_records": 0,
                        "total_pages": 0,
                        "current_page": page,
                        "page_size": page_size,
                        "has_next": False,
                        "has_previous": False
                    }
                }
            }

        # Calculate pagination values (pure calculation, no I/O)
        total_pages = (total_records + page_size - 1) // page_size
        offset = (page - 1) * page_size

        # Validate page number
        if page > total_pages:
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} exceeds total pages ({total_pages})"
            )

        # Query 2: Get paginated data (async, single query with OFFSET/LIMIT)
        data_query = select(
            ActualDiet.record_id,
            ActualDiet.client_id,
            ActualDiet.date,
            ActualDiet.diet_data
        ).where(
            and_(*conditions)
        ).order_by(
            ActualDiet.date.desc()
        ).offset(offset).limit(page_size)

        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Parse JSON data in-memory (pure functions, no DB calls)
        food_logs = [
            {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "date": row.date.isoformat() if row.date else None,
                "diet_data": _parse_diet_data(row.diet_data)
            }
            for row in rows
        ]

        return {
            "success": True,
            "data": {
                "food_logs": food_logs,
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "page_size": page_size,
                    "has_next": page < total_pages,
                    "has_previous": page > 1
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching food logs: {str(e)}"
        )


@router.get("/client/{client_id}/workout-logs")
async def get_client_workout_logs(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    start_date: Optional[date] = Query(None, description="Start date filter (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date filter (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get paginated workout logs for a client.

    Fully async with optimized queries:
    - Single count query for total records
    - Single data query with OFFSET/LIMIT for pagination
    - No loops with database calls
    - JSON parsing done in-memory (pure functions)
    """
    try:
        # Build base conditions for filtering
        conditions = [ActualWorkout.client_id == client_id]

        if start_date:
            conditions.append(ActualWorkout.date >= start_date)
        if end_date:
            conditions.append(ActualWorkout.date <= end_date)

        # Query 1: Get total count (async, single query)
        count_query = select(func.count(ActualWorkout.record_id)).where(and_(*conditions))
        count_result = await db.execute(count_query)
        total_records = count_result.scalar() or 0

        # Early return if no records
        if total_records == 0:
            return {
                "success": True,
                "data": {
                    "workout_logs": [],
                    "pagination": {
                        "total_records": 0,
                        "total_pages": 0,
                        "current_page": page,
                        "page_size": page_size,
                        "has_next": False,
                        "has_previous": False
                    }
                }
            }

        # Calculate pagination values (pure calculation, no I/O)
        total_pages = (total_records + page_size - 1) // page_size
        offset = (page - 1) * page_size

        # Validate page number
        if page > total_pages:
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} exceeds total pages ({total_pages})"
            )

        # Query 2: Get paginated data (async, single query with OFFSET/LIMIT)
        data_query = select(
            ActualWorkout.record_id,
            ActualWorkout.client_id,
            ActualWorkout.date,
            ActualWorkout.workout_details
        ).where(
            and_(*conditions)
        ).order_by(
            ActualWorkout.date.desc()
        ).offset(offset).limit(page_size)

        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Parse JSON data in-memory (pure functions, no DB calls)
        workout_logs = [
            {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "date": row.date.isoformat() if row.date else None,
                "workout_details": _parse_workout_data(row.workout_details)
            }
            for row in rows
        ]

        return {
            "success": True,
            "data": {
                "workout_logs": workout_logs,
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "page_size": page_size,
                    "has_next": page < total_pages,
                    "has_previous": page > 1
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching workout logs: {str(e)}"
        )


@router.get("/client/{client_id}/water-logs")
async def get_client_water_logs(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Number of records per page"),
    start_date: Optional[date] = Query(None, description="Start date filter (ISO format YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="End date filter (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get paginated water intake logs for a client.

    Fully async with optimized queries:
    - Single count query for total records
    - Single data query with OFFSET/LIMIT for pagination
    - No loops with database calls
    """
    try:
        # Build base conditions for filtering
        conditions = [
            ClientActual.client_id == client_id,
            ClientActual.water_intake.isnot(None),
            ClientActual.water_intake > 0
        ]

        if start_date:
            conditions.append(ClientActual.date >= start_date)
        if end_date:
            conditions.append(ClientActual.date <= end_date)

        # Query 1: Get total count (async, single query)
        count_query = select(func.count(ClientActual.record_id)).where(and_(*conditions))
        count_result = await db.execute(count_query)
        total_records = count_result.scalar() or 0

        # Early return if no records
        if total_records == 0:
            return {
                "success": True,
                "data": {
                    "water_logs": [],
                    "pagination": {
                        "total_records": 0,
                        "total_pages": 0,
                        "current_page": page,
                        "page_size": page_size,
                        "has_next": False,
                        "has_previous": False
                    }
                }
            }

        # Calculate pagination values (pure calculation, no I/O)
        total_pages = (total_records + page_size - 1) // page_size
        offset = (page - 1) * page_size

        # Validate page number
        if page > total_pages:
            raise HTTPException(
                status_code=404,
                detail=f"Page {page} exceeds total pages ({total_pages})"
            )

        # Query 2: Get paginated data (async, single query with OFFSET/LIMIT)
        data_query = select(
            ClientActual.record_id,
            ClientActual.client_id,
            ClientActual.date,
            ClientActual.water_intake
        ).where(
            and_(*conditions)
        ).order_by(
            ClientActual.date.desc()
        ).offset(offset).limit(page_size)

        data_result = await db.execute(data_query)
        rows = data_result.all()

        # Format response (pure transformation, no DB calls)
        water_logs = [
            {
                "record_id": row.record_id,
                "client_id": row.client_id,
                "date": row.date.isoformat() if row.date else None,
                "water_intake": row.water_intake
            }
            for row in rows
        ]

        return {
            "success": True,
            "data": {
                "water_logs": water_logs,
                "pagination": {
                    "total_records": total_records,
                    "total_pages": total_pages,
                    "current_page": page,
                    "page_size": page_size,
                    "has_next": page < total_pages,
                    "has_previous": page > 1
                }
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching water logs: {str(e)}"
        )


@router.get("/client/{client_id}/template-view/{template_id}")
async def get_client_template_view_details(
    client_id: int,
    template_id: int,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Get client name and template details combined for the template day-wise view page
    """
    try:
        # Fetch client details to get client name
        client_query = select(Client).where(Client.client_id == client_id)
        client_result = await db.execute(client_query)
        client = client_result.scalar_one_or_none()

        if not client:
            raise HTTPException(
                status_code=404,
                detail="Client not found"
            )

        # Fetch the diet template details
        template_query = select(DietTemplate).where(DietTemplate.id == template_id)
        template_result = await db.execute(template_query)
        template = template_result.scalar_one_or_none()

        if not template:
            raise HTTPException(
                status_code=404,
                detail="Diet template not found"
            )

        return {
            "success": True,
            "data": {
                "client_name": client.name,
                "template": {
                    "id": template.id,
                    "template_name": template.template_name,
                    "number_of_days": template.number_of_days,
                    "diet_data": template.diet_data,
                    "description": template.description,
                    "session_no": template.session_no,
                    "created_at": template.created_at.isoformat() if template.created_at else None,
                    "updated_at": template.updated_at.isoformat() if template.updated_at else None
                }
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching template view details: {str(e)}"
        )

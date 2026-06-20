from openpyxl.cell.cell import ERROR_CODES
from openai import pagination
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, cast, Date as SQLDate, update
from typing import Optional, Dict, List
from datetime import datetime, time, date, timedelta
from pydantic import BaseModel
import pytz
import httpx
import os
import base64

from app.models.async_database import get_async_db
from app.models.adminmodels import Admins
from app.models.nutrition_models import Nutritionist, NutritionBooking, NutritionEligibility, CompletedSession, DietTemplate, ClientDietTemplate
from app.models.fittbot_models import Client
from app.fittbot_admin_api.auth.authentication import get_current_admin_from_cookie
from app.fittbot_admin_api.nutritionist_sessions._helpers import resolve_nutritionist_id, resolve_nutritionist

router = APIRouter(prefix="/api/admin/nutritionist_sessions", tags=["NutritionistSessions"])

# IST Timezone
IST = pytz.timezone("Asia/Kolkata")


# Pure functions (synchronous, no I/O) - kept outside for performance
def format_time_slot(t: time) -> str:
    """Convert time to HH:MM AM/PM format - pure function, no async needed"""
    if not t:
        return ""
    return t.strftime("%I:%M %p")


def map_booking_status(status: str) -> str:
    """Map database status to frontend display status - pure function"""
    status_map = {
        "pending": "Pending",
        "booked": "Booked",
        "attended": "Completed",
        "rescheduled": "Rescheduled",
        "cancelled": "Cancelled",
        "no_show": "No Show"
    }
    return status_map.get(status, status.title())


def convert_date_to_irst(date_value: date) -> str:
    """
    Convert date object to IST date string (YYYY-MM-DD format).
    Since the database stores dates correctly in IST, we just format them properly.
    """
    if date_value is None:
        return None
    # If it's already a date object, format it directly
    # The database stores dates in IST, so we just return the ISO format
    return date_value.isoformat()


def get_current_ist_date() -> date:
    """Get current date in IST timezone"""
    utc_now = datetime.utcnow().replace(tzinfo=pytz.UTC)
    ist_now = utc_now.astimezone(IST)
    return ist_now.date()


@router.get("/calendar/counts")
async def get_calendar_counts(
    start_date: date = Query(..., description="Start date (ISO format YYYY-MM-DD)"),
    end_date: date = Query(..., description="End date (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):

    try:
        nutritionist = await resolve_nutritionist(admin, db)
        if not nutritionist:
            return {
                "success": True,
                "data": {
                    "date_counts": {},
                    "date_rescheduled": {},
                    "total_sessions": 0,
                    "nutritionist": None
                }
            }

        nutritionist_info = {
            "id": nutritionist.id,
            "name": nutritionist.full_name,
            "contact": nutritionist.contact,
        }

        nutritionist_id = nutritionist.id

        # Now get bookings for this nutritionist in the date range
        query = select(
            NutritionBooking.booking_date,
            NutritionBooking.rescheduled_at
        ).where(
            and_(
                NutritionBooking.nutritionist_id == nutritionist_id,
                # Get bookings that either:
                # 1. Have original booking_date in range, OR
                # 2. Have rescheduled_at date in range
                or_(
                    and_(
                        NutritionBooking.booking_date >= start_date,
                        NutritionBooking.booking_date <= end_date
                    ),
                    and_(
                        NutritionBooking.rescheduled_at.isnot(None),
                        cast(NutritionBooking.rescheduled_at, SQLDate) >= start_date,
                        cast(NutritionBooking.rescheduled_at, SQLDate) <= end_date
                    )
                )
            )
        )

        result = await db.execute(query)
        rows = result.all()

        # Format response
        date_counts = {}
        date_rescheduled = {}  # Track dates that have rescheduled sessions

        for row in rows:
            if row.booking_date is None:
                continue

            # Determine which date to count:
            # - If rescheduled_at exists, count on the rescheduled date
            # - Otherwise, count on the original booking_date
            if row.rescheduled_at:
                # Use the rescheduled date
                effective_date = row.rescheduled_at.date()
                is_rescheduled = True
            else:
                # Use the original booking date
                effective_date = row.booking_date
                is_rescheduled = False

            # Only count if within the requested range (double-check)
            if start_date <= effective_date <= end_date:
                date_str = convert_date_to_irst(effective_date)

                # Initialize or add to count for this date
                if date_str not in date_counts:
                    date_counts[date_str] = 0
                date_counts[date_str] += 1

                # Track if this session is rescheduled
                if is_rescheduled:
                    date_rescheduled[date_str] = True

        return {
            "success": True,
            "data": {
                "date_counts": date_counts,
                "date_rescheduled": date_rescheduled,
                "total_sessions": sum(date_counts.values()),
                "nutritionist": nutritionist_info
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching calendar counts: {str(e)}"
        )


@router.get("/sessions/by-date")
async def get_sessions_by_date(
    target_date: date = Query(..., description="Target date to fetch sessions for (ISO format YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):

    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        query = select(
            NutritionBooking.id,
            NutritionBooking.client_id,
            NutritionBooking.booking_date,
            NutritionBooking.start_time,
            NutritionBooking.end_time,
            NutritionBooking.meeting_link,
            NutritionBooking.status,
            NutritionBooking.reschedule_reason,
            NutritionBooking.rescheduled_at,
            NutritionBooking.reschedule_requested_by,
            NutritionBooking.session_number,
            NutritionEligibility.total_sessions,
            Client.name.label('client_name')
        ).select_from(
            NutritionBooking
        ).outerjoin(
            Client,
            NutritionBooking.client_id == Client.client_id
        ).outerjoin(
            NutritionEligibility,
            NutritionBooking.eligibility_id == NutritionEligibility.id
        ).where(
            and_(
                NutritionBooking.nutritionist_id == nutritionist_id,
                or_(
                    NutritionBooking.booking_date == target_date,
                    cast(NutritionBooking.rescheduled_at, SQLDate) == target_date
                )
            )
        ).order_by(NutritionBooking.start_time.asc())

        # Execute single query
        result = await db.execute(query)
        bookings = result.all()

        # Format response - pure Python, no database calls
        # For rescheduled sessions, show the rescheduled time; otherwise show original time
        sessions = []
        for booking in bookings:
            # Determine if this session is rescheduled
            is_rescheduled = booking.rescheduled_at is not None

            # Use rescheduled time if available, otherwise use original booking time
            if is_rescheduled:
                # Extract date and time from rescheduled_at
                resched_date = booking.rescheduled_at.date()
                resched_time = booking.rescheduled_at.time()

                # Check if this rescheduled date matches target_date
                # (rescheduled sessions should only appear on their new date)
                if resched_date != target_date:
                    continue

                # Calculate slot from rescheduled_at (assuming 30 min sessions)
                from datetime import timedelta as td
                resched_end_dt = booking.rescheduled_at + td(minutes=30)
                resched_end_time = resched_end_dt.time()
                slot = f"{format_time_slot(resched_time)} - {format_time_slot(resched_end_time)}"
            else:
                # Original booking time
                slot = f"{format_time_slot(booking.start_time)} - {format_time_slot(booking.end_time)}"

            # Map status using booking status only (attended = Completed)
            display_status = map_booking_status(booking.status)

            # Build plan display: "session_number / total_sessions"
            session_num = booking.session_number
            total_sess = booking.total_sessions
            if session_num is not None and total_sess is not None:
                plan_display = f"{session_num} / {total_sess}"
            elif total_sess is not None:
                plan_display = f"- / {total_sess}"
            else:
                plan_display = "-"

            sessions.append({
                "id": booking.id,
                "slot": slot,
                "client_id": booking.client_id,
                "client_name": booking.client_name,
                "meeting_link": booking.meeting_link,
                "status": display_status,
                "notes": None,
                "reschedule_reason": booking.reschedule_reason,
                "rescheduled_at": booking.rescheduled_at.isoformat() if booking.rescheduled_at else None,
                "reschedule_requested_by": booking.reschedule_requested_by,
                # Add original booking info for rescheduled sessions
                "original_slot": f"{format_time_slot(booking.start_time)} - {format_time_slot(booking.end_time)}" if is_rescheduled else None,
                "original_date": convert_date_to_irst(booking.booking_date) if is_rescheduled else None,
                "plan": plan_display
            })

        return {
            "success": True,
            "data": {
                "sessions": sessions,
                "date": convert_date_to_irst(target_date),
                "count": len(sessions)
            }
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while fetching sessions: {str(e)}"
        )


# Pydantic models for request/response
class RescheduleRequest(BaseModel):
    """Request model for rescheduling a session"""
    booking_id: int
    new_date: str  # Format: YYYY-MM-DD (IST)
    new_time: str  # Format: HH:MM (24-hour format)
    reason: str


@router.post("/reschedule")
async def reschedule_session(
    request_data: RescheduleRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    try:
        nutritionist_id = await resolve_nutritionist_id(admin, db)

        query = select(NutritionBooking).where(
            and_(
                NutritionBooking.nutritionist_id == nutritionist_id,
                NutritionBooking.id == request_data.booking_id
            )
        )

        result = await db.execute(query)
        booking = result.scalar_one_or_none()

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Booking not found or you don't have permission to reschedule this session."
            )

        # Parse new rescheduled date and time from request
        # This will be stored in rescheduled_at column (new scheduled time, not timestamp)
        try:
            new_date = datetime.strptime(request_data.new_date, "%Y-%m-%d").date()
            new_hour, new_minute = map(int, request_data.new_time.split(":"))

            # Create the new rescheduled datetime (naive, in IST)
            # This is the NEW scheduled time, not the timestamp of when reschedule happened
            from datetime import time as Time, timedelta as td
            new_start_time = Time(hour=new_hour, minute=new_minute)
            rescheduled_datetime = datetime.combine(new_date, new_start_time)

            # Calculate end time for reference in response (30 min session)
            new_end_time_dt = rescheduled_datetime + td(minutes=30)
            new_end_time = new_end_time_dt.time()

        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date or time format. Expected YYYY-MM-DD and HH:MM. Error: {str(e)}"
            )

        # Store original booking time for response
        original_date = booking.booking_date
        original_start_time = booking.start_time
        original_end_time = booking.end_time

        # Update the booking - DO NOT modify booking_date/start_time/end_time
        # These must remain unchanged to preserve the original booking time
        booking.status = "booked"  # Set to booked since we're creating a new meeting
        booking.reschedule_reason = request_data.reason
        booking.rescheduled_at = rescheduled_datetime  # NEW: stores new scheduled datetime
        booking.reschedule_requested_by = "nutritionist"

        # Generate new Zoom meeting link for the rescheduled time
        new_meeting_link = None
        try:
            # Format start time for Zoom API (YYYY-MM-DDTHH:MM:SS)
            start_datetime_str = f"{new_date.isoformat()}T{new_start_time.strftime('%H:%M:%S')}"

            # Get Zoom access token
            access_token = await get_zoom_access_token()

            # Create Zoom meeting with rescheduled time
            zoom_meeting = await create_zoom_meeting(
                access_token=access_token,
                topic=f"Nutrition Consultation - Booking #{booking.id} (Rescheduled)",
                start_time=start_datetime_str,
                duration=30
            )

            # Extract and store the new meeting link
            new_meeting_link = zoom_meeting.get("join_url")
            if new_meeting_link:
                booking.meeting_link = new_meeting_link
        except Exception as e:
            # Continue with reschedule even if Zoom meeting creation fails
            booking.meeting_link = None

        await db.commit()
        await db.refresh(booking)

        return {
            "success": True,
            "message": "Session rescheduled successfully" + (" with new meeting link" if new_meeting_link else ""),
            "data": {
                "booking_id": booking.id,
                # Original booking time (unchanged)
                "original_date": convert_date_to_irst(original_date),
                "original_time": f"{format_time_slot(original_start_time)} - {format_time_slot(original_end_time)}",
                # New rescheduled time
                "new_date": convert_date_to_irst(new_date),
                "new_time": f"{format_time_slot(new_start_time)} - {format_time_slot(new_end_time)}",
                "status": map_booking_status(booking.status),
                "rescheduled_at": booking.rescheduled_at.isoformat() if booking.rescheduled_at else None,
                "reason": booking.reschedule_reason,
                "meeting_link": booking.meeting_link
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while rescheduling the session: {str(e)}"
        )


# Zoom Meeting Configuration - Read from environment variables
from dotenv import load_dotenv
load_dotenv()

ZOOM_ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_USER_EMAIL = os.getenv("ZOOM_USER_EMAIL")


class GenerateMeetingLinkRequest(BaseModel):
    """Request model for generating meeting link"""
    booking_id: int


async def get_zoom_access_token() -> str:
    """
    Get Zoom OAuth access token using Server-to-Server OAuth.
    Returns access token valid for 1 hour.
    """
    if not ZOOM_CLIENT_ID or not ZOOM_CLIENT_SECRET or not ZOOM_ACCOUNT_ID:
        raise HTTPException(
            status_code=500,
            detail="Zoom API credentials (ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_ACCOUNT_ID) are missing or not loaded."
        )

    zoom_token_url = "https://zoom.us/oauth/token"

    # Encode Client ID and Secret to Base64 for Basic Auth
    credentials = f"{ZOOM_CLIENT_ID}:{ZOOM_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}"
    }

    data = {
        "grant_type": "account_credentials",
        "account_id": ZOOM_ACCOUNT_ID
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            zoom_token_url,
            data=data,
            headers=headers,
            timeout=30.0
        )

        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise HTTPException(
                status_code=500,
                detail="Zoom API response missing access_token"
            )

        return access_token


async def create_zoom_meeting(
    access_token: str,
    topic: str,
    start_time: str,
    duration: int = 30
) -> Dict:
    """
    Create a Zoom meeting using the access token.

    Args:
        access_token: Zoom OAuth access token
        topic: Meeting topic
        start_time: Meeting start time in ISO 8601 format (e.g., "2024-01-15T14:30:00")
        duration: Meeting duration in minutes

    Returns:
        Dictionary containing meeting details including join URL
    """
    zoom_api_url = f"https://api.zoom.us/v2/users/{ZOOM_USER_EMAIL}/meetings"

    meeting_data = {
        "topic": topic,
        "type": 2,  # Scheduled meeting
        "start_time": start_time,
        "duration": duration,
        "timezone": "Asia/Kolkata",
        "settings": {
            "host_video": True,
            "participant_video": True,
            "join_before_host": False,
            "mute_upon_entry": False,
            "watermark": False,
            "use_pmi": False,
            "approval_type": 2,  # No approval required
            "audio": "both",
            "auto_recording": "none",
            "waiting_room": True,
            "meeting_authentication": False
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            zoom_api_url,
            json=meeting_data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
        )
        response.raise_for_status()
        return response.json()


@router.post("/generate-meeting-link")
async def generate_meeting_link(
    request_data: GenerateMeetingLinkRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Generate a Zoom meeting link for a nutrition booking.

    Creates a Zoom meeting using Server-to-Server OAuth and stores
    the meeting link in the nutrition_bookings table.
    """
    try:
        # Fetch the booking details
        booking_query = select(NutritionBooking).where(
            NutritionBooking.id == request_data.booking_id
        )
        booking_result = await db.execute(booking_query)
        booking = booking_result.scalar_one_or_none()

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Booking not found"
            )

        # Check if meeting link already exists
        if booking.meeting_link:
            return {
                "success": True,
                "message": "Meeting link already exists",
                "data": {
                    "meeting_link": booking.meeting_link,
                    "booking_id": booking.id
                }
            }

        # Calculate meeting duration (difference between start and end time)
        duration_minutes = 30  # Default duration
        if booking.start_time and booking.end_time:
            start_datetime = datetime.combine(booking.booking_date, booking.start_time)
            end_datetime = datetime.combine(booking.booking_date, booking.end_time)
            duration_minutes = int((end_datetime - start_datetime).total_seconds() / 60)

        # Format start time for Zoom API (YYYY-MM-DDTHH:MM:SS)
        start_datetime_str = f"{booking.booking_date.isoformat()}T{booking.start_time.strftime('%H:%M:%S')}"

        # Get Zoom access token
        access_token = await get_zoom_access_token()

        # Create Zoom meeting
        zoom_meeting = await create_zoom_meeting(
            access_token=access_token,
            topic=f"Nutrition Consultation - Booking #{booking.id}",
            start_time=start_datetime_str,
            duration=duration_minutes
        )

        # Extract meeting link
        meeting_link = zoom_meeting.get("join_url")

        if not meeting_link:
            raise HTTPException(
                status_code=500,
                detail="Failed to get meeting link from Zoom"
            )

        # Update booking with meeting link
        booking.meeting_link = meeting_link
        booking.status = "booked"
        await db.commit()
        await db.refresh(booking)

        return {
            "success": True,
            "message": "Meeting link generated successfully",
            "data": {
                "booking_id": booking.id,
                "meeting_link": meeting_link,
                "meeting_id": zoom_meeting.get("id"),
                "start_time": zoom_meeting.get("start_time"),
                "duration": zoom_meeting.get("duration")
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while generating the meeting link: {str(e)}"
        )


# Pydantic models for Complete Session request
class CompleteSessionRequest(BaseModel):
    """Request model for completing a nutrition session"""
    booking_id: int
    meeting_duration: int  # Duration in minutes
    feedback_advice: str
    interested_in_nutrition_product: bool
    diet_template_id: Optional[int] = None  # Optional: Assign diet template to client
    notes: Optional[str] = None  # Optional notes


class AssignDietTemplateRequest(BaseModel):
    """Request model for assigning diet template to a completed session"""
    booking_id: int
    diet_template_id: Optional[int] = None  # null to remove template


@router.post("/complete-session")
async def complete_session(
    request_data: CompleteSessionRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: Admins = Depends(get_current_admin_from_cookie)
):
    """
    Complete a nutrition consultation session and save session details.

    Validates the booking, extracts session information, and creates a record
    in the completed_sessions table with meeting duration, feedback, and product interest.
    Also updates the original booking status to 'attended'.
    """
    try:
        # Validate meeting duration (must be between 1 and 300 minutes)
        if not 1 <= request_data.meeting_duration <= 300:
            raise HTTPException(
                status_code=400,
                detail="Meeting duration must be between 1 and 300 minutes"
            )

        # Validate feedback_advice is not empty
        if not request_data.feedback_advice or not request_data.feedback_advice.strip():
            raise HTTPException(
                status_code=400,
                detail="Feedback/advice cannot be empty"
            )

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
                detail="Booking not found or you don't have permission to complete this session"
            )

        # Determine the actual slot date and time (use rescheduled time if available)
        if booking.rescheduled_at:
            slot_date = booking.rescheduled_at.date()
            slot_time = booking.rescheduled_at.time()
        else:
            slot_date = booking.booking_date
            slot_time = booking.start_time

        # Create completed session record
        completed_session = CompletedSession(
            client_id=booking.client_id,
            nutritionist_id=booking.nutritionist_id,
            booking_id=booking.id,  # Store the booking_id for direct reference
            schedule_id=booking.schedule_id,  # Store the schedule_id from booking
            meeting_duration=request_data.meeting_duration,
            feedback_advice=request_data.feedback_advice.strip(),
            interested_in_nutrition_product=request_data.interested_in_nutrition_product,
            notes=request_data.notes.strip() if request_data.notes else None,
            slot_date=slot_date,
            slot_time=slot_time
        )

        db.add(completed_session)

        # Handle diet template assignment if provided
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

            if template:
                # Create new client diet template assignment
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

        # Update the booking status to 'attended'
        booking.status = "attended"

        await db.commit()
        await db.refresh(completed_session)

        response_data = {
            "completed_session_id": completed_session.id,
            "booking_id": booking.id,
            "client_id": booking.client_id,
            "nutritionist_id": booking.nutritionist_id,
            "schedule_id": booking.schedule_id,
            "meeting_duration": request_data.meeting_duration,
            "feedback_advice": request_data.feedback_advice,
            "interested_in_nutrition_product": request_data.interested_in_nutrition_product,
            "notes": completed_session.notes,
            "slot_date": convert_date_to_irst(slot_date),
            "slot_time": format_time_slot(slot_time),
            "created_at": completed_session.created_at.isoformat() if completed_session.created_at else None
        }

        # Add assigned template info if any
        if assigned_template:
            response_data["assigned_diet_template"] = assigned_template

        return {
            "success": True,
            "message": "Session completed successfully" + (" with diet template assignment" if assigned_template else ""),
            "data": response_data
        }

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while completing the session: {str(e)}"
        )
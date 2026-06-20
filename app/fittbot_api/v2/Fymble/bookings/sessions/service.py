"""Business logic for Session upcoming bookings.

Groups booked session days by purchase, enriches with gym info,
and returns a structured response.
"""

from datetime import date
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from ..shared.gym_info_repository import GymInfoRepository
from ..shared.schemas import GymAddress
from .repository import SessionBookingRepository
from .schemas import (
    SessionBookingDayItem,
    SessionPurchaseGroup,
    SessionUpcomingResponse,
)


class SessionBookingService:
    """List upcoming session bookings for a client."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SessionBookingRepository(db)
        self.gym_info = GymInfoRepository(db)

    async def get_upcoming(self, client_id: int) -> SessionUpcomingResponse:
        today = date.today()

        bookings = await self.repo.get_upcoming_bookings(client_id, today)
        if not bookings:
            return SessionUpcomingResponse(data=[])

        # Batch-fetch session metadata, gym info, and trainer names (avoids N+1)
        session_ids = list({b.SessionBookingDay.session_id for b in bookings})
        gym_ids = {b.SessionBookingDay.gym_id for b in bookings if b.SessionBookingDay.gym_id}
        trainer_ids = list({b.SessionBookingDay.trainer_id for b in bookings if b.SessionBookingDay.trainer_id})

        sessions_map = await self.repo.get_sessions_map(session_ids)
        gyms_map = await self.gym_info.get_bulk_gym_info(gym_ids)
        trainers_map = await self.repo.get_trainers_map(trainer_ids)

        # Group by purchase_id
        purchases_map: Dict[int, Dict[str, Any]] = {}

        for row in bookings:
            booking = row.SessionBookingDay
            purchase = row.SessionPurchase
            pid = booking.purchase_id

            if pid not in purchases_map:
                session_meta = sessions_map.get(booking.session_id)
                session_name = self.repo.resolve_session_name(session_meta)

                gym = gyms_map.get(booking.gym_id, {}) if booking.gym_id else {}

                purchases_map[pid] = {
                    "purchase_id": pid,
                    "session_id": booking.session_id,
                    "session_name": session_name,
                    "trainer_id": booking.trainer_id,
                    "trainer_name": trainers_map.get(booking.trainer_id) if booking.trainer_id else None,
                    "gym_id": booking.gym_id,
                    "gym_name": gym.get("name", "Unknown Gym"),
                    "address": GymAddress(**gym["address"]) if gym.get("address") else None,
                    "latitude": gym.get("latitude"),
                    "longitude": gym.get("longitude"),
                    "owner_mobile": gym.get("owner_mobile"),
                    "purchased_at": purchase.created_at.isoformat() if purchase.created_at else None,
                    "sessions": [],
                }

            purchases_map[pid]["sessions"].append(
                SessionBookingDayItem(
                    booking_id=booking.id,
                    date=booking.booking_date.isoformat(),
                    start_time=self.repo.format_time(booking.start_time),
                    end_time=self.repo.format_time(booking.end_time),
                    status=booking.status,
                    checkin_token=booking.checkin_token,
                )
            )

        groups = [
            SessionPurchaseGroup(**data) for data in purchases_map.values()
        ]
        return SessionUpcomingResponse(data=groups)

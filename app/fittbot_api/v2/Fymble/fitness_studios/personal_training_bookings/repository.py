"""Database queries specific to Personal Training Bookings (checkout preview).

Fetches gym details, trainer profile, schedule times, PT session settings,
and reward-related data. Reuses PTRepository for offer eligibility.
"""

from datetime import time as dt_time
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.pricing import get_markup_multiplier
from app.models.fittbot_models import (
    Gym,
    RewardProgramOptIn,
    SessionSchedule,
    SessionSetting,
    TrainerProfile,
)
from app.models.fittbot_models.gym import GymLocation
from app.models.fittbot_models.referral import ReferralFittbotCash

from ..personal_training.repository import PERSONAL_TRAINING_SESSION_ID
from ..shared.utils import to_12hr


class PTBookingsRepository:
    """Personal training bookings data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_gym_details(self, gym_id: int) -> Dict:
        """Fetch gym name, address, and coordinates."""
        result = await self.db.execute(
            select(Gym).where(Gym.gym_id == gym_id)
        )
        gym = result.scalars().first()
        if not gym:
            return {"gym_name": None, "address": None, "latitude": None, "longitude": None}

        loc_result = await self.db.execute(
            select(GymLocation).where(GymLocation.gym_id == gym_id)
        )
        loc = loc_result.scalars().first()

        return {
            "gym_name": gym.name,
            "address": {
                "door_no": gym.door_no,
                "building": gym.building,
                "street": gym.street,
                "area": gym.area,
                "city": gym.city,
                "state": gym.state,
                "pincode": gym.pincode,
            },
            "latitude": float(loc.latitude) if loc and loc.latitude else None,
            "longitude": float(loc.longitude) if loc and loc.longitude else None,
        }

    async def get_trainer_profile(self, gym_id: int, trainer_id: int) -> Optional[Dict]:
        """Fetch trainer name, profile_image, experience."""
        result = await self.db.execute(
            select(TrainerProfile).where(
                TrainerProfile.gym_id == gym_id,
                TrainerProfile.trainer_id == trainer_id,
            )
        )
        tp = result.scalars().first()
        if not tp:
            return None
        return {
            "trainer_id": tp.trainer_id,
            "name": tp.full_name,
            "profile_image": tp.profile_image,
            "experience": tp.experience,
        }

    async def get_schedule_times(self, schedule_id: int) -> Optional[Dict]:
        """Fetch start_time and end_time for a schedule."""
        result = await self.db.execute(
            select(
                SessionSchedule.start_time,
                SessionSchedule.end_time,
            ).where(SessionSchedule.id == schedule_id)
        )
        row = result.first()
        if not row:
            return None

        start = row.start_time
        end = row.end_time

        return {
            "start_time": to_12hr(
                start.strftime("%H:%M") if isinstance(start, dt_time) else str(start)[:5]
            ),
            "end_time": to_12hr(
                end.strftime("%H:%M") if isinstance(end, dt_time) else str(end)[:5]
            ),
        }

    async def get_pt_setting(
        self, gym_id: int, trainer_id: int,
    ) -> Optional[int]:
        """Fetch actual price for a specific gym+trainer PT setting.

        Falls back to gym-level setting if trainer-specific not found.
        Returns marked-up price in rupees, or None.
        """
        # Try trainer-specific setting first
        result = await self.db.execute(
            select(SessionSetting).where(
                SessionSetting.gym_id == gym_id,
                SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
                SessionSetting.trainer_id == trainer_id,
                SessionSetting.is_enabled.is_(True),
            )
        )
        setting = result.scalars().first()

        # Fallback to gym-level (trainer_id IS NULL)
        if not setting:
            result = await self.db.execute(
                select(SessionSetting).where(
                    SessionSetting.gym_id == gym_id,
                    SessionSetting.session_id == PERSONAL_TRAINING_SESSION_ID,
                    SessionSetting.trainer_id.is_(None),
                    SessionSetting.is_enabled.is_(True),
                )
            )
            setting = result.scalars().first()

        if not setting or not setting.final_price:
            return None

        return round(setting.final_price * get_markup_multiplier())

    async def get_reward_info(self, client_id: int) -> Dict:
        
        opt_in_result = await self.db.execute(
            select(RewardProgramOptIn).where(
                RewardProgramOptIn.client_id == client_id
            )
        )
        cash_result = await self.db.execute(
            select(ReferralFittbotCash).where(
                ReferralFittbotCash.client_id == client_id
            )
        )

        opt_in = opt_in_result.scalars().first()
        cash_entry = cash_result.scalars().first()

        return {
            "opted_in": bool(opt_in and opt_in.status == "active"),
            "available_cash_rupees": cash_entry.fittbot_cash if cash_entry else 0,
        }
    

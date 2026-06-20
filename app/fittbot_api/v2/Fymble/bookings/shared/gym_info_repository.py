"""Shared gym-info loader used by all booking domains.

Fetches gym name, full address, lat/long, and owner mobile in one place
so dailypass and session bookings don't duplicate the same queries.
"""

from typing import Dict, Any, List, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.gym import Gym, GymLocation, GymOwner


class GymInfoRepository:
    """Reusable gym detail queries for booking modules."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_bulk_gym_info(self, gym_ids: Set[int]) -> Dict[int, Dict[str, Any]]:
        """Batch-fetch gym info for multiple gyms in 3 queries (not N*3).

        Returns a dict keyed by gym_id.
        """
        if not gym_ids:
            return {}

        ids = list(gym_ids)

        # ── 1. All gym rows ──
        gym_result = await self.db.execute(
            select(Gym).where(Gym.gym_id.in_(ids))
        )
        gyms = {g.gym_id: g for g in gym_result.scalars().all()}

        # ── 2. All coordinates ──
        loc_result = await self.db.execute(
            select(GymLocation).where(GymLocation.gym_id.in_(ids))
        )
        locations = {loc.gym_id: loc for loc in loc_result.scalars().all()}

        # ── 3. All owner mobiles ──
        owner_ids = [g.owner_id for g in gyms.values() if g.owner_id]
        owners: Dict[int, str] = {}
        if owner_ids:
            owner_result = await self.db.execute(
                select(GymOwner.owner_id, GymOwner.contact_number)
                .where(GymOwner.owner_id.in_(owner_ids))
            )
            owners = {r.owner_id: r.contact_number for r in owner_result.all()}

        # ── Build result ──
        result: Dict[int, Dict[str, Any]] = {}
        for gid in gym_ids:
            gym = gyms.get(gid)
            if not gym:
                result[gid] = {
                    "name": f"Gym {gid}", "location": None, "city": None,
                    "cover_pic": None, "address": None, "latitude": None,
                    "longitude": None, "owner_mobile": None,
                }
                continue

            loc = locations.get(gid)
            result[gid] = {
                "name": gym.name,
                "location": gym.location,
                "city": gym.city,
                "cover_pic": gym.cover_pic,
                "address": {
                    "door_no": gym.door_no, "building": gym.building,
                    "street": gym.street, "area": gym.area,
                    "city": gym.city, "state": gym.state, "pincode": gym.pincode,
                },
                "latitude": float(loc.latitude) if loc and loc.latitude else None,
                "longitude": float(loc.longitude) if loc and loc.longitude else None,
                "owner_mobile": owners.get(gym.owner_id) if gym.owner_id else None,
            }

        return result

    async def get_full_gym_info(self, gym_id: int) -> Dict[str, Any]:
        """Return gym name, address, lat/long, and owner mobile."""

        # ── Gym row ──
        result = await self.db.execute(
            select(Gym).where(Gym.gym_id == gym_id)
        )
        gym = result.scalars().first()

        if not gym:
            return {
                "name": f"Gym {gym_id}",
                "location": None,
                "city": None,
                "address": None,
                "latitude": None,
                "longitude": None,
                "owner_mobile": None,
            }

        address = {
            "door_no": gym.door_no,
            "building": gym.building,
            "street": gym.street,
            "area": gym.area,
            "city": gym.city,
            "state": gym.state,
            "pincode": gym.pincode,
        }

        # ── Coordinates ──
        loc_result = await self.db.execute(
            select(GymLocation.latitude, GymLocation.longitude)
            .where(GymLocation.gym_id == gym_id)
        )
        loc_row = loc_result.first()
        latitude = float(loc_row.latitude) if loc_row and loc_row.latitude else None
        longitude = float(loc_row.longitude) if loc_row and loc_row.longitude else None

        # ── Owner mobile ──
        owner_mobile = None
        if gym.owner_id:
            owner_result = await self.db.execute(
                select(GymOwner.contact_number)
                .where(GymOwner.owner_id == gym.owner_id)
            )
            owner_row = owner_result.first()
            if owner_row:
                owner_mobile = owner_row.contact_number

        return {
            "name": gym.name,
            "location": gym.location,
            "city": gym.city,
            "address": address,
            "latitude": latitude,
            "longitude": longitude,
            "owner_mobile": owner_mobile,
        }

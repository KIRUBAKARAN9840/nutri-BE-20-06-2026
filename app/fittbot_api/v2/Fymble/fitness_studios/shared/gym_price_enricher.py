"""Shared gym info + dailypass price enricher.

Used by both `gym_mate.home._get_nearby_gyms` and
`gym_mate.sessions.list_my_matches` so the pricing logic isn't
duplicated. Anything that needs `{gym_id → name + area + cover_pic +
dailypass_price}` for a set of gyms should call `fetch_gym_info` from
here.

`area` falls back to `city` when the granular area string is empty —
same fallback the existing repository callers use.
"""
import asyncio
from typing import Dict, List, NamedTuple, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ..daily_pass.repository import DailyPassRepository
from .gym_repository import GymRepository
from .pricing_service import PricingService


class GymInfo(NamedTuple):
    """Single gym's display + pricing snapshot."""
    gym_id: int
    name: Optional[str]
    area: Optional[str]
    cover_pic: Optional[str]
    dailypass_price: Optional[int]


async def fetch_gym_info(
    db: AsyncSession,
    redis: Optional[Redis],
    gym_ids: List[int],
) -> Dict[int, GymInfo]:
    """Returns {gym_id: GymInfo} for the supplied gym_ids.

    Parallel fetch of: gyms, cover_pics, dailypass pricing rows, offer
    flags, promo counts — then `PricingService.resolve_price` for each.
    Empty input or no redis ⇒ empty dict (graceful no-op).
    """
    if not gym_ids or redis is None:
        return {}

    dp_repo = DailyPassRepository(db, redis)
    gym_repo = GymRepository(db, redis)

    gyms_map, cover_pics, pricing_map, offer_map, promo_counts = (
        await asyncio.gather(
            gym_repo.fetch_gyms(gym_ids),
            gym_repo.fetch_cover_pics(gym_ids),
            dp_repo.fetch_dailypass_pricing(gym_ids),
            dp_repo.fetch_offer_flags(gym_ids),
            dp_repo.fetch_promo_counts(gym_ids),
        )
    )

    out: Dict[int, GymInfo] = {}
    for gid in gym_ids:
        gym = gyms_map.get(gid)
        if gym is None:
            continue
        price = PricingService.resolve_price(
            gid, pricing_map, offer_map, promo_counts,
        )
        out[gid] = GymInfo(
            gym_id=gid,
            name=gym.name,
            area=(gym.area or gym.city) or None,
            cover_pic=cover_pics.get(gid),
            dailypass_price=price,
        )
    return out

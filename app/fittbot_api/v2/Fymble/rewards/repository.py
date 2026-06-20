import logging
from typing import List, Optional

from redis.asyncio import Redis
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import (
    RewardProgramOptIn,
    RewardProgramEntry,
    Client,
    CalorieEvent,
    ReferralRedeem,
    ReferralFittbotCash,
    ReferralFittbotCashLogs,
    ReferralCode,
)

logger = logging.getLogger("v2.rewards.repository")


class RewardRepository:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def get_opt_in(self, client_id: int) -> Optional[RewardProgramOptIn]:
        result = await self.db.execute(
            select(RewardProgramOptIn).where(
                RewardProgramOptIn.client_id == client_id
            )
        )
        return result.scalars().first()

    async def get_valid_entries(self, client_id: int) -> List[RewardProgramEntry]:
        result = await self.db.execute(
            select(RewardProgramEntry)
            .where(
                RewardProgramEntry.client_id == client_id,
                RewardProgramEntry.status == "valid",
            )
            .order_by(RewardProgramEntry.created_at.desc())
        )
        return result.scalars().all()

    async def get_client(self, client_id: int) -> Optional[Client]:
        result = await self.db.execute(
            select(Client).where(Client.client_id == client_id)
        )
        return result.scalars().first()

    async def get_client_xp(self, client_id: int) -> int:
        """Sum all XP from CalorieEvent (workout + diet + water)."""
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(CalorieEvent.workout_added), 0)
                + func.coalesce(func.sum(CalorieEvent.calories_added), 0)
                + func.coalesce(func.sum(CalorieEvent.water_added), 0)
            ).where(CalorieEvent.client_id == client_id)
        )
        return result.scalar() or 0

    async def get_total_redeemed(self, client_id: int) -> int:
        result = await self.db.execute(
            select(func.sum(ReferralRedeem.points_redeemed)).where(
                ReferralRedeem.client_id == client_id
            )
        )
        return result.scalar() or 0

    async def get_fittbot_cash(self, client_id: int) -> float:
        result = await self.db.execute(
            select(ReferralFittbotCash).where(
                ReferralFittbotCash.client_id == client_id
            )
        )
        entry = result.scalars().first()
        return entry.fittbot_cash if entry else 0

    async def get_referral_code(self, client_id: int) -> Optional[str]:
        result = await self.db.execute(
            select(ReferralCode).where(ReferralCode.client_id == client_id)
        )
        entry = result.scalars().first()
        return entry.referral_code if entry else None

    async def redeem_points(self, client_id: int, redeemable_points: int, cash_to_add: int):
        # Update or create fittbot cash
        result = await self.db.execute(
            select(ReferralFittbotCash).where(
                ReferralFittbotCash.client_id == client_id
            )
        )
        fittbot_cash_entry = result.scalars().first()

        if fittbot_cash_entry:
            fittbot_cash_entry.fittbot_cash += cash_to_add
        else:
            self.db.add(ReferralFittbotCash(
                client_id=client_id,
                fittbot_cash=cash_to_add,
            ))

        # Log the cash addition
        self.db.add(ReferralFittbotCashLogs(
            client_id=client_id,
            fittbot_cash=cash_to_add,
            reason=f"Redeemed {redeemable_points} XP points",
        ))

        # Record the redemption
        self.db.add(ReferralRedeem(
            client_id=client_id,
            points_redeemed=redeemable_points,
        ))

        await self.db.commit()

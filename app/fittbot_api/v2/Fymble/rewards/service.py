import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v1.client.client_api.reward_program.reward_service import (
    MAX_ENTRIES,
    PROGRAM_START_DATE,
    PROGRAM_END_DATE,
)
from .repository import RewardRepository
from app.utils.logging_utils import FittbotHTTPException
from .schemas import (
    EntryItem,
    MethodBreakdown,
    OptInStatusData,
    RewardDashboardData,
    ShowRewardsPageData,
    RedeemPointsData,
)

logger = logging.getLogger("v2.rewards.service")

#Display names mapping: internal method -> display key
METHOD_DISPLAY = {
    "dailypass": "dailypass",
    "session": "session",
    "subscription": "subscription",
    "gym_membership": "gym_membership",
    "nutrition_purchase": "nutrition",
    "ai_scanner": "ai_scanner",
    "ai_diet_coach": "ai_diet_coach",
    "referral": "referral",
}


class RewardService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.repo = RewardRepository(db, redis)

    async def get_opt_in_status(self, client_id: int) -> OptInStatusData:
        opt_in = await self.repo.get_opt_in(client_id)
        return OptInStatusData(opted_in=opt_in is not None)

    async def get_dashboard(self, client_id: int) -> RewardDashboardData:
        opt_in = await self.repo.get_opt_in(client_id)

        # Build empty breakdown with display keys
        entries_by_method = {
            display: MethodBreakdown(count=0, max=MAX_ENTRIES.get(internal, 0))
            for internal, display in METHOD_DISPLAY.items()
        }

        if not opt_in:
            return RewardDashboardData(
                opted_in=False,
                total_entries=0,
                entries_by_method=entries_by_method,
                entries=[],
            )

        entries = await self.repo.get_valid_entries(client_id)

        entries_list = []
        for entry in entries:
            display_key = METHOD_DISPLAY.get(entry.method, entry.method)
            if display_key in entries_by_method:
                entries_by_method[display_key].count += 1

            entries_list.append(
                EntryItem(
                    entry_id=entry.entry_id,
                    method=display_key,
                    created_at=entry.created_at.isoformat() if entry.created_at else None,
                )
            )

        total_entries = sum(m.count for m in entries_by_method.values())

        return RewardDashboardData(
            opted_in=True,
            opted_in_at=opt_in.opted_in_at.isoformat() if opt_in.opted_in_at else None,
            total_entries=total_entries,
            entries_by_method=entries_by_method,
            entries=entries_list,
            program_start=PROGRAM_START_DATE.isoformat(),
            program_end=PROGRAM_END_DATE.isoformat(),
        )

    async def get_show_rewards_page(self, client_id: int) -> ShowRewardsPageData:
        client_xp = await self.repo.get_client_xp(client_id)
        total_redeemed = await self.repo.get_total_redeemed(client_id)

        redeemable_xp = client_xp - total_redeemed
        actual_redeemable = (redeemable_xp // 100) * 100

        fittbot_cash = await self.repo.get_fittbot_cash(client_id)
        referral_code = await self.repo.get_referral_code(client_id)

        opt_in = await self.repo.get_opt_in(client_id)
        reward_interest_modal = opt_in is not None

        response=ShowRewardsPageData(
            actual_redeemable=actual_redeemable,
            fittbot_cash=fittbot_cash,
            referral_code=referral_code,
            reward_interest_modal=reward_interest_modal,
        )


    

        return ShowRewardsPageData(
            actual_redeemable=actual_redeemable,
            fittbot_cash=fittbot_cash,
            referral_code=referral_code,
            reward_interest_modal=reward_interest_modal,
        )

    async def redeem_points(self, client_id: int, redeemable_points: int) -> RedeemPointsData:
        client = await self.repo.get_client(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail=f"Client with id {client_id} not found",
                error_code="CLIENT_NOT_FOUND",
                log_data={"client_id": client_id},
            )

        if redeemable_points % 100 != 0:
            raise FittbotHTTPException(
                status_code=400,
                detail="Redeemable points must be a multiple of 100",
                error_code="INVALID_REDEEM_AMOUNT",
                log_data={"client_id": client_id, "redeemable_points": redeemable_points},
            )

        # Check client has enough XP to redeem
        client_xp = await self.repo.get_client_xp(client_id)
        total_redeemed = await self.repo.get_total_redeemed(client_id)
        actual_redeemable = ((client_xp - total_redeemed) // 100) * 100

        if redeemable_points > actual_redeemable:
            raise FittbotHTTPException(
                status_code=400,
                detail=f"Insufficient XP. You can redeem up to {actual_redeemable} points",
                error_code="INSUFFICIENT_XP",
                log_data={"client_id": client_id, "requested": redeemable_points, "available": actual_redeemable},
            )

        cash_to_add = redeemable_points // 100
        await self.repo.redeem_points(client_id, redeemable_points, cash_to_add)

        return RedeemPointsData(
            client_id=client_id,
            points_redeemed=redeemable_points,
            cash_earned=cash_to_add,
        )
    

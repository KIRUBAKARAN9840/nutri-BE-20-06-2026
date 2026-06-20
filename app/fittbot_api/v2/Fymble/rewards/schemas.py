from datetime import date
from typing import Dict, List, Optional

from pydantic import BaseModel


class MethodBreakdown(BaseModel):
    count: int
    max: int


class EntryItem(BaseModel):
    entry_id: str
    method: str
    created_at: Optional[str] = None


class RewardDashboardData(BaseModel):
    opted_in: bool
    opted_in_at: Optional[str] = None
    total_entries: int = 0
    entries_by_method: Dict[str, MethodBreakdown] = {}
    entries: List[EntryItem] = []
    program_start: Optional[str] = None
    program_end: Optional[str] = None


class RewardDashboardResponse(BaseModel):
    status: int = 200
    data: RewardDashboardData


class ShowRewardsPageData(BaseModel):
    actual_redeemable: int
    fittbot_cash: float
    referral_code: Optional[str] = None
    reward_interest_modal: bool


class ShowRewardsPageResponse(BaseModel):
    status: int = 200
    data: ShowRewardsPageData


class RedeemPointsRequest(BaseModel):
    redeemable_points: int


class RedeemPointsData(BaseModel):
    client_id: int
    points_redeemed: int
    cash_earned: int


class RedeemPointsResponse(BaseModel):
    status: int = 200
    message: str
    data: RedeemPointsData


class OptInStatusData(BaseModel):
    opted_in: bool


class OptInStatusResponse(BaseModel):
    status: int = 200
    data: OptInStatusData

"""Pydantic request/response models for Home feed endpoint."""

from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from app.fittbot_api.v2.Fymble.gym_mate.friends import FriendSuggestionDTO
from app.fittbot_api.v2.Fymble.gym_mate.notifications import HomeFriendRequestsDotDTO
from app.fittbot_api.v2.Fymble.gym_mate.profile import OnboardingStatusDTO
from app.fittbot_api.v2.Fymble.gym_mate.sessions import NearbyGymMateDTO
from app.fittbot_api.v2.Fymble.fitness_studios.daily_pass.schemas import DailyPassGymResponse


# ── Request Schemas ──────────────────────────────────────────────────


class HomeDataParams(BaseModel):
    client_lat: float
    client_lng: float
    client_id: int


class SaveGymRequestPayload(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    area: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=10)


class SaveGymRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request saved successfully"
    already_requested: bool = False


# ── Session Slot Schemas ─────────────────────────────────────────────


class HomeSessionSlot(BaseModel):
    key: int
    gym_id: int
    gym_name: Optional[str] = None
    distance_km: Optional[float] = None
    session_name: str
    session_id: int
    schedule_id: int
    trainer_id: Optional[int] = None
    date: str
    start_time: str
    end_time: str
    price: Optional[int] = None
    session_offer_active: bool = False


# ── Membership Schemas ───────────────────────────────────────────────


class HomeMembershipGym(BaseModel):
    key: int
    gym_id: int
    plan_id: Optional[int] = None
    # Duration (months) of the chosen plan — the longest plan the gym offers
    # (12 > 6 > 3). per_month_price = that plan's marked-up total ÷ duration.
    duration: Optional[int] = None
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    cover_pic: Optional[str] = None
    distance_km: Optional[float] = None
    per_month_price: Optional[int] = None


# ── Festival Offer Schemas ──────────────────────────────────────────


class HomeFestivalOffer(BaseModel):
    key: int
    gym_id: int
    plan_id: Optional[int] = None
    gym_name: Optional[str] = None
    cover_pic: Optional[str] = None
    distance_km: Optional[float] = None
    offer_price: Optional[int] = None
    original_price: Optional[int] = None
    duration: Optional[int] = None
    per_month_price: Optional[int] = None


# ── Frequently Booked (rebook the last daily-pass gym) ──────────────


class FrequentlyBookedGym(BaseModel):
    """Rebook card for the client's most-recently booked daily-pass gym.

    Surfaced ONLY when the client has no current/upcoming pass (we don't
    nudge a rebook while a booking is still live). Same visible shape as a
    dailypass gym card (photo/name/area/distance/commission price) plus two
    nudge signals. distance_km is null when the gym is outside the nearby
    radius (e.g. user has travelled away from it).
    """
    gym_id: int
    gym_name: Optional[str] = None
    area: Optional[str] = None
    cover_pic: Optional[str] = None
    distance_km: Optional[float] = None
    dailypass_price: Optional[int] = None
    booking_count: int = 0
    last_booked_days_ago: Optional[int] = None


# ── Active Bookings ─────────────────────────────────────────────────


class ActiveBookings(BaseModel):
    dailypass: bool = False
    sessions: bool = False
    gym_membership: bool = False


# ── Top-level Response ───────────────────────────────────────────────


class NutritionPackageCard(BaseModel):
    """Nutrition package status card for home page."""
    has_active_package: bool = False
    total_sessions: int = 0
    sessions_used: int = 0
    sessions_remaining: int = 0
    next_session_number: Optional[int] = None
    next_session_duration: Optional[int] = None
    next_session_unlocked: bool = False
    next_unlock_date: Optional[str] = None
    eligibility_id: Optional[int] = None


class FreeCreditsCard(BaseModel):
    """Welcome-bonus card state for the home page.

    state:
      "active"  → user is inside the 7-day window AND has scans left
      "expired" → either the 7-day window elapsed OR all scans used
    Frontend renders the per-day copy keyed by (state, days_left, scans_left).
    Card is hidden (null) once the user buys a paid plan or never received a bonus.
    """
    state: Literal["active", "expired"]
    scans_left: int
    days_left: int


class HomeDataResponse(BaseModel):
    status: int = 200
    profile: Optional[str] = None
    credits: int = 0
    is_unlimited: bool = False
    # Valid reward-program entries for this client (same value as
    # /api/v2/reward_program/dashboard → total_entries). 0 if not opted in.
    total_entries: int = 0
    free_credits_card: Optional[FreeCreditsCard] = None
    # New unified promo field — read this. The single promo to show, or null.
    # One of the 2-slots-per-day rotation types; legacy `*_eligibility` booleans
    # below are still sent during the transition but are superseded by this.
    modal: Optional[
        Literal[
            "dailypass", "rewards", "refer", "diet", "workout", "step", "water",
            "rewards1", "rewards2",
            # Slot-1 gymmate modals (always the day's first modal, alternating).
            "gymmate1", "gymmate2",
        ]
    ] = None
    # ── Legacy promo flags (kept for transition; superseded by `modal`) ──
    dailypass_eligibility: bool = False
    rewards_eligibility: bool = False
    refer_eligibility: bool = False
    diet_eligibility: bool = False
    workout_eligibility: bool = False
    step_eligibility: bool = False
    water_eligibility: bool = False
    webinar_eligibility: bool = False
    referral_code: Optional[str] = None
    no_of_passes_left: int = 0
    ai: bool = True
    personal_coach: bool = True
    first_time_user: bool = False
    bookings: Optional[ActiveBookings] = None
    home_gif: str = ""
    no_gyms: bool = True
    nearby_sessions: List[HomeSessionSlot] = []
    next_day: bool = False
    earliest_slot: Optional[str] = None
    nearby_memberships: List[HomeMembershipGym] = []
    festival_offers: List[HomeFestivalOffer] = []
    dailypass_gyms: List[DailyPassGymResponse] = []
    gym_mate_onboarding: Optional[OnboardingStatusDTO] = None
    gym_mate_nearby: List[NearbyGymMateDTO] = []
    # Suggested gym-mates to add as friends — same 3-tier waterfall
    # (mutual → match → fallback) and shape as the dedicated GymMate home,
    # so frontends can render identical cards. Empty when none / on failure.
    gym_mate_friend_suggestions: List[FriendSuggestionDTO] = []
    # Rebook card for the last daily-pass gym — null when the client has no
    # past pass, or still holds a current/upcoming one. See FrequentlyBookedGym.
    frequently_booked: Optional[FrequentlyBookedGym] = None
    # Pending friend-request summary for the home avatar-stack badge:
    # `{has_unread, count, recent_avatars[up to 3]}`. Same shape used by
    # the dedicated GymMate home so frontends can render identically.
    gym_mate_friend_requests: Optional[HomeFriendRequestsDotDTO] = None

    def __init__(self, **data):
        super().__init__(**data)
        self.no_gyms = not self.nearby_sessions and not self.nearby_memberships


# ── Nutrition Join Schemas ──────────────────────────────────────────


class NutritionJoinData(BaseModel):
    join_time: bool
    meeting_link: bool
    link: Optional[str] = None
    session_expired: bool = False
    message: Optional[str] = None
    booking_date: str
    start_time: str
    end_time: str


class NutritionJoinResponse(BaseModel):
    status: int = 200
    data: NutritionJoinData


# ── iPhone Nutrition Schemas ────────────────────────────────────────


class IphoneNutritionPayload(BaseModel):
    type: Literal["personal", "ai"]


class IphoneNutritionResponse(BaseModel):
    status: int = 200
    message: str = "Saved"
    already_exists: bool = False


# ── Dismiss Free-Credits Card Schemas ───────────────────────────────


class DismissFreeCreditsResponse(BaseModel):
    status: int = 200
    message: str = "Free credits card dismissed"

from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel


class SessionDTO(BaseModel):
    session_id: int
    host_client_id: int
    gym_id: int
    session_date: date
    session_time: time
    mate_preference: str
    fitness_level: str
    workout_vibes: List[str]
    payment_mode: str
    payment_status: str
    daily_pass_id: Optional[str] = None
    status: str


class SessionRequestDTO(BaseModel):
    request_id: int
    session_id: int
    requester_client_id: int
    host_client_id: int
    message: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None


class PendingRequestDTO(BaseModel):
    request_id: int
    session_id: int
    requester_client_id: int
    requester_name: Optional[str] = None
    requester_avatar_url: Optional[str] = None
    message: Optional[str] = None
    session_date: Optional[date] = None
    session_time: Optional[time] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    created_at: Optional[datetime] = None


class MatchDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    session_id: int
    session_date: date


class SentRequestDTO(BaseModel):
    """A pending request I sent to someone else's session."""
    request_id: int
    session_id: int
    session_date: date
    session_time: time
    host_client_id: int
    host_name: Optional[str] = None
    host_avatar_url: Optional[str] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    message: Optional[str] = None
    created_at: Optional[datetime] = None


class MatchedSessionMemberDTO(BaseModel):
    """One accepted member of a matched session."""
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_viewer: bool = False    # True for the JWT-holder, helps frontend mark themselves


class SessionParticipantDTO(BaseModel):
    """One accepted participant of a session — host or joiner.
    Shown on the session-detail modal when a card is tapped.
    """
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str  # "host" | "member"
    is_viewer: bool = False
    joined_at: Optional[datetime] = None


class MatchedSessionGymDTO(BaseModel):
    gym_id: int
    name: Optional[str] = None
    area: Optional[str] = None
    cover_pic: Optional[str] = None
    dailypass_price: Optional[int] = None


class MatchedSessionDTO(BaseModel):
    """One matched session — gym info + every accepted member (viewer + others)."""
    session_id: int
    session_date: date
    session_time: time
    gym: MatchedSessionGymDTO
    members: List[MatchedSessionMemberDTO]


class HostedSessionDTO(BaseModel):
    """One of the viewer's OWN future sessions, with everything they
    filled in at create-time + the current joiner count.

    `joiner_count = 0` ⇒ frontend can show "No one has joined yet" and
    the Cancel button works regardless.
    """
    session_id: int
    session_date: date
    session_time: time
    gym: MatchedSessionGymDTO
    mate_preference: str
    fitness_level: str
    workout_vibes: List[str]
    payment_mode: str
    payment_status: str
    status: str
    joiner_count: int = 0
    created_at: Optional[datetime] = None


# Kept for backward compat with anything that still imported the old name.
class MatchListItemDTO(BaseModel):
    other_client_id: int
    other_name: Optional[str] = None
    other_avatar_url: Optional[str] = None
    session_id: int
    session_date: date
    session_time: time
    joined_at: Optional[datetime] = None


class ReceivedRequestsSummaryDTO(BaseModel):
    pending_count: int
    recent_avatars: List[str]


class HostIdentityDTO(BaseModel):
    client_id: int
    name: Optional[str] = None
    avatar_url: Optional[str] = None


class HostSessionsSummaryDTO(BaseModel):
    host: HostIdentityDTO
    future_count: Optional[int] = None
    received_requests: Optional[ReceivedRequestsSummaryDTO] = None
    match: Optional[MatchDTO] = None


class NearbyGymMateDTO(BaseModel):
    sno: int
    session_id: int
    host_client_id: int
    host_name: Optional[str] = None
    host_avatar_url: Optional[str] = None
    gym_id: int
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    distance_km: float
    session_date: date
    session_time: time
    dailypass_booked: bool = False


class NearbyGymMateAllDTO(BaseModel):

    sno: int
    session_id: int
    host_client_id: int
    host_name: Optional[str] = None
    host_avatar_url: Optional[str] = None
    host_bio: Optional[str] = None
    gym_id: int
    gym_name: Optional[str] = None
    gym_area: Optional[str] = None
    distance_km: float
    session_date: date
    session_time: time
    mate_preference: str
    fitness_level: str
    workout_vibes: List[str] = []
    payment_mode: str
    dailypass_booked: bool = False
    request_status: str = "none"
    pending_request_id: Optional[int] = None

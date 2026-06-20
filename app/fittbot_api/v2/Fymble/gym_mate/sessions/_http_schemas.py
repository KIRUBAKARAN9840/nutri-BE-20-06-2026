from datetime import date, time
from typing import List, Optional

from pydantic import BaseModel, Field

from .schemas import (
    HostedSessionDTO,
    HostSessionsSummaryDTO,
    MatchedSessionDTO,
    NearbyGymMateAllDTO,
    PendingRequestDTO,
    SentRequestDTO,
    SessionDTO,
    SessionParticipantDTO,
    SessionRequestDTO,
)


class CreateSessionRequest(BaseModel):
    gym_id: int
    session_date: date
    session_time: time
    mate_preference: str = Field(..., max_length=20)
    fitness_level: str = Field(..., max_length=20)
    workout_vibes: List[str] = Field(..., min_length=1)
    payment_mode: str = Field("pay_later", max_length=20)


class CreateSessionResponse(BaseModel):
    status: int = 200
    message: str = "Session created"
    data: SessionDTO


class GetSessionResponse(BaseModel):
    status: int = 200
    data: SessionDTO


class CancelSessionResponse(BaseModel):
    status: int = 200
    message: str = "Session cancelled"


class SendRequestBody(BaseModel):
    message: Optional[str] = Field(None, max_length=280)


class SendRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request sent"
    data: SessionRequestDTO


class WithdrawRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request withdrawn"


class AcceptRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request accepted"


class RejectRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request rejected"


class ListSessionRequestsResponse(BaseModel):
    status: int = 200
    data: List[PendingRequestDTO]


class SessionParticipantsResponse(BaseModel):
    status: int = 200
    data: List[SessionParticipantDTO]


class InboxResponse(BaseModel):
    status: int = 200
    data: List[PendingRequestDTO]


class HostSummaryResponse(BaseModel):
    status: int = 200
    data: HostSessionsSummaryDTO


class SentRequestsResponse(BaseModel):
    status: int = 200
    data: List[SentRequestDTO]


class MyMatchesResponse(BaseModel):
    status: int = 200
    data: List[MatchedSessionDTO]


class HostedSessionsResponse(BaseModel):
    status: int = 200
    data: List[HostedSessionDTO]


class NearbyGymMatesAllResponse(BaseModel):
    status: int = 200
    data: List[NearbyGymMateAllDTO]

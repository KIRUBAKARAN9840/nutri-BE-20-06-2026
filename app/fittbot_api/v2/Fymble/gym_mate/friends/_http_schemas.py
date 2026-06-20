from typing import List

from pydantic import BaseModel

from .schemas import (
    DiscoverProfileDTO,
    FriendDTO,
    FriendRequestDTO,
    FriendSuggestionDTO,
    FriendSuggestionSlimDTO,
    IncomingRequestDTO,
    OutgoingRequestDTO,
)


class SendFriendRequestBody(BaseModel):
    to_client_id: int


class SendFriendRequestResponse(BaseModel):
    status: int = 200
    message: str = "Request sent"
    data: FriendRequestDTO


class IncomingRequestsResponse(BaseModel):
    status: int = 200
    data: List[IncomingRequestDTO]


class OutgoingRequestsResponse(BaseModel):
    status: int = 200
    data: List[OutgoingRequestDTO]


class AcceptResponse(BaseModel):
    status: int = 200
    message: str = "Request accepted"


class RejectResponse(BaseModel):
    status: int = 200
    message: str = "Request rejected"


class CancelResponse(BaseModel):
    status: int = 200
    message: str = "Request cancelled"


class FriendsListResponse(BaseModel):
    status: int = 200
    data: List[FriendDTO]


class UnfriendResponse(BaseModel):
    status: int = 200
    message: str = "Friend removed"


class FriendSuggestionsResponse(BaseModel):
    status: int = 200
    data: List[FriendSuggestionSlimDTO]


class DiscoverProfilesResponse(BaseModel):
    status: int = 200
    data: List[DiscoverProfileDTO]

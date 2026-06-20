from typing import List

from pydantic import BaseModel, Field

from .schemas import BlockedUserDTO


class BlockUserRequest(BaseModel):
    blocked_client_id: int = Field(..., ge=1)


class BlockUserResponse(BaseModel):
    status: int = 200
    message: str = "User blocked"


class UnblockUserResponse(BaseModel):
    status: int = 200
    message: str = "User unblocked"


class ListBlockedResponse(BaseModel):
    status: int = 200
    data: List[BlockedUserDTO]

"""HTTP request/response wrappers for the notifications routes."""

from typing import List, Optional

from pydantic import BaseModel, Field

from .schemas import (
    DeviceTokenDTO,
    NotificationDTO,
    NotificationPageDTO,
    UnreadCountDTO,
)


# ── Requests ────────────────────────────────────────────────────────────────

class RegisterDeviceTokenRequest(BaseModel):
    platform: str = Field(..., max_length=20)   # ios | android | web
    token: str = Field(..., min_length=8, max_length=512)


class MarkBucketReadRequest(BaseModel):
    # one of: "gym_mate_connections" | "friend_requests" | "chat"
    bucket: str = Field(..., max_length=40)


# ── Responses ───────────────────────────────────────────────────────────────

class NotificationFeedResponse(BaseModel):
    status: int = 200
    data: NotificationPageDTO


class UnreadCountResponse(BaseModel):
    status: int = 200
    data: UnreadCountDTO


class NotificationResponse(BaseModel):
    status: int = 200
    data: NotificationDTO


class DeviceTokenResponse(BaseModel):
    status: int = 200
    data: DeviceTokenDTO


class EmptyResponse(BaseModel):
    status: int = 200
    message: str = "ok"

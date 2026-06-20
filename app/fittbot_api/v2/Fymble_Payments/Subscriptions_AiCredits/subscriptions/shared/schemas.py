"""
Shared schemas for v2 subscription payments (both Google Play and Razorpay).
"""

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────

class CommandStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    pending_webhook = "pending_webhook"


# ── Shared response models ────────────────────────────────────────────

class SubscriptionCommandAccepted(BaseModel):
    """Returned to HTTP caller immediately (202 Accepted)."""
    request_id: str
    status: CommandStatus
    status_url: str
    retry_after_seconds: int = 2


class CommandStatusResponse(BaseModel):
    """Returned when polling command status."""
    request_id: str
    status: CommandStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    updated_at_epoch: int


# ── Shared result models ─────────────────────────────────────────────

class SubscriptionVerifyResult(BaseModel):
    """Common shape returned by both providers on verify completion."""
    verified: bool
    captured: bool
    subscription_active: bool = False
    has_premium: bool = False
    is_trial: bool = False
    message: str = ""
    subscription_id: Optional[str] = None
    payment_id: Optional[str] = None
    order_id: Optional[str] = None
    active_from: Optional[str] = None
    active_until: Optional[str] = None
    trial_end: Optional[str] = None
    auto_renew: bool = False
    retry_after_ms: Optional[int] = Field(None, alias="retryAfterMs")

    class Config:
        populate_by_name = True


class SubscriptionWebhookResult(BaseModel):
    """Common shape returned after webhook processing."""
    status: str
    event_type: Optional[str] = None
    event_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

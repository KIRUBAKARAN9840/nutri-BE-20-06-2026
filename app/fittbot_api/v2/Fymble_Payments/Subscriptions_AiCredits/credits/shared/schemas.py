"""
Shared schemas used by both Google Play (RevenueCat) and Razorpay credit flows.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CommandStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    pending_webhook = "pending_webhook"


class CreditTxnType(str, Enum):
    purchase = "purchase"
    trial_bonus = "trial_bonus"
    subscription_bonus = "subscription_bonus"
    used = "used"
    expired = "expired"
    refunded = "refunded"
    admin_grant = "admin_grant"


# ── Shared HTTP response models ────────────────────────────────────

class CreditCommandAccepted(BaseModel):
    request_id: str
    status: CommandStatus
    status_url: str
    retry_after_seconds: int = 2


class CommandStatusResponse(BaseModel):
    request_id: str
    status: CommandStatus
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    updated_at_epoch: int


class CreditBalanceResponse(BaseModel):
    customer_id: str
    balance: int
    total_purchased: int
    total_bonus: int
    total_used: int
    # Unlimited-scan pass (credit_999). When is_unlimited is true, scans are
    # free until unlimited_until regardless of `balance`.
    is_unlimited: bool = False
    unlimited_until: Optional[str] = None


class CreditLedgerEntry(BaseModel):
    id: str
    txn_type: str
    credits: int
    balance_after: int
    source_order_id: Optional[str] = None
    source_subscription_id: Optional[str] = None
    description: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: str


class CreditHistoryResponse(BaseModel):
    customer_id: str
    entries: List[CreditLedgerEntry]
    total: int


# ── Shared processor result models ─────────────────────────────────

class CreditVerifyResult(BaseModel):
    """Returned by verify/webhook workers — stored in command.data."""
    verified: bool
    captured: bool
    message: str
    order_id: Optional[str] = None
    payment_id: Optional[str] = None
    credits_granted: Optional[int] = None
    new_balance: Optional[int] = None
    verify_path: Optional[str] = None


class CreditWebhookResult(BaseModel):
    """Returned by webhook worker — stored in command.data."""
    status: str
    event_type: str
    credits_granted: Optional[int] = None
    credits_refunded: Optional[int] = None
    new_balance: Optional[int] = None
    order_id: Optional[str] = None
    payment_id: Optional[str] = None
    reason: Optional[str] = None

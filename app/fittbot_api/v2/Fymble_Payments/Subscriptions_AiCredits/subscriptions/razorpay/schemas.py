"""
Razorpay subscription-specific schemas.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ── HTTP request models ───────────────────────────────────────────────

class RpSubscriptionCheckoutRequest(BaseModel):
    """Client initiates a subscription checkout via Razorpay."""
    plan_sku: str = Field(..., description="Catalog SKU that maps to Razorpay plan_id")
    client_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None


class RpSubscriptionVerifyRequest(BaseModel):
    """Client submits Razorpay subscription payment details for verification."""
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── Internal command payloads (Celery workers) ────────────────────────

class RpSubscriptionCheckoutCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_checkout worker."""
    command_id: Optional[str] = None
    user_id: str
    plan_sku: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RpSubscriptionVerifyCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_verify worker."""
    command_id: Optional[str] = None
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
    user_id: Optional[str] = None


class RpSubscriptionWebhookCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_webhook worker."""
    raw_body: str
    signature: str
    webhook_id: Optional[str] = None

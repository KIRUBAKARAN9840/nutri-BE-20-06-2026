"""
Razorpay-specific schemas for credit purchases.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ── HTTP request models ─────────────────────────────────────────────

class RpCreditCheckoutRequest(BaseModel):
    """Client initiates a credit-pack checkout via Razorpay."""
    product_sku: str = Field(..., description="Catalog SKU, e.g. credit_50")
    currency: str = "INR"
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class RpCreditVerifyRequest(BaseModel):
    """Client submits Razorpay payment details for verification."""
    order_id: str = Field(..., description="Our internal order ID from checkout step")
    razorpay_payment_id: str = Field(..., description="Razorpay payment ID (pay_xxx)")
    razorpay_signature: str = Field(..., description="Razorpay checkout signature")
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── Processor result models ─────────────────────────────────────────

class RpCreditCheckoutResult(BaseModel):
    """Returned by process_checkout worker — stored in command.data."""
    order_id: str
    client_id: str
    product_sku: str
    amount: int                    # amount in minor (paise)
    currency: str
    credits: int
    status: str
    key_id: str                    # Razorpay key_id for client-side SDK
    provider_order_id: str         # Razorpay order_id (order_xxx)
    prefill: Dict[str, Any] = {}   # name, email, contact for Razorpay checkout
    expires_at: str
    created_at: str


# ── Internal command payloads (Celery workers) ──────────────────────

class RpCreditCheckoutCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_checkout worker."""
    client_id: str
    product_sku: str
    currency: str = "INR"


class RpCreditVerifyCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_verify worker."""
    client_id: str
    order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class RpCreditWebhookCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_webhook worker."""
    raw_body: str
    razorpay_signature: str

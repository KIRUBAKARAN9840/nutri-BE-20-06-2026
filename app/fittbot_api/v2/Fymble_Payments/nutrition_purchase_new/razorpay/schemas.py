"""
Razorpay schemas for the new nutrition package purchase.

Same flow shape as Google Play (pay first, book later) but the verify step
carries Razorpay-specific fields (payment id + signature).
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


# ── HTTP request models ─────────────────────────────────────────────

class BookingDetails(BaseModel):
    """Optional slot selection for ad-funnel flow (pick slot, then pay).

    When present at checkout, the chosen slot is validated, soft-locked
    in Redis for the payment window, and persisted on the Order. At
    fulfillment, a NutritionBooking is auto-created so the user lands
    on a confirmation page with the slot already booked.
    """
    schedule_id: int
    booking_date: str = Field(..., description="ISO date YYYY-MM-DD")
    start_time: str = Field(..., description="Slot start HH:MM (24hr)")
    end_time: str = Field(..., description="Slot end HH:MM (24hr)")


class RpNutritionPackageCheckoutRequest(BaseModel):
    """Client initiates a nutrition package checkout via Razorpay."""
    product_sku: str = Field(
        ...,
        description="Catalog SKU. One of nutri_basic, nutri_1m, nutri_1m_off, nutrition_service_30, nutri_3m, nutri_3m_off, ai_diet_coach.",
    )
    currency: str = "INR"
    booking: Optional[BookingDetails] = Field(
        None,
        description="Optional. If set, slot is held for payment window and auto-booked on fulfillment.",
    )
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class RpNutritionPackageVerifyRequest(BaseModel):
    """Client submits Razorpay payment details for verification."""
    order_id: str = Field(..., description="Our internal Order ID from checkout")
    razorpay_payment_id: str = Field(..., description="Razorpay payment ID (pay_xxx)")
    razorpay_signature: str = Field(..., description="Razorpay checkout HMAC signature")
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── Processor result models ─────────────────────────────────────────

class RpNutritionPackageCheckoutResult(BaseModel):
    """Returned by process_checkout worker — stored in command.data."""
    order_id: str
    client_id: str
    product_sku: str
    amount: int
    currency: str
    status: str
    key_id: str
    provider_order_id: str
    prefill: Dict[str, Any] = {}
    expires_at: str
    created_at: str
    total_sessions: int


class RpNutritionPackageVerifyResult(BaseModel):
    """Returned by verify/webhook workers — stored in command.data."""
    verified: bool
    captured: bool
    message: str
    order_id: Optional[str] = None
    payment_id: Optional[str] = None
    entitlement_id: Optional[str] = None
    eligibility_id: Optional[int] = None
    total_sessions: Optional[int] = None
    credits_granted: Optional[int] = None
    credits_balance: Optional[int] = None
    verify_path: Optional[str] = None

    # Ad-funnel: present only when checkout carried a booking selection.
    booking_id: Optional[int] = None
    booking_date: Optional[str] = None
    booking_start_time: Optional[str] = None
    booking_end_time: Optional[str] = None
    booking_status: Optional[str] = None  # "booked" | "booking_failed"


class RpNutritionPackageWebhookResult(BaseModel):
    """Returned by webhook worker — stored in command.data."""
    status: str
    event_type: str
    order_id: Optional[str] = None
    payment_id: Optional[str] = None
    entitlement_id: Optional[str] = None
    eligibility_id: Optional[int] = None
    total_sessions: Optional[int] = None
    credits_granted: Optional[int] = None
    credits_balance: Optional[int] = None
    reason: Optional[str] = None


# ── Internal command payloads (Celery workers) ──────────────────────

class RpNutritionPackageCheckoutCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_checkout worker."""
    client_id: str
    product_sku: str
    currency: str = "INR"
    booking: Optional[BookingDetails] = None


class RpNutritionPackageVerifyCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_verify worker."""
    client_id: str
    order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class RpNutritionPackageWebhookCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_webhook worker."""
    raw_body: str
    razorpay_signature: str

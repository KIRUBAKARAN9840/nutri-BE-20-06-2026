"""
Google Play / RevenueCat schemas for the new nutrition package purchase.

Key difference from old flow: purchase does NOT require schedule_id/booking_date.
Booking happens separately after payment is verified.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── HTTP request models ─────────────────────────────────────────────

class NutritionPackagePurchaseRequest(BaseModel):
    """Client initiates a nutrition package purchase via Google Play."""
    product_sku: str = Field(
        ...,
        description="Catalog SKU. One of nutri_basic, nutri_1m, nutrition_service_30, nutri_3m, ai_diet_coach.",
    )
    currency: str = "INR"
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    os: Optional[Literal["android", "ios"]] = Field(
        default=None,
        description="Client platform — selects which RevenueCat SDK key is returned.",
    )


class NutritionPackageVerifyRequest(BaseModel):
    """Client asks us to verify that RevenueCat purchase landed."""
    order_id: str = Field(..., description="Order ID from the purchase step")
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── Processor result models ─────────────────────────────────────────

class NutritionPackagePurchaseResult(BaseModel):
    """Returned by process_purchase worker — stored in command.data."""
    order_id: str
    client_id: str
    product_sku: str
    amount: int
    currency: str
    status: str
    api_key: str
    expires_at: str
    created_at: str
    total_sessions: int


class NutritionPackageVerifyResult(BaseModel):
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


class NutritionPackageWebhookResult(BaseModel):
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

class NutritionPackagePurchaseCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_purchase worker."""
    client_id: str
    product_sku: str
    currency: str = "INR"
    os: Optional[Literal["android", "ios"]] = None


class NutritionPackageVerifyCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_verify worker."""
    client_id: str
    order_id: str


class NutritionPackageWebhookCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_webhook worker."""
    signature: str
    raw_body: str

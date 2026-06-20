"""
Google Play / RevenueCat specific schemas for credit purchases.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── HTTP request models ─────────────────────────────────────────────

class CreditPurchaseRequest(BaseModel):
    """Client initiates a credit-pack purchase via Google Play (RevenueCat)."""
    product_sku: str = Field(..., description="Catalog SKU, e.g. credit_50")
    currency: str = "INR"
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    os: Optional[Literal["android", "ios"]] = Field(
        default=None,
        description="Client platform — selects which RevenueCat SDK key is returned.",
    )


class CreditVerifyRequest(BaseModel):
    """Client asks us to verify that RevenueCat purchase landed."""
    order_id: str = Field(..., description="Order ID from the purchase step")
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── Processor result models ─────────────────────────────────────────

class CreditPurchaseResult(BaseModel):
    """Returned by process_purchase worker — stored in command.data."""
    order_id: str
    client_id: str
    product_sku: str
    amount: int
    currency: str
    credits: int
    status: str
    api_key: str
    expires_at: str
    created_at: str


# ── Internal command payloads (Celery workers) ──────────────────────

class CreditPurchaseCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_purchase worker."""
    client_id: str
    product_sku: str
    currency: str = "INR"
    os: Optional[Literal["android", "ios"]] = None


class CreditVerifyCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_verify worker."""
    client_id: str
    order_id: str


class CreditWebhookCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_webhook worker."""
    signature: str
    raw_body: str

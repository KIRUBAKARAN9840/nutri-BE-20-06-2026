"""
Google Play (RevenueCat) subscription-specific schemas.
"""

from typing import Optional

from pydantic import BaseModel, Field


# ── HTTP request models ───────────────────────────────────────────────

class GpSubscriptionCreateOrderRequest(BaseModel):
    """Client requests a pending order before RevenueCat checkout."""
    product_sku: str = Field(..., description="Catalog SKU, e.g. fittbot_diamond_12m")
    currency: str = "INR"
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


class GpSubscriptionVerifyRequest(BaseModel):
    """Client asks us to confirm purchase via RevenueCat."""
    client_id: Optional[str] = None
    idempotency_key: Optional[str] = None


# ── Internal command payloads (Celery workers) ────────────────────────

class GpSubscriptionOrderCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_order worker."""
    client_id: str
    product_sku: str
    currency: str = "INR"


class GpSubscriptionVerifyCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_verify worker."""
    client_id: str


class GpSubscriptionWebhookCommand(BaseModel):
    """Payload persisted in Redis, consumed by process_webhook worker."""
    signature: str
    raw_body: str

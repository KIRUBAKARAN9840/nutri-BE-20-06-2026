"""
Re-exports SubscriptionSyncService.

This service is tightly coupled with the DB models (Subscription, Order,
Payment, Entitlement, etc.) which must remain in one place due to
SQLAlchemy constraints. Kept as re-export.
"""

from app.fittbot_api.v1.payments.services.subscription_sync_service import (  # noqa: F401
    SubscriptionSyncService,
)

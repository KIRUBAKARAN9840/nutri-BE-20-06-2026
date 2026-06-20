"""
Small utility functions extracted from v1 legacy modules.

These were originally scattered across revenue_cat.py and other v1
files. Collected here so v2 processors don't import legacy modules.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from app.services.timezone_utils import IST, now_ist  # noqa: F401

from .models import (
    Provider,
    SubscriptionStatus,
    Subscription,
    WebhookProcessingLog,
)

logger = logging.getLogger("payments.v2.utils")

# Re-export timedelta for callers that used `legacy_rc.timedelta`
timedelta = timedelta  # noqa: F811


# ── Masking ────────────────────────────────────────────────────────

def mask_value(value: Optional[str], left: int = 4, right: int = 4) -> str:
    """Mask sensitive data for logging."""
    if not value:
        return ""
    if len(value) <= left + right:
        return "*" * len(value)
    return f"{value[:left]}...{value[-right:]}"


# ── Row-level locking ──────────────────────────────────────────────

def lock_query(query):
    """Attempt row-level lock; fall back silently if unsupported."""
    try:
        return query.with_for_update()
    except (InvalidRequestError, AttributeError):
        return query
    except Exception as lock_err:
        logger.debug("Lock not applied: %s", lock_err)
        return query


# ── Webhook event ID generation ────────────────────────────────────

def generate_event_id(event: Dict[str, Any]) -> str:
    """Generate unique event ID for idempotency."""
    customer_id = event.get("app_user_id", "unknown")
    event_type = event.get("type", "unknown")

    if event.get("id"):
        return event["id"]

    if event_type == "INITIAL_PURCHASE":
        transaction_id = event.get("transaction_id", "")
        return f"{event_type}_{customer_id}_{transaction_id}"
    elif event_type == "RENEWAL":
        transaction_id = event.get("transaction_id", "")
        purchased_at = event.get("purchased_at_ms", 0)
        return f"{event_type}_{customer_id}_{transaction_id}_{purchased_at}"
    elif event_type == "CANCELLATION":
        product_id = event.get("product_id", "")
        cancelled_at = event.get("cancelled_at_ms", datetime.now().timestamp() * 1000)
        return f"{event_type}_{customer_id}_{product_id}_{int(cancelled_at)}"
    elif event_type == "EXPIRATION":
        product_id = event.get("product_id", "")
        expiration_at = event.get("expiration_at_ms", 0)
        return f"{event_type}_{customer_id}_{product_id}_{expiration_at}"
    else:
        timestamp = int(datetime.now().timestamp() * 1000)
        return f"{event_type}_{customer_id}_{timestamp}"


# ── Security logging ──────────────────────────────────────────────

def log_security_event(event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Log security-related events."""
    logger.warning(
        "SECURITY_EVENT",
        extra={"event": event_type, "timestamp": now_ist().isoformat(), **(data or {})},
    )


# ── Billing issues handler ────────────────────────────────────────

def handle_billing_issues(
    event: Dict[str, Any],
    db: Session,
    processing_log: WebhookProcessingLog,
) -> Dict[str, Any]:
    """Handle billing issues webhook event."""
    customer_id = event.get("app_user_id")
    product_id = event.get("product_id")

    if not product_id:
        error_msg = "No product_id found in billing issues event"
        logger.error(error_msg)
        processing_log.status = "failed"
        processing_log.error_message = error_msg
        processing_log.completed_at = now_ist()
        db.commit()
        return {"success": False, "error": error_msg}

    logger.warning("Billing issues for user %s, product %s", customer_id, product_id)

    try:
        subscription = db.query(Subscription).filter(
            Subscription.customer_id == customer_id,
            Subscription.product_id == product_id,
            Subscription.provider == Provider.google_play.value,
        ).first()

        if subscription:
            processing_log.status = "completed"
            processing_log.completed_at = now_ist()
            processing_log.result_summary = f"Marked billing issue for subscription {subscription.id}"
            db.commit()
            return {"success": True, "subscription_id": subscription.id}

        processing_log.status = "completed"
        processing_log.completed_at = now_ist()
        processing_log.result_summary = "No subscription found for billing issue"
        db.commit()
        return {"success": True, "message": "No subscription found"}

    except Exception as e:
        logger.error("Error handling billing issues: %s", str(e))
        processing_log.status = "failed"
        processing_log.error_message = str(e)
        processing_log.completed_at = now_ist()
        db.commit()
        return {"success": False, "error": str(e)}

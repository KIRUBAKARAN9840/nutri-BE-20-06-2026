"""
Structured payment event logger for CloudWatch observability.

Emits structured JSON log events with consistent field names across all
payment types so CloudWatch Log Insights queries work uniformly.
"""

import logging
import threading
from datetime import datetime
from typing import Any, Optional

import boto3

logger = logging.getLogger("payments.events")

# ── Notification email ─────────────────────────────────────────────

SOURCE_EMAIL = "support@fittbot.com"
PAYMENT_NOTIFY_TO = [
    "martinraju53@gmail.com",
    "naveenkulandasamy@gmail.com",
]


def _send_payment_email(
    *,
    provider: str,
    payment_type: str,
    client_id: Optional[str] = None,
    command_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    razorpay_subscription_id: Optional[str] = None,
    plan_sku: Optional[str] = None,
    amount: Optional[str] = None,
    extra_fields: Optional[dict] = None,
) -> None:
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            ("Timestamp", now),
            ("Provider", provider),
            ("Payment Type", payment_type),
            ("Client ID", client_id or "N/A"),
            ("Command ID", command_id or "N/A"),
        ]
        if razorpay_payment_id:
            rows.append(("Razorpay Payment ID", razorpay_payment_id))
        if razorpay_subscription_id:
            rows.append(("Razorpay Subscription ID", razorpay_subscription_id))
        if plan_sku:
            rows.append(("Plan SKU", plan_sku))
        if amount:
            rows.append(("Amount", amount))
        if extra_fields:
            for k, v in extra_fields.items():
                rows.append((k, str(v)))

        table_rows = "".join(
            f"<tr><td style='padding:6px 12px;border:1px solid #ddd;background:#f8f9fa;'>"
            f"<b>{label}</b></td><td style='padding:6px 12px;border:1px solid #ddd;'>{value}</td></tr>"
            for label, value in rows
        )
        html_body = f"""
        <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;">
            <h2 style="color:#27ae60;">Payment Captured</h2>
            <table style="border-collapse:collapse;width:100%;margin:12px 0;">
                {table_rows}
            </table>
            <p style="font-size:13px;color:#666;">Automated notification from Fymble payment system.</p>
        </div>
        """
        subject = f"Payment Captured | {payment_type} | {provider} | {now}"
        ses = boto3.client("ses", region_name="ap-south-1")
        ses.send_email(
            Source=SOURCE_EMAIL,
            Destination={"ToAddresses": PAYMENT_NOTIFY_TO},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Charset": "UTF-8", "Data": html_body}},
            },
        )
        logger.info("Payment notification email sent",
                     extra={"provider": provider, "payment_type": payment_type, "client_id": client_id})
    except Exception:
        logger.exception("Failed to send payment notification email")


def fire_payment_notification_email(**kwargs) -> None:
    t = threading.Thread(target=_send_payment_email, kwargs=kwargs, daemon=True)
    t.start()


class PaymentEventLogger:
    """Emits structured payment events for CloudWatch Log Insights."""

    def __init__(self, provider: str, payment_type: str):
        self.provider = provider
        self.payment_type = payment_type

    def _emit(self, event: str, level: str = "info", **fields: Any) -> None:
        extra = {
            "event": event,
            "provider": self.provider,
            "payment_type": self.payment_type,
        }
        for k, v in fields.items():
            if v is not None:
                extra[k] = v
        log_fn = getattr(logger, level, logger.info)
        log_fn(event, extra=extra)

    # ---- Checkout ----
    def checkout_started(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.checkout.started", operation="checkout", command_id=command_id, **kw)

    def checkout_completed(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.checkout.completed", operation="checkout", outcome="success", command_id=command_id, **kw)

    def checkout_failed(self, command_id: str, error_code: str, **kw: Any) -> None:
        self._emit("payment.checkout.failed", level="error", operation="checkout", outcome="failed",
                    command_id=command_id, error_code=error_code, **kw)

    # ---- Verify ----
    def verify_started(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.verify.started", operation="verify", command_id=command_id, **kw)

    def verify_completed(self, command_id: str, verify_path: str, **kw: Any) -> None:
        self._emit("payment.verify.completed", operation="verify", outcome="success",
                    command_id=command_id, verify_path=verify_path, **kw)

    def verify_failed(self, command_id: str, error_code: str, **kw: Any) -> None:
        self._emit("payment.verify.failed", level="error", operation="verify", outcome="failed",
                    command_id=command_id, error_code=error_code, **kw)

    def verify_pending(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.verify.pending", operation="verify", outcome="pending", command_id=command_id, **kw)

    # ---- Webhook ----
    def webhook_received(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.webhook.received", operation="webhook", command_id=command_id, **kw)

    def webhook_processed(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.webhook.processed", operation="webhook", outcome="success", command_id=command_id, **kw)

    def webhook_failed(self, command_id: str, error_code: str, **kw: Any) -> None:
        self._emit("payment.webhook.failed", level="error", operation="webhook", outcome="failed",
                    command_id=command_id, error_code=error_code, **kw)

    def webhook_signature_invalid(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.webhook.signature_invalid", level="warning", operation="webhook",
                    outcome="failed", error_code="invalid_signature", command_id=command_id, **kw)

    # ---- Provider API calls ----
    def provider_call_started(self, command_id: str, provider_endpoint: str, **kw: Any) -> None:
        self._emit("payment.provider.call_started", command_id=command_id, provider_endpoint=provider_endpoint, **kw)

    def provider_call_completed(self, command_id: str, provider_endpoint: str, duration_ms: int, **kw: Any) -> None:
        self._emit("payment.provider.call_completed", command_id=command_id,
                    provider_endpoint=provider_endpoint, duration_ms=duration_ms, **kw)

    def provider_call_failed(self, command_id: str, provider_endpoint: str, error_code: str, **kw: Any) -> None:
        self._emit("payment.provider.call_failed", level="error", command_id=command_id,
                    provider_endpoint=provider_endpoint, error_code=error_code, **kw)

    # ---- Side effects ----
    def side_effect_success(self, command_id: str, side_effect: str, **kw: Any) -> None:
        self._emit("payment.side_effect.success", command_id=command_id, side_effect=side_effect, **kw)

    def side_effect_failed(self, command_id: str, side_effect: str, error_detail: str, **kw: Any) -> None:
        self._emit("payment.side_effect.failed", level="warning", command_id=command_id,
                    side_effect=side_effect, error_detail=error_detail, **kw)

    def side_effect_skipped(self, command_id: str, side_effect: str, reason: str, **kw: Any) -> None:
        self._emit("payment.side_effect.skipped", command_id=command_id,
                    side_effect=side_effect, skip_reason=reason, **kw)

    # ---- Funnel tracking ----
    def order_created(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.order.created", operation="checkout", command_id=command_id, **kw)

    def payment_captured(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.captured", operation="verify", outcome="captured", command_id=command_id, **kw)
        fire_payment_notification_email(
            provider=self.provider,
            payment_type=self.payment_type,
            client_id=str(kw.get("client_id", "")),
            command_id=command_id,
            razorpay_payment_id=kw.get("razorpay_payment_id"),
            razorpay_subscription_id=kw.get("razorpay_subscription_id"),
            plan_sku=kw.get("plan_sku"),
            amount=str(kw.get("amount", "")) if kw.get("amount") else None,
        )

    def payment_authorized(self, command_id: str, **kw: Any) -> None:
        self._emit("payment.authorized", operation="verify", outcome="authorized", command_id=command_id, **kw)

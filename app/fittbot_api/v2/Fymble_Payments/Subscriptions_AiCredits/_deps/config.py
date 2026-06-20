"""
Centralized configuration for v2 payment processing.
"""

import os
from datetime import timedelta, timezone
from functools import lru_cache
from typing import Optional

try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings

from pydantic import Field
from app.config.settings import settings as app_settings


# ── HighConcurrencyConfig ──────────────────────────────────────────

class HighConcurrencyConfig(BaseSettings):
    """
    Centralized knobs for the v2 flow.
    Tuned defaults allow 1k+ concurrent requests while keeping provider pressure bounded.
    """

    # ── Google Play subscription queues ────────────────────────────
    gp_subscription_order_queue_name: str = Field(
        default="payments.gp_subscription.process_order",
    )
    gp_subscription_verify_queue_name: str = Field(
        default="payments.gp_subscription.process_verify",
    )
    gp_subscription_webhook_queue_name: str = Field(
        default="payments.gp_subscription.process_webhook",
    )
    gp_subscription_redis_prefix: str = Field(
        default="payments:gp_subscription:v2",
    )

    # ── Google Play credits queues ─────────────────────────────────
    credits_purchase_queue_name: str = Field(
        default="payments.credits.process_purchase",
    )
    credits_verify_queue_name: str = Field(
        default="payments.credits.process_verify",
    )
    credits_webhook_queue_name: str = Field(
        default="payments.credits.process_webhook",
    )
    credits_verify_fallback_queue_name: str = Field(
        default="payments.credits.process_verify_fallback",
    )
    credits_redis_prefix: str = Field(
        default="payments:credits:v2",
    )

    # ── Razorpay subscription queues ───────────────────────────────
    rp_subscription_checkout_queue_name: str = Field(
        default="payments.rp_subscription.process_checkout",
    )
    rp_subscription_verify_queue_name: str = Field(
        default="payments.rp_subscription.process_verify",
    )
    rp_subscription_webhook_queue_name: str = Field(
        default="payments.rp_subscription.process_webhook",
    )
    rp_subscription_redis_prefix: str = Field(
        default="payments:rp_subscription:v2",
    )

    # ── Razorpay credits queues ────────────────────────────────────
    rp_credits_checkout_queue_name: str = Field(
        default="payments.rp_credits.process_checkout",
    )
    rp_credits_verify_queue_name: str = Field(
        default="payments.rp_credits.process_verify",
    )
    rp_credits_webhook_queue_name: str = Field(
        default="payments.rp_credits.process_webhook",
    )
    rp_credits_verify_fallback_queue_name: str = Field(
        default="payments.rp_credits.process_verify_fallback",
    )
    rp_credits_redis_prefix: str = Field(
        default="payments:rp_credits:v2",
    )

    # ── Google Play nutrition purchase queues ────────────────────────
    gp_nutrition_purchase_queue_name: str = Field(
        default="payments.gp_nutrition.process_purchase",
    )
    gp_nutrition_verify_queue_name: str = Field(
        default="payments.gp_nutrition.process_verify",
    )
    gp_nutrition_webhook_queue_name: str = Field(
        default="payments.gp_nutrition.process_webhook",
    )
    gp_nutrition_verify_fallback_queue_name: str = Field(
        default="payments.gp_nutrition.process_verify_fallback",
    )
    gp_nutrition_redis_prefix: str = Field(
        default="payments:gp_nutrition:v2",
    )
    gp_nutrition_verify_total_timeout_seconds: int = Field(default=20)
    gp_nutrition_capture_cache_ttl_seconds: int = Field(default=600)

    # ── Razorpay nutrition-package queues ────────────────────────────
    rp_nutrition_pkg_checkout_queue_name: str = Field(
        default="payments.rp_nutrition_pkg.process_checkout",
    )
    rp_nutrition_pkg_verify_queue_name: str = Field(
        default="payments.rp_nutrition_pkg.process_verify",
    )
    rp_nutrition_pkg_webhook_queue_name: str = Field(
        default="payments.rp_nutrition_pkg.process_webhook",
    )
    rp_nutrition_pkg_redis_prefix: str = Field(
        default="payments:rp_nutrition_pkg:v2",
    )
    rp_nutrition_pkg_capture_cache_ttl_seconds: int = Field(default=600)

    # ── General settings ───────────────────────────────────────────
    command_ttl_seconds: int = Field(default=900)
    redis_prefix: str = Field(default="payments:razorpay:v2")
    revenuecat_redis_prefix: str = Field(default="payments:revenuecat:v2")
    max_provider_concurrency: int = Field(default=40)
    provider_timeout_seconds: int = Field(default=8)
    default_retry_backoff_seconds: int = Field(default=5)

    # ── Polling / verify settings ──────────────────────────────────
    revenuecat_capture_cache_ttl_seconds: int = Field(default=600)
    revenuecat_verify_poll_attempts: int = Field(default=10)
    revenuecat_verify_poll_base_delay_ms: int = Field(default=600)
    revenuecat_verify_poll_max_delay_ms: int = Field(default=4000)
    revenuecat_verify_total_timeout_seconds: int = Field(default=20)
    credits_verify_poll_attempts: int = Field(default=10)
    credits_verify_poll_base_delay_ms: int = Field(default=600)
    credits_verify_poll_max_delay_ms: int = Field(default=4000)
    credits_verify_total_timeout_seconds: int = Field(default=20)
    credits_capture_cache_ttl_seconds: int = Field(default=600)
    rp_credits_verify_total_timeout_seconds: int = Field(default=20)
    rp_credits_capture_cache_ttl_seconds: int = Field(default=600)
    verify_capture_cache_ttl_seconds: int = Field(default=600)

    class Config:
        env_prefix = "RAZORPAY_V2_"
        case_sensitive = False


@lru_cache
def get_high_concurrency_config() -> HighConcurrencyConfig:
    return HighConcurrencyConfig()


# ── PaymentSettings ────────────────────────────────────────────────

class PaymentSettings:
    """Payment system configuration using main app settings."""

    def __init__(self):
        self._settings = app_settings

    @property
    def database_url(self) -> str:
        return self._settings.database_url

    @property
    def razorpay_webhook_secret(self) -> str:
        return self._settings.razorpay_webhook_secret

    @property
    def revenuecat_webhook_secret(self) -> str:
        return self._settings.revenuecat_webhook_secret

    @property
    def revenuecat_api_key(self) -> str:
        api_key = self._settings.revenuecat_api_key
        if not api_key:
            import logging
            logging.getLogger("payments.settings").warning(
                "REVENUECAT_API_KEY not set! Verify endpoint will fail."
            )
            return ""
        return api_key

    @property
    def revenuecat_api_key_ios(self) -> str:
        api_key = self._settings.revenuecat_api_key_ios
        if not api_key:
            import logging
            logging.getLogger("payments.settings").warning(
                "REVENUECAT_API_KEY_IOS not set! iOS clients will receive empty key."
            )
            return ""
        return api_key

    @property
    def razorpay_key_id(self) -> str:
        return self._settings.razorpay_key_id

    @property
    def razorpay_key_secret(self) -> str:
        return self._settings.razorpay_key_secret

    @property
    def ist_timezone(self) -> timezone:
        return timezone(timedelta(hours=5, minutes=30))

    @property
    def environment(self) -> str:
        return self._settings.environment

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


_payment_settings: Optional[PaymentSettings] = None


def get_payment_settings() -> PaymentSettings:
    global _payment_settings
    if _payment_settings is None:
        _payment_settings = PaymentSettings()
    return _payment_settings

from functools import lru_cache

try:
    from pydantic_settings import BaseSettings
except ImportError:  # pragma: no cover - fallback for Pydantic v1 environments
    from pydantic import BaseSettings

from pydantic import Field


class HighConcurrencyConfig(BaseSettings):
    """
    Centralized knobs for the v2 flow.
    Tuned defaults allow 1k+ concurrent requests while keeping provider pressure bounded.
    """

    checkout_queue_name: str = Field(
        default="payments.razorpay.process_checkout",
        description="Celery task name for checkout processing",
    )
    verify_queue_name: str = Field(
        default="payments.razorpay.process_verify",
        description="Celery task name for verification processing",
    )
    webhook_queue_name: str = Field(
        default="payments.razorpay.process_webhook",
        description="Celery task name for webhook processing",
    )
    revenuecat_order_queue_name: str = Field(
        default="payments.revenuecat.process_order",
        description="Celery task for RevenueCat order creation",
    )
    revenuecat_verify_queue_name: str = Field(
        default="payments.revenuecat.process_verify",
        description="Celery task for RevenueCat verification",
    )
    revenuecat_webhook_queue_name: str = Field(
        default="payments.revenuecat.process_webhook",
        description="Celery task for RevenueCat webhook processing",
    )
    dailypass_checkout_queue_name: str = Field(
        default="payments.dailypass.process_checkout",
        description="Celery task for DailyPass checkout processing",
    )
    dailypass_verify_queue_name: str = Field(
        default="payments.dailypass.process_verify",
        description="Celery task for DailyPass verification",
    )
    dailypass_upgrade_checkout_queue_name: str = Field(
        default="payments.dailypass.process_upgrade_checkout",
        description="Celery task for DailyPass upgrade checkout processing",
    )
    dailypass_upgrade_verify_queue_name: str = Field(
        default="payments.dailypass.process_upgrade_verify",
        description="Celery task for DailyPass upgrade verification processing",
    )
    dailypass_edit_topup_checkout_queue_name: str = Field(
        default="payments.dailypass.process_edit_topup_checkout",
        description="Celery task for DailyPass edit top-up checkout processing",
    )
    dailypass_edit_topup_verify_queue_name: str = Field(
        default="payments.dailypass.process_edit_topup_verify",
        description="Celery task for DailyPass edit top-up verification processing",
    )
    gym_membership_checkout_queue_name: str = Field(
        default="payments.gym_membership.process_checkout",
        description="Celery task for gym membership checkout processing",
    )
    gym_membership_verify_queue_name: str = Field(
        default="payments.gym_membership.process_verify",
        description="Celery task for gym membership verification processing",
    )
    gym_membership_webhook_queue_name: str = Field(
        default="payments.gym_membership.process_webhook",
        description="Celery task for gym membership webhook processing",
    )
    sessions_checkout_queue_name: str = Field(
        default="payments.sessions.process_checkout",
        description="Celery task for session booking checkout processing",
    )
    sessions_verify_queue_name: str = Field(
        default="payments.sessions.process_verify",
        description="Celery task for session booking verification processing",
    )
    sessions_webhook_queue_name: str = Field(
        default="payments.sessions.process_webhook",
        description="Celery task for session booking webhook processing",
    )
    nutrition_purchase_checkout_queue_name: str = Field(
        default="payments.nutrition_purchase.process_checkout",
        description="Celery task for nutrition purchase checkout processing",
    )
    nutrition_purchase_verify_queue_name: str = Field(
        default="payments.nutrition_purchase.process_verify",
        description="Celery task for nutrition purchase verification processing",
    )
    nutrition_purchase_webhook_queue_name: str = Field(
        default="payments.nutrition_purchase.process_webhook",
        description="Celery task for nutrition purchase webhook processing",
    )
    credits_purchase_queue_name: str = Field(
        default="payments.credits.process_purchase",
        description="Celery task for credit purchase order creation",
    )
    credits_verify_queue_name: str = Field(
        default="payments.credits.process_verify",
        description="Celery task for credit purchase verification",
    )
    credits_webhook_queue_name: str = Field(
        default="payments.credits.process_webhook",
        description="Celery task for credit purchase webhook processing",
    )
    credits_verify_fallback_queue_name: str = Field(
        default="payments.credits.process_verify_fallback",
        description="Celery task for credit verify fallback (fires after timeout)",
    )
    rp_credits_checkout_queue_name: str = Field(
        default="payments.rp_credits.process_checkout",
        description="Celery task for Razorpay credit checkout processing",
    )
    rp_credits_verify_queue_name: str = Field(
        default="payments.rp_credits.process_verify",
        description="Celery task for Razorpay credit verification processing",
    )
    rp_credits_webhook_queue_name: str = Field(
        default="payments.rp_credits.process_webhook",
        description="Celery task for Razorpay credit webhook processing",
    )
    rp_credits_verify_fallback_queue_name: str = Field(
        default="payments.rp_credits.process_verify_fallback",
        description="Celery task for Razorpay credit verify fallback",
    )
    # ── v2 Subscriptions (Google Play) ─────────────────────────────
    gp_subscription_order_queue_name: str = Field(
        default="payments.gp_subscription.process_order",
        description="Celery task for Google Play subscription order creation",
    )
    gp_subscription_verify_queue_name: str = Field(
        default="payments.gp_subscription.process_verify",
        description="Celery task for Google Play subscription verification",
    )
    gp_subscription_webhook_queue_name: str = Field(
        default="payments.gp_subscription.process_webhook",
        description="Celery task for Google Play subscription webhook processing",
    )
    gp_subscription_redis_prefix: str = Field(
        default="payments:gp_subscription:v2",
        description="Redis namespace for Google Play subscription commands",
    )
    # ── v2 Subscriptions (Razorpay) ────────────���───────────────────
    rp_subscription_checkout_queue_name: str = Field(
        default="payments.rp_subscription.process_checkout",
        description="Celery task for Razorpay subscription checkout",
    )
    rp_subscription_verify_queue_name: str = Field(
        default="payments.rp_subscription.process_verify",
        description="Celery task for Razorpay subscription verification",
    )
    rp_subscription_webhook_queue_name: str = Field(
        default="payments.rp_subscription.process_webhook",
        description="Celery task for Razorpay subscription webhook processing",
    )
    rp_subscription_redis_prefix: str = Field(
        default="payments:rp_subscription:v2",
        description="Redis namespace for Razorpay subscription commands",
    )
    command_ttl_seconds: int = Field(
        default=900,
        description="How long commands live in Redis (keeps polling windows bounded)",
    )
    redis_prefix: str = Field(
        default="payments:razorpay:v2",
        description="Namespace prefix for Redis keys",
    )
    revenuecat_redis_prefix: str = Field(
        default="payments:revenuecat:v2",
        description="Redis namespace for RevenueCat commands",
    )
    dailypass_redis_prefix: str = Field(
        default="payments:dailypass:v2",
        description="Redis namespace for DailyPass commands",
    )
    gym_membership_redis_prefix: str = Field(
        default="payments:gym_membership:v2",
        description="Redis namespace for gym membership commands",
    )
    sessions_redis_prefix: str = Field(
        default="payments:sessions:v2",
        description="Redis namespace for session booking commands",
    )
    nutrition_purchase_redis_prefix: str = Field(
        default="payments:nutrition_purchase:v2",
        description="Redis namespace for nutrition purchase commands",
    )
    gp_nutrition_purchase_queue_name: str = Field(
        default="payments.gp_nutrition.process_purchase",
        description="Celery task for Google Play nutrition purchase creation",
    )
    gp_nutrition_verify_queue_name: str = Field(
        default="payments.gp_nutrition.process_verify",
        description="Celery task for Google Play nutrition purchase verification",
    )
    gp_nutrition_verify_fallback_queue_name: str = Field(
        default="payments.gp_nutrition.process_verify_fallback",
        description="Celery task for Google Play nutrition verify fallback",
    )
    gp_nutrition_webhook_queue_name: str = Field(
        default="payments.gp_nutrition.process_webhook",
        description="Celery task for Google Play nutrition webhook processing",
    )
    gp_nutrition_redis_prefix: str = Field(
        default="payments:gp_nutrition:v2",
        description="Redis namespace for Google Play nutrition purchase commands",
    )
    gp_nutrition_verify_total_timeout_seconds: int = Field(
        default=20,
        description="Seconds before verify fallback fires for nutrition purchases",
    )
    gp_nutrition_capture_cache_ttl_seconds: int = Field(
        default=600,
        description="TTL for nutrition capture markers in Redis",
    )
    # ── v2 Razorpay nutrition-package queues ────────────────────────
    rp_nutrition_pkg_checkout_queue_name: str = Field(
        default="payments.rp_nutrition_pkg.process_checkout",
        description="Celery task for Razorpay nutrition-package checkout",
    )
    rp_nutrition_pkg_verify_queue_name: str = Field(
        default="payments.rp_nutrition_pkg.process_verify",
        description="Celery task for Razorpay nutrition-package verification",
    )
    rp_nutrition_pkg_webhook_queue_name: str = Field(
        default="payments.rp_nutrition_pkg.process_webhook",
        description="Celery task for Razorpay nutrition-package webhook processing",
    )
    rp_nutrition_pkg_redis_prefix: str = Field(
        default="payments:rp_nutrition_pkg:v2",
        description="Redis namespace for Razorpay nutrition-package commands",
    )
    rp_nutrition_pkg_capture_cache_ttl_seconds: int = Field(
        default=600,
        description="TTL for Razorpay nutrition-package capture markers in Redis",
    )
    credits_redis_prefix: str = Field(
        default="payments:credits:v2",
        description="Redis namespace for food-scanner credit commands (Google Play)",
    )
    rp_credits_redis_prefix: str = Field(
        default="payments:rp_credits:v2",
        description="Redis namespace for Razorpay food-scanner credit commands",
    )
    max_provider_concurrency: int = Field(
        default=40,
        description="Semaphore limit inside workers for outbound provider calls",
    )
    provider_timeout_seconds: int = Field(
        default=8,
        description="Timeout passed to httpx Razorpay client",
    )
    default_retry_backoff_seconds: int = Field(
        default=5, description="Base backoff when provider is throttling"
    )
    status_base_path: str = Field(
        default="/payments/razorpay/v2/commands",
        description="Relative URL used to build pollable status links",
    )
    revenuecat_status_base_path: str = Field(
        default="/payments/revenuecat/v2/commands",
        description="Status URL base for RevenueCat commands",
    )
    dailypass_status_base_path: str = Field(
        default="/pay/dailypass_v2/commands",
        description="Status URL base for DailyPass commands",
    )
    gym_membership_status_base_path: str = Field(
        default="/pay/gym_membership_v2/commands",
        description="Status URL base for gym membership commands",
    )
    nutrition_purchase_status_base_path: str = Field(
        default="/pay/nutrition_purchase_v2/commands",
        description="Status URL base for nutrition purchase commands",
    )
    credits_status_base_path: str = Field(
        default="/api/v2/credits/commands",
        description="Status URL base for credit purchase commands",
    )
    verify_db_poll_attempts: int = Field(
        default=12,
        description="Maximum iterations when polling the DB for webhook confirmation before we give up",
    )
    verify_db_poll_base_delay_ms: int = Field(
        default=600,
        description="Base delay between DB polls in milliseconds",
    )
    verify_db_poll_max_delay_ms: int = Field(
        default=4000,
        description="Maximum delay between DB polls in milliseconds",
    )
    verify_db_poll_total_timeout_seconds: int = Field(
        default=20,
        description="Absolute max seconds to wait for webhook/premium to propagate before calling Razorpay",
    )
    verify_provider_max_attempts: int = Field(
        default=2,
        description="Maximum times to hit Razorpay for verification when webhook hasn't landed",
    )
    verify_capture_cache_ttl_seconds: int = Field(
        default=600,
        description="TTL for capture markers dropped by the webhook so verify can finish instantly",
    )
    revenuecat_capture_cache_ttl_seconds: int = Field(
        default=600,
        description="TTL for RevenueCat capture markers dropped by webhook events",
    )
    revenuecat_verify_poll_attempts: int = Field(
        default=10,
        description="Max iterations when polling local state before hitting RevenueCat APIs",
    )
    revenuecat_verify_poll_base_delay_ms: int = Field(
        default=600,
        description="Base delay between RevenueCat DB/marker polls",
    )
    revenuecat_verify_poll_max_delay_ms: int = Field(
        default=4000,
        description="Max delay between RevenueCat DB/marker polls",
    )
    revenuecat_verify_total_timeout_seconds: int = Field(
        default=20,
        description="Absolute timeout before RevenueCat verify falls back to provider API",
    )
    credits_verify_poll_attempts: int = Field(
        default=10,
        description="Max iterations polling capture marker before hitting RevenueCat API for credits",
    )
    credits_verify_poll_base_delay_ms: int = Field(
        default=600,
        description="Base delay between credit verify polls",
    )
    credits_verify_poll_max_delay_ms: int = Field(
        default=4000,
        description="Max delay between credit verify polls",
    )
    credits_verify_total_timeout_seconds: int = Field(
        default=20,
        description="Absolute timeout before credits verify falls back to provider API",
    )
    credits_capture_cache_ttl_seconds: int = Field(
        default=600,
        description="TTL for Google Play credit capture markers in Redis",
    )
    rp_credits_verify_total_timeout_seconds: int = Field(
        default=20,
        description="Absolute timeout before Razorpay credits verify falls back to provider API",
    )
    rp_credits_capture_cache_ttl_seconds: int = Field(
        default=600,
        description="TTL for Razorpay credit capture markers in Redis",
    )
    rp_credits_status_base_path: str = Field(
        default="/api/v2/credits/razorpay/commands",
        description="Status URL base for Razorpay credit purchase commands",
    )

    class Config:
        env_prefix = "RAZORPAY_V2_"
        case_sensitive = False


@lru_cache
def get_high_concurrency_config() -> HighConcurrencyConfig:
    """FastAPI-friendly cached accessor."""
    return HighConcurrencyConfig()

# app/celery_app.py
# Most I/O-bound AI tasks run in FastAPI async (OpenAI, Groq) — prefork pool below.
# Exception: long-running plan generation (chat_diet) runs on a SEPARATE gevent
# worker pool against the `chat_diet_jobs` queue, so 90+ second OpenAI waits
# don't drain the API's connection pool. See README/deployment for the gevent
# worker invocation.

import os
import ssl
from celery import Celery
from celery.signals import setup_logging as celery_setup_logging, worker_process_init, worker_shutdown
from dotenv import load_dotenv

from app.models.async_database import (
    dispose_celery_async_engine,
    init_celery_async_db,
)
from app.utils.celery_asyncio import close_worker_loop, get_worker_loop
from app.fittbot_api.v1.payments.razorpay_async_gateway import (
    init_client as init_rzp_client,
    close_client as close_rzp_client,
)

load_dotenv()

@celery_setup_logging.connect
def _configure_celery_logging(**kwargs):
    """Override Celery's default logging with our JSON formatter so
    structured fields (event, payment_type, duration_ms, etc.) are
    serialized into the log output for CloudWatch Log Insights."""
    from app.utils.logging_config import setup_logging
    setup_logging()

from app.config.settings import settings
REDIS_URL = settings.redis_url_resolved





_SSL_OPTIONS = {
    "ssl_cert_reqs": ssl.CERT_NONE,
} if REDIS_URL.startswith("rediss://") else {}

celery_app = Celery(
    "fittbot",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "app.tasks.image_scanner_tasks",  # Image compression (CPU-bound) + legacy async endpoint
        # "app.tasks.chatbot_tasks",        # DEAD CODE - chatbot already uses direct async in ai_chatbot.py
        "app.tasks.pdf_tasks",            # PDF agreement generation
        "app.tasks.load_test_tasks",      # Load testing (no external API calls)
        "app.tasks.notification_tasks",   # Owner push notifications
        "app.tasks.gymmate_notification_tasks",  # GymMate push notifications (friends, sessions, chat, stories)
        "app.tasks.chat_diet_tasks",      # AI diet plan generation (gevent queue)
        "app.tasks.activity_tasks",       # Client activity tracking & WhatsApp follow-ups
        "app.fittbot_api.v1.payments.Fittbot_Subscriptions_concurrent.tasks",  # Payment queues
        "app.fittbot_api.v1.payments.auto_settlements.tasks",  # Auto settlement & payout tasks
    ]
)

# Celery Configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,

    # Result backend
    result_expires=3600,
    result_extended=True,

    # Broker connection settings - prevents heartbeat timeouts
    broker_heartbeat=10,  # Send heartbeat every 10 seconds
    broker_heartbeat_checkrate=2,  # Check heartbeat 2x per interval
    broker_connection_timeout=30,  # Connection timeout
    broker_pool_limit=10,  # Limit broker connections (important for gevent)

    # Worker settings - CRITICAL for rate limiting
    worker_prefetch_multiplier=1,  # One task at a time
    worker_max_tasks_per_child=100,  # Restart after 100 tasks (prevents memory leaks)
    worker_disable_rate_limits=False,  # ENABLE rate limiting


    task_annotations={
        # Image compression task (CPU-bound, stays in Celery)
        "app.tasks.image_scanner_tasks.compress_food_images": {
            "rate_limit": "500/m",  # CPU-bound compression, no API limits
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        # Legacy image scanning (for /analyze_async endpoint)
        "app.tasks.image_scanner_tasks.analyze_food_image": {
            "rate_limit": "800/m"  # Image/food scanning (Vision API)
        },

        # PDF agreement generation tasks - IO-bound (S3 upload/download)
        "app.tasks.pdf_tasks.generate_agreement_pdf_task": {
            "rate_limit": "60/m",  # 60 PDF generations per minute
            "time_limit": 120,     # 2 min hard limit
            "soft_time_limit": 90, # 1.5 min soft limit
        },

        # Payment tasks - NO rate limits, just time limits for safety
        "payments.razorpay.process_checkout": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.razorpay.process_verify": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "payments.razorpay.process_webhook": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.dailypass.process_checkout": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.dailypass.process_verify": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "payments.dailypass.process_upgrade_checkout": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.dailypass.process_upgrade_verify": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "payments.gym_membership.process_checkout": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.gym_membership.process_verify": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "payments.gym_membership.process_webhook": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.revenuecat.process_order": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.revenuecat.process_verify": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "payments.revenuecat.process_webhook": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        # Session booking tasks
        "payments.sessions.process_checkout": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        "payments.sessions.process_verify": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "payments.sessions.process_webhook": {
            "time_limit": 30,
            "soft_time_limit": 25,
        },
        # Auto settlement & payout tasks
        "settlements.daily_reconciliation": {
            "time_limit": 300,
            "soft_time_limit": 240,
        },
        "settlements.process_gym_membership_payouts": {
            "time_limit": 300,
            "soft_time_limit": 240,
        },
        "settlements.process_monday_bulk_payouts": {
            "time_limit": 300,
            "soft_time_limit": 240,
        },
        "settlements.retry_failed_payouts": {
            "time_limit": 120,
            "soft_time_limit": 90,
        },
        # Owner notification tasks
        "notifications.send_owner_booking": {
            "rate_limit": "100/m",
            "time_limit": 30,
            "soft_time_limit": 25,
        },

        # Chat-diet plan generation (gevent queue, long-running OpenAI calls)
        "chat_diet.generate": {
            "rate_limit": "60/m",      # tied to OpenAI capacity; tune later
            "time_limit": 240,         # hard ceiling: 4 min
            "soft_time_limit": 200,    # 3m20s — gives the task time to mark failed + notify
        },

        # Activity tracking tasks
        "activity.process_events": {
            "time_limit": 60,
            "soft_time_limit": 50,
        },
        "activity.check_abandoned_checkouts": {
            "time_limit": 120,
            "soft_time_limit": 100,
        },
        "activity.check_repeated_browsing": {
            "time_limit": 120,
            "soft_time_limit": 100,
        },
    },

    # Task routing - separate queues for different worker types
    task_routes={
        # ALL Payment tasks → payments queue (critical, on-demand workers)
        "payments.*": {"queue": "payments"},
        "settlements.*": {"queue": "payments"},

        # AI/ML tasks → ai queue (can use spot instances)
        "app.tasks.image_scanner_tasks.*": {"queue": "ai"},
        "app.tasks.pdf_tasks.*": {"queue": "ai"},

        # Load testing tasks (route to appropriate queues)
        "load_test.simulate_ai_task": {"queue": "ai"},
        "load_test.simulate_heavy_ai_task": {"queue": "ai"},
        "load_test.simulate_light_ai_task": {"queue": "ai"},
        "load_test.simulate_payment_task": {"queue": "payments"},

        # Notification tasks → ai queue (non-critical, fire-and-forget)
        "notifications.*": {"queue": "ai"},
        "gymmate.notifications.*": {"queue": "ai"},

        # Chat-diet generation → dedicated gevent queue (long-running OpenAI)
        "chat_diet.*": {"queue": "chat_diet_jobs"},

        # Activity tracking tasks → celery default queue (lightweight, non-critical)
        "activity.*": {"queue": "celery"},

        # Default queue for anything else → celery
    },

    # Task execution
    task_acks_late=True,  # Acknowledge only after completion
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    task_time_limit=300,  # 5 min hard limit
    task_soft_time_limit=240,  # 4 min soft limit

    # Broker settings
    broker_use_ssl=_SSL_OPTIONS if _SSL_OPTIONS else None,
    redis_backend_use_ssl=_SSL_OPTIONS if _SSL_OPTIONS else None,
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_transport_options={
        "visibility_timeout": 3600,  # 1 hour
        "fanout_prefix": True,
        "fanout_patterns": True,
    },
)

from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "process-activity-events": {
        "task": "activity.process_events",
        "schedule": 70.0,  # every 30 seconds
    },
    "check-abandoned-checkouts": {
        "task": "activity.check_abandoned_checkouts",
        "schedule": crontab(minute="*/30"),  # every 30 minutes
    },
    "check-repeated-browsing": {
        "task": "activity.check_repeated_browsing",
        "schedule": crontab(minute=0),  # every hour on the hour
    },
    "monitor-queue-health": {
        "task": "activity.monitor_queue_health",
        "schedule": 300.0,  # every 5 minutes
    },
    # # ── Auto Settlements & Payouts ──────────────────────────────────
    # "daily-reconciliation": {
    #     "task": "settlements.daily_reconciliation",
    #     "schedule": crontab(minute=0, hour=11),  # 11:00 AM IST daily
    #     "options": {"queue": "payments"},
    # },
    # "gym-membership-daily-payout": {
    #     "task": "settlements.process_gym_membership_payouts",
    #     "schedule": crontab(minute=0, hour=10),  # 10:00 AM IST daily
    #     "options": {"queue": "payments"},
    # },
    # "monday-bulk-payout": {
    #     "task": "settlements.process_monday_bulk_payouts",
    #     "schedule": crontab(minute=0, hour=10, day_of_week=1),  # Monday 10:00 AM IST
    #     "options": {"queue": "payments"},
    # },
    # "retry-failed-payouts": {
    #     "task": "settlements.retry_failed_payouts",
    #     "schedule": crontab(minute=0, hour=15),  # 3:00 PM IST daily
    #     "options": {"queue": "payments"},
    # },
}


# Default retry policy
class BaseTaskWithRetry(celery_app.Task):
    """Base task with automatic retry on failure"""
    autoretry_for = (Exception,)
    retry_kwargs = {"max_retries": 3, "countdown": 5}
    retry_backoff = True
    retry_backoff_max = 600  # Max 10 minutes
    retry_jitter = True

celery_app.Task = BaseTaskWithRetry

# Celery worker lifecycle hooks to keep async DB + event loop healthy
@worker_process_init.connect
def _init_worker_process(**kwargs):
    # Ensure a shared event loop and async engine exist for this worker process
    loop = get_worker_loop()
    init_celery_async_db()
    try:
        loop.run_until_complete(init_rzp_client())
    except Exception as exc:
        print(f"Failed to init Razorpay async client: {exc}")


@worker_shutdown.connect
def _shutdown_worker_process(**kwargs):
    # Dispose async engine cleanly and close the shared loop
    try:
        loop = get_worker_loop()
        loop.run_until_complete(dispose_celery_async_engine())
        loop.run_until_complete(close_rzp_client())
    finally:
        close_worker_loop()

if __name__ == "__main__":
    celery_app.start()

"""Celery task for AI diet plan generation (initial + follow-ups).

Runs on the dedicated `chat_diet_jobs` queue with a gevent worker pool
(see celery_app.py / deployment notes). Each task:
  1. Marks Redis job state = processing
  2. Calls generate_diet_plan() OR generate_followup_diet_plan() based on `step`
  3. Persists the plan in nutrition.ai_diet_coach with step + parent_id
  4. Marks Redis job state = complete with the new plan_id
  5. Pushes an Expo notification (title varies by step)

Failure path: BaseTaskWithRetry retries 3x with exponential backoff. After
the final failure, the task marks job state = failed and pushes a
"please try again" notification.
"""

import logging
from typing import Any, Dict, Optional

from app.celery_app import celery_app
from app.fittbot_api.v2.Fymble.diet.chat_diet.ai_service import (
    PRIMARY_MODEL,
    generate_diet_plan,
    generate_followup_diet_plan,
)
from app.fittbot_api.v2.Fymble.diet.chat_diet.repository import ChatDietRepository
from app.models.async_database import create_celery_async_sessionmaker
from app.services.client_notification_service import ClientNotificationService
from app.utils.celery_asyncio import get_worker_loop
from app.utils.redis_config import get_redis

logger = logging.getLogger("tasks.chat_diet")

DIET_NOTIFICATION_CHANNEL = "diet_notifications"

_STEP_NOTIFICATION_TITLE = {
    0: "Your diet plan is ready",
    1: "Your week-2 follow-up plan is ready",
    2: "Your week-3 follow-up plan is ready",
    3: "Your final follow-up plan is ready",
}


def _notification_title(step: int) -> str:
    return _STEP_NOTIFICATION_TITLE.get(step, "Your diet plan is ready")


@celery_app.task(
    name="chat_diet.generate",
    bind=True,
    max_retries=3,
    default_retry_delay=15,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def generate_chat_diet_plan_task(
    self,
    job_id: str,
    client_id: int,
    fingerprint: str,
    collected_data: Dict[str, Any],
    step: int = 0,
    parent_id: Optional[int] = None,
    feedback: Optional[str] = None,
):
    """Celery entry point. Sync wrapper that runs the async generation flow.

    For step==0 (initial), behaves exactly as before.
    For step in {1,2,3} (followup), pulls the parent plan from DB and feeds
    it as context to ``generate_followup_diet_plan``.
    """
    loop = get_worker_loop()
    try:
        return loop.run_until_complete(
            _run_generation(
                job_id=job_id,
                client_id=client_id,
                fingerprint=fingerprint,
                collected_data=collected_data,
                step=step,
                parent_id=parent_id,
                feedback=feedback,
            )
        )
    except Exception as exc:
        logger.error(
            "CHAT_DIET_TASK_ERROR",
            extra={
                "job_id": job_id,
                "client_id": client_id,
                "step": step,
                "attempt": self.request.retries,
                "error": repr(exc),
            },
        )
        if self.request.retries >= self.max_retries:
            try:
                loop.run_until_complete(
                    _finalize_failure(job_id, client_id, step, str(exc))
                )
            except Exception as final_exc:
                logger.error(
                    "CHAT_DIET_FINALIZE_FAILURE_ERROR",
                    extra={"job_id": job_id, "error": repr(final_exc)},
                )
        raise


async def _run_generation(
    job_id: str,
    client_id: int,
    fingerprint: str,
    collected_data: Dict[str, Any],
    step: int,
    parent_id: Optional[int],
    feedback: Optional[str],
) -> Dict[str, Any]:
    """Async generation flow. Owns its own DB session + Redis client."""
    SessionLocal = create_celery_async_sessionmaker()
    redis_client = await get_redis()

    async with SessionLocal() as db:
        repo = ChatDietRepository(db, redis_client)
        await repo.set_job_state(job_id, "processing")

        if step == 0:
            plan = await generate_diet_plan(collected_data)
        else:
            if parent_id is None:
                raise ValueError(
                    f"Followup task requires parent_id (step={step}, job_id={job_id})"
                )
            parent = await repo.fetch_plan_by_id(parent_id, client_id)
            if parent is None:
                raise ValueError(
                    f"Parent plan {parent_id} not found for client {client_id}"
                )
            plan = await generate_followup_diet_plan(
                data=collected_data,
                previous_plan=parent["plan"],
                step=step,
                feedback=feedback,
            )

        plan_id = await repo.persist_plan(
            client_id=client_id,
            fingerprint=fingerprint,
            collected_data=collected_data,
            plan=plan,
            model_used=PRIMARY_MODEL,
            step=step,
            parent_id=parent_id,
        )

        await repo.set_job_state(job_id, "complete", plan_id=plan_id)

        notif = ClientNotificationService()
        notif_result = await notif.send_notification(
            db=db,
            client_id=client_id,
            title=_notification_title(step),
            body="Tap to view your personalised 7-day plan.",
            data={
                "type": "chat_diet_plan_ready",
                "job_id": job_id,
                "plan_id": plan_id,
                "step": step,
            },
            channel_id=DIET_NOTIFICATION_CHANNEL,
        )

        logger.info(
            "CHAT_DIET_TASK_COMPLETED",
            extra={
                "job_id": job_id,
                "client_id": client_id,
                "plan_id": plan_id,
                "step": step,
                "notification": notif_result,
            },
        )

        return {
            "success": True,
            "job_id": job_id,
            "plan_id": plan_id,
            "step": step,
        }


async def _finalize_failure(
    job_id: str, client_id: int, step: int, error: str
) -> None:
    """Mark job failed + push a 'try again' notification."""
    SessionLocal = create_celery_async_sessionmaker()
    redis_client = await get_redis()
    async with SessionLocal() as db:
        repo = ChatDietRepository(db, redis_client)
        await repo.set_job_state(job_id, "failed", error=error)

        notif = ClientNotificationService()
        await notif.send_notification(
            db=db,
            client_id=client_id,
            title="Plan generation failed",
            body="We couldn't generate your diet plan. Please try again.",
            data={
                "type": "chat_diet_plan_failed",
                "job_id": job_id,
                "step": step,
            },
            channel_id=DIET_NOTIFICATION_CHANNEL,
        )


# ─── Convenience for callers (mirrors notification_tasks.py pattern) ───


def queue_chat_diet_generation(
    job_id: str,
    client_id: int,
    fingerprint: str,
    collected_data: Dict[str, Any],
    step: int = 0,
    parent_id: Optional[int] = None,
    feedback: Optional[str] = None,
) -> None:
    """Enqueue an async plan generation. Returns immediately."""
    generate_chat_diet_plan_task.delay(
        job_id=job_id,
        client_id=client_id,
        fingerprint=fingerprint,
        collected_data=collected_data,
        step=step,
        parent_id=parent_id,
        feedback=feedback,
    )
    logger.info(
        "CHAT_DIET_TASK_QUEUED",
        extra={
            "job_id": job_id,
            "client_id": client_id,
            "step": step,
            "parent_id": parent_id,
        },
    )

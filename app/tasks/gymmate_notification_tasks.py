"""Celery tasks for gym_mate push notifications.

Fire-and-forget from the web process:
    dispatch_gymmate_push.delay(notification_id, recipient_client_id,
                                title, body, data)

Failures retry automatically (Celery autoretry). Invalid FCM tokens
are dropped from the DB so we stop wasting send budget on them.
"""

import logging
from typing import Any, Dict, Optional

from app.celery_app import celery_app
from app.models.async_database import create_celery_async_sessionmaker
from app.utils.celery_asyncio import get_worker_loop


logger = logging.getLogger("tasks.gymmate_notifications")


@celery_app.task(
    name="gymmate.notifications.dispatch_push",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
)
def dispatch_gymmate_push_task(
    self,
    notification_id: int,
    recipient_client_id: int,
    title: str,
    body: Optional[str],
    data: Dict[str, Any],
):
    """Send the FCM multicast to all of `recipient_client_id`'s devices.

    Looks up live tokens from `fcm_tokens` in a fresh async DB session
    (the web request that triggered this task may already have closed
    its session), sends, and prunes any tokens FCM reported as dead.
    """
    try:
        loop = get_worker_loop()
        result = loop.run_until_complete(
            _dispatch_async(
                notification_id=notification_id,
                recipient_client_id=recipient_client_id,
                title=title,
                body=body,
                data=data or {},
            )
        )
        logger.info(
            "GYMMATE_PUSH_DISPATCHED",
            extra={
                "notification_id": notification_id,
                "recipient_client_id": recipient_client_id,
                "result": result,
            },
        )
        return result

    except Exception as exc:
        logger.error(
            "GYMMATE_PUSH_TASK_ERROR",
            extra={
                "notification_id": notification_id,
                "recipient_client_id": recipient_client_id,
                "error": repr(exc),
                "retry_count": self.request.retries,
            },
        )
        raise


async def _dispatch_async(
    notification_id: int,
    recipient_client_id: int,
    title: str,
    body: Optional[str],
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """The actual work: load tokens → send → prune invalid."""
    from app.fittbot_api.v2.Fymble.gym_mate.notifications._fcm import (
        send_multicast,
    )
    from app.fittbot_api.v2.Fymble.gym_mate.notifications._repository import (
        DeviceTokenRepository,
    )

    SessionLocal = create_celery_async_sessionmaker()
    async with SessionLocal() as db:
        tokens_repo = DeviceTokenRepository(db)
        tokens = await tokens_repo.list_tokens(recipient_client_id)
        if not tokens:
            return {
                "tokens": 0, "success": 0, "failure": 0, "dropped": 0,
            }

        # Tag every push with the notification id so the frontend can
        # mark-as-read directly when the user opens via the push.
        full_data = {**data, "notification_id": notification_id}
        success, failure, _errors, invalid = send_multicast(
            tokens, title=title, body=body, data=full_data,
        )

        dropped = 0
        if invalid:
            dropped = await tokens_repo.drop_invalid_tokens(invalid)
            await db.commit()

        return {
            "tokens": len(tokens),
            "success": success,
            "failure": failure,
            "dropped": dropped,
        }

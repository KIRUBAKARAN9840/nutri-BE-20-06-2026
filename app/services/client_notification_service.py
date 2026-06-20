"""Push notifications to clients via Expo SDK.

General-purpose: any feature can use this to push to a client (diet plan ready,
workout reminder, etc.). Mirrors OwnerNotificationService so the patterns line up.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from exponent_server_sdk import (
    PushClient,
    PushMessage,
    PushServerError,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import Client

logger = logging.getLogger("services.client_notification")


class ClientNotificationService:

    def __init__(self):
        self.push_client = PushClient()

    async def get_client_tokens(
        self, db: AsyncSession, client_id: int
    ) -> Tuple[List[str], Optional[str]]:
        """Return (tokens, client_name). Empty list if client has no registered devices."""
        client = (
            await db.execute(
                select(Client).where(Client.client_id == client_id)
            )
        ).scalars().first()

        if not client:
            logger.warning(
                "CLIENT_NOTIFICATION_CLIENT_NOT_FOUND",
                extra={"client_id": client_id},
            )
            return [], None

        tokens = client.expo_token
        if not tokens:
            logger.info(
                "CLIENT_NOTIFICATION_NO_TOKENS",
                extra={"client_id": client_id, "reason": "no expo tokens registered"},
            )
            return [], client.name

        if not isinstance(tokens, list):
            tokens = [tokens]
        tokens = [t for t in tokens if t]
        return tokens, client.name

    def send_push_notifications(
        self,
        tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        channel_id: str = "default",
    ) -> Dict[str, Any]:
        """Send push to multiple tokens. Returns sent/failed/invalid_tokens."""
        if not tokens:
            return {"sent": 0, "failed": 0, "invalid_tokens": []}

        messages = [
            PushMessage(
                to=token,
                title=title,
                body=body,
                sound="default",
                priority="high",
                channel_id=channel_id,
                data=data or {},
            )
            for token in tokens
        ]

        invalid_tokens: List[str] = []
        sent_count = 0
        failed_count = 0

        try:
            responses = self.push_client.publish_multiple(messages)
            for token, response in zip(tokens, responses):
                if response.status == "ok":
                    sent_count += 1
                else:
                    failed_count += 1
                    error_type = (
                        getattr(response.details, "error", None)
                        if response.details
                        else None
                    )
                    if error_type == "DeviceNotRegistered":
                        invalid_tokens.append(token)
                        logger.info(
                            "CLIENT_TOKEN_INVALID",
                            extra={"token": token[:20] + "...", "error": error_type},
                        )

        except PushServerError as exc:
            logger.error(
                "CLIENT_NOTIFICATION_PUSH_ERROR",
                extra={"error": repr(exc), "tokens_count": len(tokens)},
            )
            return {
                "sent": 0,
                "failed": len(tokens),
                "invalid_tokens": [],
                "error": str(exc),
            }

        return {
            "sent": sent_count,
            "failed": failed_count,
            "invalid_tokens": invalid_tokens,
        }

    async def cleanup_invalid_tokens(
        self, db: AsyncSession, client_id: int, invalid_tokens: List[str]
    ) -> None:
        """Strip DeviceNotRegistered tokens from client.expo_token."""
        if not invalid_tokens:
            return

        try:
            client = (
                await db.execute(
                    select(Client).where(Client.client_id == client_id)
                )
            ).scalars().first()
            if not client or not client.expo_token:
                return

            current_tokens = (
                client.expo_token
                if isinstance(client.expo_token, list)
                else [client.expo_token]
            )
            updated_tokens = [t for t in current_tokens if t and t not in invalid_tokens]

            await db.execute(
                update(Client)
                .where(Client.client_id == client_id)
                .values(expo_token=updated_tokens if updated_tokens else None)
            )
            await db.commit()

            logger.info(
                "CLIENT_TOKENS_CLEANED",
                extra={
                    "client_id": client_id,
                    "removed_count": len(invalid_tokens),
                    "remaining_count": len(updated_tokens),
                },
            )

        except Exception as exc:
            logger.warning(
                "CLIENT_TOKEN_CLEANUP_FAILED",
                extra={"client_id": client_id, "error": repr(exc)},
            )

    async def send_notification(
        self,
        db: AsyncSession,
        client_id: int,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        channel_id: str = "default",
    ) -> Dict[str, Any]:
        """Single entry point: fetch tokens, send, clean up invalid ones."""
        try:
            tokens, _ = await self.get_client_tokens(db, client_id)
            if not tokens:
                return {
                    "success": False,
                    "reason": "no_tokens",
                    "client_id": client_id,
                }

            result = self.send_push_notifications(
                tokens=tokens,
                title=title,
                body=body,
                data=data,
                channel_id=channel_id,
            )

            if result.get("invalid_tokens"):
                await self.cleanup_invalid_tokens(
                    db, client_id, result["invalid_tokens"]
                )

            logger.info(
                "CLIENT_NOTIFICATION_SENT",
                extra={
                    "client_id": client_id,
                    "channel_id": channel_id,
                    "sent": result.get("sent", 0),
                    "failed": result.get("failed", 0),
                },
            )

            return {
                "success": True,
                "client_id": client_id,
                "sent": result.get("sent", 0),
                "failed": result.get("failed", 0),
            }

        except Exception as exc:
            logger.error(
                "CLIENT_NOTIFICATION_ERROR",
                extra={"client_id": client_id, "error": repr(exc)},
            )
            return {
                "success": False,
                "reason": "exception",
                "error": str(exc),
            }

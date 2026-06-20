"""Redis pub/sub helpers for chat fan-out across stateless workers.

Channel layout:
    chat:user:{client_id}   per-user fan-in; every event addressed to a user
                            goes here regardless of room

One worker subscribes once via PSUBSCRIBE chat:user:* and routes inbound
payloads to whichever local WebSocket owns that client_id. The sender
worker does not need to know which worker hosts the receiver — the
recipient's worker picks the message up from Redis.
"""

import json
from typing import Iterable

from redis.asyncio import Redis


USER_CHANNEL_PREFIX = "chat:user:"
USER_CHANNEL_PATTERN = "chat:user:*"


def user_channel(client_id: int) -> str:
    return f"{USER_CHANNEL_PREFIX}{client_id}"


def client_id_from_channel(channel: str) -> int:
    return int(channel.removeprefix(USER_CHANNEL_PREFIX))


class ChatPublisher:
    """Publishes JSON-encoded events to per-user Redis channels."""

    def __init__(self, redis: Redis):
        self._redis = redis

    async def fan_out(self, recipient_ids: Iterable[int], event: dict) -> None:
        payload = json.dumps(event, default=str)
        for cid in recipient_ids:
            await self._redis.publish(user_channel(cid), payload)

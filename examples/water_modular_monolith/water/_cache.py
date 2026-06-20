"""Redis adapter for the water module.

Owns its own key namespace (`water:*`). The service never builds a
Redis key directly — it calls these methods. That means renaming a
key is a one-file change.
"""

import json
from datetime import date as date_type
from typing import Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError


def _status_key(client_id: int, today: date_type) -> str:
    return f"water:status:{client_id}:{today.isoformat()}"


def _reminder_key(client_id: int, today: date_type) -> str:
    return f"water:reminder:{client_id}:{today.isoformat()}"


class WaterCache:
    """Redis I/O for water tracking. Best-effort: failures degrade silently."""

    def __init__(self, redis: Redis):
        self._redis = redis

    async def get_status(
        self, client_id: int, today: date_type,
    ) -> Optional[dict]:
        return await self._get_json(_status_key(client_id, today))

    async def set_status(
        self, client_id: int, today: date_type, payload: dict, *, ttl: int,
    ) -> None:
        await self._set_json(_status_key(client_id, today), payload, ttl)

    async def get_reminder(
        self, client_id: int, today: date_type,
    ) -> Optional[dict]:
        return await self._get_json(_reminder_key(client_id, today))

    async def set_reminder(
        self, client_id: int, today: date_type, payload: dict, *, ttl: int,
    ) -> None:
        await self._set_json(_reminder_key(client_id, today), payload, ttl)

    async def invalidate(self, client_id: int, today: date_type) -> None:
        try:
            await self._redis.delete(
                _status_key(client_id, today),
                _reminder_key(client_id, today),
            )
        except RedisError:
            pass

    async def _get_json(self, key: str) -> Optional[dict]:
        try:
            raw = await self._redis.get(key)
            if raw:
                return json.loads(raw)
        except (RedisError, json.JSONDecodeError):
            pass
        return None

    async def _set_json(self, key: str, payload: dict, ttl: int) -> None:
        try:
            await self._redis.setex(key, ttl, json.dumps(payload))
        except (RedisError, TypeError, ValueError):
            pass

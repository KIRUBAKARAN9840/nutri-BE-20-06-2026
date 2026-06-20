import json
from typing import Optional

from redis.asyncio import Redis

from .schemas import HomeDTO


_PREFIX = "gymmate:home"
TTL_SECONDS = 60


def _round_geo(value: float) -> float:
    """~1km precision at the equator (2 decimal places)."""
    return round(value, 2)


def _key(client_id: int, lat: Optional[float] = None, lng: Optional[float] = None) -> str:
    if lat is None or lng is None:
        return f"{_PREFIX}:{client_id}"
    return f"{_PREFIX}:{client_id}:{_round_geo(lat)}:{_round_geo(lng)}"


class HomeCache:
    def __init__(self, redis: Optional[Redis]):
        self.redis = redis

    async def get(
        self,
        client_id: int,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
    ) -> Optional[HomeDTO]:
        if self.redis is None:
            return None
        raw = await self.redis.get(_key(client_id, lat, lng))
        if raw is None:
            return None
        try:
            return HomeDTO(**json.loads(raw))
        except (ValueError, TypeError):
            await self.redis.delete(_key(client_id, lat, lng))
            return None

    async def set(
        self,
        client_id: int,
        payload: HomeDTO,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
    ) -> None:
        if self.redis is None:
            return
        await self.redis.set(
            _key(client_id, lat, lng),
            payload.model_dump_json(),
            ex=TTL_SECONDS,
        )

    async def invalidate(self, client_id: int) -> None:

        if self.redis is None:
            return
        pattern = f"{_PREFIX}:{client_id}*"
        keys_to_delete = []
        async for key in self.redis.scan_iter(match=pattern, count=100):
            keys_to_delete.append(key)
            if len(keys_to_delete) >= 100:
                await self.redis.delete(*keys_to_delete)
                keys_to_delete = []
        if keys_to_delete:
            await self.redis.delete(*keys_to_delete)


def make_home_invalidator(redis: Optional[Redis]):
    """Returns an async callable suitable for `on_owner_change` in
    StoriesAPI factory: bumps the owner's home cache (all geo cells)."""
    cache = HomeCache(redis)

    async def invalidator(client_id: int) -> None:
        await cache.invalidate(client_id)

    return invalidator

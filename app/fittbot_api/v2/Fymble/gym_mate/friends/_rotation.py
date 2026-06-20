"""Per-client rotation cache for friend suggestions.

Goal: don't show the same 5 faces every time the user loads home /
View-all. We track who was recently surfaced in a Redis sorted set and
soft-exclude them from the next suggestion run for a configurable
window. If too few candidates remain after soft-excluding, the service
relaxes back to hard-exclusions only so the UI never goes empty.

Why a sorted set (not a plain set):
    - score = last-shown unix timestamp → cheap ZRANGEBYSCORE to fetch
      anyone shown within the rotation window
    - cheap ZREMRANGEBYSCORE to trim entries older than the retention
      cap → keeps the key from growing unboundedly per user
"""
import time
from typing import Iterable, Optional, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError


_PREFIX = "gymmate:friends:recent"
# Don't re-surface someone shown within this window.
RECENT_WINDOW_SECONDS = 60 * 60          # 1 hour
# Hard cap on the key's TTL so dormant users' state expires naturally.
KEY_TTL_SECONDS = 24 * 60 * 60           # 24 hours
# Cap the set size to avoid unbounded growth for very active users.
MAX_RECENT_ENTRIES = 200


def _key(client_id: int) -> str:
    return f"{_PREFIX}:{client_id}"


class RotationCache:
    """Tracks which suggestion ids each client was recently shown."""

    def __init__(self, redis: Optional[Redis]):
        self.redis = redis

    async def get_recently_shown(
        self,
        client_id: int,
        window_seconds: int = RECENT_WINDOW_SECONDS,
    ) -> Set[int]:
        """Return client_ids shown to this user within the window.

        Returns an empty set on any Redis error so a cache outage never
        breaks the suggestion endpoint.
        """
        if self.redis is None:
            return set()
        now = time.time()
        try:
            members = await self.redis.zrangebyscore(
                _key(client_id), min=now - window_seconds, max="+inf",
            )
        except RedisError:
            return set()
        return {int(m if isinstance(m, str) else m.decode()) for m in members}

    async def record_shown(
        self,
        client_id: int,
        shown_ids: Iterable[int],
    ) -> None:
        """Add the shown ids with the current timestamp. Best-effort:
        any Redis error is swallowed (rotation is a UX nicety, not
        correctness-critical)."""
        if self.redis is None:
            return
        ids = [int(i) for i in shown_ids]
        if not ids:
            return
        now = time.time()
        key = _key(client_id)
        try:
            pipe = self.redis.pipeline()
            pipe.zadd(key, {str(i): now for i in ids})
            # Trim entries older than the key's TTL window.
            pipe.zremrangebyscore(key, min=0, max=now - KEY_TTL_SECONDS)
            # Cap the set size (drop oldest if we grow beyond MAX_RECENT_ENTRIES).
            pipe.zremrangebyrank(key, 0, -(MAX_RECENT_ENTRIES + 1))
            pipe.expire(key, KEY_TTL_SECONDS)
            await pipe.execute()
        except RedisError:
            return

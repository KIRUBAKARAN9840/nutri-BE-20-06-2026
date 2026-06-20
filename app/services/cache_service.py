"""
Centralized Redis cache utilities.

Eliminates duplicated `delete_keys_by_pattern` and inline cache-aside logic
scattered across endpoint files.
"""

import json
import logging
from functools import wraps
from typing import Optional

from redis.asyncio import Redis

_log = logging.getLogger("app.services.cache_service")


# ── Key deletion ────────────────────────────────────────────

async def delete_keys_by_pattern(redis: Redis, pattern: str) -> int:
    """
    Delete all Redis keys matching *pattern*.

    Returns the number of keys deleted.
    """
    keys = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)
        return len(keys)
    return 0


async def delete_multiple_patterns(redis: Redis, *patterns: str) -> int:
    """Delete keys matching any of the given patterns. Returns total deleted."""
    total = 0
    for pattern in patterns:
        total += await delete_keys_by_pattern(redis, pattern)
    return total


# ── Cache-aside helpers ─────────────────────────────────────

async def cache_get_or_set(
    redis: Redis,
    key: str,
    fetch_fn,
    ttl: int = 86400,
) -> dict:
    """
    Cache-aside pattern: return cached value if present, otherwise call
    *fetch_fn* (an async callable returning a JSON-serialisable value),
    cache the result, and return it.
    """
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)

    data = await fetch_fn()
    await redis.set(key, json.dumps(data, default=str), ex=ttl)
    return data


async def cache_set(redis: Redis, key: str, data, ttl: int = 86400) -> None:
    """Store *data* in Redis under *key* with a TTL."""
    await redis.set(key, json.dumps(data, default=str), ex=ttl)


async def cache_invalidate(redis: Redis, *keys: str) -> int:
    """Delete one or more exact Redis keys. Returns the number deleted."""
    if not keys:
        return 0
    return await redis.delete(*keys)

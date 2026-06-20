# app/utils/ai_rate_limiter.py
"""
Per-user AI rate limiting.
Tracks daily AI API call count per user in Redis.
100 calls/day default, configurable via AI_RATE_LIMIT_PER_DAY env var.

Usage in FastAPI endpoints:
    # Option 1: As a route dependency (preferred - zero boilerplate)
    @router.get("/chat/stream", dependencies=[Depends(require_ai_rate_limit)])

    # Option 2: Inline for form-based endpoints where user_id is in body
    allowed, remaining = await check_ai_rate_limit(redis, client_id, settings.ai_rate_limit_per_day)
"""
import logging
from datetime import date
from typing import Tuple

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


async def check_ai_rate_limit(
    redis_client,
    user_id,
    daily_limit: int = 100,
) -> Tuple[bool, int]:
    """
    Check and increment per-user daily AI call counter.

    Args:
        redis_client: async Redis client
        user_id: user/client ID (int or str). If None/0, allows (backward compat).
        daily_limit: max AI calls per day per user (default 100)

    Returns:
        (allowed: bool, remaining: int)
        - allowed=True means the request can proceed
        - remaining is how many calls are left today
    """
    if not user_id:
        return True, daily_limit

    try:
        today = date.today().strftime("%Y-%m-%d")
        key = f"ai_calls:{user_id}:{today}"

        # Atomic increment
        count = await redis_client.incr(key)

        # Set expiry on first increment
        if count == 1:
            await redis_client.expire(key, 86400)  # 24 hours

        remaining = max(0, daily_limit - count)

        if count > daily_limit:
            # Undo the increment so the counter stays at limit
            await redis_client.decr(key)
            logger.warning(
                f"[AI Rate Limit] User {user_id} exceeded daily AI limit "
                f"({count}/{daily_limit})"
            )
            return False, 0

        return True, remaining

    except Exception as e:
        # Fail open - if Redis is down, allow the request
        logger.error(f"[AI Rate Limit] Redis error for user {user_id}: {e}")
        return True, daily_limit


def check_ai_rate_limit_sync(
    user_id,
    daily_limit: int = 100,
) -> Tuple[bool, int]:
    """
    Synchronous version for Celery tasks.
    Uses sync Redis client.

    Args:
        user_id: user/client ID
        daily_limit: max AI calls per day per user

    Returns:
        (allowed: bool, remaining: int)
    """
    if not user_id:
        return True, daily_limit

    try:
        from app.utils.redis_config import get_redis_sync

        redis_client = get_redis_sync()
        today = date.today().strftime("%Y-%m-%d")
        key = f"ai_calls:{user_id}:{today}"

        count = redis_client.incr(key)

        if count == 1:
            redis_client.expire(key, 86400)

        remaining = max(0, daily_limit - count)

        if count > daily_limit:
            redis_client.decr(key)
            logger.warning(
                f"[AI Rate Limit] User {user_id} exceeded daily AI limit "
                f"({count}/{daily_limit}) [sync]"
            )
            return False, 0

        return True, remaining

    except Exception as e:
        logger.error(f"[AI Rate Limit] Redis error for user {user_id} [sync]: {e}")
        return True, daily_limit


# ─── FASTAPI DEPENDENCY (centralized, enterprise-grade) ─────────────────────

async def require_ai_rate_limit(request: Request) -> None:
    """
    FastAPI dependency that enforces per-user AI rate limits.

    Automatically extracts user_id from query params (user_id or client_id).
    Raises HTTP 429 if the daily limit is exceeded.

    Usage:
        @router.get("/endpoint", dependencies=[Depends(require_ai_rate_limit)])
        async def my_endpoint(...):
            ...

    Skips silently when user_id cannot be determined (backward compat).
    For form-based endpoints (food scanner), use check_ai_rate_limit() inline.
    """
    from app.config.settings import settings
    from app.utils.redis_config import get_redis

    # Extract user_id from query params (covers GET and POST with query params)
    user_id_raw = (
        request.query_params.get("user_id")
        or request.query_params.get("client_id")
    )

    if not user_id_raw:
        return  # No user_id in query params → skip (food scanner uses form data)

    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        return

    if not user_id:
        return

    redis_client = await get_redis()

    allowed, remaining = await check_ai_rate_limit(
        redis_client, user_id, settings.ai_rate_limit_per_day
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Daily AI limit reached. Resets at midnight.",
        )

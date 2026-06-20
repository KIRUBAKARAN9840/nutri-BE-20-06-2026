# app/routers/websocket_feed.py
from __future__ import annotations

import os
import json
import asyncio
import time
from typing import Dict, List

from fastapi import APIRouter, Header, Depends
from fastapi import HTTPException, status
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from redis.asyncio import Redis

from app.utils.logging_utils import FittbotHTTPException
from app.utils.redis_config import get_redis
from app.services.websocket_hub import WebSocketHub

router = APIRouter(prefix="/websocket_feed", tags=["websocket_feed"])

# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────
from app.config.settings import settings
REDIS_DSN = os.getenv("WEBSOCKET_REDIS_DSN", settings.redis_url_resolved)
PING_SEC = 25  # heartbeat

# Globals (lazily initialized)
redis_pool: Redis | None = None
hub: WebSocketHub | None = None


async def create_redis() -> Redis:
    return Redis.from_url(REDIS_DSN, decode_responses=True)


async def ensure_hub() -> WebSocketHub:
    """Ensure Redis connection and WebSocketHub are ready and started."""
    global redis_pool, hub
    if redis_pool is None:
        redis_pool = await create_redis()
    if hub is None:
        hub = WebSocketHub(redis_pool, prefix="gym:")
        await hub.start()
    return hub

# Backward-compatible alias
RoomHub = WebSocketHub


# ──────────────────────────────────────────────────────────────
# HTTP endpoint to notify clients about new posts
# (used by internal producers like Lambdas / workers)
# ──────────────────────────────────────────────────────────────
@router.post("/internal/new_post", status_code=status.HTTP_202_ACCEPTED)
async def internal_new_post(
    payload: dict,
    x_api_key: str = Header(..., alias="x-api-key"),
):
    # Simple auth: shared header
    expected = os.getenv("LAMBDA_HEADER", "lambda_header_feed_not_out")
    if x_api_key != expected:
        raise FittbotHTTPException(
            status_code=401,
            detail="Invalid API key",
            error_code="WEBSOCKET_INVALID_API_KEY",
        )

    # Validate payload
    try:
        gym_id = int(payload["gym_id"])
        post_id = int(payload["post_id"])
    except Exception:
        raise FittbotHTTPException(
            status_code=422,
            detail="Invalid payload format (gym_id/post_id required and must be integers)",
            error_code="WEBSOCKET_INVALID_PAYLOAD",
        )

    # Ensure hub and publish
    try:
        _hub = await ensure_hub()
        await _hub.publish(
            gym_id, {"action": "new_post", "gym_id": gym_id, "post_id": post_id}
        )
        return {"ok": True}
    except FittbotHTTPException:
        raise
    except Exception as e:
        raise FittbotHTTPException(
            status_code=500,
            detail="Internal server error during post notification",
            error_code="WEBSOCKET_POST_NOTIFICATION_ERROR",
            log_data={"error": repr(e)},
        )


@router.post("/internal/invalidate_cache", status_code=status.HTTP_200_OK)
async def internal_invalidate_cache(
    payload: dict,
    x_api_key: str = Header(..., alias="x-api-key"),
    redis: Redis = Depends(get_redis),
):
    """
    Lambda endpoint to invalidate Redis cache after S3 upload completes.
    This prevents race condition where cache has local paths instead of S3 URLs.
    """
    # Simple auth: shared header
    expected = os.getenv("LAMBDA_HEADER", "lambda_header_feed_not_out")
    if x_api_key != expected:
        raise FittbotHTTPException(
            status_code=401,
            detail="Invalid API key",
            error_code="CACHE_INVALIDATION_INVALID_API_KEY",
        )

    # Validate payload
    try:
        gym_id = int(payload["gym_id"])
        post_id = int(payload["post_id"])
    except Exception:
        raise FittbotHTTPException(
            status_code=422,
            detail="Invalid payload format (gym_id/post_id required and must be integers)",
            error_code="CACHE_INVALIDATION_INVALID_PAYLOAD",
        )

    # Delete cache keys
    try:
        media_cache_key = f"post:{post_id}:media"
        gym_cache_key = f"gym:{gym_id}:posts"

        deleted_media = await redis.delete(media_cache_key)
        deleted_gym = await redis.delete(gym_cache_key)

        return {
            "ok": True,
            "deleted": {
                "media_cache": deleted_media,
                "gym_cache": deleted_gym
            }
        }
    except Exception as e:
        raise FittbotHTTPException(
            status_code=500,
            detail="Internal server error during cache invalidation",
            error_code="CACHE_INVALIDATION_ERROR",
            log_data={"error": repr(e)},
        )


# ──────────────────────────────────────────────────────────────
# WebSocket endpoint for clients to receive feed updates
# ──────────────────────────────────────────────────────────────
@router.websocket("/ws/posts/{gym_id}")
async def posts_ws(ws: WebSocket, gym_id: int):
    _hub = await ensure_hub()

    await ws.accept()
    await _hub.join(gym_id, ws)
    await ws.send_json({"action": "probe", "msg": "hello"})

    async def heartbeat():
        while ws.application_state == WebSocketState.CONNECTED:
            await asyncio.sleep(PING_SEC)
            try:
                await ws.send_json({"type": "ping"})
            except Exception:
                break

    hb_task = asyncio.create_task(heartbeat())
    try:
        while True:
            # We don't expect messages from client; just keep the socket open.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hb_task.cancel()
        await _hub._drop(gym_id, ws)

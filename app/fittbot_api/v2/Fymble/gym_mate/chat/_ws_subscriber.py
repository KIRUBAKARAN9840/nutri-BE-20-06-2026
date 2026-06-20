"""Worker-level Redis subscriber.

Each FastAPI worker runs a single background task that PSUBSCRIBEs to
`chat:user:*` and dispatches inbound JSON payloads to whichever local
WebSocket owns that client_id. Adding rooms or users does not add Redis
subscriptions — the wildcard pattern handles them all.

Lifecycle:
    start(redis) is called on FastAPI startup. The task runs until
    stop() is called on shutdown.
"""

import asyncio
import logging
from typing import Optional

from redis.asyncio import Redis

from ._connection_registry import registry
from ._pubsub import USER_CHANNEL_PATTERN, client_id_from_channel


logger = logging.getLogger("gymmate.chat.subscriber")


class ChatSubscriber:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    async def start(self, redis: Redis) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(redis), name="chat-subscriber")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self, redis: Redis) -> None:
        pubsub = redis.pubsub()
        try:
            await pubsub.psubscribe(USER_CHANNEL_PATTERN)
            logger.info("[chat] subscriber listening on %s", USER_CHANNEL_PATTERN)
            async for raw in pubsub.listen():
                if self._stopping.is_set():
                    break
                if raw is None or raw.get("type") not in ("pmessage", "message"):
                    continue
                await self._dispatch(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[chat] subscriber crashed")
        finally:
            try:
                await pubsub.punsubscribe(USER_CHANNEL_PATTERN)
                await pubsub.close()
            except Exception:
                pass

    async def _dispatch(self, raw: dict) -> None:
        channel = raw.get("channel")
        if isinstance(channel, bytes):
            channel = channel.decode()
        try:
            client_id = client_id_from_channel(channel)
        except Exception:
            return
        ws = registry.get(client_id)
        if ws is None:
            return
        data = raw.get("data")
        if isinstance(data, bytes):
            data = data.decode()
        try:
            await ws.send_text(data)
        except Exception:
            # Connection probably already closed; the WS handler will
            # detach on its own.
            logger.debug("[chat] send failed for client %s", client_id)


subscriber = ChatSubscriber()

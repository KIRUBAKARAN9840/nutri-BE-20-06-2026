"""
Base WebSocket hub for Redis pub/sub fan-out.

Replaces the near-identical RoomHub (websocket_feed.py) and PatternHub
(websocket_live_gb.py) with a single configurable class.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextlib import suppress
from typing import Dict, List, Optional

from starlette.websockets import WebSocket, WebSocketState
from redis.asyncio import Redis


class WebSocketHub:
    """
    A Redis pub/sub-backed WebSocket fan-out hub.

    *prefix* controls the Redis channel pattern:
      - ``prefix="gym:"``  → subscribes to ``gym:*``, extracts key from ``gym:<id>``
      - ``prefix="live:"`` → subscribes to ``live:*``, extracts key from ``live:<id>``

    Each unique integer key maps to a list of connected WebSocket clients.
    """

    def __init__(self, redis: Redis, prefix: str = "gym:") -> None:
        self._redis = redis
        self._prefix = prefix if prefix.endswith(":") else f"{prefix}:"
        self._conns: Dict[int, List[WebSocket]] = defaultdict(list)
        self._rx_task: Optional[asyncio.Task] = None

    # ── Lifecycle ───────────────────────────────────────────

    async def start(self) -> None:
        """Start the background fan-in task (idempotent)."""
        if self._rx_task and not self._rx_task.done():
            return
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(f"{self._prefix}*")
        self._rx_task = asyncio.create_task(self._fan_in(pubsub))

    # ── Fan-in / Fan-out ────────────────────────────────────

    async def _fan_in(self, pubsub) -> None:
        """Read from Redis pattern channel and fan out to room subscribers."""
        async for msg in pubsub.listen():
            if msg.get("type") != "pmessage":
                continue
            channel = msg.get("channel")
            payload = msg.get("data")
            if not isinstance(channel, str):
                continue
            key = self._extract_key(channel)
            if key is not None:
                await self._fan_out(key, payload)

    def _extract_key(self, channel: str) -> Optional[int]:
        """Extract the integer room/session key from a channel name."""
        try:
            suffix = channel[len(self._prefix):]
            return int(suffix)
        except (ValueError, IndexError):
            return None

    async def _fan_out(self, key: int, payload: str) -> None:
        """Send payload to all active connections for *key*. Drop dead ones."""
        stale: List[WebSocket] = []
        for ws in list(self._conns[key]):
            try:
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.leave(key, ws)

    # ── Connection management ───────────────────────────────

    async def join(self, key: int, ws: WebSocket) -> None:
        """Add a WebSocket to a room."""
        if ws not in self._conns[key]:
            self._conns[key].append(ws)

    async def leave(self, key: int, ws: WebSocket) -> None:
        """Remove a WebSocket from a room."""
        with suppress(ValueError):
            self._conns[key].remove(ws)

    # Alias used by websocket_feed.py (was `_drop`)
    _drop = leave

    async def publish(self, key: int, obj) -> None:
        """Publish a message to a room via Redis (all app instances receive it)."""
        payload = obj if isinstance(obj, str) else json.dumps(obj, default=str)
        await self._redis.publish(f"{self._prefix}{key}", payload)

"""In-memory map of client_id → WebSocket for the local worker.

Single-device policy: a new connection from the same client closes the
previous one with code 4001. The registry is process-local; cross-worker
delivery happens via Redis pub/sub, not via this map.
"""

import asyncio
import logging
from typing import Dict, Optional

from fastapi import WebSocket


logger = logging.getLogger("gymmate.chat.registry")

WS_CLOSE_REPLACED = 4001


class ChatConnectionRegistry:
    def __init__(self):
        self._conns: Dict[int, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def attach(self, client_id: int, ws: WebSocket) -> None:
        """Register the new WebSocket; close any prior connection for
        this client first. Held by lock so a fast reconnect doesn't race
        an in-flight detach."""
        async with self._lock:
            old = self._conns.get(client_id)
            self._conns[client_id] = ws
        if old is not None and old is not ws:
            try:
                await old.close(
                    code=WS_CLOSE_REPLACED, reason="replaced_by_new_connection",
                )
            except Exception as e:
                logger.debug("[chat] failed to close prior ws: %s", e)

    async def detach(self, client_id: int, ws: WebSocket) -> None:
        """Drop the entry only if it still points at this ws — avoids
        clobbering a freshly-attached replacement."""
        async with self._lock:
            if self._conns.get(client_id) is ws:
                self._conns.pop(client_id, None)

    def get(self, client_id: int) -> Optional[WebSocket]:
        return self._conns.get(client_id)

    def __len__(self) -> int:
        return len(self._conns)


# One registry per worker process.
registry = ChatConnectionRegistry()

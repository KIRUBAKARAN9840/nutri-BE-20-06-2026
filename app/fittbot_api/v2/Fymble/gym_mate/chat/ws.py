"""WebSocket endpoint for chat delivery.

Auth happens at the HTTP upgrade. Two paths are accepted:

    Native:   Authorization: Bearer <jwt>
    Browser:  Sec-WebSocket-Protocol: bearer.<jwt>

If neither validates, the upgrade is rejected before the handshake
completes — the WebSocket never enters the OPEN state unauthenticated.

The endpoint is push-only from the server. Clients may send `ping` and
`typing` frames; everything else is rejected.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.security import ALGORITHM, SECRET_KEY

from ._connection_registry import registry
from .api import build_chat_api


logger = logging.getLogger("gymmate.chat.ws")

ws_router = APIRouter()

BEARER_SUBPROTOCOL_PREFIX = "bearer."


def _extract_token(ws: WebSocket) -> tuple[Optional[str], Optional[str]]:
    """Return (token, accepted_subprotocol) or (None, None) if not found.
    The accepted subprotocol must be echoed back on accept() for the
    browser subprotocol path to succeed."""
    auth_header = ws.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip(), None

    raw = ws.headers.get("sec-websocket-protocol")
    if raw:
        for proto in [p.strip() for p in raw.split(",")]:
            if proto.startswith(BEARER_SUBPROTOCOL_PREFIX):
                return proto[len(BEARER_SUBPROTOCOL_PREFIX):], proto
    return None, None


def _decode_client_id(token: str) -> tuple[Optional[int], Optional[str]]:
    """Decode the JWT and extract client_id.

    Returns (client_id, error_reason). error_reason is set when decode
    fails — `expired`, `bad_signature`, `bad_claims`, `bad_subject`,
    `unknown`. We surface this to the WS log so a 403 storm in prod
    can be traced to the exact cause (stale token on FE vs malformed
    payload vs missing claim).
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        msg = str(exc).lower()
        if "expired" in msg:
            return None, "expired"
        if "signature" in msg:
            return None, "bad_signature"
        if "claim" in msg or "audience" in msg or "issuer" in msg:
            return None, "bad_claims"
        return None, f"jwt_error:{exc}"
    except Exception as exc:
        return None, f"unknown:{exc}"
    sub = payload.get("sub")
    if sub is None:
        return None, "bad_subject:missing_sub"
    try:
        return int(sub), None
    except (ValueError, TypeError):
        return None, f"bad_subject:not_int:{sub!r}"


@ws_router.websocket("/api/v2/gym_mate/chat/ws")
async def chat_ws(
    ws: WebSocket,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
):
    token, accepted_proto = _extract_token(ws)
    if token is None:
        logger.info(
            "[chat] ws rejected reason=missing_auth headers=%s",
            {k: v for k, v in ws.headers.items() if k.lower() in (
                "authorization", "sec-websocket-protocol", "user-agent",
            )},
        )
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing_auth")
        return
    client_id, decode_err = _decode_client_id(token)
    if client_id is None:
        logger.info(
            "[chat] ws rejected reason=%s token_prefix=%s",
            decode_err, (token[:12] + "...") if token else "",
        )
        # Specific close reason so the FE can decide: refresh the access
        # token and reconnect (expired/bad_signature/bad_claims) vs
        # surface a real auth error to the user (bad_subject/unknown).
        close_reason = decode_err or "invalid_auth"
        # Cap close-reason to 123 bytes per RFC 6455.
        close_reason = close_reason[:120]
        await ws.close(
            code=status.WS_1008_POLICY_VIOLATION, reason=close_reason,
        )
        return

    if accepted_proto:
        await ws.accept(subprotocol=accepted_proto)
    else:
        await ws.accept()

    await registry.attach(client_id, ws)
    api = build_chat_api(db, redis)
    logger.info("[chat] ws connected client_id=%s", client_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ftype = frame.get("type")
            if ftype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            elif ftype == "typing":
                room_id = frame.get("room_id")
                if isinstance(room_id, int):
                    try:
                        await api.typing(viewer_client_id=client_id, room_id=room_id)
                    except Exception:
                        # Failures here are best-effort; don't tear the WS.
                        logger.debug("[chat] typing publish failed")
            else:
                # Unknown client → server frame; ignore silently so the
                # contract can evolve without breaking older clients.
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("[chat] ws loop crashed client_id=%s", client_id)
    finally:
        await registry.detach(client_id, ws)
        logger.info("[chat] ws closed client_id=%s", client_id)

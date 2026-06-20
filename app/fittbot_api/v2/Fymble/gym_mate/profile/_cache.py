

from __future__ import annotations

import json
from typing import Optional

from redis.asyncio import Redis

from .schemas import OnboardingStatusDTO, ProfileMatchAttributesDTO, ProfileSummaryDTO


# ---------------------------------------------------------------------------
# Key schema + TTLs
# ---------------------------------------------------------------------------
_PREFIX = "gymmate:profile"
TTL_STATUS_SECONDS = 300            # 5 min — invalidated on writes anyway
TTL_MATCH_ATTRS_SECONDS = 300       # 5 min
TTL_SUMMARY_SECONDS = 300           # 5 min


def _status_key(client_id: int) -> str:
    return f"{_PREFIX}:status:{client_id}"


def _match_attrs_key(client_id: int) -> str:
    return f"{_PREFIX}:match_attrs:{client_id}"


def _summary_key(client_id: int) -> str:
    return f"{_PREFIX}:summary:{client_id}"


# ---------------------------------------------------------------------------
# Cache adapter
# ---------------------------------------------------------------------------
class ProfileCache:
    def __init__(self, redis: Optional[Redis]):
        # redis is Optional so unit tests can pass None and skip caching.
        self.redis = redis

    # ---------------- status ----------------
    async def get_status(self, client_id: int) -> Optional[OnboardingStatusDTO]:
        if self.redis is None:
            return None
        raw = await self.redis.get(_status_key(client_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return OnboardingStatusDTO(**data)
        except (ValueError, TypeError):
            # Corrupt cache entry — drop it and miss through.
            await self.redis.delete(_status_key(client_id))
            return None

    async def set_status(self, dto: OnboardingStatusDTO) -> None:
        if self.redis is None:
            return
        await self.redis.set(
            _status_key(dto.client_id),
            dto.model_dump_json(),
            ex=TTL_STATUS_SECONDS,
        )

    # ---------------- match attrs ----------------
    async def get_match_attrs(
        self, client_id: int
    ) -> Optional[ProfileMatchAttributesDTO]:
        if self.redis is None:
            return None
        raw = await self.redis.get(_match_attrs_key(client_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return ProfileMatchAttributesDTO(**data)
        except (ValueError, TypeError):
            await self.redis.delete(_match_attrs_key(client_id))
            return None

    async def set_match_attrs(self, dto: ProfileMatchAttributesDTO) -> None:
        if self.redis is None:
            return
        await self.redis.set(
            _match_attrs_key(dto.client_id),
            dto.model_dump_json(),
            ex=TTL_MATCH_ATTRS_SECONDS,
        )

    # ---------------- summary ----------------
    async def get_summary(
        self, client_id: int
    ) -> Optional[ProfileSummaryDTO]:
        if self.redis is None:
            return None
        raw = await self.redis.get(_summary_key(client_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return ProfileSummaryDTO(**data)
        except (ValueError, TypeError):
            await self.redis.delete(_summary_key(client_id))
            return None

    async def set_summary(self, dto: ProfileSummaryDTO) -> None:
        if self.redis is None:
            return
        await self.redis.set(
            _summary_key(dto.client_id),
            dto.model_dump_json(),
            ex=TTL_SUMMARY_SECONDS,
        )

    # ---------------- invalidation ----------------
    async def invalidate(self, client_id: int) -> None:
        """Delete every cached entry for this client. Idempotent — safe to
        call even if no entries exist."""
        if self.redis is None:
            return
        await self.redis.delete(
            _status_key(client_id),
            _match_attrs_key(client_id),
            _summary_key(client_id),
        )

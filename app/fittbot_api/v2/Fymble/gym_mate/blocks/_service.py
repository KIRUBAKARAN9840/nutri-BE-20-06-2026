from datetime import datetime
from typing import List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import EventBus, UserBlocked, UserUnblocked
from ._repository import BlockRepository


def _avatar_url_or_none(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    from app.fittbot_api.v2.Fymble.gym_mate.stories._storage import build_cdn_url
    return build_cdn_url(value)


class BlockService:
    def __init__(
        self,
        repository: BlockRepository,
        event_bus: EventBus,
        on_change=None,
    ):
        self.repo = repository
        self.bus = event_bus
        self._on_change = on_change

    async def _notify(self, client_id: int) -> None:
        if self._on_change is None:
            return
        try:
            await self._on_change(client_id)
        except Exception:
            pass

    async def block(self, blocker_id: int, blocked_id: int) -> None:
        try:
            block = d.Block.new(blocker_id, blocked_id)
        except d.CannotBlockSelf as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_BLOCK_SELF",
                log_data={"client_id": blocker_id},
            )

        inserted = await self.repo.add(
            block.blocker_client_id, block.blocked_client_id, block.created_at
        )
        if inserted:
            # Bidirectional cascade: friendship gone, pending friend +
            # session requests cancelled. Mirrors big-tech behavior so
            # the pair vanishes from each other's view of the app.
            await self.repo.cascade_cleanup_on_block(
                blocker_id, blocked_id, block.created_at,
            )
            await self.bus.publish(UserBlocked(
                blocker_client_id=blocker_id,
                blocked_client_id=blocked_id,
            ))
            # Invalidate caches for BOTH sides — the blocked user's
            # home/inbox/suggestion lists now exclude the blocker too.
            await self._notify(blocker_id)
            await self._notify(blocked_id)

    async def unblock(self, blocker_id: int, blocked_id: int) -> None:
        removed = await self.repo.remove(blocker_id, blocked_id)
        if removed:
            await self.bus.publish(UserUnblocked(
                blocker_client_id=blocker_id,
                blocked_client_id=blocked_id,
            ))
            await self._notify(blocker_id)
            await self._notify(blocked_id)

    async def list_blocked(self, blocker_id: int) -> List[dto.BlockedUserDTO]:
        rows = await self.repo.list_blocked_by(blocker_id)
        return [
            dto.BlockedUserDTO(
                block_id=r["block_id"],
                client_id=r["client_id"],
                name=r["name"],
                avatar_url=_avatar_url_or_none(r["avatar_url"]),
                blocked_at=r["blocked_at"],
            )
            for r in rows
        ]

    async def is_blocked_either_way(self, a: int, b: int) -> bool:
        return await self.repo.is_blocked_either_way(a, b)

    async def get_blocked_ids(self, blocker_id: int) -> List[int]:
        return await self.repo.get_blocked_ids(blocker_id)

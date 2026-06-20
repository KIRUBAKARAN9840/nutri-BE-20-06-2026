from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class BlockDomainError(Exception):
    pass


class CannotBlockSelf(BlockDomainError):
    pass


@dataclass(frozen=True)
class Block:
    blocker_client_id: int
    blocked_client_id: int
    created_at: datetime
    id: Optional[int] = None

    @classmethod
    def new(cls, blocker_id: int, blocked_id: int, now: Optional[datetime] = None) -> "Block":
        if blocker_id == blocked_id:
            raise CannotBlockSelf("You cannot block yourself")
        return cls(
            blocker_client_id=blocker_id,
            blocked_client_id=blocked_id,
            created_at=now or datetime.now(),
        )

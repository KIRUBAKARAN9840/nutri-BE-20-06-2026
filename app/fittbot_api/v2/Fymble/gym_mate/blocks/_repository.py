from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gymmate import (
    GymMateBlock as BlockORM,
    GymMateFriendRequest as FriendRequestORM,
    GymMateFriendship as FriendshipORM,
    GymMateSessionRequest as SessionRequestORM,
)


class BlockRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, blocker_id: int, blocked_id: int, when: datetime) -> bool:
        """INSERT IGNORE — returns True if a new row was inserted."""
        stmt = mysql_insert(BlockORM).values(
            blocker_client_id=blocker_id,
            blocked_client_id=blocked_id,
            created_at=when,
        ).prefix_with("IGNORE")
        result = await self.db.execute(stmt)
        return bool(result.rowcount)

    async def remove(self, blocker_id: int, blocked_id: int) -> bool:
        """DELETE — returns True if a row was removed."""
        result = await self.db.execute(
            delete(BlockORM).where(
                (BlockORM.blocker_client_id == blocker_id)
                & (BlockORM.blocked_client_id == blocked_id)
            )
        )
        return bool(result.rowcount)

    async def list_blocked_by(self, blocker_id: int) -> List[dict]:
        """All users this blocker has blocked, with name + avatar."""
        stmt = (
            select(
                BlockORM.id,
                BlockORM.blocked_client_id,
                BlockORM.created_at,
                ClientORM.name,
                ClientORM.profile,
            )
            .join(ClientORM, ClientORM.client_id == BlockORM.blocked_client_id, isouter=True)
            .where(BlockORM.blocker_client_id == blocker_id)
            .order_by(BlockORM.created_at.desc())
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "block_id": r.id,
                "client_id": r.blocked_client_id,
                "name": r.name,
                "avatar_url": r.profile,
                "blocked_at": r.created_at,
            }
            for r in rows
        ]

    async def is_blocked_either_way(self, a: int, b: int) -> bool:
        stmt = (
            select(BlockORM.id)
            .where(
                or_(
                    and_(BlockORM.blocker_client_id == a, BlockORM.blocked_client_id == b),
                    and_(BlockORM.blocker_client_id == b, BlockORM.blocked_client_id == a),
                )
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).first() is not None

    async def get_blocked_ids(self, blocker_id: int) -> List[int]:
        stmt = select(BlockORM.blocked_client_id).where(
            BlockORM.blocker_client_id == blocker_id
        )
        return [r[0] for r in (await self.db.execute(stmt)).all()]

    async def get_bidirectional_block_ids(self, client_id: int) -> set:
        """All client_ids in a block pair with this client, in either
        direction. Use as an exclusion set when listing peers across
        any surface (inbox, friends, suggestions, matches, etc.)."""
        stmt = select(
            BlockORM.blocker_client_id,
            BlockORM.blocked_client_id,
        ).where(
            or_(
                BlockORM.blocker_client_id == client_id,
                BlockORM.blocked_client_id == client_id,
            )
        )
        out: set = set()
        for r in (await self.db.execute(stmt)).all():
            other = r.blocked_client_id if r.blocker_client_id == client_id \
                else r.blocker_client_id
            out.add(other)
        return out

    async def cascade_cleanup_on_block(
        self, blocker_id: int, blocked_id: int, now: datetime,
    ) -> dict:
        """Atomically clean up shared state between a pair after one
        side blocks the other. Mirrors big-tech behavior: the pair
        disappears from each other's view of the app.

        Returns counts so the service can publish a richer event /
        log what was cleaned.
        """
        a, b = (blocker_id, blocked_id) if blocker_id < blocked_id \
            else (blocked_id, blocker_id)

        # 1. Drop the friendship row (canonical pair is min-max).
        friendship_res = await self.db.execute(
            delete(FriendshipORM).where(
                FriendshipORM.client_a_id == a,
                FriendshipORM.client_b_id == b,
            )
        )

        # 2. Cancel any pending friend requests between the pair, either direction.
        friend_req_res = await self.db.execute(
            update(FriendRequestORM)
            .where(
                FriendRequestORM.status == "pending",
                or_(
                    and_(
                        FriendRequestORM.from_client_id == blocker_id,
                        FriendRequestORM.to_client_id == blocked_id,
                    ),
                    and_(
                        FriendRequestORM.from_client_id == blocked_id,
                        FriendRequestORM.to_client_id == blocker_id,
                    ),
                ),
            )
            .values(status="cancelled", responded_at=now)
        )

        # 3. Withdraw pending session join requests between the pair.
        #    (Already-accepted session members keep their rows — the
        #     session itself is group-context and stays public.)
        session_req_res = await self.db.execute(
            update(SessionRequestORM)
            .where(
                SessionRequestORM.status == "pending",
                or_(
                    and_(
                        SessionRequestORM.requester_client_id == blocker_id,
                        SessionRequestORM.host_client_id == blocked_id,
                    ),
                    and_(
                        SessionRequestORM.requester_client_id == blocked_id,
                        SessionRequestORM.host_client_id == blocker_id,
                    ),
                ),
            )
            .values(status="withdrawn", responded_at=now)
        )

        return {
            "friendship_removed": bool(friendship_res.rowcount),
            "friend_requests_cancelled": int(friend_req_res.rowcount or 0),
            "session_requests_withdrawn": int(session_req_res.rowcount or 0),
        }

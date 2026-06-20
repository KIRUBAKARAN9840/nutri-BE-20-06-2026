from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gym import Gym as GymORM, GymStudiosPic as GymCoverORM
from app.models.fittbot_models.gymmate import (
    GymMateBlock as BlockORM,
    GymMateFriendship as FriendshipORM,
    GymMateProfile as GymMateProfileORM,
    GymMateProfilePhoto as GymMatePhotoORM,
    GymMateSession as SessionORM,
    GymMateSessionMember as SessionMemberORM,
)
from app.models.fittbot_models.gymmate_chat import (
    GymMateChatMessage as MessageORM,
    GymMateChatParticipant as ParticipantORM,
    GymMateChatRoom as RoomORM,
)

from . import _domain as d


class ChatRoomRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, room_id: int) -> Optional[d.Room]:
        row = (await self.db.execute(
            select(RoomORM).where(RoomORM.id == room_id)
        )).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def find_direct(
        self,
        kind: d.ChatRoomKind,
        pair_key: str,
        session_id: Optional[int] = None,
    ) -> Optional[d.Room]:
        stmt = select(RoomORM).where(
            RoomORM.kind == kind.value,
            RoomORM.pair_key == pair_key,
        )
        if session_id is None:
            stmt = stmt.where(RoomORM.session_id.is_(None))
        else:
            stmt = stmt.where(RoomORM.session_id == session_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def find_session_group(self, session_id: int) -> Optional[d.Room]:
        row = (await self.db.execute(
            select(RoomORM).where(
                RoomORM.kind == d.ChatRoomKind.SESSION_GROUP.value,
                RoomORM.session_id == session_id,
            )
        )).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def add(self, room: d.Room) -> d.Room:
        orm = RoomORM(
            kind=room.kind.value,
            session_id=room.session_id,
            pair_key=room.pair_key,
        )
        self.db.add(orm)
        await self.db.flush()
        room.id = orm.id
        room.created_at = orm.created_at
        return room

    async def update_last_message(
        self, room_id: int, message_id: int, message_at: datetime,
    ) -> None:
        await self.db.execute(
            update(RoomORM)
            .where(RoomORM.id == room_id)
            .values(last_message_id=message_id, last_message_at=message_at)
        )

    async def list_inbox(
        self,
        client_id: int,
        before_at: Optional[datetime] = None,
        limit: int = 30,
    ) -> List[dict]:
        """Rooms the client participates in, with last-message preview +
        unread count. Sorted by most-recent activity first.

        Sessions whose status is not 'open' are dropped on the read path
        — DB rows stay (audit), but users lose access once the session
        ends.

        Cursor pagination: pass `before_at` (the last item's
        `last_message_at` from the previous page) to fetch the next
        page. `limit` is clamped server-side.
        """
        last_msg = (
            select(
                MessageORM.id,
                MessageORM.room_id,
                MessageORM.sender_client_id,
                MessageORM.body,
                MessageORM.kind,
                MessageORM.created_at,
                MessageORM.deleted_at,
            )
            .where(MessageORM.id == RoomORM.last_message_id)
            .subquery()
        )

        unread_subq = (
            select(func.count(MessageORM.id))
            .where(
                MessageORM.room_id == RoomORM.id,
                MessageORM.sender_client_id != client_id,
                MessageORM.deleted_at.is_(None),
                or_(
                    ParticipantORM.last_read_message_id.is_(None),
                    MessageORM.id > ParticipantORM.last_read_message_id,
                ),
            )
            .correlate(RoomORM, ParticipantORM)
            .scalar_subquery()
        )

        stmt = (
            select(
                RoomORM.id,
                RoomORM.kind,
                RoomORM.session_id,
                RoomORM.pair_key,
                RoomORM.last_message_at,
                last_msg.c.id.label("lm_id"),
                last_msg.c.sender_client_id.label("lm_sender"),
                last_msg.c.body.label("lm_body"),
                last_msg.c.kind.label("lm_kind"),
                last_msg.c.created_at.label("lm_created_at"),
                last_msg.c.deleted_at.label("lm_deleted_at"),
                unread_subq.label("unread_count"),
                SessionORM.status.label("session_status"),
                SessionORM.session_date.label("session_date"),
            )
            .select_from(ParticipantORM)
            .join(RoomORM, RoomORM.id == ParticipantORM.room_id)
            .outerjoin(last_msg, last_msg.c.room_id == RoomORM.id)
            .outerjoin(SessionORM, SessionORM.id == RoomORM.session_id)
            .where(ParticipantORM.client_id == client_id)
        )
        if before_at is not None:
            # Pull the next page: rooms older than the previous tail.
            stmt = stmt.where(RoomORM.last_message_at < before_at)
        # MySQL doesn't support `NULLS LAST`; emulate via IS NULL sort key
        # so rooms with no messages yet sink to the bottom.
        stmt = stmt.order_by(
            RoomORM.last_message_at.is_(None).asc(),
            RoomORM.last_message_at.desc(),
            RoomORM.id.desc(),
        ).limit(max(1, min(limit, 50)) + 1)
        rows = (await self.db.execute(stmt)).all()
        from datetime import date as _date
        today = _date.today()
        out: List[dict] = []
        for r in rows:
            if r.session_id is not None:
                if r.session_status not in (None, "open"):
                    continue
                if r.session_date is not None and r.session_date < today:
                    continue
            out.append({
                "room_id": r.id,
                "kind": r.kind,
                "session_id": r.session_id,
                "pair_key": r.pair_key,
                "last_message_at": r.last_message_at,
                "last_message": {
                    "id": r.lm_id,
                    "sender_client_id": r.lm_sender,
                    "body": "[deleted]" if r.lm_deleted_at else r.lm_body,
                    "kind": r.lm_kind,
                    "created_at": r.lm_created_at,
                    "is_deleted": r.lm_deleted_at is not None,
                } if r.lm_id else None,
                "unread_count": int(r.unread_count or 0),
            })
        return out

    async def list_recent_friends(
        self, client_id: int, limit: int = 5,
    ) -> List[dict]:
        """Most-recently-friended users for the quick-start rail.
        Friendship rows are canonicalised (smaller_id, larger_id) so we
        pick whichever side isn't the viewer."""
        friend_id_expr = case(
            (FriendshipORM.client_a_id == client_id, FriendshipORM.client_b_id),
            else_=FriendshipORM.client_a_id,
        )
        stmt = (
            select(
                friend_id_expr.label("friend_id"),
                FriendshipORM.created_at.label("friended_at"),
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(FriendshipORM)
            .join(ClientORM, ClientORM.client_id == friend_id_expr)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == friend_id_expr,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                or_(
                    FriendshipORM.client_a_id == client_id,
                    FriendshipORM.client_b_id == client_id,
                )
            )
            .order_by(FriendshipORM.created_at.desc())
            .limit(max(1, min(limit, 20)))
        )
        rows = (await self.db.execute(stmt)).all()
        seen: set = set()
        out: List[dict] = []
        for r in rows:
            if r.friend_id in seen:
                continue
            seen.add(r.friend_id)
            out.append({
                "client_id": r.friend_id,
                "name": r.name,
                "avatar_url": r.avatar,
                "friended_at": r.friended_at,
            })
        return out

    async def fetch_peers_for_rooms(
        self, viewer_client_id: int, room_ids: List[int],
    ) -> dict:
        """For 1:1 rooms, return {room_id: {client_id, name, avatar_url}}
        — the OTHER participant. Avatar prefers gym_mate primary photo,
        falls back to clients.profile."""
        if not room_ids:
            return {}
        stmt = (
            select(
                ParticipantORM.room_id,
                ParticipantORM.client_id,
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(ParticipantORM)
            .join(ClientORM, ClientORM.client_id == ParticipantORM.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ParticipantORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(
                ParticipantORM.room_id.in_(room_ids),
                ParticipantORM.client_id != viewer_client_id,
            )
        )
        rows = (await self.db.execute(stmt)).all()
        # If a participant joined under multiple photo rows (defensive),
        # keep the first one — primary photo uniqueness is enforced upstream.
        peers: dict = {}
        for r in rows:
            if r.room_id in peers:
                continue
            peers[r.room_id] = {
                "client_id": r.client_id,
                "name": r.name,
                "avatar_url": r.avatar,
            }
        return peers

    async def fetch_groups_for_sessions(
        self, session_ids: List[int],
    ) -> dict:
        """For session_group rooms, return {session_id: group_dict} with
        gym info + session date/time + member count + up to 3 avatars
        for stacked display."""
        if not session_ids:
            return {}
        # Session + gym info in one query. Cover pic comes from
        # gym_studios_pic (type='cover_pic') — same source the inside-
        # chat room API uses via get_session_meta. The legacy
        # `gyms.cover_pic` column is NOT used here so the inbox card
        # and the chat-thread header always agree on the picture.
        info_stmt = (
            select(
                SessionORM.id.label("session_id"),
                SessionORM.session_date,
                SessionORM.session_time,
                SessionORM.gym_id,
                GymORM.name.label("gym_name"),
                GymORM.area.label("gym_area"),
                GymORM.city.label("gym_city"),
                GymCoverORM.image_url.label("gym_cover_pic"),
            )
            .select_from(SessionORM)
            .outerjoin(GymORM, GymORM.gym_id == SessionORM.gym_id)
            .outerjoin(
                GymCoverORM,
                and_(
                    GymCoverORM.gym_id == SessionORM.gym_id,
                    GymCoverORM.type == "cover_pic",
                ),
            )
            .where(SessionORM.id.in_(session_ids))
        )
        info_rows = (await self.db.execute(info_stmt)).all()
        out: dict = {}
        for r in info_rows:
            out[r.session_id] = {
                "session_id": r.session_id,
                "gym_id": r.gym_id,
                "gym_name": r.gym_name,
                "gym_area": (r.gym_area or r.gym_city) or None,
                "gym_cover_pic": r.gym_cover_pic,
                "session_date": r.session_date.isoformat() if r.session_date else None,
                "session_time": r.session_time.isoformat() if r.session_time else None,
                "member_count": 0,
                "member_avatars": [],
            }

        # Member counts.
        count_stmt = (
            select(
                SessionMemberORM.session_id,
                func.count(SessionMemberORM.id).label("cnt"),
            )
            .where(SessionMemberORM.session_id.in_(session_ids))
            .group_by(SessionMemberORM.session_id)
        )
        for r in (await self.db.execute(count_stmt)).all():
            if r.session_id in out:
                out[r.session_id]["member_count"] = int(r.cnt or 0)

        # Top-3 member avatars per session for stacked display.
        avatar_stmt = (
            select(
                SessionMemberORM.session_id,
                SessionMemberORM.client_id,
                SessionMemberORM.joined_at,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(SessionMemberORM)
            .join(ClientORM, ClientORM.client_id == SessionMemberORM.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == SessionMemberORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(SessionMemberORM.session_id.in_(session_ids))
            .order_by(
                SessionMemberORM.session_id.asc(),
                SessionMemberORM.joined_at.asc(),
            )
        )
        for r in (await self.db.execute(avatar_stmt)).all():
            bucket = out.get(r.session_id)
            if bucket is None:
                continue
            if len(bucket["member_avatars"]) >= 3:
                continue
            if r.avatar:
                bucket["member_avatars"].append(r.avatar)
        return out

    @staticmethod
    def _to_domain(row: RoomORM) -> d.Room:
        return d.Room(
            id=row.id,
            kind=d.ChatRoomKind(row.kind),
            session_id=row.session_id,
            pair_key=row.pair_key,
            last_message_id=row.last_message_id,
            last_message_at=row.last_message_at,
            created_at=row.created_at,
        )


class ChatParticipantRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, room_id: int, client_id: int) -> None:
        """INSERT IGNORE so adding a participant is idempotent."""
        stmt = mysql_insert(ParticipantORM).values(
            room_id=room_id,
            client_id=client_id,
            joined_at=datetime.now(),
        ).prefix_with("IGNORE")
        await self.db.execute(stmt)

    async def add_many(self, room_id: int, client_ids: List[int]) -> None:
        if not client_ids:
            return
        for cid in client_ids:
            await self.add(room_id, cid)

    async def list_members(self, room_id: int) -> List[int]:
        rows = (await self.db.execute(
            select(ParticipantORM.client_id).where(
                ParticipantORM.room_id == room_id,
            )
        )).all()
        return [r.client_id for r in rows]

    async def is_member(self, room_id: int, client_id: int) -> bool:
        row = (await self.db.execute(
            select(ParticipantORM.id).where(
                ParticipantORM.room_id == room_id,
                ParticipantORM.client_id == client_id,
            )
        )).first()
        return row is not None

    async def remove(self, room_id: int, client_id: int) -> bool:
        """Drop a participant from a room. Returns True if a row was
        deleted. Used when a member leaves a group chat — the
        session_member row is dropped by the service in the same
        transaction so the user vanishes from the workout entirely."""
        from sqlalchemy import delete
        res = await self.db.execute(
            delete(ParticipantORM).where(
                ParticipantORM.room_id == room_id,
                ParticipantORM.client_id == client_id,
            )
        )
        return bool(res.rowcount)

    async def mark_read(
        self, room_id: int, client_id: int, up_to_message_id: int,
    ) -> None:
        await self.db.execute(
            update(ParticipantORM)
            .where(
                ParticipantORM.room_id == room_id,
                ParticipantORM.client_id == client_id,
                or_(
                    ParticipantORM.last_read_message_id.is_(None),
                    ParticipantORM.last_read_message_id < up_to_message_id,
                ),
            )
            .values(last_read_message_id=up_to_message_id)
        )

    async def list_member_profiles(self, room_id: int) -> List[dict]:
        """Name + avatar for everyone in the room, in join order."""
        stmt = (
            select(
                ParticipantORM.client_id,
                ParticipantORM.joined_at,
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(ParticipantORM)
            .join(ClientORM, ClientORM.client_id == ParticipantORM.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ParticipantORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(ParticipantORM.room_id == room_id)
            .order_by(ParticipantORM.joined_at.asc())
        )
        rows = (await self.db.execute(stmt)).all()
        seen: set = set()
        out: List[dict] = []
        for r in rows:
            if r.client_id in seen:
                continue
            seen.add(r.client_id)
            out.append({
                "client_id": r.client_id,
                "name": r.name,
                "avatar_url": r.avatar,
                "joined_at": r.joined_at,
            })
        return out


class ChatMessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, msg: d.Message) -> d.Message:
        """Insert a message. If a row already exists for the same
        (room_id, client_msg_id), return the existing row instead — the
        UNIQUE index lets us tell a retry from a fresh send."""
        if msg.client_msg_id:
            existing = (await self.db.execute(
                select(MessageORM).where(
                    MessageORM.room_id == msg.room_id,
                    MessageORM.client_msg_id == msg.client_msg_id,
                )
            )).scalar_one_or_none()
            if existing is not None:
                return self._to_domain(existing)

        orm = MessageORM(
            room_id=msg.room_id,
            sender_client_id=msg.sender_client_id,
            body=msg.body.value,
            kind=msg.kind.value,
            client_msg_id=msg.client_msg_id,
            created_at=datetime.now(),
        )
        self.db.add(orm)
        await self.db.flush()
        msg.id = orm.id
        msg.created_at = orm.created_at
        return msg

    async def get_by_id(self, message_id: int) -> Optional[d.Message]:
        row = (await self.db.execute(
            select(MessageORM).where(MessageORM.id == message_id)
        )).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update_body(
        self, message_id: int, body: str, edited_at: datetime,
    ) -> None:
        await self.db.execute(
            update(MessageORM)
            .where(MessageORM.id == message_id)
            .values(body=body, edited_at=edited_at)
        )

    async def soft_delete(self, message_id: int, deleted_at: datetime) -> None:
        await self.db.execute(
            update(MessageORM)
            .where(MessageORM.id == message_id)
            .values(deleted_at=deleted_at)
        )

    async def list_history(
        self,
        room_id: int,
        before_id: Optional[int] = None,
        limit: int = 50,
    ) -> List[d.Message]:
        """Cursor-paginated history, newest first. `before_id` is exclusive."""
        stmt = select(MessageORM).where(MessageORM.room_id == room_id)
        if before_id is not None:
            stmt = stmt.where(MessageORM.id < before_id)
        stmt = stmt.order_by(MessageORM.id.desc()).limit(limit)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row: MessageORM) -> d.Message:
        return d.Message(
            id=row.id,
            room_id=row.room_id,
            sender_client_id=row.sender_client_id,
            body=d.MessageBody(row.body),
            kind=d.ChatMessageKind(row.kind),
            client_msg_id=row.client_msg_id,
            created_at=row.created_at,
            edited_at=row.edited_at,
            deleted_at=row.deleted_at,
        )


class ChatPolicyRepository:
    """Cross-domain reads used by the authorization layer: friendship,
    block, session membership + status. Kept separate so the chat service
    has one knob to mock in tests."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def are_friends(self, a: int, b: int) -> bool:
        lo, hi = (a, b) if a < b else (b, a)
        row = (await self.db.execute(
            select(FriendshipORM.id).where(
                FriendshipORM.client_a_id == lo,
                FriendshipORM.client_b_id == hi,
            )
        )).first()
        return row is not None

    async def is_blocked_either_way(self, a: int, b: int) -> bool:
        row = (await self.db.execute(
            select(BlockORM.id).where(
                or_(
                    and_(
                        BlockORM.blocker_client_id == a,
                        BlockORM.blocked_client_id == b,
                    ),
                    and_(
                        BlockORM.blocker_client_id == b,
                        BlockORM.blocked_client_id == a,
                    ),
                )
            )
        )).first()
        return row is not None

    async def get_session_status(self, session_id: int) -> Optional[str]:
        row = (await self.db.execute(
            select(SessionORM.status).where(SessionORM.id == session_id)
        )).first()
        return row.status if row else None

    async def is_session_member(self, session_id: int, client_id: int) -> bool:
        row = (await self.db.execute(
            select(SessionMemberORM.id).where(
                SessionMemberORM.session_id == session_id,
                SessionMemberORM.client_id == client_id,
            )
        )).first()
        return row is not None

    async def list_session_member_ids(self, session_id: int) -> List[int]:
        rows = (await self.db.execute(
            select(SessionMemberORM.client_id).where(
                SessionMemberORM.session_id == session_id,
            )
        )).all()
        return [r.client_id for r in rows]

    async def get_session_meta(self, session_id: int) -> Optional[dict]:
        """Gym name + session date/time + gym cover pic for the room
        header. Single query — LEFT JOINs the gym's cover_pic row from
        gym_studios_pic (type='cover_pic'), nullable when the gym hasn't
        uploaded one yet."""
        row = (await self.db.execute(
            select(
                GymORM.name.label("gym_name"),
                SessionORM.session_date,
                SessionORM.session_time,
                GymCoverORM.image_url.label("gym_cover_pic"),
            )
            .join(SessionORM, SessionORM.gym_id == GymORM.gym_id)
            .outerjoin(
                GymCoverORM,
                and_(
                    GymCoverORM.gym_id == GymORM.gym_id,
                    GymCoverORM.type == "cover_pic",
                ),
            )
            .where(SessionORM.id == session_id)
            # If a gym has multiple cover_pic rows (data anomaly), keep
            # one deterministic row so the JOIN doesn't duplicate session.
            .limit(1)
        )).first()
        if row is None:
            return None
        return {
            "gym_name": row.gym_name,
            "session_date": row.session_date,
            "session_time": row.session_time,
            "gym_cover_pic": row.gym_cover_pic,
        }

    async def get_session_host(self, session_id: int) -> Optional[int]:
        """Used to refuse a leave-group call from the host — the host
        must cancel the session instead, not silently exit it."""
        row = (await self.db.execute(
            select(SessionORM.host_client_id).where(SessionORM.id == session_id)
        )).first()
        return row.host_client_id if row else None

    async def remove_session_member(
        self, session_id: int, client_id: int,
    ) -> bool:
        """Drop a member from session_member. Used by the chat 'leave
        group' flow — the user vanishes from the workout entirely.
        Returns True if a row was deleted."""
        from sqlalchemy import delete
        res = await self.db.execute(
            delete(SessionMemberORM).where(
                SessionMemberORM.session_id == session_id,
                SessionMemberORM.client_id == client_id,
            )
        )
        return bool(res.rowcount)

    async def insert_report(
        self,
        reporter_client_id: int,
        entity_type: str,
        entity_id: int,
        reason: str,
        details: Optional[str],
        when: datetime,
    ) -> None:
        """Insert into gym_mate.report — idempotent via UNIQUE
        (reporter, entity_type, entity_id) so a duplicate report from
        the same user on the same entity silently no-ops."""
        from app.models.fittbot_models.gymmate import GymMateReport as ReportORM
        stmt = mysql_insert(ReportORM).values(
            reporter_client_id=reporter_client_id,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            details=details,
            status="pending",
            created_at=when,
        ).prefix_with("IGNORE")
        await self.db.execute(stmt)

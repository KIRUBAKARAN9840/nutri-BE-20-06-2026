from datetime import datetime
from typing import List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import (
    EventBus,
    MessageDeleted,
    MessageEdited,
    MessageSent,
    NoopEventBus,
    RoomCreated,
)
from ._pubsub import ChatPublisher
from ._repository import (
    ChatMessageRepository,
    ChatParticipantRepository,
    ChatPolicyRepository,
    ChatRoomRepository,
)


def _avatar_url_or_none(value):
    """Pipe stored s3_path / clients.profile through the CDN URL builder.
    Pre-existing http(s) URLs (dummy DPs from seed data) pass through."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    from app.fittbot_api.v2.Fymble.gym_mate.profile._storage import build_cdn_url
    return build_cdn_url(value)


class ChatService:
    def __init__(
        self,
        rooms: ChatRoomRepository,
        participants: ChatParticipantRepository,
        messages: ChatMessageRepository,
        policy: ChatPolicyRepository,
        publisher: ChatPublisher,
        event_bus: EventBus,
        blocks=None,
    ):
        self.rooms = rooms
        self.participants = participants
        self.messages = messages
        self.policy = policy
        self.publisher = publisher
        self.bus = event_bus
        self.blocks = blocks

    async def _block_set_for(self, client_id: int) -> set:
        """Bidirectional block set — anyone in a block pair with this
        client (either side). Empty if `blocks` repo not wired (tests)."""
        if self.blocks is None:
            return set()
        return await self.blocks.get_bidirectional_block_ids(client_id)

    async def open_direct_room(
        self,
        viewer_client_id: int,
        peer_client_id: int,
        session_id: Optional[int] = None,
    ) -> dto.RoomDTO:
        """Open or create a 1:1 room. `session_id` decides the kind:
        omitted → friend_direct (requires friendship); supplied →
        session_direct (requires both to be members of that session)."""
        if peer_client_id == viewer_client_id:
            raise FittbotHTTPException(
                status_code=400,
                detail="Cannot open a chat with yourself",
                error_code="GYMMATE_CHAT_SELF",
                log_data={"client_id": viewer_client_id},
            )
        if await self.policy.is_blocked_either_way(viewer_client_id, peer_client_id):
            raise FittbotHTTPException(
                status_code=403,
                detail="Cannot chat with a blocked user",
                error_code="GYMMATE_CHAT_BLOCKED",
                log_data={"client_id": viewer_client_id, "peer": peer_client_id},
            )

        if session_id is None:
            kind = d.ChatRoomKind.FRIEND_DIRECT
            if not await self.policy.are_friends(viewer_client_id, peer_client_id):
                raise FittbotHTTPException(
                    status_code=403,
                    detail="You must be friends to chat",
                    error_code="GYMMATE_CHAT_NOT_FRIENDS",
                    log_data={"client_id": viewer_client_id, "peer": peer_client_id},
                )
        else:
            kind = d.ChatRoomKind.SESSION_DIRECT
            status = await self.policy.get_session_status(session_id)
            if status is None:
                raise FittbotHTTPException(
                    status_code=404,
                    detail="Session not found",
                    error_code="GYMMATE_CHAT_SESSION_NOT_FOUND",
                    log_data={"client_id": viewer_client_id, "session_id": session_id},
                )
            if status != "open":
                raise FittbotHTTPException(
                    status_code=403,
                    detail="Chat is closed for this session",
                    error_code="GYMMATE_CHAT_SESSION_CLOSED",
                    log_data={"client_id": viewer_client_id, "session_id": session_id},
                )
            if not await self.policy.is_session_member(session_id, viewer_client_id):
                raise self._not_member(viewer_client_id, session_id)
            if not await self.policy.is_session_member(session_id, peer_client_id):
                raise FittbotHTTPException(
                    status_code=403,
                    detail="Peer is not a member of this session",
                    error_code="GYMMATE_CHAT_PEER_NOT_SESSION_MEMBER",
                    log_data={
                        "client_id": viewer_client_id,
                        "peer": peer_client_id,
                        "session_id": session_id,
                    },
                )

        pair_key = d.canonical_pair_key(viewer_client_id, peer_client_id)
        room = await self.rooms.find_direct(kind, pair_key, session_id)
        if room is None:
            room = await self.rooms.add(
                d.Room(
                    kind=kind,
                    pair_key=pair_key,
                    session_id=session_id,
                )
            )
            await self.participants.add_many(
                room.id, [viewer_client_id, peer_client_id],
            )
            await self.bus.publish(RoomCreated(
                room_id=room.id,
                kind=kind.value,
                session_id=session_id,
                participant_ids=[viewer_client_id, peer_client_id],
            ))
        return await self._room_to_dto(room, viewer_client_id=viewer_client_id)

    async def open_session_group_room(
        self, viewer_client_id: int, session_id: int,
    ) -> dto.RoomDTO:
        status = await self.policy.get_session_status(session_id)
        if status is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Session not found",
                error_code="GYMMATE_CHAT_SESSION_NOT_FOUND",
                log_data={"client_id": viewer_client_id, "session_id": session_id},
            )
        if status != "open":
            raise FittbotHTTPException(
                status_code=403,
                detail="Chat is closed for this session",
                error_code="GYMMATE_CHAT_SESSION_CLOSED",
                log_data={"client_id": viewer_client_id, "session_id": session_id},
            )
        if not await self.policy.is_session_member(session_id, viewer_client_id):
            raise self._not_member(viewer_client_id, session_id)

        room = await self.rooms.find_session_group(session_id)
        member_ids = await self.policy.list_session_member_ids(session_id)
        if room is None:
            room = await self.rooms.add(d.Room.session_group(session_id))
            await self.participants.add_many(room.id, member_ids)
            await self.bus.publish(RoomCreated(
                room_id=room.id,
                kind=d.ChatRoomKind.SESSION_GROUP.value,
                session_id=session_id,
                participant_ids=member_ids,
            ))
        else:
            # Late joiners — keep participant list in sync with session_member.
            existing = set(await self.participants.list_members(room.id))
            missing = [c for c in member_ids if c not in existing]
            if missing:
                await self.participants.add_many(room.id, missing)
        return await self._room_to_dto(room, viewer_client_id=viewer_client_id)

    async def get_room(
        self, viewer_client_id: int, room_id: int,
    ) -> dto.RoomDTO:
        room = await self._authorize_room_access(viewer_client_id, room_id)
        return await self._room_to_dto(room, viewer_client_id=viewer_client_id)

    async def list_inbox(
        self,
        client_id: int,
        before_at=None,
        limit: int = 30,
    ) -> dto.InboxPageDTO:
        """Enriched paginated inbox. Each row carries the title +
        avatar + peer/group context the chat-list UI needs so the
        frontend doesn't have to do per-row roundtrips."""
        capped_limit = max(1, min(limit, 50))
        rows = await self.rooms.list_inbox(
            client_id=client_id, before_at=before_at, limit=capped_limit,
        )

        # The repo asks for limit+1 so we can tell if there's a next page
        # without a second count query. Trim the overflow before enrichment.
        has_more = len(rows) > capped_limit
        if has_more:
            rows = rows[:capped_limit]

        direct_room_ids = [
            r["room_id"] for r in rows
            if r["kind"] in (
                d.ChatRoomKind.FRIEND_DIRECT.value,
                d.ChatRoomKind.SESSION_DIRECT.value,
            )
        ]
        group_session_ids = [
            r["session_id"] for r in rows
            if r["kind"] == d.ChatRoomKind.SESSION_GROUP.value
            and r["session_id"] is not None
        ]
        peers_map = await self.rooms.fetch_peers_for_rooms(
            client_id, direct_room_ids,
        )
        groups_map = await self.rooms.fetch_groups_for_sessions(
            group_session_ids,
        )

        blocked_ids = await self._block_set_for(client_id)
        if blocked_ids:
            blocked_room_ids = {
                rid for rid, peer in peers_map.items()
                if peer["client_id"] in blocked_ids
            }
            if blocked_room_ids:
                rows = [r for r in rows if r["room_id"] not in blocked_room_ids]
                for rid in blocked_room_ids:
                    peers_map.pop(rid, None)

        items: List[dto.InboxItemDTO] = []
        
        for r in rows:
            lm = r["last_message"]
            preview = None
            if lm is not None:
                preview = dto.LastMessagePreviewDTO(
                    message_id=lm["id"],
                    sender_client_id=lm["sender_client_id"],
                    body=lm["body"],
                    kind=lm["kind"],
                    created_at=lm["created_at"],
                    is_deleted=lm["is_deleted"],
                )

            peer_dto = None
            group_dto = None
            title = ""
            avatar_url = None
            subtitle = None

            if r["kind"] in (
                d.ChatRoomKind.FRIEND_DIRECT.value,
                d.ChatRoomKind.SESSION_DIRECT.value,
            ):
                peer = peers_map.get(r["room_id"])
                if peer is not None:
                    peer_avatar = _avatar_url_or_none(peer["avatar_url"])
                    peer_dto = dto.InboxPeerDTO(
                        client_id=peer["client_id"],
                        name=peer["name"],
                        avatar_url=peer_avatar,
                    )
                    title = peer["name"] or f"User {peer['client_id']}"
                    avatar_url = peer_avatar
                else:
                    # Defensive — should not happen for a direct room.
                    title = "Unknown"
            elif r["kind"] == d.ChatRoomKind.SESSION_GROUP.value:
                grp = groups_map.get(r["session_id"]) if r["session_id"] else None
                if grp is not None:
                    grp_view = {
                        **grp,
                        "gym_cover_pic": _avatar_url_or_none(grp.get("gym_cover_pic")),
                        "member_avatars": [
                            _avatar_url_or_none(a) for a in grp.get("member_avatars", []) if a
                        ],
                    }
                    grp_view["member_avatars"] = [a for a in grp_view["member_avatars"] if a]
                    group_dto = dto.InboxGroupDTO(**grp_view)
                    title = grp["gym_name"] or "Group session"
                    avatar_url = grp_view["gym_cover_pic"]
                    parts = []
                    if grp.get("gym_area"):
                        parts.append(grp["gym_area"])
                    if grp.get("member_count"):
                        parts.append(f"{grp['member_count']} members")
                    subtitle = " · ".join(parts) or None
                else:
                    title = "Group session"

            items.append(dto.InboxItemDTO(
                room_id=r["room_id"],
                kind=r["kind"],
                session_id=r["session_id"],
                title=title,
                avatar_url=avatar_url,
                subtitle=subtitle,
                last_message_at=r["last_message_at"],
                last_message=preview,
                unread_count=r["unread_count"],
                peer=peer_dto,
                group=group_dto,
            ))

        next_cursor = items[-1].last_message_at if (items and has_more) else None


        recent_friends: List[dto.RecentFriendDTO] = []
        if before_at is None:
            friend_rows = await self.rooms.list_recent_friends(
                client_id=client_id, limit=5,
            )

            recent_friends = [
                dto.RecentFriendDTO(
                    client_id=f["client_id"],
                    name=f["name"],
                    avatar_url=_avatar_url_or_none(f["avatar_url"]),
                    friended_at=f["friended_at"],
                )
                for f in friend_rows
                if f["client_id"] not in blocked_ids
            ]

        return dto.InboxPageDTO(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            recent_friends=recent_friends,
        )

    async def send_message(
        self,
        sender_client_id: int,
        room_id: int,
        body: str,
        client_msg_id: Optional[str] = None,
    ) -> dto.MessageDTO:
        room = await self._authorize_room_access(sender_client_id, room_id)
        # 1:1 rooms also enforce block at send time. Group rooms ignore
        # blocks — the message is visible to everyone in the room equally.
        peer: Optional[int] = None
        if room.kind in (d.ChatRoomKind.FRIEND_DIRECT, d.ChatRoomKind.SESSION_DIRECT):
            peer = await self._peer_of_direct(room_id, sender_client_id)
            if peer is not None and await self.policy.is_blocked_either_way(
                sender_client_id, peer,
            ):
                raise FittbotHTTPException(
                    status_code=403,
                    detail="Cannot message a blocked user",
                    error_code="GYMMATE_CHAT_BLOCKED",
                    log_data={"client_id": sender_client_id, "peer": peer},
                )

        try:
            msg = d.Message.create(
                room_id=room_id,
                sender_client_id=sender_client_id,
                body=body,
                client_msg_id=client_msg_id,
            )
        except d.InvalidMessageBody as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_CHAT_INVALID_BODY",
                log_data={"client_id": sender_client_id, "room_id": room_id},
            )

        saved = await self.messages.add(msg)
        await self.rooms.update_last_message(room_id, saved.id, saved.created_at)

        recipients = await self.participants.list_members(room_id)
        dto_msg = self._message_to_dto(saved)
        await self.publisher.fan_out(
            recipients,
            {"type": "message", "room_id": room_id, "message": dto_msg.model_dump(mode="json")},
        )
        await self.bus.publish(MessageSent(
            room_id=room_id,
            message_id=saved.id,
            sender_client_id=sender_client_id,
            recipient_ids=recipients,
            created_at=saved.created_at,
        ))
        return dto_msg

    async def edit_message(
        self, sender_client_id: int, message_id: int, body: str,
    ) -> dto.MessageDTO:
        msg = await self.messages.get_by_id(message_id)
        if msg is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Message not found",
                error_code="GYMMATE_CHAT_MESSAGE_NOT_FOUND",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        await self._authorize_room_access(sender_client_id, msg.room_id)
        try:
            msg.edit(sender_client_id, body)
        except d.NotMessageOwner as exc:
            raise FittbotHTTPException(
                status_code=403, detail=str(exc),
                error_code="GYMMATE_CHAT_NOT_OWNER",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        except d.EditWindowExpired as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_CHAT_EDIT_EXPIRED",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        except d.MessageAlreadyDeleted as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_CHAT_MESSAGE_DELETED",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        except d.InvalidMessageBody as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_CHAT_INVALID_BODY",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        await self.messages.update_body(msg.id, msg.body.value, msg.edited_at)
        recipients = await self.participants.list_members(msg.room_id)
        dto_msg = self._message_to_dto(msg)
        await self.publisher.fan_out(
            recipients,
            {"type": "edited", "room_id": msg.room_id, "message": dto_msg.model_dump(mode="json")},
        )
        await self.bus.publish(MessageEdited(
            room_id=msg.room_id,
            message_id=msg.id,
            sender_client_id=sender_client_id,
            edited_at=msg.edited_at,
        ))
        return dto_msg

    async def delete_message(
        self, sender_client_id: int, message_id: int,
    ) -> None:
        msg = await self.messages.get_by_id(message_id)
        if msg is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Message not found",
                error_code="GYMMATE_CHAT_MESSAGE_NOT_FOUND",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        await self._authorize_room_access(sender_client_id, msg.room_id)
        try:
            msg.soft_delete(sender_client_id)
        except d.NotMessageOwner as exc:
            raise FittbotHTTPException(
                status_code=403, detail=str(exc),
                error_code="GYMMATE_CHAT_NOT_OWNER",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        except d.MessageAlreadyDeleted as exc:
            raise FittbotHTTPException(
                status_code=400, detail=str(exc),
                error_code="GYMMATE_CHAT_MESSAGE_DELETED",
                log_data={"client_id": sender_client_id, "message_id": message_id},
            )
        await self.messages.soft_delete(msg.id, msg.deleted_at)
        recipients = await self.participants.list_members(msg.room_id)
        await self.publisher.fan_out(
            recipients,
            {"type": "deleted", "room_id": msg.room_id, "message_id": msg.id},
        )
        await self.bus.publish(MessageDeleted(
            room_id=msg.room_id,
            message_id=msg.id,
            sender_client_id=sender_client_id,
            deleted_at=msg.deleted_at,
        ))

    async def leave_session_group(
        self, viewer_client_id: int, room_id: int,
    ) -> None:
        """Leave a session_group chat AND the session itself. Per
        product call: the chat is the coordination layer for the
        workout — opting out of it means opting out of the workout.

        Host can't leave their own session this way; they must use the
        dedicated cancel-session endpoint (which also tears down the
        session for everyone).
        """
        room = await self.rooms.get_by_id(room_id)
        if room is None:
            raise FittbotHTTPException(
                status_code=404, detail="Room not found",
                error_code="GYMMATE_CHAT_ROOM_NOT_FOUND",
                log_data={"client_id": viewer_client_id, "room_id": room_id},
            )
        if room.kind != d.ChatRoomKind.SESSION_GROUP:
            raise FittbotHTTPException(
                status_code=400,
                detail="Only session_group rooms can be left",
                error_code="GYMMATE_CHAT_LEAVE_NOT_GROUP",
                log_data={"client_id": viewer_client_id, "room_id": room_id},
            )
        if room.session_id is None:
            # Defensive — a session_group room should always carry a session_id.
            raise FittbotHTTPException(
                status_code=400,
                detail="Group room has no associated session",
                error_code="GYMMATE_CHAT_LEAVE_INVALID",
                log_data={"client_id": viewer_client_id, "room_id": room_id},
            )

        host_id = await self.policy.get_session_host(room.session_id)
        if host_id == viewer_client_id:
            raise FittbotHTTPException(
                status_code=400,
                detail=(
                    "The host cannot leave their own session"
                ),
                error_code="GYMMATE_CHAT_HOST_CANNOT_LEAVE",
                log_data={
                    "client_id": viewer_client_id,
                    "room_id": room_id,
                    "session_id": room.session_id,
                },
            )

        if not await self.participants.is_member(room_id, viewer_client_id):
            raise FittbotHTTPException(
                status_code=403,
                detail="Not a participant of this room",
                error_code="GYMMATE_CHAT_NOT_PARTICIPANT",
                log_data={"client_id": viewer_client_id, "room_id": room_id},
            )

        # Atomic: drop from chat_participant AND session_member. Other
        # surfaces (matches, joiner_count, nearby) see the change on
        # next refresh.
        await self.participants.remove(room_id, viewer_client_id)
        await self.policy.remove_session_member(
            room.session_id, viewer_client_id,
        )

        # Tell the remaining participants someone left so the UI can
        # refresh the member list / show a chip.
        remaining = await self.participants.list_members(room_id)
        await self.publisher.fan_out(
            remaining,
            {
                "type": "member_left",
                "room_id": room_id,
                "client_id": viewer_client_id,
            },
        )

    async def report_room(
        self,
        reporter_client_id: int,
        room_id: int,
        reason: str,
        details: Optional[str] = None,
    ) -> None:
        """Report an entire chat room (group or 1:1) for admin review.
        Per-message reports live behind a separate flow — this one is
        for the conversation as a whole.

        Idempotent via UNIQUE(reporter, entity_type, entity_id) — a
        duplicate report from the same user silently no-ops.
        """
        from datetime import datetime
        from app.models.fittbot_models.gymmate import REPORT_REASONS

        # Whitelist reason against the existing enum so we don't accept
        # arbitrary strings.
        if reason not in REPORT_REASONS:
            raise FittbotHTTPException(
                status_code=400,
                detail=f"Invalid reason. Must be one of: {', '.join(REPORT_REASONS)}",
                error_code="GYMMATE_REPORT_INVALID_REASON",
                log_data={"client_id": reporter_client_id, "reason": reason},
            )
        if details is not None and len(details) > 500:
            raise FittbotHTTPException(
                status_code=400,
                detail="Details must be 500 characters or fewer",
                error_code="GYMMATE_REPORT_INVALID_DETAILS",
                log_data={"client_id": reporter_client_id},
            )

        room = await self.rooms.get_by_id(room_id)
        if room is None:
            raise FittbotHTTPException(
                status_code=404, detail="Room not found",
                error_code="GYMMATE_CHAT_ROOM_NOT_FOUND",
                log_data={"client_id": reporter_client_id, "room_id": room_id},
            )
        if not await self.participants.is_member(room_id, reporter_client_id):
            raise FittbotHTTPException(
                status_code=403,
                detail="Only participants can report this room",
                error_code="GYMMATE_CHAT_NOT_PARTICIPANT",
                log_data={"client_id": reporter_client_id, "room_id": room_id},
            )

        await self.policy.insert_report(
            reporter_client_id=reporter_client_id,
            entity_type="chat_room",
            entity_id=room_id,
            reason=reason,
            details=(details or None),
            when=datetime.now(),
        )

    async def delete_messages_bulk(
        self, sender_client_id: int, message_ids: List[int],
    ) -> dict:
        """Delete many messages in one round-trip. Each id is validated
        independently — a failure on one does NOT abort the rest. Returns
        per-id outcome so the frontend can show "5 deleted, 1 already
        deleted, 1 not yours" without N round-trips.

        Dedupes input ids defensively; preserves first-seen order in the
        `deleted` list for UX predictability.
        """
        seen: set = set()
        ordered_ids: List[int] = []
        for mid in message_ids:
            if mid in seen:
                continue
            seen.add(mid)
            ordered_ids.append(mid)

        deleted: List[int] = []
        failed: List[dict] = []
        for mid in ordered_ids:
            try:
                await self.delete_message(
                    sender_client_id=sender_client_id, message_id=mid,
                )
                deleted.append(mid)
            except FittbotHTTPException as exc:
                failed.append({
                    "message_id": mid,
                    "error_code": exc.error_code or "GYMMATE_CHAT_DELETE_FAILED",
                    "detail": str(exc.detail) if exc.detail else "",
                })
        return {"deleted": deleted, "failed": failed}

    async def list_history(
        self,
        viewer_client_id: int,
        room_id: int,
        before: Optional[int] = None,
        limit: int = 50,
    ) -> List[dto.MessageDTO]:
        await self._authorize_room_access(viewer_client_id, room_id)
        rows = await self.messages.list_history(
            room_id=room_id, before_id=before, limit=min(max(limit, 1), 100),
        )
        return [self._message_to_dto(m) for m in rows]

    async def mark_read(
        self, viewer_client_id: int, room_id: int, up_to_message_id: int,
    ) -> None:
        await self._authorize_room_access(viewer_client_id, room_id)
        await self.participants.mark_read(
            room_id=room_id,
            client_id=viewer_client_id,
            up_to_message_id=up_to_message_id,
        )

    async def typing(self, viewer_client_id: int, room_id: int) -> None:
        await self._authorize_room_access(viewer_client_id, room_id)
        recipients = [
            cid for cid in await self.participants.list_members(room_id)
            if cid != viewer_client_id
        ]
        if not recipients:
            return
        # 5-second presence on the wire — frontend decides how long to
        # display the dots before fading.
        expires_at = datetime.now().isoformat()
        await self.publisher.fan_out(
            recipients,
            {
                "type": "typing",
                "room_id": room_id,
                "sender_id": viewer_client_id,
                "expires_at": expires_at,
            },
        )

    async def _authorize_room_access(
        self, viewer_client_id: int, room_id: int,
    ) -> d.Room:
        room = await self.rooms.get_by_id(room_id)
        if room is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Room not found",
                error_code="GYMMATE_CHAT_ROOM_NOT_FOUND",
                log_data={"client_id": viewer_client_id, "room_id": room_id},
            )
        if not await self.participants.is_member(room_id, viewer_client_id):
            raise FittbotHTTPException(
                status_code=403,
                detail="You are not a participant of this room",
                error_code="GYMMATE_CHAT_NOT_PARTICIPANT",
                log_data={"client_id": viewer_client_id, "room_id": room_id},
            )
        if room.is_session_scoped() and room.session_id is not None:
            status = await self.policy.get_session_status(room.session_id)
            if status is not None and status != "open":
                raise FittbotHTTPException(
                    status_code=403,
                    detail="Chat is closed for this session",
                    error_code="GYMMATE_CHAT_SESSION_CLOSED",
                    log_data={
                        "client_id": viewer_client_id,
                        "room_id": room_id,
                        "session_id": room.session_id,
                    },
                )
        return room

    async def _peer_of_direct(
        self, room_id: int, viewer_client_id: int,
    ) -> Optional[int]:
        members = await self.participants.list_members(room_id)
        for cid in members:
            if cid != viewer_client_id:
                return cid
        return None

    async def peer_of_room(
        self, room_id: int, viewer_client_id: int,
    ) -> Optional[int]:
        """Public helper for routes — returns the other party's
        client_id in a 1:1 room, None for session_group rooms or when
        the viewer isn't a participant. Used to populate the
        envelope-level `peer_client_id` on message responses without
        baking it into every MessageDTO row."""
        room = await self.rooms.get_by_id(room_id)
        if room is None or room.kind == d.ChatRoomKind.SESSION_GROUP:
            return None
        return await self._peer_of_direct(room_id, viewer_client_id)

    async def _room_to_dto(
        self,
        room: d.Room,
        viewer_client_id: Optional[int] = None,
    ) -> dto.RoomDTO:
        profiles = await self.participants.list_member_profiles(room.id)
        gym_name = None
        gym_cover_pic = None
        session_date = None
        session_time = None
        if room.session_id is not None:
            meta = await self.policy.get_session_meta(room.session_id)
            if meta is not None:
                gym_name = meta["gym_name"]
                gym_cover_pic = meta.get("gym_cover_pic")
                session_date = meta["session_date"]
                session_time = meta["session_time"]

        # For 1:1 rooms, surface the OTHER participant's id directly so
        # the frontend doesn't have to filter `participants` by "not me".
        # Group rooms have no single peer → stays None.
        peer_client_id: Optional[int] = None
        if (
            viewer_client_id is not None
            and room.kind in (d.ChatRoomKind.FRIEND_DIRECT, d.ChatRoomKind.SESSION_DIRECT)
        ):
            for p in profiles:
                if p["client_id"] != viewer_client_id:
                    peer_client_id = p["client_id"]
                    break

        return dto.RoomDTO(
            room_id=room.id,
            kind=room.kind.value,
            session_id=room.session_id,
            pair_key=room.pair_key,
            participants=[
                dto.ParticipantDTO(
                    client_id=p["client_id"],
                    name=p["name"],
                    avatar_url=_avatar_url_or_none(p["avatar_url"]),
                    joined_at=p["joined_at"],
                )
                for p in profiles
            ],
            last_message_at=room.last_message_at,
            gym_name=gym_name,
            gym_cover_pic=gym_cover_pic,
            session_date=session_date,
            session_time=session_time,
            peer_client_id=peer_client_id,
        )

    @staticmethod
    def _message_to_dto(msg: d.Message) -> dto.MessageDTO:
        deleted = msg.deleted_at is not None
        return dto.MessageDTO(
            message_id=msg.id,
            room_id=msg.room_id,
            sender_client_id=msg.sender_client_id,
            body="[deleted]" if deleted else msg.body.value,
            kind=msg.kind.value,
            client_msg_id=msg.client_msg_id,
            created_at=msg.created_at,
            edited_at=msg.edited_at,
            is_deleted=deleted,
        )

    @staticmethod
    def _not_member(client_id: int, session_id: int) -> FittbotHTTPException:
        return FittbotHTTPException(
            status_code=403,
            detail="You are not a member of this session",
            error_code="GYMMATE_CHAT_NOT_SESSION_MEMBER",
            log_data={"client_id": client_id, "session_id": session_id},
        )


def _noop_bus() -> EventBus:
    return NoopEventBus()

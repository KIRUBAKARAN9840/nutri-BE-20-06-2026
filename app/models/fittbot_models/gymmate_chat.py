"""GymMate chat ORM models.

All tables live in the `gym_mate` schema alongside the rest of the social
module. Three room kinds share one set of tables so the inbox query stays
simple:

    friend_direct   1:1 between two friends (session_id NULL)
    session_direct  1:1 between two members of one session
    session_group   all accepted members of one session

Cross-schema client_id references are app-managed, no FK — same convention
as the rest of the gym_mate module.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import TINYINT

from app.models.database import Base


GYMMATE_SCHEMA = "gym_mate"


CHAT_ROOM_KINDS = ("friend_direct", "session_direct", "session_group")
CHAT_MESSAGE_KINDS = ("text", "system")


class GymMateChatRoom(Base):
    __tablename__ = "chat_room"
    __table_args__ = (
        # Direct (friend_direct + session_direct) is identified by pair_key;
        # session-scoped variants additionally key on session_id. We declare
        # one wide unique covering all kinds — the values guarantee
        # uniqueness inside each subset (session_id is NULL for friend_direct;
        # pair_key is NULL for session_group). MySQL allows multiple NULLs
        # in a unique index, but here every row supplies a non-NULL value
        # for at least one of the two — the index still functions.
        UniqueConstraint(
            "kind", "session_id", "pair_key", name="uk_chat_room_identity",
        ),
        Index("idx_chat_room_session", "session_id"),
        Index("idx_chat_room_last_msg", "last_message_at"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    kind = Column(String(20), nullable=False)
    session_id = Column(BigInteger, nullable=True)
    pair_key = Column(
        String(32),
        nullable=True,
        comment="'min_cid-max_cid' for direct rooms; NULL for session_group",
    )

    last_message_id = Column(BigInteger, nullable=True)
    last_message_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now,
    )


class GymMateChatParticipant(Base):
    __tablename__ = "chat_participant"
    __table_args__ = (
        UniqueConstraint(
            "room_id", "client_id", name="uk_chat_participant_room_client",
        ),
        Index("idx_chat_participant_client", "client_id"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    room_id = Column(
        BigInteger,
        ForeignKey(f"{GYMMATE_SCHEMA}.chat_room.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id = Column(BigInteger, nullable=False)
    joined_at = Column(DateTime, nullable=False, default=datetime.now)
    last_read_message_id = Column(
        BigInteger,
        nullable=True,
        comment="Internal only — used for unread badge, never broadcast",
    )
    muted = Column(TINYINT(unsigned=True), nullable=False, default=0)


class GymMateChatMessage(Base):
    __tablename__ = "chat_message"
    __table_args__ = (
        Index(
            "idx_chat_message_room_created",
            "room_id", "created_at", "id",
        ),
        # Server-side dedupe of retried sends. Multiple NULLs allowed by
        # MySQL — messages without a client_msg_id can coexist freely.
        UniqueConstraint(
            "room_id", "client_msg_id", name="uk_chat_message_client_msg",
        ),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    room_id = Column(
        BigInteger,
        ForeignKey(f"{GYMMATE_SCHEMA}.chat_room.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_client_id = Column(BigInteger, nullable=False)
    body = Column(Text, nullable=False)
    kind = Column(String(20), nullable=False, default="text")
    client_msg_id = Column(
        String(36),
        nullable=True,
        comment="Client-generated UUID for idempotent retries",
    )

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    edited_at = Column(DateTime, nullable=True)
    deleted_at = Column(
        DateTime,
        nullable=True,
        comment="Soft delete on the wire; body retained for admin/audit",
    )

"""GymMate notification ORM models.

One row per delivered notification. Push fan-out (FCM) is handled out of
band via Celery; this table is the durable history that powers the
in-app notification center + unread badge.

Device tokens are NOT stored here — the existing `fcm_tokens` table
(`app.models.fittbot_models.messaging.FcmToken`) is reused. Cross-schema
client_id references stay app-managed, no FK — same convention as the
rest of the gym_mate module.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    JSON,
    String,
)

from app.models.database import Base


GYMMATE_SCHEMA = "gym_mate"


# Allowed-value tuple — validated at the domain layer. New categories
# require a one-line append here AND a handler in `notifications/_handlers.py`.
NOTIFICATION_CATEGORIES = (
    # Friends
    "friend_request_received",
    "friend_request_accepted",
    # Sessions
    "session_request_received",
    "session_request_accepted",
    "session_new_match",
    "session_cancelled_by_host",
    # Chat
    "chat_message_direct",
    "chat_message_group",
    # Stories
    "story_from_friend",
)


class GymMateNotification(Base):
    """In-app notification row for the bell-icon center.

    Columns:
        recipient_client_id   the JWT-holder who sees this in their feed
        category              one of NOTIFICATION_CATEGORIES (app-validated)
        title / body          OS push + in-app row text
        actor_client_id       who triggered the notification (for avatar)
        entity_type           "friend_request" | "session" | "session_request"
                              | "room" | "message" | "story"  — for the
                              deep-link to know what to load
        entity_id             id of the entity (FK app-managed, no SQL FK)
        payload_json          structured deep-link payload (see schemas.py)
        read_at               NULL = unread; set to NOW() when user opens
                              the bell or taps the row
        created_at            indexed DESC for inbox / unread queries
    """

    __tablename__ = "notification"
    __table_args__ = (
        # Bell-badge query: unread newest first per recipient.
        Index(
            "idx_notif_recipient_read_created",
            "recipient_client_id", "read_at", "created_at",
        ),
        # Category-filtered feed (e.g. "show me only friend notifications").
        Index(
            "idx_notif_recipient_category_created",
            "recipient_client_id", "category", "created_at",
        ),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    recipient_client_id = Column(BigInteger, nullable=False)
    category = Column(String(50), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(String(500), nullable=True)
    actor_client_id = Column(BigInteger, nullable=True)
    entity_type = Column(String(40), nullable=True)
    entity_id = Column(BigInteger, nullable=True)
    payload_json = Column(JSON, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=datetime.now,
    )

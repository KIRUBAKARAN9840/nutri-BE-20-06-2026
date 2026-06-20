"""GymMate / Fymble social models.

All tables live in the `gym_mate` MySQL schema (separate database on the
same MySQL instance). Cross-schema references to `clients.client_id`
and `gyms.gym_id` are deliberately NOT enforced as foreign keys — the
`gym_mate` bounded context manages those references at the application
layer. This keeps the social module loosely coupled and makes future
extraction trivial.

Storage choice for option fields:
    We store `primary_goal`, `preferred_timing`, `gym_personality` as
    VARCHAR(30) rather than native MySQL ENUM. Reasons:
        - Adding/removing a value is a metadata-only ALTER (fast on big
          tables), unlike ENUM where adding a non-trailing value can
          force a full table rewrite.
        - The application enforces the allowed-value set via Python
          Enums in `_domain.py` — same safety, more flexibility.
"""

from datetime import datetime

from app.utils.time_utils import utc_now

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import relationship

from app.models.database import Base


GYMMATE_SCHEMA = "gym_mate"


# ---------------------------------------------------------------------------
# Allowed-value tuples — the source of truth for valid column values.
# Mirror these in `_domain.py` Python Enums + frontend dropdowns.
# Note: en-dash "–" (U+2013) is used in the timing strings, not hyphen "-".
# ---------------------------------------------------------------------------
PRIMARY_GOAL_VALUES = (
    "Weight Loss",
    "Weight Gain",
    "Muscle Building",
    "Stay Fit",
    "Improve Endurance",
    "Flexibility & Mobility",
    "Athletic Performance",
    "Stress Relief",
)

PREFERRED_TIMING_VALUES = (
    "Early Morning (5–7 AM)",
    "Morning (7–10 AM)",
    "Afternoon (12–3 PM)",
    "Evening (5–8 PM)",
    "Late Night (8–11 PM)",
    "Flexible",
)

GYM_PERSONALITY_VALUES = (
    "Serious & Focused",
    "Friendly & Social",
    "Motivator",
    "Chill & Relaxed",
    "Competitive",
    "Beginner-friendly",
    "No-nonsense",
)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
class GymMateProfile(Base):


    __tablename__ = "profile"
    __table_args__ = (
        UniqueConstraint("client_id", name="uk_profile_client"),
        Index("idx_profile_goal_timing", "primary_goal", "preferred_timing"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    client_id = Column(
        BigInteger,
        nullable=False,
        comment="References fittbot.clients.client_id (app-managed, no FK)",
    )

    primary_goal = Column(String(30), nullable=False)
    activity_interests = Column(
        JSON,
        nullable=False,
        comment="JSON array of activity strings (e.g. ['Cardio','Yoga'])",
    )
    preferred_timing = Column(String(30), nullable=False)
    gym_personality = Column(String(30), nullable=False)

    # Step 2: Bio
    bio = Column(String(300), nullable=True)
    city=Column(String(100), nullable=True)

    # Lifecycle
    onboarding_completed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    

    photos = relationship(
        "GymMateProfilePhoto",
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="GymMateProfilePhoto.display_order",
    )


# ---------------------------------------------------------------------------
# Profile Photo
# ---------------------------------------------------------------------------
class GymMateProfilePhoto(Base):

    __tablename__ = "profile_photo"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "display_order", name="uk_photo_profile_order"
        ),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    profile_id = Column(
        BigInteger,
        ForeignKey(f"{GYMMATE_SCHEMA}.profile.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    s3_path = Column(String(500), nullable=False)
    display_order = Column(TINYINT(unsigned=True), nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    profile = relationship("GymMateProfile", back_populates="photos")


# ---------------------------------------------------------------------------
# Friend request
# ---------------------------------------------------------------------------
FRIEND_REQUEST_STATUSES = (
    "pending",
    "accepted",
    "rejected",
    "cancelled",
)


class GymMateFriendRequest(Base):
    """Friend requests between two clients.

    Lifecycle: pending → (accepted | rejected | cancelled). On accept, a
    row is created in `friendship` and this row's status flips to
    'accepted' (kept for audit / history).
    """

    __tablename__ = "friend_request"
    __table_args__ = (
        UniqueConstraint("from_client_id", "to_client_id", name="uk_fr_from_to"),
        Index("idx_fr_to_status", "to_client_id", "status"),
        Index("idx_fr_from_status", "from_client_id", "status"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    from_client_id = Column(BigInteger, nullable=False)
    to_client_id = Column(BigInteger, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    responded_at = Column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Friendship (mutual, canonical — one row per pair)
# ---------------------------------------------------------------------------
class GymMateFriendship(Base):
    """A mutual friendship between two clients.

    `client_a_id` is always the SMALLER of the two IDs and `client_b_id`
    the larger — that way each friendship is exactly one row.

    Count my friends:  WHERE client_a_id = :me OR client_b_id = :me
    """

    __tablename__ = "friendship"
    __table_args__ = (
        UniqueConstraint("client_a_id", "client_b_id", name="uk_friendship_pair"),
        Index("idx_friendship_b", "client_b_id"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    client_a_id = Column(BigInteger, nullable=False)
    client_b_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


# ---------------------------------------------------------------------------
# Story (24-hour ephemeral post)
# ---------------------------------------------------------------------------
STORY_MEDIA_TYPES = ("image", "video")
STORY_AUDIENCES = ("public", "friends")


class GymMateStoryView(Base):
    __tablename__ = "story_view"
    __table_args__ = (
        UniqueConstraint("story_id", "viewer_client_id", name="uk_story_viewer"),
        Index("idx_story_view_viewer", "viewer_client_id"),
        Index("idx_story_view_story", "story_id"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    story_id = Column(BigInteger, nullable=False)
    viewer_client_id = Column(BigInteger, nullable=False)
    viewed_at = Column(DateTime, nullable=False, default=utc_now)


class GymMateStory(Base):
    """A 24-hour ephemeral story.

    Lifecycle:
        - Author POSTs media to S3, then POSTs metadata here.
        - `expires_at = created_at + 24h` is set in the service layer.
        - Reads filter `expires_at > NOW() AND is_deleted = FALSE`.
        - Owner can DELETE early → flips `is_deleted = TRUE`.
        - A future nightly Celery job hard-deletes expired rows + S3
          objects (~7 days after expiry, for audit).
    """

    __tablename__ = "story"
    __table_args__ = (
        Index("idx_story_client_active", "client_id", "expires_at", "is_deleted"),
        Index("idx_story_audience_active", "audience", "expires_at", "is_deleted"),
        Index("idx_story_expires", "expires_at"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    client_id = Column(BigInteger, nullable=False)
    media_type = Column(String(20), nullable=False, default="image",
                        comment="image | video")
    s3_key = Column(String(500), nullable=False)
    thumbnail_key = Column(String(500), nullable=True,
                           comment="for video only; null for images")
    caption = Column(String(300), nullable=True)
    audience = Column(String(20), nullable=False, default="public",
                      comment="public | friends")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    expires_at = Column(DateTime, nullable=False,
                        comment="always created_at + 24h")
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime, nullable=True)


MATE_PREFERENCE_VALUES = (
    "Male",
    "Female",
    "Group Workout",
    "No Preference",
)

FITNESS_LEVEL_VALUES = (
    "Beginner",
    "Intermediate",
    "Advanced",
    "Athlete",
)

WORKOUT_VIBE_VALUES = (
    "Push Day", "Leg Day", "Pull Day", "Functional",
    "HIIT", "Yoga", "CrossFit", "Cardio", "Strength",
    "Core & Abs", "Mobility", "No Preference",
)

SESSION_STATUSES = ("open", "cancelled", "matched", "completed")
SESSION_PAYMENT_STATUSES = ("unpaid", "pending", "paid")
SESSION_PAYMENT_MODES = ("pay_now", "pay_later")


from sqlalchemy import Date, Time


REPORT_ENTITY_TYPES = (
    "story", "user", "profile", "post", "comment",
    "chat_message", "chat_room",
)

REPORT_REASONS = (
    "inappropriate_content",
    "spam",
    "harassment",
    "violence",
    "false_information",
    "self_injury",
    "scam",
    "nudity",
    "ip_infringement",
    "restricted_items",
)

REPORT_STATUSES = ("pending", "reviewed", "actioned", "dismissed")


class GymMateDefaultProfile(Base):
    """Curated avatar gallery shown during onboarding when the user
    hasn't uploaded their own photo. Frontend filters by `gender` and
    renders the picker; the chosen URL is stored in profile_photo
    exactly like a user-uploaded URL (the existing PhotoSlot value
    object already accepts http(s) URLs).
    """

    __tablename__ = "default_profile"
    __table_args__ = (
        Index("idx_default_profile_gender", "gender"),
        {"schema": GYMMATE_SCHEMA},
    )

    sno = Column(BigInteger, primary_key=True, autoincrement=True)
    gender = Column(String(10), nullable=False)        # "Male" | "Female"
    profile_url = Column(String(500), nullable=False)


class GymMateBlock(Base):
    __tablename__ = "block"
    __table_args__ = (
        UniqueConstraint("blocker_client_id", "blocked_client_id", name="uk_block_pair"),
        Index("idx_block_blocked", "blocked_client_id"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    blocker_client_id = Column(BigInteger, nullable=False)
    blocked_client_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class GymMateReport(Base):
    __tablename__ = "report"
    __table_args__ = (
        UniqueConstraint(
            "reporter_client_id", "entity_type", "entity_id",
            name="uk_report_reporter_entity",
        ),
        Index("idx_report_entity", "entity_type", "entity_id"),
        Index("idx_report_reporter", "reporter_client_id"),
        Index("idx_report_status", "status", "created_at"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    reporter_client_id = Column(BigInteger, nullable=False)
    entity_type = Column(String(20), nullable=False)
    entity_id = Column(BigInteger, nullable=False)
    reason = Column(String(40), nullable=False)
    details = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    reviewed_at = Column(DateTime, nullable=True)


class GymMateSession(Base):
    __tablename__ = "session"
    __table_args__ = (
        Index("idx_session_host_status", "host_client_id", "status"),
        Index("idx_session_gym_date", "gym_id", "session_date"),
        Index("idx_session_open_date", "status", "session_date"),
        Index("idx_session_host_future", "host_client_id", "session_date", "status"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    host_client_id = Column(BigInteger, nullable=False)
    gym_id = Column(BigInteger, nullable=False)

    session_date = Column(Date, nullable=False)
    session_time = Column(Time, nullable=False)

    mate_preference = Column(String(20), nullable=False)
    fitness_level = Column(String(20), nullable=False)
    workout_vibes = Column(JSON, nullable=False)

    payment_mode = Column(String(20), nullable=False, default="pay_later")
    payment_status = Column(String(20), nullable=False, default="unpaid")
    daily_pass_id = Column(String(60), nullable=True)
    razorpay_order_id = Column(String(60), nullable=True)

    status = Column(String(20), nullable=False, default="open")

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


SESSION_REQUEST_STATUSES = (
    "pending",
    "accepted",
    "rejected",
    "withdrawn",
)


class GymMateSessionRequest(Base):
    """A client's request to join another client's session.

    Lifecycle: pending -> (accepted | rejected | withdrawn). On accept,
    a row is inserted in session_member; this row's status flips to
    'accepted' as the audit trail. Only the host can accept/reject;
    only the requester can withdraw a pending request.
    """

    __tablename__ = "session_request"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "requester_client_id", name="uk_sr_session_requester"
        ),
        Index("idx_sr_host_status", "host_client_id", "status", "created_at"),
        Index("idx_sr_session_status", "session_id", "status"),
        Index("idx_sr_requester_status", "requester_client_id", "status"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, nullable=False)
    requester_client_id = Column(BigInteger, nullable=False)
    host_client_id = Column(
        BigInteger,
        nullable=False,
        comment="Denormalized for fast 'pending requests to me' lookup",
    )
    message = Column(String(280), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    responded_at = Column(DateTime, nullable=True)


class GymMateSessionMember(Base):
    """An accepted participant of a session (host + accepted requesters).

    A separate table from session_request because:
        - it lets matches survive even if a request row is archived
        - JOIN-by-session for member-listing stays small
    Insert is idempotent via uk_sm_session_client.
    """

    __tablename__ = "session_member"
    __table_args__ = (
        UniqueConstraint("session_id", "client_id", name="uk_sm_session_client"),
        Index("idx_sm_client", "client_id"),
        Index("idx_sm_session", "session_id"),
        {"schema": GYMMATE_SCHEMA},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, nullable=False)
    client_id = Column(BigInteger, nullable=False)
    role = Column(
        String(20),
        nullable=False,
        default="member",
        comment="host | member",
    )
    joined_at = Column(DateTime, nullable=False, default=datetime.now)

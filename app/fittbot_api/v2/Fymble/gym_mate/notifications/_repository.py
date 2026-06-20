"""Repositories — ORM only. No business rules, no domain validation."""

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gymmate import (
    GymMateProfile as GymMateProfileORM,
    GymMateProfilePhoto as GymMatePhotoORM,
)
from app.models.fittbot_models.gymmate_notification import (
    GymMateNotification as NotificationORM,
)
from app.models.fittbot_models.messaging import FcmToken as FcmTokenORM

from . import _domain as d


class NotificationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Writes ───────────────────────────────────────────────────────────

    async def add(self, notif: d.Notification) -> d.Notification:
        row = NotificationORM(
            recipient_client_id=notif.recipient_client_id,
            category=notif.category.value,
            title=notif.title,
            body=notif.body,
            actor_client_id=notif.actor_client_id,
            entity_type=notif.entity_type,
            entity_id=notif.entity_id,
            payload_json=notif.payload or None,
            created_at=notif.created_at or datetime.now(),
        )
        self.db.add(row)
        await self.db.flush()
        notif.id = row.id
        notif.created_at = row.created_at
        return notif

    async def mark_read(self, notif_id: int, recipient_client_id: int) -> bool:
        """Set read_at = NOW for one notification, only if it belongs to
        this recipient. Returns True if a row was updated."""
        result = await self.db.execute(
            update(NotificationORM)
            .where(
                NotificationORM.id == notif_id,
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.read_at.is_(None),
            )
            .values(read_at=datetime.now())
        )
        return bool(result.rowcount)

    async def mark_all_read(self, recipient_client_id: int) -> int:
        """Set read_at on every unread row for this recipient. Returns
        the count of rows updated (= the prior unread badge value)."""
        result = await self.db.execute(
            update(NotificationORM)
            .where(
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.read_at.is_(None),
            )
            .values(read_at=datetime.now())
        )
        return int(result.rowcount or 0)

    async def mark_read_by_categories(
        self, recipient_client_id: int, categories: List[str],
    ) -> int:
        """Mark every unread row of these categories as read. Used when
        the user opens a specific tab whose dot covers exactly these
        categories (e.g. opening the chat tab clears chat_message_*)."""
        if not categories:
            return 0
        result = await self.db.execute(
            update(NotificationORM)
            .where(
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.read_at.is_(None),
                NotificationORM.category.in_(categories),
            )
            .values(read_at=datetime.now())
        )
        return int(result.rowcount or 0)

    async def remove(self, notif_id: int, recipient_client_id: int) -> bool:
        result = await self.db.execute(
            delete(NotificationORM).where(
                NotificationORM.id == notif_id,
                NotificationORM.recipient_client_id == recipient_client_id,
            )
        )
        return bool(result.rowcount)

    async def clear_older_than(
        self, recipient_client_id: int, days: int,
    ) -> int:
        """Used by a future cron task to trim notification history."""
        cutoff = datetime.now() - timedelta(days=days)
        result = await self.db.execute(
            delete(NotificationORM).where(
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.created_at < cutoff,
            )
        )
        return int(result.rowcount or 0)

    # ── Reads ────────────────────────────────────────────────────────────

    async def unread_count(self, recipient_client_id: int) -> int:
        stmt = (
            select(func.count(NotificationORM.id))
            .where(
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.read_at.is_(None),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def unread_summary_by_categories(
        self, recipient_client_id: int, categories: list,
    ) -> dict:
        """One round-trip count + latest-timestamp per category in
        `categories`. Returns `{category: {"count": N, "latest_at": dt}}`
        for any category with at least one unread row; categories with
        zero unreads are absent from the dict.

        Used by the home page to paint the three notification dots
        (gym_mate connections, friend requests, chat) without a separate
        roundtrip per bucket.
        """
        if not categories:
            return {}
        stmt = (
            select(
                NotificationORM.category,
                func.count(NotificationORM.id).label("cnt"),
                func.max(NotificationORM.created_at).label("latest_at"),
            )
            .where(
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.read_at.is_(None),
                NotificationORM.category.in_(categories),
            )
            .group_by(NotificationORM.category)
        )
        rows = (await self.db.execute(stmt)).all()
        return {
            r.category: {"count": int(r.cnt or 0), "latest_at": r.latest_at}
            for r in rows
        }

    async def list_feed(
        self,
        recipient_client_id: int,
        before_at: Optional[datetime] = None,
        limit: int = 30,
    ) -> List[dict]:
        """Newest-first feed. Asks for limit+1 so the caller can detect
        a next page without a count query.

        Avatar precedence for the actor matches the rest of the app:
            gym_mate.profile_photo (is_primary) → clients.profile
        """
        actor_avatar = func.coalesce(
            GymMatePhotoORM.s3_path, ClientORM.profile,
        ).label("actor_avatar")
        stmt = (
            select(
                NotificationORM.id,
                NotificationORM.category,
                NotificationORM.title,
                NotificationORM.body,
                NotificationORM.actor_client_id,
                NotificationORM.entity_type,
                NotificationORM.entity_id,
                NotificationORM.payload_json,
                NotificationORM.read_at,
                NotificationORM.created_at,
                ClientORM.name.label("actor_name"),
                actor_avatar,
            )
            .select_from(NotificationORM)
            .outerjoin(
                ClientORM,
                ClientORM.client_id == NotificationORM.actor_client_id,
            )
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == NotificationORM.actor_client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(NotificationORM.recipient_client_id == recipient_client_id)
        )
        if before_at is not None:
            stmt = stmt.where(NotificationORM.created_at < before_at)
        stmt = stmt.order_by(
            NotificationORM.created_at.desc(), NotificationORM.id.desc(),
        ).limit(max(1, min(limit, 50)) + 1)

        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "body": r.body,
                "actor_client_id": r.actor_client_id,
                "actor_name": r.actor_name,
                "actor_avatar": r.actor_avatar,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "payload_json": r.payload_json or {},
                "read_at": r.read_at,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    # ── Coalesce / dedup helpers ─────────────────────────────────────────

    async def get_recent_for_coalesce(
        self,
        recipient_client_id: int,
        category: str,
        entity_type: Optional[str],
        entity_id: Optional[int],
        within_seconds: int = 30,
    ) -> Optional[dict]:
        """Most-recent unread notification matching (category, entity)
        within the time window — used to coalesce chat bursts. None when
        nothing recent enough exists.

        We coalesce only on UNREAD entries: once the user has opened
        the bell and marked them read, a new notification is a new event
        worth showing distinctly.
        """
        cutoff = datetime.now() - timedelta(seconds=within_seconds)
        stmt = (
            select(
                NotificationORM.id,
                NotificationORM.title,
                NotificationORM.body,
                NotificationORM.payload_json,
                NotificationORM.created_at,
            )
            .where(
                NotificationORM.recipient_client_id == recipient_client_id,
                NotificationORM.category == category,
                NotificationORM.entity_type == entity_type,
                NotificationORM.entity_id == entity_id,
                NotificationORM.read_at.is_(None),
                NotificationORM.created_at > cutoff,
            )
            .order_by(NotificationORM.created_at.desc())
            .limit(1)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        return {
            "id": row.id,
            "title": row.title,
            "body": row.body,
            "payload_json": row.payload_json or {},
            "created_at": row.created_at,
        }

    async def update_coalesced(
        self,
        notif_id: int,
        title: str,
        body: Optional[str],
        payload: dict,
    ) -> None:
        """Bump the existing coalesced notification — refresh created_at
        so it sorts to the top of the feed."""
        await self.db.execute(
            update(NotificationORM)
            .where(NotificationORM.id == notif_id)
            .values(
                title=title,
                body=body,
                payload_json=payload or None,
                created_at=datetime.now(),
            )
        )


class DeviceTokenRepository:
    """Wraps the existing `fcm_tokens` table (one row per (client, token)).

    A single user can have multiple tokens (phone + tablet). On register
    we upsert; on logout the frontend calls DELETE to drop the token so
    the device stops getting pushes addressed to this account.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(
        self, client_id: int, platform: str, token: str,
    ) -> None:
        """Idempotent INSERT — re-registering the same token for the
        same client is a no-op (UNIQUE on fcm_token prevents dupes).
        If the token was previously bound to a DIFFERENT client (the
        phone changed accounts), reassign it to the new client."""
        # First try INSERT IGNORE for the common case (new token).
        stmt = mysql_insert(FcmTokenORM).values(
            user_id=client_id,
            gym_id=0,   # gym_mate notifications aren't gym-scoped; keep 0
            fcm_token=token,
            created_at=datetime.now(),
        ).prefix_with("IGNORE")
        await self.db.execute(stmt)
        # If the token already exists but bound to a different user_id,
        # rebind it. (INSERT IGNORE above would have skipped silently.)
        await self.db.execute(
            update(FcmTokenORM)
            .where(
                FcmTokenORM.fcm_token == token,
                FcmTokenORM.user_id != client_id,
            )
            .values(user_id=client_id, gym_id=0, created_at=datetime.now())
        )

    async def unregister(self, client_id: int, token: str) -> bool:
        result = await self.db.execute(
            delete(FcmTokenORM).where(
                FcmTokenORM.user_id == client_id,
                FcmTokenORM.fcm_token == token,
            )
        )
        return bool(result.rowcount)

    async def list_tokens(self, client_id: int) -> List[str]:
        """Tokens come from `clients.expo_token` first (Expo push tokens
        the legacy onboarding flow already registers). If that's empty,
        fall back to `clients.device_token` (raw FCM tokens).
        """
        row = (await self.db.execute(
            select(ClientORM.expo_token, ClientORM.device_token)
            .where(ClientORM.client_id == client_id)
        )).first()
        if row is None:
            return []
        expo_raw = row.expo_token
        device_raw = row.device_token
        expo = _coerce_token_list(expo_raw)
        if expo:
            return expo
        return _coerce_token_list(device_raw)

    async def drop_invalid_tokens(self, tokens: List[str]) -> int:
        """Remove dead tokens from BOTH `clients.expo_token` and
        `clients.device_token` for every client. Called when the push
        provider reports UNREGISTERED / NOT_FOUND / INVALID_ARGUMENT.
        """
        if not tokens:
            return 0
        dead = set(tokens)
        rows = (await self.db.execute(
            select(
                ClientORM.client_id, ClientORM.expo_token, ClientORM.device_token,
            ).where(
                or_(
                    ClientORM.expo_token.isnot(None),
                    ClientORM.device_token.isnot(None),
                )
            )
        )).all()
        dropped = 0
        for r in rows:
            expo = _coerce_token_list(r.expo_token)
            dev = _coerce_token_list(r.device_token)
            new_expo = [t for t in expo if t not in dead]
            new_dev = [t for t in dev if t not in dead]
            if len(new_expo) == len(expo) and len(new_dev) == len(dev):
                continue
            await self.db.execute(
                update(ClientORM)
                .where(ClientORM.client_id == r.client_id)
                .values(
                    expo_token=(new_expo or None),
                    device_token=(new_dev or None),
                )
            )
            dropped += (len(expo) - len(new_expo)) + (len(dev) - len(new_dev))
        return dropped


def _coerce_token_list(raw) -> List[str]:
    """clients.expo_token / device_token can be: list, JSON-encoded
    string, single string, or None. Normalise to a clean list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            try:
                import json
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(t) for t in parsed if t]
            except (ValueError, TypeError):
                pass
        return [s] if s else []
    return []

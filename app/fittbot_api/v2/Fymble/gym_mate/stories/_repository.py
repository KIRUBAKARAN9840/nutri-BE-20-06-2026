from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.client import Client as ClientORM
from app.utils.time_utils import utc_now
from app.models.fittbot_models.gymmate import (
    GymMateBlock as BlockORM,
    GymMateFriendship as FriendshipORM,
    GymMateProfile as GymMateProfileORM,
    GymMateProfilePhoto as GymMatePhotoORM,
    GymMateStory as StoryORM,
    GymMateStoryView as StoryViewORM,
)

from . import _domain as d


class StoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, story: d.Story) -> d.Story:
        row = StoryORM(
            client_id=story.client_id,
            media_type=story.media_type.value,
            s3_key=story.s3_key.value,
            thumbnail_key=story.thumbnail_key.value if story.thumbnail_key else None,
            caption=story.caption.value if story.caption else None,
            audience=story.audience.value,
            created_at=story.created_at,
            expires_at=story.expires_at,
            is_deleted=False,
        )
        self.db.add(row)
        await self.db.flush()
        story.id = row.id
        return story

    async def get_by_id(self, story_id: int) -> Optional[d.Story]:
        row = (await self.db.execute(
            select(StoryORM).where(StoryORM.id == story_id)
        )).scalar_one_or_none()
        if row is None:
            return None
        return self._to_domain(row)

    async def mark_deleted(self, story_id: int, deleted_at: datetime) -> None:
        await self.db.execute(
            update(StoryORM)
            .where(StoryORM.id == story_id)
            .values(is_deleted=True, deleted_at=deleted_at)
        )

    async def get_my_active_summary(self, client_id: int) -> dict:
        """Identity (client_id, name, avatar) is always returned so the
        my_story slot can render the same circle component as carousel
        items. Story-related fields are populated only when there's an
        active story.

        Avatar prefers the gym_mate primary photo, falling back to
        clients.profile — same precedence as matches/sessions DPs.
        """
        client_stmt = (
            select(
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(ClientORM)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ClientORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(ClientORM.client_id == client_id)
        )
        client_row = (await self.db.execute(client_stmt)).first()
        name = client_row.name if client_row else None
        avatar = client_row.avatar if client_row else None

        result = {
            "client_id": client_id,
            "name": name,
            "avatar_url": avatar,
            "has_active": False,
            "story_id": None,
            "story_count": 0,
            "view_count": 0,
            "expires_at": None,
            "latest_at": None,
        }

        latest_stmt = (
            select(StoryORM.id, StoryORM.expires_at, StoryORM.created_at)
            .where(
                (StoryORM.client_id == client_id)
                & (StoryORM.is_deleted.is_(False))
                & (StoryORM.expires_at > func.utc_timestamp())
            )
            .order_by(StoryORM.created_at.desc())
            .limit(1)
        )
        latest = (await self.db.execute(latest_stmt)).first()
        if latest is None:
            return result

        count_stmt = (
            select(func.count())
            .select_from(StoryORM)
            .where(
                (StoryORM.client_id == client_id)
                & (StoryORM.is_deleted.is_(False))
                & (StoryORM.expires_at > func.utc_timestamp())
            )
        )
        story_count = int((await self.db.execute(count_stmt)).scalar_one())

        views_stmt = (
            select(func.count(func.distinct(StoryViewORM.viewer_client_id)))
            .select_from(StoryViewORM)
            .join(StoryORM, StoryORM.id == StoryViewORM.story_id)
            .where(
                (StoryORM.client_id == client_id)
                & (StoryORM.is_deleted.is_(False))
                & (StoryORM.expires_at > func.utc_timestamp())
            )
        )
        view_count = int((await self.db.execute(views_stmt)).scalar_one())

        result.update({
            "has_active": True,
            "story_id": latest.id,
            "story_count": story_count,
            "view_count": view_count,
            "expires_at": latest.expires_at,
            "latest_at": latest.created_at,
        })
        return result

    async def get_home_carousel(self, viewer_id: int, limit: int = 20) -> List[dict]:
        """One row per author with active stories visible to viewer.
        Sorted: any_unviewed first, friends before public, newest first."""
        is_viewed = case((StoryViewORM.id.is_(None), 0), else_=1)
        is_friend = case((FriendshipORM.id.is_(None), 0), else_=1)

        all_viewed_expr = func.min(is_viewed)
        is_friend_expr = func.max(is_friend)
        latest_at_expr = func.max(StoryORM.created_at)
        story_count_expr = func.count()

        not_blocked = ~exists(
            select(BlockORM.id).where(
                or_(
                    and_(
                        BlockORM.blocker_client_id == viewer_id,
                        BlockORM.blocked_client_id == StoryORM.client_id,
                    ),
                    and_(
                        BlockORM.blocker_client_id == StoryORM.client_id,
                        BlockORM.blocked_client_id == viewer_id,
                    ),
                )
            )
        )

        stmt = (
            select(
                StoryORM.client_id.label("author_id"),
                ClientORM.name.label("author_name"),
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("author_avatar"),
                all_viewed_expr.label("all_viewed"),
                story_count_expr.label("story_count"),
                latest_at_expr.label("latest_at"),
                is_friend_expr.label("is_friend"),
            )
            .select_from(StoryORM)
            .join(ClientORM, ClientORM.client_id == StoryORM.client_id)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == StoryORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .outerjoin(
                StoryViewORM,
                and_(
                    StoryViewORM.story_id == StoryORM.id,
                    StoryViewORM.viewer_client_id == viewer_id,
                ),
            )
            .outerjoin(
                FriendshipORM,
                and_(
                    FriendshipORM.client_a_id
                        == func.least(viewer_id, StoryORM.client_id),
                    FriendshipORM.client_b_id
                        == func.greatest(viewer_id, StoryORM.client_id),
                ),
            )
            .where(
                and_(
                    StoryORM.client_id != viewer_id,
                    StoryORM.is_deleted.is_(False),
                    StoryORM.expires_at > func.utc_timestamp(),
                    or_(
                        StoryORM.audience == "public",
                        and_(
                            StoryORM.audience == "friends",
                            FriendshipORM.id.isnot(None),
                        ),
                    ),
                    not_blocked,
                )
            )
            .group_by(
                StoryORM.client_id,
                ClientORM.name,
                ClientORM.profile,
                GymMatePhotoORM.s3_path,
            )
            .order_by(
                all_viewed_expr.asc(),
                is_friend_expr.desc(),
                latest_at_expr.desc(),
            )
            .limit(limit)
        )

        rows = (await self.db.execute(stmt)).all()
        return [
            {
                "author_id": r.author_id,
                "author_name": r.author_name,
                "author_avatar": r.author_avatar,
                "all_viewed": int(r.all_viewed),
                "story_count": int(r.story_count),
                "latest_at": r.latest_at,
                "is_friend": int(r.is_friend),
            }
            for r in rows
        ]

    async def _is_blocked_either_way(self, a: int, b: int) -> bool:
        stmt = (
            select(BlockORM.id)
            .where(
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
            .limit(1)
        )
        return (await self.db.execute(stmt)).first() is not None

    async def get_active_stories_for_client(
        self, viewer_id: int, author_id: int
    ) -> Optional[dict]:
        """Author + their active stories the viewer is allowed to see.
        Returns None when the author has no visible active stories,
        or when either side has blocked the other (except for self-view)."""
        if viewer_id != author_id and await self._is_blocked_either_way(viewer_id, author_id):
            return None

        client_stmt = (
            select(
                ClientORM.client_id,
                ClientORM.name,
                func.coalesce(GymMatePhotoORM.s3_path, ClientORM.profile)
                    .label("avatar"),
            )
            .select_from(ClientORM)
            .outerjoin(
                GymMateProfileORM,
                GymMateProfileORM.client_id == ClientORM.client_id,
            )
            .outerjoin(
                GymMatePhotoORM,
                and_(
                    GymMatePhotoORM.profile_id == GymMateProfileORM.id,
                    GymMatePhotoORM.is_primary.is_(True),
                ),
            )
            .where(ClientORM.client_id == author_id)
        )
        client_row = (await self.db.execute(client_stmt)).first()
        if client_row is None:
            return None

        # Self-view: owner always sees every one of their own stories
        # regardless of audience setting. Friendship lookup is only needed
        # for the public/friends visibility check on someone else's stories.
        is_self = viewer_id == author_id
        if is_self:
            audience_filter = None
        else:
            is_friend_stmt = (
                select(FriendshipORM.id)
                .where(
                    (FriendshipORM.client_a_id == min(viewer_id, author_id))
                    & (FriendshipORM.client_b_id == max(viewer_id, author_id))
                )
                .limit(1)
            )
            friend_row = (await self.db.execute(is_friend_stmt)).first()
            is_friend = friend_row is not None
            audience_filter = (StoryORM.audience == "public")
            if is_friend:
                audience_filter = audience_filter | (StoryORM.audience == "friends")

        where_clauses = [
            StoryORM.client_id == author_id,
            StoryORM.is_deleted.is_(False),
            StoryORM.expires_at > func.utc_timestamp(),
        ]
        if audience_filter is not None:
            where_clauses.append(audience_filter)

        stories_stmt = (
            select(
                StoryORM.id,
                StoryORM.media_type,
                StoryORM.s3_key,
                StoryORM.thumbnail_key,
                StoryORM.caption,
                StoryORM.created_at,
                StoryORM.expires_at,
                StoryViewORM.id.label("view_id"),
            )
            .select_from(StoryORM)
            .outerjoin(
                StoryViewORM,
                (StoryViewORM.story_id == StoryORM.id)
                & (StoryViewORM.viewer_client_id == viewer_id),
            )
            .where(and_(*where_clauses))
            .order_by(StoryORM.created_at.asc())
        )
        rows = (await self.db.execute(stories_stmt)).all()

        if not rows and viewer_id != author_id:
            return None

        stories = [
            {
                "story_id": r.id,
                "media_type": r.media_type,
                "s3_key": r.s3_key,
                "thumbnail_key": r.thumbnail_key,
                "caption": r.caption,
                "created_at": r.created_at,
                "expires_at": r.expires_at,
                "is_viewed": r.view_id is not None,
            }
            for r in rows
        ]

        return {
            "author_id": client_row.client_id,
            "author_name": client_row.name,
            "author_avatar": client_row.avatar,
            "stories": stories,
        }

    async def record_view(self, viewer_id: int, story_id: int) -> bool:
        """Insert into story_view. Returns False if the story is not
        visible to the viewer (deleted, expired, not friend for
        friends-only). True on success or if already recorded."""
        story_stmt = (
            select(StoryORM.client_id, StoryORM.audience, StoryORM.is_deleted, StoryORM.expires_at)
            .where(StoryORM.id == story_id)
        )
        row = (await self.db.execute(story_stmt)).first()
        if row is None or row.is_deleted or row.expires_at <= utc_now():
            return False
        if row.client_id == viewer_id:
            return True
        if await self._is_blocked_either_way(viewer_id, row.client_id):
            return False
        if row.audience == "friends":
            f_stmt = (
                select(FriendshipORM.id)
                .where(
                    (FriendshipORM.client_a_id == min(viewer_id, row.client_id))
                    & (FriendshipORM.client_b_id == max(viewer_id, row.client_id))
                )
                .limit(1)
            )
            if (await self.db.execute(f_stmt)).first() is None:
                return False

        stmt = mysql_insert(StoryViewORM).values(
            story_id=story_id,
            viewer_client_id=viewer_id,
            viewed_at=utc_now(),
        )
        stmt = stmt.prefix_with("IGNORE")
        await self.db.execute(stmt)
        return True

    @staticmethod
    def _to_domain(row: StoryORM) -> d.Story:
        return d.Story(
            id=row.id,
            client_id=row.client_id,
            media_type=d.StoryMediaType(row.media_type),
            s3_key=d.S3MediaKey(row.s3_key),
            thumbnail_key=d.S3MediaKey(row.thumbnail_key) if row.thumbnail_key else None,
            caption=d.StoryCaption(row.caption) if row.caption else None,
            audience=d.StoryAudience(row.audience),
            created_at=row.created_at,
            expires_at=row.expires_at,
            is_deleted=bool(row.is_deleted),
            deleted_at=row.deleted_at,
        )

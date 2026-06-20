"""DB adapter for the profile module.

The ONLY file in this module that imports SQLAlchemy. Translates between
ORM rows and the domain aggregate. Domain code never sees ORM objects.

Query strategy:
  - Hot paths (status, is_onboarded, match attrs) use *lite* queries that
    fetch only the columns they need — no JOIN to profile_photo, no
    selectinload. One round trip, no row materialization for unused fields.
  - Mutation paths and the full-profile read use the heavy query with
    selectinload(photos).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.fittbot_models.client import Client as ClientORM
from app.models.fittbot_models.gymmate import (
    GymMateDefaultProfile as GymMateDefaultProfileORM,
    GymMateFriendRequest as GymMateFriendRequestORM,
    GymMateFriendship as GymMateFriendshipORM,
    GymMateProfile as GymMateProfileORM,
    GymMateProfilePhoto as GymMateProfilePhotoORM,
)

from . import _domain as d


class ProfileRepository:
    """Async SQLAlchemy repository for the Profile aggregate.

    Shares the caller's AsyncSession — transactions are managed by the
    service / FastAPI dependency, not here.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # =================================================================
    # HEAVY reads (load full aggregate)
    # =================================================================
    async def get_by_client_id(self, client_id: int) -> Optional[d.Profile]:
        """Load the full aggregate (profile + photos) — used by write paths
        and full-profile read. 1 + 1 queries via selectinload."""
        stmt = (
            select(GymMateProfileORM)
            .where(GymMateProfileORM.client_id == client_id)
            .options(selectinload(GymMateProfileORM.photos))
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row is not None else None

    # =================================================================
    # LITE reads — single round trip, no photos
    # =================================================================
    async def get_status_lite(
        self, client_id: int
    ) -> Optional[Tuple[int, bool]]:
        """Returns (profile_id, onboarding_completed) or None.

        Used by status endpoint and is_onboarded port — avoids loading
        photos and JSON columns when they're not needed.
        """
        stmt = (
            select(
                GymMateProfileORM.id,
                GymMateProfileORM.onboarding_completed,
            )
            .where(GymMateProfileORM.client_id == client_id)
        )
        row = (await self.db.execute(stmt)).first()
        return (row.id, bool(row.onboarding_completed)) if row else None

    async def get_match_attrs_lite(
        self, client_id: int
    ) -> Optional[dict]:
        """Returns just the columns the sessions module needs to compute
        a match score. Skips photos and bio entirely."""
        stmt = (
            select(
                GymMateProfileORM.primary_goal,
                GymMateProfileORM.activity_interests,
                GymMateProfileORM.preferred_timing,
                GymMateProfileORM.gym_personality,
            )
            .where(GymMateProfileORM.client_id == client_id)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        return {
            "primary_goal": row.primary_goal,
            "activity_interests": row.activity_interests,
            "preferred_timing": row.preferred_timing,
            "gym_personality": row.gym_personality,
        }

    async def get_client_name(self, client_id: int) -> Optional[str]:

        stmt = select(ClientORM.name).where(ClientORM.client_id == client_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_client_gender(self, client_id: int) -> Optional[str]:
        stmt = select(ClientORM.gender).where(ClientORM.client_id == client_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_default_avatars(
        self, gender: Optional[str],
    ) -> List[dict]:

        stmt = select(
            GymMateDefaultProfileORM.sno,
            GymMateDefaultProfileORM.profile_url,
        )
        normalised = (gender or "").strip().lower()
        if normalised in ("male", "female"):
            stmt = stmt.where(
                func.lower(GymMateDefaultProfileORM.gender) == normalised,
            )
        stmt = stmt.order_by(GymMateDefaultProfileORM.sno.asc())
        rows = (await self.db.execute(stmt)).all()
        return [{"sno": r.sno, "profile_url": r.profile_url} for r in rows]

    async def is_default_avatar_url(self, url: str) -> bool:
        # Whitelist check: a URL is only allowed as a profile photo if it
        # exists in the curated default_profile table. Prevents users from
        # sending arbitrary HTTPS URLs as their photo.
        stmt = (
            select(func.count())
            .select_from(GymMateDefaultProfileORM)
            .where(GymMateDefaultProfileORM.profile_url == url)
        )
        return int((await self.db.execute(stmt)).scalar_one()) > 0

    async def count_friends(self, client_id: int) -> int:
        """Single-query count of mutual friendships for this client."""
        stmt = (
            select(func.count())
            .select_from(GymMateFriendshipORM)
            .where(
                or_(
                    GymMateFriendshipORM.client_a_id == client_id,
                    GymMateFriendshipORM.client_b_id == client_id,
                )
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def count_pending_received_requests(self, client_id: int) -> int:
        """Pending friend requests TO this client — drives the badge."""
        stmt = (
            select(func.count())
            .select_from(GymMateFriendRequestORM)
            .where(
                (GymMateFriendRequestORM.to_client_id == client_id)
                & (GymMateFriendRequestORM.status == "pending")
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def get_summary_lite(
        self, client_id: int
    ) -> Optional[dict]:
        """Returns bio + onboarding_completed + the primary photo's S3 key
        via a single LEFT JOIN (constrained to is_primary=TRUE).

        One round trip; the cardinality is at most 1 + 1 (one profile,
        one primary photo).
        """
        stmt = (
            select(
                GymMateProfileORM.bio,
                GymMateProfileORM.onboarding_completed,
                GymMateProfilePhotoORM.s3_path,
            )
            .select_from(GymMateProfileORM)
            .join(
                GymMateProfilePhotoORM,
                (GymMateProfilePhotoORM.profile_id == GymMateProfileORM.id) &
                (GymMateProfilePhotoORM.is_primary.is_(True)),
                isouter=True,
            )
            .where(GymMateProfileORM.client_id == client_id)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        return {
            "bio": row.bio,
            "onboarding_completed": bool(row.onboarding_completed),
            "primary_photo_s3_path": row.s3_path,
        }

    # =================================================================
    # Write
    # =================================================================
    async def save(self, profile: d.Profile) -> d.Profile:
        """Insert or update the profile + photos atomically.

        Photo replacement strategy: if the aggregate has photos, delete-
        then-bulk-insert. Acceptable because:
          (a) max 3 photos per profile,
          (b) onboarding-photo edits are rare,
          (c) the deletion is by indexed FK column.
        Caller is responsible for `await db.commit()`.
        """
        if profile.id is None:
            row = self._new_orm_from_domain(profile)
            self.db.add(row)
            await self.db.flush()       # populates row.id
            profile.id = row.id
        else:
            row = await self._load_orm_for_update(profile.id)
            if row is None:
                raise LookupError(f"Profile id={profile.id} not found for update")
            self._apply_to_orm(profile, row)

        # Replace photo set only when the caller marked them dirty (a
        # genuine replace or an explicit "user removed photos" clear).
        # Untouched-on-this-write paths (Step 1 submissions, edit calls
        # that don't include photo_paths) skip the DELETE entirely.
        if profile._photos_dirty:
            await self.db.execute(
                delete(GymMateProfilePhotoORM)
                .where(GymMateProfilePhotoORM.profile_id == profile.id)
            )
            if profile.photos:
                self.db.add_all([
                    GymMateProfilePhotoORM(
                        profile_id=profile.id,
                        s3_path=p.s3_path,
                        display_order=p.display_order,
                        is_primary=p.is_primary,
                    )
                    for p in profile.photos
                ])

        await self.db.flush()
        return profile

    # =================================================================
    # Internal mappers
    # =================================================================
    async def _load_orm_for_update(
        self, profile_id: int
    ) -> Optional[GymMateProfileORM]:
        # Heavy load — we need photos to know whether to replace them.
        stmt = (
            select(GymMateProfileORM)
            .where(GymMateProfileORM.id == profile_id)
            .options(selectinload(GymMateProfileORM.photos))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _to_domain(row: GymMateProfileORM) -> d.Profile:
        # Defensive-read: skip photos whose s3_path fails domain
        # validation (e.g. legacy seed rows with non-`gym_mate/profile/`
        # prefixes). Writes go through the full presigned-upload flow
        # which enforces the prefix; this protects READS from a single
        # bad row poisoning the whole API call.
        photos: List[d.PhotoSlot] = []
        for p in sorted(row.photos, key=lambda x: x.display_order):
            try:
                photos.append(
                    d.PhotoSlot(s3_path=p.s3_path, display_order=p.display_order)
                )
            except d.ProfileDomainError:
                continue
        # Tolerate legacy/unknown values stored before the enum was
        # tightened. Writes still go through strict validation; only
        # READS are forgiving so a single bad row can't kill the API.
        cleaned_interests = tuple(
            v for v in (row.activity_interests or [])
            if v in d.ACTIVITY_INTEREST_VALUES
        )
        # If everything got filtered out (very old garbage data), keep
        # at least one safe default so the value object can construct.
        if not cleaned_interests:
            cleaned_interests = ("Cardio",)
        return d.Profile(
            id=row.id,
            client_id=row.client_id,
            primary_goal=d.PrimaryGoal(row.primary_goal),
            activity_interests=d.ActivityInterests(cleaned_interests),
            preferred_timing=d.PreferredTiming(row.preferred_timing),
            gym_personality=d.GymPersonality(row.gym_personality),
            city=row.city,
            bio=d.Bio(row.bio) if row.bio else None,
            photos=photos,
            onboarding_completed=bool(row.onboarding_completed),
        )

    @staticmethod
    def _new_orm_from_domain(profile: d.Profile) -> GymMateProfileORM:
        return GymMateProfileORM(
            client_id=profile.client_id,
            primary_goal=profile.primary_goal.value,
            activity_interests=profile.activity_interests.as_list(),
            preferred_timing=profile.preferred_timing.value,
            gym_personality=profile.gym_personality.value,
            city=profile.city,
            bio=profile.bio.value if profile.bio else None,
            onboarding_completed=profile.onboarding_completed,
        )

    @staticmethod
    def _apply_to_orm(profile: d.Profile, row: GymMateProfileORM) -> None:
        row.primary_goal = profile.primary_goal.value
        row.activity_interests = profile.activity_interests.as_list()
        row.preferred_timing = profile.preferred_timing.value
        row.gym_personality = profile.gym_personality.value
        row.city = profile.city
        row.bio = profile.bio.value if profile.bio else None
        row.onboarding_completed = profile.onboarding_completed

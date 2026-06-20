

from __future__ import annotations

from typing import List, Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._cache import ProfileCache
from ._events import EventBus, ProfileOnboarded
from ._repository import ProfileRepository
from ._storage import (
    PresignSlotRequest,
    PresignedPhotoUpload,
    ProfilePhotoStorage,
    build_cdn_url,
)


FRIEND_SUGGESTIONS_LIMIT = 5


class ProfileService:
    def __init__(
        self,
        repository: ProfileRepository,
        event_bus: EventBus,
        cache: ProfileCache,
        storage: ProfilePhotoStorage,
        friends_api=None,   
    ):
        self.repo = repository
        self.bus = event_bus
        self.cache = cache
        self.storage = storage
        self.friends = friends_api


    async def submit_step1(
        self,
        client_id: int,
        primary_goal: str,
        activity_interests: List[str],
        preferred_timing: str,
        gym_personality: str,
        city: Optional[str] = None,
    ) -> dto.OnboardingStatusDTO:

        try:
            goal_vo = d.PrimaryGoal.from_input(primary_goal)
            timing_vo = d.PreferredTiming.from_input(preferred_timing)
            vibe_vo = d.GymPersonality.from_input(gym_personality)
            interests_vo = d.ActivityInterests(tuple(activity_interests or []))
        except (ValueError, d.ProfileDomainError) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_PROFILE_STEP1_INVALID",
                log_data={"client_id": client_id, "exc": repr(exc)},
            )

        city = city.strip() if city else None

        existing = await self.repo.get_by_client_id(client_id)
        if existing is None:
            profile = d.Profile.start(
                client_id=client_id,
                primary_goal=goal_vo,
                activity_interests=interests_vo,
                preferred_timing=timing_vo,
                gym_personality=vibe_vo,
                city=city,
            )
        else:
            profile = existing
            profile.update_step1(
                primary_goal=goal_vo,
                activity_interests=interests_vo,
                preferred_timing=timing_vo,
                gym_personality=vibe_vo,
                city=city,
            )

        await self.repo.save(profile)
        await self.cache.invalidate(client_id)
        return self._to_status_dto(profile)

    # =================================================================
    # WRITE — Step 2
    # =================================================================
    async def submit_step2(
        self,
        client_id: int,
        photo_paths: Optional[List[str]],
        bio: Optional[str],
    ) -> dto.OnboardingStatusDTO:

        # Normalise inputs
        photo_paths = photo_paths or []

        # Security: each photo must EITHER live under this user's S3
        # prefix (their own upload) OR match a row in default_profile
        # (curated avatar gallery). Arbitrary HTTPS URLs are rejected.
        await self._validate_photo_paths(
            client_id, photo_paths,
            error_code="GYMMATE_PROFILE_STEP2_FOREIGN_KEY",
        )

        profile = await self.repo.get_by_client_id(client_id)
        if profile is None:
            raise FittbotHTTPException(
                status_code=400,
                detail="Submit Step 1 before Step 2",
                error_code="GYMMATE_PROFILE_STEP2_BEFORE_STEP1",
                log_data={"client_id": client_id},
            )

        was_first_time = not profile.onboarding_completed

        try:
            photos = [
                d.PhotoSlot(s3_path=path, display_order=i)
                for i, path in enumerate(photo_paths)
            ]
            bio_vo = d.Bio(bio) if bio else None
            profile.complete_step2(photos=photos, bio=bio_vo)
        except d.ProfileDomainError as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_PROFILE_STEP2_INVALID",
                log_data={"client_id": client_id, "exc": repr(exc)},
            )

        await self.repo.save(profile)
        await self.cache.invalidate(client_id)

        if was_first_time:
            await self.bus.publish(
                ProfileOnboarded(client_id=client_id, profile_id=profile.id)
            )

        return self._to_status_dto(profile)

    # =================================================================
    # WRITE — presign upload URLs
    # =================================================================
    async def presign_photos(
        self,
        client_id: int,
        slots: List[PresignSlotRequest],
    ) -> List[PresignedPhotoUpload]:
        """Issue presigned POST URLs scoped to this client's S3 prefix."""
        return self.storage.presign_uploads(client_id, slots)

    # =================================================================
    # READ — onboarding status (HOT — cache first, lite DB on miss)
    # =================================================================
    async def get_status(self, client_id: int) -> dto.OnboardingStatusDTO:
        cached = await self.cache.get_status(client_id)
        if cached is not None:
            return cached

        row = await self.repo.get_status_lite(client_id)
        if row is None:
            status = dto.OnboardingStatusDTO(
                client_id=client_id,
                next_step=1,
                onboarding_completed=False,
            )
        else:
            _profile_id, completed = row
            status = dto.OnboardingStatusDTO(
                client_id=client_id,
                next_step=0 if completed else 2,
                onboarding_completed=completed,
            )
        await self.cache.set_status(status)
        return status

    async def list_default_avatars_for_client(
        self, client_id: int,
    ) -> tuple[Optional[str], List[dto.DefaultAvatarDTO]]:
        """Returns (gender_from_clients, avatars). Gender comes from the
        central clients table, not the request body — single source of
        truth. Anything other than male/female falls back to all 20 rows."""
        gender = await self.repo.get_client_gender(client_id)
        rows = await self.repo.list_default_avatars(gender)
        avatars = [
            dto.DefaultAvatarDTO(sno=r["sno"], profile_url=r["profile_url"])
            for r in rows
        ]
        return gender, avatars

    async def get_onboarding_step2_suggestions(self, client_id: int):
        """Passthrough to FriendsAPI — keeps the contract on ProfileAPI
        so the Step 2 route doesn't have to depend on friends directly.
        Returns [] when friends_api isn't wired (older tests / callers).
        """
        if self.friends is None:
            return []
        return await self.friends.get_onboarding_step2_suggestions(
            client_id=client_id,
        )

    async def get_full_profile(
        self,
        target_client_id: int,
        viewer_client_id: Optional[int] = None,
    ) -> dto.FullProfileDTO:

        if viewer_client_id is None:
            viewer_client_id = target_client_id


        if viewer_client_id != target_client_id:
            from app.fittbot_api.v2.Fymble.gym_mate.blocks._repository import (
                BlockRepository,
            )
            if await BlockRepository(self.repo.db).is_blocked_either_way(
                viewer_client_id, target_client_id,
            ):
                raise FittbotHTTPException(
                    status_code=404,
                    detail="Profile not found",
                    error_code="GYMMATE_PROFILE_NOT_FOUND",
                    log_data={
                        "client_id": target_client_id,
                        "viewer": viewer_client_id,
                        "blocked": True,
                    },
                )

        profile = await self.repo.get_by_client_id(target_client_id)
        if profile is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Profile not found",
                error_code="GYMMATE_PROFILE_NOT_FOUND",
                log_data={"client_id": target_client_id},
            )

        friends_count = await self.repo.count_friends(target_client_id)
        pending_count = await self.repo.count_pending_received_requests(target_client_id)
        name = await self.repo.get_client_name(target_client_id)

        extra_exclude = (
            {target_client_id} if target_client_id != viewer_client_id else None
        )
        suggestions = await self._fetch_friend_suggestions(
            viewer_client_id, extra_exclude=extra_exclude,
        )
        # Mutual friends + relationship only make sense when viewing
        # someone else's profile (never on own).
        mutual_friends = await self._fetch_mutual_friends(
            viewer_client_id, target_client_id,
        )
        relationship = await self._fetch_relationship(
            viewer_client_id, target_client_id,
        )
        match_pct, target_goal = await self._fetch_match_and_goal(
            viewer_client_id, target_client_id, profile,
        )

        return self._to_full_profile_dto(
            profile, name, friends_count, pending_count,
            suggestions, mutual_friends, relationship,
            match_pct, target_goal,
        )

    # =================================================================
    # WRITE — edit profile (partial update of any subset of fields)
    # =================================================================
    async def edit_profile(
        self,
        client_id: int,
        primary_goal: Optional[str] = None,
        activity_interests: Optional[List[str]] = None,
        preferred_timing: Optional[str] = None,
        gym_personality: Optional[str] = None,
        bio: Optional[str] = None,
        city: Optional[str] = None,
        photo_paths: Optional[List[str]] = None,
    ) -> dto.FullProfileDTO:
        # Same rule as Step 2: own S3 key OR curated default avatar URL.
        await self._validate_photo_paths(
            client_id, photo_paths or [],
            error_code="GYMMATE_PROFILE_EDIT_FOREIGN_KEY",
        )

        profile = await self.repo.get_by_client_id(client_id)
        if profile is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Profile not found — finish onboarding first",
                error_code="GYMMATE_PROFILE_NOT_FOUND",
                log_data={"client_id": client_id},
            )

        try:
            goal_vo = d.PrimaryGoal(primary_goal) if primary_goal is not None else None
            timing_vo = d.PreferredTiming(preferred_timing) if preferred_timing is not None else None
            vibe_vo = d.GymPersonality(gym_personality) if gym_personality is not None else None
            interests_vo = (
                d.ActivityInterests(tuple(activity_interests))
                if activity_interests is not None
                else None
            )
            bio_vo = d.Bio(bio) if bio else None
            # City is free text like bio: send a string to set it (empty/blank
            # clears to NULL); omit (None) to leave it untouched.
            city_val = city.strip() if isinstance(city, str) else None

            clear_photos = isinstance(photo_paths, list) and len(photo_paths) == 0
            photos = [
                d.PhotoSlot(s3_path=path, display_order=i)
                for i, path in enumerate(photo_paths or [])
            ]

            profile.update_partial(
                primary_goal=goal_vo,
                activity_interests=interests_vo,
                preferred_timing=timing_vo,
                gym_personality=vibe_vo,
                bio=bio_vo,
                city=city_val,
                photos=photos,
                clear_photos=clear_photos,
            )
        except (ValueError, d.ProfileDomainError) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_PROFILE_EDIT_INVALID",
                log_data={"client_id": client_id, "exc": repr(exc)},
            )

        await self.repo.save(profile)
        await self.cache.invalidate(client_id)

        friends_count = await self.repo.count_friends(client_id)
        pending_count = await self.repo.count_pending_received_requests(client_id)
        name = await self.repo.get_client_name(client_id)
        suggestions = await self._fetch_friend_suggestions(client_id)
        return self._to_full_profile_dto(
            profile, name, friends_count, pending_count, suggestions,
        )

    async def _validate_photo_paths(
        self, client_id: int, photo_paths: List[str], *, error_code: str,
    ) -> None:
        if not photo_paths:
            return
        expected_prefix = ProfilePhotoStorage.expected_prefix_for(client_id)
        for path in photo_paths:
            if path.startswith(expected_prefix):
                continue
            if path.startswith(("http://", "https://")):
                if await self.repo.is_default_avatar_url(path):
                    continue
            raise FittbotHTTPException(
                status_code=400,
                detail="Photo path does not belong to this user",
                error_code=error_code,
                log_data={"client_id": client_id, "path": path},
            )

    async def _fetch_friend_suggestions(self, client_id: int, extra_exclude=None):

        if self.friends is None:
            return []
        return await self.friends.suggest_for_home(
            client_id=client_id,
            limit=FRIEND_SUGGESTIONS_LIMIT,
            extra_exclude=extra_exclude,
        )

    async def _fetch_mutual_friends(self, viewer_id: int, target_id: int):
        """Up to 3 friends-in-common — only when viewing someone else.
        Returns [] for self-view (viewer == target)."""
        if self.friends is None or viewer_id == target_id:
            return []
        return await self.friends.list_mutual_friends(
            viewer_id=viewer_id, target_id=target_id, limit=3,
        )

    async def _fetch_relationship(self, viewer_id: int, target_id: int):
        """Returns None on own profile; the RelationshipDTO on others'."""
        if self.friends is None or viewer_id == target_id:
            return None
        return await self.friends.get_relationship(
            viewer_id=viewer_id, target_id=target_id,
        )

    async def _fetch_match_and_goal(self, viewer_id: int, target_id: int, target_profile):
        """Returns (match_percentage, target_primary_goal) — both None
        on own profile or when viewer hasn't onboarded."""
        if self.friends is None or viewer_id == target_id:
            return None, None
        info = await self.friends.get_match_info(
            viewer_id=viewer_id, target_id=target_id,
        )
        if info is None:
            return None, None
        return info.percentage, target_profile.primary_goal.value

    # =================================================================
    # CROSS-MODULE PORTS (cache-first, lite DB on miss)
    # =================================================================
    async def is_onboarded(self, client_id: int) -> bool:
        # Reuse the cached/lite status path — same data.
        status = await self.get_status(client_id)
        return status.onboarding_completed

    async def get_match_attributes(
        self, client_id: int
    ) -> Optional[dto.ProfileMatchAttributesDTO]:
        cached = await self.cache.get_match_attrs(client_id)
        if cached is not None:
            return cached

        row = await self.repo.get_match_attrs_lite(client_id)
        if row is None:
            return None

        attrs = dto.ProfileMatchAttributesDTO(
            client_id=client_id,
            primary_goal=row["primary_goal"],
            activity_interests=list(row["activity_interests"]),
            preferred_timing=row["preferred_timing"],
            gym_personality=row["gym_personality"],
        )
        await self.cache.set_match_attrs(attrs)
        return attrs

    async def get_summary(
        self, client_id: int
    ) -> Optional[dto.ProfileSummaryDTO]:
        cached = await self.cache.get_summary(client_id)
        if cached is not None:
            return cached

        row = await self.repo.get_summary_lite(client_id)
        if row is None:
            return None

        primary_key = row["primary_photo_s3_path"]
        summary = dto.ProfileSummaryDTO(
            client_id=client_id,
            primary_photo_url=build_cdn_url(primary_key) if primary_key else None,
            bio=row["bio"],
            onboarding_completed=row["onboarding_completed"],
        )
        await self.cache.set_summary(summary)
        return summary

    # =================================================================
    # Helpers
    # =================================================================
    @staticmethod
    def _to_status_dto(profile: d.Profile) -> dto.OnboardingStatusDTO:
        return dto.OnboardingStatusDTO(
            client_id=profile.client_id,
            next_step=0 if profile.onboarding_completed else 2,
            onboarding_completed=profile.onboarding_completed,
        )

    @staticmethod
    def _to_full_profile_dto(
        profile: d.Profile,
        name: Optional[str],
        friends_count: int,
        pending_received_requests_count: int,
        friend_suggestions: Optional[list] = None,
        mutual_friends: Optional[list] = None,
        relationship=None,
        match: Optional[int] = None,
        goal: Optional[str] = None,
    ) -> dto.FullProfileDTO:
        return dto.FullProfileDTO(
            client_id=profile.client_id,
            name=name,
            primary_goal=profile.primary_goal.value,
            activity_interests=profile.activity_interests.as_list(),
            preferred_timing=profile.preferred_timing.value,
            gym_personality=profile.gym_personality.value,
            details=d.build_profile_details(
                profile.primary_goal.value,
                profile.activity_interests.as_list(),
                profile.preferred_timing.value,
                profile.gym_personality.value,
            ),
            bio=profile.bio.value if profile.bio else None,
            city=profile.city,
            photos=[
                dto.PhotoDTO(
                    s3_path=p.s3_path,
                    cdn_url=build_cdn_url(p.s3_path),
                    display_order=p.display_order,
                    is_primary=p.is_primary,
                )
                for p in profile.photos
            ],
            onboarding_completed=profile.onboarding_completed,
            social=dto.SocialCountsDTO(
                friends_count=friends_count,
                pending_received_requests_count=pending_received_requests_count,
            ),
            friend_suggestions=friend_suggestions or [],
            mutual_friends=mutual_friends or [],
            relationship=relationship,
            match=match,
            goal=goal,
        )



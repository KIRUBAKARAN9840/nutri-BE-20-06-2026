

from __future__ import annotations

from typing import List, Optional, Protocol

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v2.Fymble.gym_mate.friends import OnboardingSuggestionDTO
from .schemas import (
    DefaultAvatarDTO,
    FullProfileDTO,
    OnboardingStatusDTO,
    PresignedPhotoUploadDTO,
    ProfileMatchAttributesDTO,
    ProfileSummaryDTO,
)
from ._events import EventBus, NoopEventBus
from ._storage import PresignSlotRequest


class ProfileAPI(Protocol):
    """The contract other modules program against."""

    # ------- Onboarding (also exposed via HTTP) ----------------------
    async def submit_step1(
        self,
        client_id: int,
        primary_goal: str,
        activity_interests: List[str],
        preferred_timing: str,
        gym_personality: str,
        city: Optional[str] = None,
    ) -> OnboardingStatusDTO: ...

    async def submit_step2(
        self,
        client_id: int,
        photo_paths: Optional[List[str]],
        bio: Optional[str],
    ) -> OnboardingStatusDTO: ...

    async def presign_photos(
        self,
        client_id: int,
        slots: List[PresignSlotRequest],
    ) -> List[PresignedPhotoUploadDTO]: ...

    async def get_status(self, client_id: int) -> OnboardingStatusDTO: ...

    async def get_onboarding_step2_suggestions(
        self, client_id: int,
    ) -> List[OnboardingSuggestionDTO]: ...

    async def list_default_avatars_for_client(
        self, client_id: int,
    ) -> tuple[Optional[str], List[DefaultAvatarDTO]]: ...

    async def get_full_profile(
        self,
        target_client_id: int,
        viewer_client_id: Optional[int] = None,
    ) -> FullProfileDTO: ...

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
    ) -> FullProfileDTO: ...

    # ------- Cross-module port methods -------------------------------
    async def is_onboarded(self, client_id: int) -> bool: ...

    async def get_match_attributes(
        self, client_id: int
    ) -> Optional[ProfileMatchAttributesDTO]: ...

    async def get_summary(
        self, client_id: int
    ) -> Optional[ProfileSummaryDTO]: ...


def build_profile_api(
    db: AsyncSession,
    redis: Optional[Redis] = None,
    *,
    event_bus: Optional[EventBus] = None,
) -> ProfileAPI:

    from app.fittbot_api.v2.Fymble.gym_mate.friends import build_friends_api

    from ._cache import ProfileCache
    from ._repository import ProfileRepository
    from ._service import ProfileService
    from ._storage import ProfilePhotoStorage

    return ProfileService(
        repository=ProfileRepository(db),
        event_bus=event_bus or NoopEventBus(),
        cache=ProfileCache(redis),
        storage=ProfilePhotoStorage(),
        friends_api=build_friends_api(db, redis),
    )

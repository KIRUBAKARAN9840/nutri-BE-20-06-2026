
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import FittbotHTTPException, log_exceptions
from app.utils.redis_config import get_redis

from .api import ProfileAPI, build_profile_api
from ._http_schemas import (
    EditProfileRequest,
    EditProfileResponse,
    GetProfileResponse,
    OnboardingStatusResponse,
    OnboardingStep1Request,
    OnboardingStep1Response,
    OnboardingStep2Request,
    OnboardingStep2Response,
    PresignPhotosRequest,
    PresignPhotosResponse,
)
from ._storage import PresignSlotRequest
from .schemas import PresignedPhotoUploadDTO, PresignedUploadEnvelopeDTO


router = APIRouter(prefix="/gym_mate/profile", tags=["GymMate Profile V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> ProfileAPI:
    return build_profile_api(db, redis)


@router.post(
    "/onboarding/step1",
    response_model=OnboardingStep1Response,
)
@log_exceptions
async def submit_onboarding_step1(
    req: OnboardingStep1Request,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ProfileAPI = Depends(_api),
):
    status = await api.submit_step1(
        client_id=client_id,
        primary_goal=req.primary_goal,
        activity_interests=req.activity_interests,
        preferred_timing=req.preferred_timing,
        gym_personality=req.gym_personality,
        city=req.city
    )
    await db.commit()
    gender, avatars = await api.list_default_avatars_for_client(client_id)
    return OnboardingStep1Response(
        data=status, gender=gender, default_avatars=avatars,
    )


@router.post(
    "/photos/presign",
    response_model=PresignPhotosResponse,
)
@log_exceptions
async def presign_profile_photos(
    req: PresignPhotosRequest,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: ProfileAPI = Depends(_api),
):
    slots = [
        PresignSlotRequest(
            display_order=s.display_order,
            content_type=s.content_type,
        )
        for s in req.slots
    ]
    uploads = await api.presign_photos(client_id=client_id, slots=slots)
    return PresignPhotosResponse(data=[
        PresignedPhotoUploadDTO(
            upload=PresignedUploadEnvelopeDTO(
                url=u.url,
                fields=u.fields,
            ),
            cdn_url=u.cdn_url,
            version=u.version,
        )
        for u in uploads
    ])


# ---------------------------------------------------------------------------
# Onboarding — Step 2: photos + bio (marks onboarding complete)
# Screen: "Step 2 of 2 — Profile Photos & Bio"
# ---------------------------------------------------------------------------
@router.post(
    "/onboarding/step2",
    response_model=OnboardingStep2Response,
)
@log_exceptions
async def submit_onboarding_step2(
    req: OnboardingStep2Request,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ProfileAPI = Depends(_api),
):
    status = await api.submit_step2(
        client_id=client_id,
        photo_paths=req.photo_paths,
        bio=req.bio,
    )
    await db.commit()
    suggested = await api.get_onboarding_step2_suggestions(client_id)
    return OnboardingStep2Response(
        data=status, suggested_gym_mates=suggested,
    )



@router.get(
    "/onboarding/status",
    response_model=OnboardingStatusResponse,
)
@log_exceptions
async def get_onboarding_status(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: ProfileAPI = Depends(_api),
):
    status = await api.get_status(client_id)
    return OnboardingStatusResponse(data=status)


# ---------------------------------------------------------------------------
# Full profile read (own profile screen)
# Returns the 4 Step-1 fields, bio, kept photos, and social counts.
# ---------------------------------------------------------------------------
@router.get(
    "/me",
    response_model=GetProfileResponse,
)
@log_exceptions
async def get_my_profile(
    request: Request,
    view: str = Query(
        "own",
        pattern="^(own|others)$",
        description="`own` (default) returns the viewer's own profile. "
                    "`others` requires `client_id` and returns that profile.",
    ),
    client_id: Optional[int] = Query(
        None, ge=1,
        description="Required when view=others. Ignored when view=own.",
    ),
    viewer_id: int = Depends(get_verified_client_id),
    api: ProfileAPI = Depends(_api),
):
    if view == "own":
        target_id = viewer_id
    else:  # view == "others"
        if client_id is None:
            raise FittbotHTTPException(
                status_code=400,
                detail="client_id query param is required when view=others",
                error_code="GYMMATE_PROFILE_TARGET_MISSING",
                log_data={"viewer_id": viewer_id},
            )
        target_id = client_id

    profile = await api.get_full_profile(
        target_client_id=target_id, viewer_client_id=viewer_id,
    )
    # Avatars only on own profile — when viewing someone else's profile
    # the picker is irrelevant.
    if view == "own":
        gender, avatars = await api.list_default_avatars_for_client(viewer_id)
    else:
        gender, avatars = None, []
    return GetProfileResponse(
        data=profile, gender=gender, default_avatars=avatars,
    )


@router.put(
    "/me",
    response_model=EditProfileResponse,
)
@log_exceptions
async def edit_my_profile(
    req: EditProfileRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ProfileAPI = Depends(_api),
):
    profile = await api.edit_profile(
        client_id=client_id,
        primary_goal=req.primary_goal,
        activity_interests=req.activity_interests,
        preferred_timing=req.preferred_timing,
        gym_personality=req.gym_personality,
        bio=req.bio,
        city=req.city,
        photo_paths=req.photo_paths,
    )
    await db.commit()
    gender, avatars = await api.list_default_avatars_for_client(client_id)
    return EditProfileResponse(
        data=profile, gender=gender, default_avatars=avatars,
    )

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import StoriesAPI, build_stories_api
from ._http_schemas import (
    CreateStoryRequest,
    CreateStoryResponse,
    DeleteStoryResponse,
    PresignStoryMediaRequest,
    PresignStoryMediaResponse,
    StoriesByClientResponse,
    ViewStoryResponse,
)
from .schemas import PresignedStoryUploadDTO, PresignedUploadEnvelopeDTO


router = APIRouter(prefix="/gym_mate/stories", tags=["GymMate Stories V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> StoriesAPI:
    # Wire the home-cache invalidator so create/delete/view from any
    # standalone story endpoint immediately busts the actor's home cache.
    from app.fittbot_api.v2.Fymble.gym_mate.home._cache import make_home_invalidator
    try:
        from app.fittbot_api.v2.Fymble.gym_mate.notifications import (
            gymmate_event_bus,
        )
        return build_stories_api(
            db, redis,
            event_bus=gymmate_event_bus,
            on_owner_change=make_home_invalidator(redis),
        )
    except ImportError:
        return build_stories_api(
            db, redis, on_owner_change=make_home_invalidator(redis),
        )


@router.post("/media/presign", response_model=PresignStoryMediaResponse)
@log_exceptions
async def presign_story_media(
    req: PresignStoryMediaRequest,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: StoriesAPI = Depends(_api),
):
    upload = await api.presign_media(client_id=client_id, content_type=req.content_type)
    return PresignStoryMediaResponse(
        data=PresignedStoryUploadDTO(
            upload=PresignedUploadEnvelopeDTO(url=upload.url, fields=upload.fields),
            cdn_url=upload.cdn_url,
            version=upload.version,
        )
    )


@router.post("", response_model=CreateStoryResponse)
@log_exceptions
async def create_story(
    req: CreateStoryRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: StoriesAPI = Depends(_api),
):
    story = await api.create_story(
        client_id=client_id,
        s3_key=req.s3_key,
        media_type=req.media_type,
        caption=req.caption,
        audience=req.audience,
        thumbnail_key=req.thumbnail_key,
    )
    await db.commit()
    return CreateStoryResponse(data=story)


@router.delete("/{story_id}", response_model=DeleteStoryResponse)
@log_exceptions
async def delete_story(
    story_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: StoriesAPI = Depends(_api),
):
    await api.delete_story(client_id=client_id, story_id=story_id)
    await db.commit()
    return DeleteStoryResponse()


@router.get("/by-client/{author_id}", response_model=StoriesByClientResponse)
@log_exceptions
async def get_stories_by_client(
    author_id: int,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: StoriesAPI = Depends(_api),
):
    data = await api.get_stories_for_client(viewer_id=client_id, author_id=author_id)
    return StoriesByClientResponse(data=data)


@router.post("/{story_id}/view", response_model=ViewStoryResponse)
@log_exceptions
async def record_story_view(
    story_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: StoriesAPI = Depends(_api),
):
    await api.record_view(viewer_id=client_id, story_id=story_id)
    await db.commit()
    return ViewStoryResponse()

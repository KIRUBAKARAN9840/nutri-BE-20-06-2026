from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import BlocksAPI, build_blocks_api
from ._http_schemas import (
    BlockUserRequest,
    BlockUserResponse,
    ListBlockedResponse,
    UnblockUserResponse,
)


router = APIRouter(prefix="/gym_mate/blocks", tags=["GymMate Blocks V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> BlocksAPI:
    from app.fittbot_api.v2.Fymble.gym_mate.home._cache import make_home_invalidator
    return build_blocks_api(db, redis, on_change=make_home_invalidator(redis))


@router.post("", response_model=BlockUserResponse)
@log_exceptions
async def block_user(
    req: BlockUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: BlocksAPI = Depends(_api),
):
    await api.block(blocker_id=client_id, blocked_id=req.blocked_client_id)
    await db.commit()
    return BlockUserResponse()


@router.delete("/{blocked_client_id}", response_model=UnblockUserResponse)
@log_exceptions
async def unblock_user(
    blocked_client_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: BlocksAPI = Depends(_api),
):
    await api.unblock(blocker_id=client_id, blocked_id=blocked_client_id)
    await db.commit()
    return UnblockUserResponse()


@router.get("", response_model=ListBlockedResponse)
@log_exceptions
async def list_blocked(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: BlocksAPI = Depends(_api),
):
    data = await api.list_blocked(blocker_id=client_id)
    return ListBlockedResponse(data=data)

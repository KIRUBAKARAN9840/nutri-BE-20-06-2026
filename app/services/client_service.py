"""
Reusable FastAPI dependencies for fetching and validating Client / Gym entities.

Eliminates the "query + if-not-found + raise 404" boilerplate duplicated in
80+ endpoint files.
"""

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.fittbot_models import Client, Gym
from app.utils.logging_utils import FittbotHTTPException


# ── Sync helpers (for endpoints using Session / get_db) ─────

def get_client_or_404(db: Session, client_id: int) -> Client:
    """
    Fetch a Client by ID or raise 404.
    Use in sync endpoints: `client = get_client_or_404(db, client_id)`
    """
    client = db.query(Client).filter(Client.client_id == client_id).first()
    if not client:
        raise FittbotHTTPException(
            status_code=404,
            detail="Client not found",
            error_code="CLIENT_NOT_FOUND",
            log_data={"client_id": client_id},
        )
    return client


def get_gym_or_404(db: Session, gym_id: int) -> Gym:
    """
    Fetch a Gym by ID or raise 404.
    Use in sync endpoints: `gym = get_gym_or_404(db, gym_id)`
    """
    gym = db.query(Gym).filter(Gym.gym_id == gym_id).first()
    if not gym:
        raise FittbotHTTPException(
            status_code=404,
            detail="Gym not found",
            error_code="GYM_NOT_FOUND",
            log_data={"gym_id": gym_id},
        )
    return gym


# ── Async helpers (for endpoints using AsyncSession / get_async_db) ─

async def async_get_client_or_404(db: AsyncSession, client_id: int) -> Client:
    """
    Async version of get_client_or_404.
    Use in async endpoints: `client = await async_get_client_or_404(db, client_id)`
    """
    stmt = select(Client).where(Client.client_id == client_id)
    result = await db.execute(stmt)
    client = result.scalars().first()
    if not client:
        raise FittbotHTTPException(
            status_code=404,
            detail="Client not found",
            error_code="CLIENT_NOT_FOUND",
            log_data={"client_id": client_id},
        )
    return client


async def async_get_gym_or_404(db: AsyncSession, gym_id: int) -> Gym:
    """
    Async version of get_gym_or_404.
    Use in async endpoints: `gym = await async_get_gym_or_404(db, gym_id)`
    """
    stmt = select(Gym).where(Gym.gym_id == gym_id)
    result = await db.execute(stmt)
    gym = result.scalars().first()
    if not gym:
        raise FittbotHTTPException(
            status_code=404,
            detail="Gym not found",
            error_code="GYM_NOT_FOUND",
            log_data={"gym_id": gym_id},
        )
    return gym

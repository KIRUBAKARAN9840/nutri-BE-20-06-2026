"""Database & Redis queries for registration steps.

All raw DB/Redis access lives here. No business logic.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import (
    CharactersCombination,
    Client,
    ClientCharacter,
    ClientTarget,
    ClientWeightSelection,
)


class RegistrationStepsRepository:
    """Encapsulates all DB + Redis queries for the registration steps flow."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # -- Client lookup ---------------------------------------------------------

    async def get_client_by_id(self, client_id: int) -> Optional[Client]:
        stmt = select(Client).where(Client.client_id == client_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    # -- Client Target ---------------------------------------------------------

    async def get_client_target(self, client_id: int) -> Optional[ClientTarget]:
        stmt = select(ClientTarget).where(ClientTarget.client_id == client_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def create_client_target(self, client_id: int, water_intake: float) -> ClientTarget:
        target = ClientTarget(client_id=client_id, water_intake=water_intake)
        self.db.add(target)
        return target

    # -- Body Shape ------------------------------------------------------------

    async def get_characters_combination(
        self, current_id: str, target_id: str
    ) -> Optional[CharactersCombination]:
        stmt = select(CharactersCombination).where(
            CharactersCombination.characters_id == current_id,
            CharactersCombination.combination_id == target_id,
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def upsert_client_character(self, client_id: int, character_id: int) -> None:
        stmt = select(ClientCharacter).where(ClientCharacter.client_id == client_id)
        result = await self.db.execute(stmt)
        existing = result.scalars().first()

        if existing:
            existing.character_id = character_id
        else:
            self.db.add(ClientCharacter(client_id=client_id, character_id=character_id))

    async def upsert_weight_selection(
        self, client_id: int, current_image_id: str, target_image_id: str
    ) -> None:
        stmt = select(ClientWeightSelection).where(
            ClientWeightSelection.client_id == str(client_id)
        )
        result = await self.db.execute(stmt)
        existing = result.scalars().first()

        combination_id = f"{current_image_id}+{target_image_id}"
        if existing:
            existing.current_image_id = current_image_id
            existing.target_image_id = target_image_id
            existing.combination_id = combination_id
        else:
            self.db.add(
                ClientWeightSelection(
                    client_id=str(client_id),
                    current_image_id=current_image_id,
                    target_image_id=target_image_id,
                    combination_id=combination_id,
                )
            )

    async def get_weight_selection(self, client_id: int) -> Optional[ClientWeightSelection]:
        stmt = select(ClientWeightSelection).where(
            ClientWeightSelection.client_id == str(client_id)
        )
        result = await self.db.execute(stmt)
        return result.scalars().first()

    # -- Cache invalidation (targeted, no KEYS *) ------------------------------

    async def clear_client_caches(self, client_id: int) -> None:
        """Delete known cache keys for a client. Never uses KEYS/SCAN."""
        keys = [
            f"client{client_id}:initial_target_actual",
            f"client{client_id}:initialstatus",
            f"client{client_id}:status",
            f"{client_id}:target_actual:{date.today().isoformat()}",
            f"client{client_id}:chart",
            f"client{client_id}:analytics",
        ]
        await self.redis.delete(*keys)

    # -- Transaction helpers ---------------------------------------------------

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()

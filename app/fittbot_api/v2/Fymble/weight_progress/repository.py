"""Database queries for Weight Progress.

Only data access lives here — no business logic.
"""

from datetime import date as date_type
from typing import List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import (
    Client,
    ClientActual,
    ClientCharacter,
    CharactersCombination,
    CharactersCombinationOld,
    ClientTarget,
    ClientWeightSelection,
    WeightJourney,
    ClientWeightData,
    ClientGeneralAnalysis,
)



class WeightProgressRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Client ───────────────────────────────────────────────────

    async def get_client(self, client_id: int) -> Optional[Client]:
        result = await self.db.execute(
            select(Client).where(Client.client_id == client_id)
        )
        return result.scalars().first()

    # ── Targets ──────────────────────────────────────────────────

    async def get_target(self, client_id: int) -> Optional[ClientTarget]:
        result = await self.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        return result.scalars().first()

    # ── Registration / Profile ───────────────────────────────────

    async def get_weight_selection(self, client_id: int) -> Optional[ClientWeightSelection]:
        result = await self.db.execute(
            select(ClientWeightSelection).where(
                ClientWeightSelection.client_id == str(client_id)
            )
        )
        return result.scalars().first()

    # ── Character URL ────────────────────────────────────────────

    async def get_character_url(self, client_id: int) -> Optional[str]:
        result = await self.db.execute(
            select(ClientCharacter).where(ClientCharacter.client_id == client_id)
        )

        client_character = result.scalars().first()

        if not client_character:
            return None


        url_result = await self.db.execute(
            select(CharactersCombination).where(
                CharactersCombination.id == client_character.character_id
            )
        )

        url_db = url_result.scalars().first()
        
        return url_db.characters_url if url_db else None

    # ── Weight Journey ───────────────────────────────────────────

    async def get_all_journeys(self, client_id: int) -> List[WeightJourney]:
        result = await self.db.execute(
            select(WeightJourney).where(WeightJourney.client_id == client_id)
        )
        return list(result.scalars().all())

    async def get_all_weight_records(self, client_id: int) -> List[ClientWeightData]:
        result = await self.db.execute(
            select(ClientWeightData)
            .where(ClientWeightData.client_id == client_id)
            .order_by(ClientWeightData.id.desc())
        )
        return list(result.scalars().all())

    async def get_general_analysis(self, client_id: int) -> List[ClientGeneralAnalysis]:
        result = await self.db.execute(
            select(ClientGeneralAnalysis)
            .where(ClientGeneralAnalysis.client_id == client_id)
            .order_by(ClientGeneralAnalysis.date.asc())
        )
        return list(result.scalars().all())

    # ── Add Weight (writes) ──────────────────────────────────────

    async def get_actual_today(self, client_id: int, today: date_type) -> Optional[ClientActual]:
        result = await self.db.execute(
            select(ClientActual).where(
                ClientActual.client_id == client_id,
                ClientActual.date == today,
            )
        )
        return result.scalars().first()

    async def upsert_actual_weight(self, client_id: int, today: date_type, weight: float) -> None:
        existing = await self.get_actual_today(client_id, today)
        if existing:
            existing.weight = weight
        else:
            self.db.add(ClientActual(client_id=client_id, date=today, weight=weight))
        await self.db.commit()

    async def update_client_weight_bmi(self, client_id: int, weight: float) -> None:
        client = await self.get_client(client_id)
        if client:
            height = (client.height or 0) / 100 if client.height else 0
            bmi = round(weight / (height ** 2), 2) if height > 0 else None
            client.weight = weight
            client.bmi = bmi
            await self.db.commit()

    async def upsert_general_analysis_weight(self, client_id: int, today: date_type, weight: float) -> None:
        month_start = date_type(today.year, today.month, 1)
        result = await self.db.execute(
            select(ClientGeneralAnalysis).where(
                ClientGeneralAnalysis.client_id == client_id,
                ClientGeneralAnalysis.date == month_start,
            )
        )
        record = result.scalars().first()
        if record:
            record.weight = (record.weight + weight) / 2 if record.weight is not None else weight
        else:
            self.db.add(ClientGeneralAnalysis(client_id=client_id, date=month_start, weight=weight))
        await self.db.commit()

    async def get_last_weight_record(self, client_id: int) -> Optional[ClientWeightData]:
        result = await self.db.execute(
            select(ClientWeightData)
            .where(ClientWeightData.client_id == client_id)
            .order_by(desc(ClientWeightData.id))
        )
        return result.scalars().first()

    async def add_weight_record(self, client_id: int, weight: float, status: bool, today: date_type) -> None:
        self.db.add(ClientWeightData(client_id=client_id, weight=weight, status=status, date=today))
        await self.db.commit()

    async def upsert_target_weight(self, client_id: int, target_weight: float) -> None:
        existing = await self.get_target(client_id)
        if existing:
            existing.weight = target_weight
        else:
            self.db.add(ClientTarget(client_id=client_id, weight=target_weight))
        await self.db.commit()

    async def upsert_start_weight(self, client_id: int, start_weight: float) -> None:
        existing = await self.get_target(client_id)
        if existing:
            existing.start_weight = start_weight
        else:
            self.db.add(ClientTarget(client_id=client_id, start_weight=start_weight))
        await self.db.commit()

    async def get_active_journey(self, client_id: int) -> Optional[WeightJourney]:
        result = await self.db.execute(
            select(WeightJourney)
            .where(WeightJourney.client_id == client_id, WeightJourney.end_date.is_(None))
            .order_by(desc(WeightJourney.start_date), desc(WeightJourney.id))
        )
        return result.scalars().first()

    async def close_journey_and_create_new(
        self, journey: WeightJourney, client_id: int,
        actual_weight: float, target_weight: float, start_weight: float, today: date_type,
    ) -> None:
        journey.end_date = today
        journey.actual_weight = actual_weight
        await self.db.commit()

        self.db.add(WeightJourney(
            client_id=client_id,
            start_date=today,
            start_weight=start_weight,
            actual_weight=actual_weight,
            target_weight=target_weight,
        ))
        await self.db.commit()

    async def create_journey(self, client_id: int, actual_weight: float, target_weight: float, today: date_type) -> None:
        self.db.add(WeightJourney(
            client_id=client_id,
            start_date=today,
            start_weight=actual_weight,
            actual_weight=actual_weight,
            target_weight=target_weight,
        ))
        await self.db.commit()

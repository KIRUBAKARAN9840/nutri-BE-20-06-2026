from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models.gymmate import GymMateReport as ReportORM

from . import _domain as d


class ReportRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, report: d.Report) -> Optional[int]:
        """Insert with IGNORE on the UNIQUE(reporter, entity_type, entity_id).
        Returns the inserted id, or the existing report's id if dupe."""
        stmt = mysql_insert(ReportORM).values(
            reporter_client_id=report.reporter_client_id,
            entity_type=report.entity_type.value,
            entity_id=report.entity_id,
            reason=report.reason.value,
            details=report.details,
            status=report.status.value,
            created_at=report.created_at,
        ).prefix_with("IGNORE")
        result = await self.db.execute(stmt)
        if result.rowcount:
            return result.lastrowid

        existing = await self.db.execute(
            select(ReportORM.id).where(
                (ReportORM.reporter_client_id == report.reporter_client_id)
                & (ReportORM.entity_type == report.entity_type.value)
                & (ReportORM.entity_id == report.entity_id)
            )
        )
        row = existing.first()
        return int(row[0]) if row else None

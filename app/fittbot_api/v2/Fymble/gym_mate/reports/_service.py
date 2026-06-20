from typing import Optional

from app.utils.logging_utils import FittbotHTTPException

from . import _domain as d
from . import schemas as dto
from ._events import ContentReported, EventBus
from ._repository import ReportRepository


class ReportService:
    def __init__(self, repository: ReportRepository, event_bus: EventBus):
        self.repo = repository
        self.bus = event_bus

    async def submit(
        self,
        reporter_id: int,
        entity_type: str,
        entity_id: int,
        reason: str,
        details: Optional[str] = None,
    ) -> dto.ReportSubmittedDTO:
        try:
            etype = d.EntityType(entity_type)
            rreason = d.ReportReason(reason)
            report = d.Report.new(
                reporter_id=reporter_id,
                entity_type=etype,
                entity_id=entity_id,
                reason=rreason,
                details=details,
            )
        except (ValueError, d.ReportDomainError) as exc:
            raise FittbotHTTPException(
                status_code=400,
                detail=str(exc),
                error_code="GYMMATE_REPORT_INVALID",
                log_data={
                    "reporter_id": reporter_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "exc": repr(exc),
                },
            )

        report_id = await self.repo.add(report)
        if report_id is None:
            raise FittbotHTTPException(
                status_code=500,
                detail="Could not record report",
                error_code="GYMMATE_REPORT_STORE_FAILED",
                log_data={"reporter_id": reporter_id},
            )

        await self.bus.publish(ContentReported(
            report_id=report_id,
            reporter_client_id=reporter_id,
            entity_type=etype.value,
            entity_id=entity_id,
            reason=rreason.value,
        ))

        return dto.ReportSubmittedDTO(
            report_id=report_id,
            entity_type=etype.value,
            entity_id=entity_id,
            reason=rreason.value,
        )

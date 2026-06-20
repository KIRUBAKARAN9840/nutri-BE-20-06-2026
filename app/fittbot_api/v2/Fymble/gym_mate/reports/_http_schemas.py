from typing import Optional

from pydantic import BaseModel, Field

from .schemas import ReportSubmittedDTO


class SubmitReportRequest(BaseModel):
    entity_type: str = Field(..., max_length=20)
    entity_id: int = Field(..., ge=1)
    reason: str = Field(..., max_length=40)
    details: Optional[str] = Field(None, max_length=500)


class SubmitReportResponse(BaseModel):
    status: int = 200
    message: str = "Report submitted"
    data: ReportSubmittedDTO

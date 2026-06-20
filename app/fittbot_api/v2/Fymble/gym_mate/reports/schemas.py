from typing import Optional

from pydantic import BaseModel


class ReportSubmittedDTO(BaseModel):
    report_id: int
    entity_type: str
    entity_id: int
    reason: str

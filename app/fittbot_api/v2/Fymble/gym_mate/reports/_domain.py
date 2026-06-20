from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class EntityType(str, Enum):
    STORY = "story"
    USER = "user"
    PROFILE = "profile"
    POST = "post"
    COMMENT = "comment"


class ReportReason(str, Enum):
    INAPPROPRIATE_CONTENT = "inappropriate_content"
    SPAM = "spam"
    HARASSMENT = "harassment"
    VIOLENCE = "violence"
    FALSE_INFORMATION = "false_information"
    SELF_INJURY = "self_injury"
    SCAM = "scam"
    NUDITY = "nudity"
    IP_INFRINGEMENT = "ip_infringement"
    RESTRICTED_ITEMS = "restricted_items"


class ReportStatus(str, Enum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


MAX_DETAILS_LEN = 500


class ReportDomainError(Exception):
    pass


class InvalidReportDetails(ReportDomainError):
    pass


@dataclass(frozen=True)
class Report:
    reporter_client_id: int
    entity_type: EntityType
    entity_id: int
    reason: ReportReason
    details: Optional[str]
    status: ReportStatus
    created_at: datetime
    id: Optional[int] = None

    @classmethod
    def new(
        cls,
        reporter_id: int,
        entity_type: EntityType,
        entity_id: int,
        reason: ReportReason,
        details: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> "Report":
        trimmed = (details or "").strip() or None
        if trimmed and len(trimmed) > MAX_DETAILS_LEN:
            raise InvalidReportDetails(f"details max {MAX_DETAILS_LEN} chars")
        return cls(
            reporter_client_id=reporter_id,
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            details=trimmed,
            status=ReportStatus.PENDING,
            created_at=now or datetime.now(),
        )

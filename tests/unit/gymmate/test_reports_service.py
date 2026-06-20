from datetime import datetime
from typing import Optional

import pytest

from app.fittbot_api.v2.Fymble.gym_mate.reports import _domain as d
from app.fittbot_api.v2.Fymble.gym_mate.reports._events import ContentReported, NoopEventBus
from app.fittbot_api.v2.Fymble.gym_mate.reports._service import ReportService
from app.utils.logging_utils import FittbotHTTPException


class InMemoryReportRepository:
    def __init__(self):
        self.rows = {}
        self._next = 1

    async def add(self, report):
        key = (report.reporter_client_id, report.entity_type.value, report.entity_id)
        if key in self.rows:
            return self.rows[key]
        rid = self._next
        self._next += 1
        self.rows[key] = rid
        return rid


class RecordingBus:
    def __init__(self): self.events = []
    async def publish(self, event): self.events.append(event)


@pytest.fixture
def repo(): return InMemoryReportRepository()

@pytest.fixture
def bus(): return RecordingBus()

@pytest.fixture
def service(repo, bus):
    return ReportService(repository=repo, event_bus=bus)


class TestSubmit:
    @pytest.mark.asyncio
    async def test_submit_story_report(self, service, bus):
        result = await service.submit(
            reporter_id=42, entity_type="story", entity_id=51,
            reason="harassment", details="said mean things",
        )
        assert result.entity_type == "story"
        assert result.entity_id == 51
        assert result.reason == "harassment"
        assert result.report_id > 0
        assert any(isinstance(e, ContentReported) for e in bus.events)

    @pytest.mark.asyncio
    async def test_submit_without_details(self, service):
        result = await service.submit(
            reporter_id=42, entity_type="story", entity_id=51, reason="spam",
        )
        assert result.report_id > 0

    @pytest.mark.asyncio
    async def test_invalid_entity_type(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.submit(
                reporter_id=42, entity_type="meme", entity_id=51, reason="spam",
            )
        assert exc.value.error_code == "GYMMATE_REPORT_INVALID"

    @pytest.mark.asyncio
    async def test_invalid_reason(self, service):
        with pytest.raises(FittbotHTTPException) as exc:
            await service.submit(
                reporter_id=42, entity_type="story", entity_id=51, reason="bad_vibes",
            )
        assert exc.value.error_code == "GYMMATE_REPORT_INVALID"

    @pytest.mark.asyncio
    async def test_details_over_max_rejected(self, service):
        with pytest.raises(FittbotHTTPException):
            await service.submit(
                reporter_id=42, entity_type="story", entity_id=51,
                reason="spam", details="x" * 501,
            )

    @pytest.mark.asyncio
    async def test_idempotent_on_repeat(self, service, bus):
        first = await service.submit(
            reporter_id=42, entity_type="story", entity_id=51, reason="spam",
        )
        second = await service.submit(
            reporter_id=42, entity_type="story", entity_id=51, reason="spam",
        )
        assert first.report_id == second.report_id

    @pytest.mark.asyncio
    async def test_all_reasons_accepted(self, service):
        reasons = [
            "inappropriate_content", "spam", "harassment", "violence",
            "false_information", "self_injury", "scam", "nudity",
            "ip_infringement", "restricted_items",
        ]
        for i, r in enumerate(reasons, start=1):
            await service.submit(
                reporter_id=42, entity_type="story", entity_id=i, reason=r,
            )

    @pytest.mark.asyncio
    async def test_all_entity_types_accepted(self, service):
        types = ["story", "user", "profile", "post", "comment"]
        for i, t in enumerate(types, start=1):
            await service.submit(
                reporter_id=42, entity_type=t, entity_id=i, reason="spam",
            )

    @pytest.mark.asyncio
    async def test_submit_user_report(self, service, bus):
        result = await service.submit(
            reporter_id=42, entity_type="user", entity_id=99,
            reason="harassment", details="creep behavior",
        )
        assert result.entity_type == "user"
        assert result.entity_id == 99
        assert result.reason == "harassment"
        assert result.report_id > 0

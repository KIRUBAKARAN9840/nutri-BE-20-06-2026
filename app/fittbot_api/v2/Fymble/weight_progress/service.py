"""Business logic for Weight Progress."""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException
from .repository import WeightProgressRepository
from redis.asyncio import Redis
from .schemas import (
    WeightProgressResponse,
    WeightProgressFullData,
    WeightProgressData,
    RegistrationSteps,
    JourneyItem,
    WeightRecord,
    WeightChartPoint,
    AddWeightRequest,
    AddWeightResponse,
)


class WeightProgressService:

    def __init__(self, db: AsyncSession, redis: Redis = None):
        self.repo = WeightProgressRepository(db)
        self.redis = redis

    # ── GET /data ────────────────────────────────────────────────

    async def get_data(self, client_id: int) -> WeightProgressResponse:
        client = await self.repo.get_client(client_id)
        if not client:
            raise FittbotHTTPException(
                status_code=404,
                detail="Client not found",
                error_code="CLIENT_NOT_FOUND",
                log_data={"client_id": client_id},
            )

        # ── Weight progress ──────────────────────────────────────
        target = await self.repo.get_target(client_id)

        actual_weight = client.weight if client.weight is not None else None
        target_weight = target.weight if target and target.weight is not None else 0
        start_weight = target.start_weight if target and target.start_weight is not None else 0
        goals = client.goals if client.goals else ""

        progress = self._get_progress(goals, actual_weight, target_weight, start_weight)
        progress = min(progress, 100)

        weight_progress = WeightProgressData(
            actual_weight=actual_weight,
            target_weight=target_weight,
            start_weight=start_weight,
            progress=progress,
        )

        # ── Registration steps ───────────────────────────────────
        registration_steps = await self._get_registration_steps(client_id, client)

        all_steps_completed = all([
            registration_steps.dob,
            registration_steps.goal,
            registration_steps.height,
            registration_steps.weight,
            registration_steps.body_shape,
            registration_steps.lifestyle,
        ])
        usertype = "full_user" if all_steps_completed else "guest"

        # ── Character URL ─────────────────────────────────────────
        url = await self.repo.get_character_url(client_id)

        if url is None:
            if client.goals=="weight_gain":
                url="https://fittbot-uploads.s3.ap-south-2.amazonaws.com/combination_male_latest/M2M3.png" if client.gender.lower()=="male" else "https://fittbot-uploads.s3.ap-south-2.amazonaws.com/combination_female_latest/F7F4.png"
            else:
                url="https://fittbot-uploads.s3.ap-south-2.amazonaws.com/combination_male_latest/M6M4.png" if client.gender.lower()=="male" else "https://fittbot-uploads.s3.ap-south-2.amazonaws.com/combination_female_latest/F2F4.png"



        journeys = await self.repo.get_all_journeys(client_id)
        journey_list = []
        for idx, j in enumerate(journeys, start=1):
            if j.start_date and j.end_date:
                days_diff = (j.end_date - j.start_date).days
            elif j.end_date is None and j.start_date:
                days_diff = (date.today() - j.start_date).days
            else:
                days_diff = 0

            journey_list.append(
                JourneyItem(
                    id=idx,
                    client_id=j.client_id,
                    start_date=j.start_date,
                    end_date=j.end_date,
                    start_weight=j.start_weight,
                    actual_weight=j.actual_weight,
                    target_weight=j.target_weight,
                    days_diff=days_diff,
                )
            )

        records = await self.repo.get_all_weight_records(client_id)
        record_list = [
            WeightRecord(
                id=r.id,
                client_id=r.client_id,
                weight=r.weight,
                status=r.status,
                date=r.date,
            )
            for r in records
        ]

        analysis = await self.repo.get_general_analysis(client_id)
        weight_chart = self._build_weight_chart(analysis)

        bmi = float(client.bmi) if usertype != "guest" and client.bmi is not None else None
        bmi_status = self._get_bmi_status(bmi) if bmi is not None else None

        return WeightProgressResponse(
            data=WeightProgressFullData(
                weight_progress=weight_progress,
                registration_steps=registration_steps,
                usertype=usertype,
                bmi=bmi,
                bmi_status=bmi_status,
                gender=client.gender if client.gender else None,
                url=url,
                weight=weight_chart,
                journey_list=journey_list,
                record_list=record_list,
            )
        )

    # ── POST /add_weight ─────────────────────────────────────────

    async def add_weight(self, client_id: int, req: AddWeightRequest) -> AddWeightResponse:
        today = date.today()
        journey_completion = False

        # ── actual_weight ────────────────────────────────────────
        if req.actual_weight:
            await self.repo.upsert_actual_weight(client_id, today, req.actual_weight)
            await self.repo.update_client_weight_bmi(client_id, req.actual_weight)
            await self.repo.upsert_general_analysis_weight(client_id, today, req.actual_weight)

            # Weight record with gain/loss status
            last_record = await self.repo.get_last_weight_record(client_id)
            if not last_record:
                status = True
            else:
                try:
                    status = float(req.actual_weight) > float(last_record.weight or 0)
                except (ValueError, TypeError):
                    status = True

            if last_record:
                if last_record.weight != req.actual_weight:
                    await self.repo.add_weight_record(client_id, req.actual_weight, status, today)
            else:
                await self.repo.add_weight_record(client_id, req.actual_weight, status, today)

            # Check journey completion
            existing_target = await self.repo.get_target(client_id)
            if existing_target and existing_target.weight and req.actual_weight:
                try:
                    if float(req.actual_weight) > float(existing_target.weight):
                        journey_completion = True
                except (ValueError, TypeError):
                    pass

            # Update active journey's actual weight
            active_journey = await self.repo.get_active_journey(client_id)
            if active_journey:
                active_journey.actual_weight = req.actual_weight
                await self.repo.db.commit()

        # ── target_weight ────────────────────────────────────────
        if req.target_weight:
            await self.repo.upsert_target_weight(client_id, req.target_weight)

            client = await self.repo.get_client(client_id)
            actual_weight_now = client.weight if client else None

            active_journey = await self.repo.get_active_journey(client_id)
            if active_journey:
                if active_journey.target_weight != req.target_weight:
                    await self.repo.close_journey_and_create_new(
                        active_journey, client_id,
                        actual_weight_now or 0, req.target_weight,
                        req.start_weight or actual_weight_now or 0, today,
                    )
            else:
                await self.repo.create_journey(
                    client_id, actual_weight_now or 0, req.target_weight, today,
                )

        # ── start_weight ─────────────────────────────────────────
        if req.start_weight:
            await self.repo.upsert_start_weight(client_id, req.start_weight)

        # ── Cache invalidation ───────────────────────────────────
        if self.redis:
            await self._invalidate_caches(client_id)

        return AddWeightResponse(journey_completion=journey_completion)

    async def _invalidate_caches(self, client_id: int) -> None:
        keys_to_delete = [
            f"client{client_id}:initial_target_actual",
            f"client{client_id}:initialstatus",
        ]
        patterns = ["*:status", "*:analytics", "*:target_actual", "*:chart", "gym:*:clientdata"]
        for pattern in patterns:
            matched = await self.redis.keys(pattern)
            keys_to_delete.extend(matched)

        if keys_to_delete:
            await self.redis.delete(*keys_to_delete)

    # ── Helpers ──────────────────────────────────────────────────

    async def _get_registration_steps(self, client_id: int, client) -> RegistrationSteps:
        dob_completed = client.dob is not None
        goal_completed = bool(client.goals and str(client.goals).strip())
        height_completed = client.height is not None
        weight_completed = client.weight is not None and client.bmi is not None

        weight_selection = await self.repo.get_weight_selection(client_id)
        body_shape_completed = weight_selection is not None

        lifestyle_completed = bool(client.lifestyle and str(client.lifestyle).strip())

        registration_complete = not client.incomplete if client.incomplete is not None else False

        return RegistrationSteps(
            dob=dob_completed,
            goal=goal_completed,
            height=height_completed,
            weight=weight_completed,
            body_shape=body_shape_completed,
            lifestyle=lifestyle_completed,
            registration_complete=registration_complete,
        )

    @staticmethod
    def _build_weight_chart(analysis) -> list:
        if not analysis:
            return []

        by_month = {r.date: r.weight for r in analysis if r.date is not None}
        if not by_month:
            return []

        start = min(by_month.keys())
        today = date.today()
        end = date(today.year, today.month, 1)

        points = []
        last_weight = 0
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            if cursor in by_month and by_month[cursor] is not None:
                last_weight = by_month[cursor]
            points.append(WeightChartPoint(label=cursor, value=last_weight or 0))
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = date(year, month, 1)
        return points

    @staticmethod
    def _get_bmi_status(bmi: float) -> str:
        if bmi < 18.5:
            return "low"
        elif bmi < 25:
            return "normal"
        else:
            return "high"

    @staticmethod
    def _get_progress(goals, actual_weight, target_weight, start_weight) -> float:
        try:
            if actual_weight is not None and target_weight is not None and start_weight is not None:
                actual_weight = float(actual_weight)
                target_weight = float(target_weight)
                start_weight = float(start_weight)

                if not goals:
                    return 0

                if goals.lower() == "weight_gain":
                    if actual_weight < start_weight:
                        return 0
                    return (
                        ((actual_weight - start_weight) / (target_weight - start_weight)) * 100
                        if (target_weight - start_weight) > 0
                        else 0
                    )
                elif goals.lower() == "weight_loss":
                    if actual_weight > start_weight:
                        return 0
                    return (
                        ((start_weight - actual_weight) / (start_weight - target_weight)) * 100
                        if (start_weight - target_weight) > 0
                        else 0
                    )
            return 0
        except (ValueError, TypeError, ZeroDivisionError):
            return 0

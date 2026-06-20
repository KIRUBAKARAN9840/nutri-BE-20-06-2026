"""Chat-diet orchestration: dedup, enqueue, expose status/plan + follow-up flow.

The slow LLM work runs in a Celery task on the `chat_diet_jobs` queue.
This service stays thin: validates state, hits Redis, hits the DB cache,
and either returns the cached plan inline or enqueues a new generation.
"""

from typing import Any, Dict, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException

from .ai_service import swap_meal_item as ai_swap_meal_item
from .repository import (
    ChatDietRepository,
    fingerprint_collected_data,
    make_job_id,
    step_label,
)
from .schemas import (
    ChatDietGenerateResponse,
    ChatDietRequest,
    CurrentPlanResponse,
    FollowupEligibilityResponse,
    FollowupGenerateRequest,
    FollowupInfo,
    JobPlanResponse,
    JobStatusResponse,
    LatestPlanData,
    SwapMealRequest,
    SwapMealResponse,
)

ETA_SECONDS_DEFAULT = 90


class ChatDietService:
    _error_code_prefix = "CHAT_DIET"

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.repo = ChatDietRepository(db, redis)

    # ── POST /chat-diet/generate (initial, step=0) ──────────────

    async def process_chat(
        self, request: ChatDietRequest, client_id: int
    ) -> ChatDietGenerateResponse:
        collected_data = self._collect(request)
        fingerprint = fingerprint_collected_data(collected_data, step=0, parent_id=None)
        job_id = make_job_id(client_id, fingerprint)

        cached = await self.repo.fetch_plan_by_fingerprint(client_id, fingerprint)
        if cached:
            return ChatDietGenerateResponse(
                status=200,
                success=True,
                job_id=job_id,
                plan=cached["plan"],
                plan_id=cached["id"],
                completed=True,
                message="Plan retrieved from cache.",
            )

        if not await self.repo.check_and_increment_rate_limit(client_id):
            raise FittbotHTTPException(
                status_code=429,
                detail="Daily plan generation limit reached. Try again tomorrow.",
                error_code=f"{self._error_code_prefix}_RATE_LIMIT",
            )

        newly_registered = await self.repo.try_register_job(
            job_id=job_id, client_id=client_id, collected_data=collected_data,
        )
        if newly_registered:
            from app.tasks.chat_diet_tasks import queue_chat_diet_generation
            queue_chat_diet_generation(
                job_id=job_id,
                client_id=client_id,
                fingerprint=fingerprint,
                collected_data=collected_data,
                step=0,
                parent_id=None,
                feedback=None,
            )

        return ChatDietGenerateResponse(
            status=202,
            success=True,
            job_id=job_id,
            status_url=f"/api/v2/chat-diet/status/{job_id}",
            eta_seconds=ETA_SECONDS_DEFAULT,
            message="Plan generation queued. We'll notify you when it's ready.",
        )

    # ── GET /chat-diet/current (single call for the diet page) ──

    async def get_current_state(self, client_id: int) -> CurrentPlanResponse:
        """Latest plan (with per-day target_calories + single consumed_calories)
        + followup eligibility, in one shot.
        """
        info = await self.repo.compute_followup_eligibility(client_id)
        followup = FollowupInfo(
            current_step=info["current_step"],
            next_step=info["next_step"],
            next_step_label=info["next_step_label"],
            eligible=info["eligible"],
            last_plan_id=info["last_plan_id"],
            last_plan_created_at=info["last_plan_created_at"],
            days_until_eligible=info["days_until_eligible"],
            series_complete=info["series_complete"],
        )

        last_plan_id = info["last_plan_id"]
        if last_plan_id is None:
            return CurrentPlanResponse(
                status=200,
                has_plan=False,
                latest_plan=None,
                followup=followup,
            )

        plan_record = await self.repo.fetch_plan_by_id(last_plan_id, client_id)
        if plan_record is None:
            return CurrentPlanResponse(
                status=200,
                has_plan=False,
                latest_plan=None,
                followup=followup,
            )

        consumed_today = await self.repo.fetch_consumed_calories_today(client_id)

        latest_plan = LatestPlanData(
            plan_id=plan_record["id"],
            step=plan_record["step"],
            step_label=step_label(plan_record["step"]),
            consumed_calories=consumed_today,
            plan=plan_record["plan"],
            model_used=plan_record.get("model_used"),
            created_at=plan_record["created_at"],
        )

        return CurrentPlanResponse(
            status=200,
            has_plan=True,
            latest_plan=latest_plan,
            followup=followup,
        )

    # ── GET /chat-diet/followup/eligibility ─────────────────────

    async def get_followup_eligibility(
        self, client_id: int
    ) -> FollowupEligibilityResponse:
        info = await self.repo.compute_followup_eligibility(client_id)
        return FollowupEligibilityResponse(
            status=200,
            current_step=info["current_step"],
            next_step=info["next_step"],
            next_step_label=info["next_step_label"],
            eligible=info["eligible"],
            last_plan_id=info["last_plan_id"],
            last_plan_created_at=info["last_plan_created_at"],
            days_until_eligible=info["days_until_eligible"],
            series_complete=info["series_complete"],
        )

    # ── POST /chat-diet/followup/generate ───────────────────────

    async def enqueue_followup(
        self, request: FollowupGenerateRequest, client_id: int
    ) -> ChatDietGenerateResponse:
        info = await self.repo.compute_followup_eligibility(client_id)

        # The follow-up endpoint NEVER creates an initial plan; if next_step is 0
        # the caller should use POST /chat-diet/generate instead.
        if info["next_step"] == 0:
            if info["last_plan_id"] is None:
                raise FittbotHTTPException(
                    status_code=409,
                    detail="No prior plan found. Generate an initial plan first.",
                    error_code=f"{self._error_code_prefix}_NO_INITIAL_PLAN",
                )
            raise FittbotHTTPException(
                status_code=409,
                detail="Series complete. Start a new initial plan instead.",
                error_code=f"{self._error_code_prefix}_SERIES_COMPLETE",
            )

        if not info["eligible"]:
            raise FittbotHTTPException(
                status_code=409,
                detail=(
                    f"Not yet eligible for {step_label(info['next_step'])}. "
                    f"Try again in {info['days_until_eligible']} day(s)."
                ),
                error_code=f"{self._error_code_prefix}_FOLLOWUP_TOO_EARLY",
            )

        parent_id = info["last_plan_id"]
        next_step = info["next_step"]
        feedback = request.feedback

        # Inherit the original profile from the parent so the user doesn't
        # have to resubmit height/weight/preferences for follow-ups.
        parent = await self.repo.fetch_plan_by_id(plan_id=parent_id, client_id=client_id)
        if parent is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Parent plan record missing.",
                error_code=f"{self._error_code_prefix}_PARENT_MISSING",
            )
        collected_data = parent["collected_data"]

        fingerprint = fingerprint_collected_data(
            collected_data, step=next_step, parent_id=parent_id, feedback=feedback,
        )
        job_id = make_job_id(client_id, fingerprint)

        cached = await self.repo.fetch_plan_by_fingerprint(client_id, fingerprint)
        if cached:
            return ChatDietGenerateResponse(
                status=200,
                success=True,
                job_id=job_id,
                plan=cached["plan"],
                plan_id=cached["id"],
                completed=True,
                message="Follow-up plan retrieved from cache.",
            )

        if not await self.repo.check_and_increment_rate_limit(client_id):
            raise FittbotHTTPException(
                status_code=429,
                detail="Daily plan generation limit reached. Try again tomorrow.",
                error_code=f"{self._error_code_prefix}_RATE_LIMIT",
            )

        newly_registered = await self.repo.try_register_job(
            job_id=job_id, client_id=client_id, collected_data=collected_data,
        )
        if newly_registered:
            from app.tasks.chat_diet_tasks import queue_chat_diet_generation
            queue_chat_diet_generation(
                job_id=job_id,
                client_id=client_id,
                fingerprint=fingerprint,
                collected_data=collected_data,
                step=next_step,
                parent_id=parent_id,
                feedback=feedback,
            )

        return ChatDietGenerateResponse(
            status=202,
            success=True,
            job_id=job_id,
            status_url=f"/api/v2/chat-diet/status/{job_id}",
            eta_seconds=ETA_SECONDS_DEFAULT,
            message=f"{step_label(next_step)} generation queued. We'll notify you when it's ready.",
        )

    # ── GET /chat-diet/status/{job_id} ──────────────────────────

    async def get_status(self, job_id: str, client_id: int) -> JobStatusResponse:
        state = await self.repo.get_job_state(job_id)
        if state is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Job not found or expired.",
                error_code=f"{self._error_code_prefix}_JOB_NOT_FOUND",
            )

        if state.get("client_id") != client_id:
            raise FittbotHTTPException(
                status_code=403,
                detail="You do not own this job.",
                error_code=f"{self._error_code_prefix}_JOB_FORBIDDEN",
                security_event=True,
            )

        return JobStatusResponse(
            status=200,
            job_id=job_id,
            state=state.get("state", "queued"),
            plan_id=state.get("plan_id"),
            error=state.get("error"),
            created_at=state.get("created_at"),
            completed_at=state.get("completed_at"),
        )

    # ── GET /chat-diet/plan/{job_id} ────────────────────────────

    async def get_plan(self, job_id: str, client_id: int) -> JobPlanResponse:
        state = await self.repo.get_job_state(job_id)
        if state is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Job not found or expired.",
                error_code=f"{self._error_code_prefix}_JOB_NOT_FOUND",
            )

        if state.get("client_id") != client_id:
            raise FittbotHTTPException(
                status_code=403,
                detail="You do not own this job.",
                error_code=f"{self._error_code_prefix}_JOB_FORBIDDEN",
                security_event=True,
            )

        if state.get("state") != "complete" or not state.get("plan_id"):
            raise FittbotHTTPException(
                status_code=409,
                detail=f"Plan not yet ready (state={state.get('state')}).",
                error_code=f"{self._error_code_prefix}_PLAN_NOT_READY",
            )

        plan_record = await self.repo.fetch_plan_by_id(
            plan_id=state["plan_id"], client_id=client_id,
        )
        if plan_record is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Plan record missing.",
                error_code=f"{self._error_code_prefix}_PLAN_MISSING",
            )

        return JobPlanResponse(
            status=200,
            plan_id=plan_record["id"],
            plan=plan_record["plan"],
            model_used=plan_record.get("model_used"),
            created_at=plan_record["created_at"],
        )

    # ── POST /chat-diet/plans/{plan_id}/swap ────────────────────

    async def swap_meal(
        self, plan_id: int, request: SwapMealRequest, client_id: int
    ) -> SwapMealResponse:
        """Replace a single meal item in an existing plan with an AI-generated alternative."""
        plan_record = await self.repo.fetch_plan_by_id(plan_id, client_id)
        if plan_record is None:
            raise FittbotHTTPException(
                status_code=404,
                detail="Plan not found.",
                error_code=f"{self._error_code_prefix}_PLAN_NOT_FOUND",
            )

        plan_json = plan_record["plan"]
        day_block = next((d for d in plan_json if d.get("day") == request.day), None)
        if day_block is None:
            raise FittbotHTTPException(
                status_code=404,
                detail=f"Day {request.day} not found in plan.",
                error_code=f"{self._error_code_prefix}_SWAP_DAY_NOT_FOUND",
            )

        meals = day_block.get(request.meal_type) or []
        if request.item_index >= len(meals):
            raise FittbotHTTPException(
                status_code=404,
                detail=(
                    f"item_index {request.item_index} out of range for "
                    f"{request.meal_type} on day {request.day} "
                    f"(has {len(meals)} item(s))."
                ),
                error_code=f"{self._error_code_prefix}_SWAP_ITEM_NOT_FOUND",
            )

        if not await self.repo.check_and_increment_swap_rate_limit(client_id):
            raise FittbotHTTPException(
                status_code=429,
                detail="Daily swap limit reached. Try again tomorrow.",
                error_code=f"{self._error_code_prefix}_SWAP_RATE_LIMIT",
            )

        current_item = meals[request.item_index]
        other_dish_names = self.repo.collect_other_dish_names(
            plan_json,
            skip_day=request.day,
            skip_meal_type=request.meal_type,
            skip_item_index=request.item_index,
        )

        try:
            new_item = await ai_swap_meal_item(
                profile=plan_record["collected_data"],
                current_item=current_item,
                day=request.day,
                meal_type=request.meal_type,
                other_dish_names=other_dish_names,
                reason=request.reason,
            )
        except Exception as exc:
            raise FittbotHTTPException(
                status_code=503,
                detail="AI swap failed. Please try again in a moment.",
                error_code=f"{self._error_code_prefix}_SWAP_AI_FAILED",
                log_data={"plan_id": plan_id, "detail": str(exc)},
            )

        try:
            update = await self.repo.update_plan_meal(
                plan_id=plan_id,
                client_id=client_id,
                day=request.day,
                meal_type=request.meal_type,
                item_index=request.item_index,
                new_item=new_item,
            )
        except ValueError as exc:
            raise FittbotHTTPException(
                status_code=409,
                detail=str(exc),
                error_code=f"{self._error_code_prefix}_SWAP_PERSIST_FAILED",
            )

        return SwapMealResponse(
            status=200,
            plan_id=plan_id,
            day=request.day,
            meal_type=request.meal_type,
            item_index=request.item_index,
            previous_item=update["previous_item"],
            new_item=new_item,
        )

    # ── helpers ─────────────────────────────────────────────────

    @staticmethod
    def _collect(request: ChatDietRequest) -> Dict[str, Any]:
        return {
            "height": request.height,
            "weight": request.weight,
            "target_weight": request.target_weight,
            "goal": request.goal,
            "allergies": request.allergies,
            "preferences": request.preferences,
            "dietary_preference": request.dietary_preference,
            "other": request.other,
        }

"""Data access for chat-diet — Redis job state + Postgres plan persistence + rate limit."""

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import Any, Dict, Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.fittbot_api.v2.Fymble.diet.food_scanner.repository import calculate_totals
from app.models.fittbot_models import ActualDiet
from app.models.nutrition_models import AiDietCoach
from app.services.timezone_utils import now_ist, today_ist
from app.utils.logging_setup import jlog

CACHE_TTL_JOB = 24 * 60 * 60          # 24h job state
CACHE_TTL_RATE_LIMIT = 24 * 60 * 60   # 24h per-day rate window
DAILY_GENERATION_LIMIT = 5             # plans per client per day (initial + followups share quota)
DAILY_SWAP_LIMIT = 30                   # single-meal swaps per client per day
REDIS_CALL_TIMEOUT = 0.05              # 50ms — match home/repository pattern

# Followup state machine constants
FOLLOWUP_COOLDOWN_DAYS = 7             # gap between consecutive plans in a series
MAX_STEP = 3                           # final follow-up step (0=initial, 3=final)

# Valid meal slots in a DayPlan
VALID_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snacks")


_NUTRITION_NUMERIC_FIELDS = (
    "calories", "protein", "carbs", "fat", "fiber", "sugar",
    "sodium", "calcium", "iron", "magnesium", "potassium",
)


def _coerce_int(value: Any) -> int:
    """Round any numeric / numeric-string value to the nearest non-negative integer."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))
    if isinstance(value, str):
        try:
            return max(0, int(round(float(value.strip()))))
        except (TypeError, ValueError):
            return 0
    return 0


def _normalize_meal_numbers(item: Dict[str, Any]) -> None:
    """Round every nutrition field on a meal item to a non-negative integer.

    Industry-standard nutrition labelling (FDA / IFCT / EU) uses whole numbers
    for grams and milligrams. This guards against any AI output that slips in
    floats like 13.7 or strings like "21 g".
    """
    for field in _NUTRITION_NUMERIC_FIELDS:
        if field in item:
            item[field] = _coerce_int(item[field])


def attach_daily_calories(plan: list) -> list:
    """Mutate ``plan`` in place: normalize meal numerics + add per-day target.

    Two responsibilities:
      1. Round every nutrition field on every meal item to a non-negative int.
      2. Sum the day's calories into ``target_calories`` per day block.

    Idempotent — safe to call repeatedly. Applied at:
      - persist time (values baked into the JSON column as ints)
      - swap time (recompute the affected day's target)
      - read time (defensive normalize for legacy rows + drift safety,
        including legacy rows that stored the value under ``total_calories``)
    """
    if not isinstance(plan, list):
        return plan
    for day_block in plan:
        if not isinstance(day_block, dict):
            continue
        total = 0
        for meal_type in VALID_MEAL_TYPES:
            items = day_block.get(meal_type) or []
            for item in items:
                if isinstance(item, dict):
                    _normalize_meal_numbers(item)
                    total += int(item.get("calories", 0) or 0)
        day_block["target_calories"] = total
        day_block.pop("total_calories", None)
    return plan

_STEP_LABELS = {
    0: "initial",
    1: "follow_up_1",
    2: "follow_up_2",
    3: "follow_up_3",
}


def step_label(step: int) -> str:
    return _STEP_LABELS.get(step, f"step_{step}")


def fingerprint_collected_data(
    collected_data: Dict[str, Any],
    step: int = 0,
    parent_id: Optional[int] = None,
    feedback: Optional[str] = None,
) -> str:
    """Stable hash of (collected_data + step + parent_id + feedback) → 32 hex chars.

    Including step + parent_id ensures step 1, 2, 3 produce distinct fingerprints
    even when the user's profile inputs are identical.
    """
    canonical = json.dumps(
        {
            "collected_data": collected_data,
            "step": step,
            "parent_id": parent_id,
            "feedback": feedback,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def make_job_id(client_id: int, fingerprint: str) -> str:
    return f"chatdiet_{client_id}_{fingerprint[:16]}"


async def _safe_redis(coro, default=None):
    """Same shape as home/repository:_safe_redis — never let Redis slowness block."""
    try:
        return await asyncio.wait_for(coro, timeout=REDIS_CALL_TIMEOUT)
    except (asyncio.TimeoutError, RedisError) as exc:
        jlog("warning", {
            "type": "redis_call_failed",
            "error_code": "CHAT_DIET_REDIS",
            "detail": str(exc),
        })
        return default


class ChatDietRepository:
    """Combined Redis (job state, rate limit) + DB (plan persistence) ops."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Job state in Redis ──────────────────────────────────────

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"chat_diet:job:{job_id}"

    async def try_register_job(
        self, job_id: str, client_id: int, collected_data: Dict[str, Any]
    ) -> bool:
        """SET NX — returns True if newly registered, False if a job for this fingerprint already exists."""
        payload = json.dumps({
            "state": "queued",
            "client_id": client_id,
            "collected_data": collected_data,
            "created_at": now_ist().isoformat(),
            "completed_at": None,
            "plan_id": None,
            "error": None,
        })
        result = await _safe_redis(
            self.redis.set(self._job_key(job_id), payload, nx=True, ex=CACHE_TTL_JOB)
        )
        return bool(result)

    async def set_job_state(
        self,
        job_id: str,
        state: str,
        plan_id: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update an existing job entry. Preserves client_id + created_at."""
        raw = await _safe_redis(self.redis.get(self._job_key(job_id)))
        if raw is None:
            jlog("warning", {
                "type": "job_state_missing_on_update",
                "error_code": "CHAT_DIET_JOB_MISSING",
                "job_id": job_id,
                "state": state,
            })
            return

        data = raw.decode() if isinstance(raw, bytes) else raw
        existing = json.loads(data)
        existing["state"] = state
        if plan_id is not None:
            existing["plan_id"] = plan_id
        if error is not None:
            existing["error"] = error
        if state in ("complete", "failed"):
            existing["completed_at"] = now_ist().isoformat()

        await _safe_redis(
            self.redis.setex(
                self._job_key(job_id), CACHE_TTL_JOB, json.dumps(existing)
            )
        )

    async def get_job_state(self, job_id: str) -> Optional[Dict[str, Any]]:
        raw = await _safe_redis(self.redis.get(self._job_key(job_id)))
        if raw is None:
            return None
        try:
            data = raw.decode() if isinstance(raw, bytes) else raw
            return json.loads(data)
        except (json.JSONDecodeError, AttributeError):
            return None

    # ── Rate limiting ───────────────────────────────────────────

    async def check_and_increment_rate_limit(self, client_id: int) -> bool:
        """Return True if under the daily cap; False if the client has hit the limit.

        On Redis failure we fail OPEN (return True) — better to occasionally
        let a request through than to lock everyone out when Redis is down.
        """
        key = f"chat_diet:rate:{client_id}:{today_ist().isoformat()}"
        try:
            count = await asyncio.wait_for(
                self.redis.incr(key), timeout=REDIS_CALL_TIMEOUT
            )
            if count == 1:
                await asyncio.wait_for(
                    self.redis.expire(key, CACHE_TTL_RATE_LIMIT),
                    timeout=REDIS_CALL_TIMEOUT,
                )
            if count > DAILY_GENERATION_LIMIT:
                # Roll back this attempt so it doesn't count against tomorrow
                await _safe_redis(self.redis.decr(key))
                return False
            return True
        except (asyncio.TimeoutError, RedisError) as exc:
            jlog("warning", {
                "type": "rate_limit_check_failed",
                "error_code": "CHAT_DIET_RATE_LIMIT_REDIS",
                "client_id": client_id,
                "detail": str(exc),
            })
            return True

    async def check_and_increment_swap_rate_limit(self, client_id: int) -> bool:
        """Separate counter for meal swaps (cheaper than full plan generation).

        Returns True if under DAILY_SWAP_LIMIT, False otherwise. Fail OPEN on Redis errors.
        """
        key = f"chat_diet:swap_rate:{client_id}:{today_ist().isoformat()}"
        try:
            count = await asyncio.wait_for(
                self.redis.incr(key), timeout=REDIS_CALL_TIMEOUT
            )
            if count == 1:
                await asyncio.wait_for(
                    self.redis.expire(key, CACHE_TTL_RATE_LIMIT),
                    timeout=REDIS_CALL_TIMEOUT,
                )
            if count > DAILY_SWAP_LIMIT:
                await _safe_redis(self.redis.decr(key))
                return False
            return True
        except (asyncio.TimeoutError, RedisError) as exc:
            jlog("warning", {
                "type": "swap_rate_limit_check_failed",
                "error_code": "CHAT_DIET_SWAP_RATE_LIMIT_REDIS",
                "client_id": client_id,
                "detail": str(exc),
            })
            return True

    # ── Plan persistence (DB) ───────────────────────────────────

    async def fetch_plan_by_fingerprint(
        self, client_id: int, fingerprint: str
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent plan for this (client, fingerprint), or None."""
        stmt = (
            select(AiDietCoach)
            .where(
                AiDietCoach.client_id == client_id,
                AiDietCoach.fingerprint == fingerprint,
            )
            .order_by(desc(AiDietCoach.created_at))
            .limit(1)
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return {
            "id": row.id,
            "plan": attach_daily_calories(row.plan),
            "model_used": row.model_used,
            "step": row.step,
            "parent_id": row.parent_id,
            "created_at": row.created_at.isoformat(),
        }

    async def fetch_plan_by_id(
        self, plan_id: int, client_id: int
    ) -> Optional[Dict[str, Any]]:
        """Return a plan iff it belongs to *client_id* (ownership check)."""
        stmt = select(AiDietCoach).where(
            AiDietCoach.id == plan_id,
            AiDietCoach.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return {
            "id": row.id,
            "plan": attach_daily_calories(row.plan),
            "collected_data": row.collected_data,
            "model_used": row.model_used,
            "step": row.step,
            "parent_id": row.parent_id,
            "created_at": row.created_at.isoformat(),
        }

    async def fetch_latest_plan(self, client_id: int) -> Optional[Dict[str, Any]]:
        """Return the client's most recent plan (any step), or None.

        Used by the followup-eligibility computation: the latest row defines
        the user's current step in the series.
        """
        stmt = (
            select(AiDietCoach)
            .where(AiDietCoach.client_id == client_id)
            .order_by(desc(AiDietCoach.created_at))
            .limit(1)
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if row is None:
            return None
        return {
            "id": row.id,
            "plan": attach_daily_calories(row.plan),
            "collected_data": row.collected_data,
            "model_used": row.model_used,
            "step": row.step,
            "parent_id": row.parent_id,
            "created_at": row.created_at,
        }

    async def fetch_consumed_calories_today(self, client_id: int) -> int:
        """Return today's consumed calories from ``actual_diet`` (IST date).

        Reuses the food_scanner ``calculate_totals`` helper so this matches whatever
        the rest of the app already shows the user.
        Returns 0 if no ActualDiet row exists for today.
        """
        today = today_ist()
        stmt = select(ActualDiet).where(
            ActualDiet.client_id == client_id,
            ActualDiet.date == today,
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if row is None or not row.diet_data:
            return 0
        totals = calculate_totals(row.diet_data)
        return int(round(totals.get("calories", 0) or 0))

    async def persist_plan(
        self,
        client_id: int,
        fingerprint: str,
        collected_data: Dict[str, Any],
        plan: list,
        model_used: Optional[str] = None,
        step: int = 0,
        parent_id: Optional[int] = None,
    ) -> int:
        """Insert a generated plan. Returns the new row's id."""
        attach_daily_calories(plan)
        record = AiDietCoach(
            client_id=client_id,
            fingerprint=fingerprint,
            collected_data=collected_data,
            plan=plan,
            model_used=model_used,
            step=step,
            parent_id=parent_id,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record.id

    async def update_plan_meal(
        self,
        plan_id: int,
        client_id: int,
        day: int,
        meal_type: str,
        item_index: int,
        new_item: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Replace one meal item in an existing plan's JSON.

        Returns ``{"previous_item": ..., "plan": <updated plan>}``.
        Raises ValueError on out-of-range day / meal_type / item_index.
        """
        if meal_type not in VALID_MEAL_TYPES:
            raise ValueError(f"Invalid meal_type: {meal_type}")

        stmt = select(AiDietCoach).where(
            AiDietCoach.id == plan_id,
            AiDietCoach.client_id == client_id,
        )
        result = await self.db.execute(stmt)
        row = result.scalars().first()
        if row is None:
            raise ValueError("Plan not found or not owned by client")

        plan_json = row.plan
        if not isinstance(plan_json, list):
            raise ValueError("Plan is not in expected list format")

        # Locate the day. Plans are sorted by `day` field (1..7); locate explicitly.
        day_block = next((d for d in plan_json if d.get("day") == day), None)
        if day_block is None:
            raise ValueError(f"Day {day} not found in plan")

        meals = day_block.get(meal_type)
        if not isinstance(meals, list):
            raise ValueError(f"Meal slot '{meal_type}' missing or invalid for day {day}")

        if item_index < 0 or item_index >= len(meals):
            raise ValueError(
                f"item_index {item_index} out of range for {meal_type} on day {day} "
                f"(has {len(meals)} item(s))"
            )

        previous_item = meals[item_index]
        meals[item_index] = new_item

        # Recompute the affected day's total after the swap
        attach_daily_calories(plan_json)

        # MySQL JSON column needs explicit dirty-flag for SQLAlchemy to issue UPDATE
        flag_modified(row, "plan")
        await self.db.commit()
        await self.db.refresh(row)

        return {"previous_item": previous_item, "plan": row.plan}

    @staticmethod
    def collect_other_dish_names(
        plan_json: list, skip_day: int, skip_meal_type: str, skip_item_index: int
    ) -> list:
        """Return all dish names in the plan EXCEPT the one being swapped.

        Used as a "do not duplicate" list in the AI prompt.
        """
        names = []
        for day_block in plan_json:
            day_num = day_block.get("day")
            for mt in VALID_MEAL_TYPES:
                items = day_block.get(mt) or []
                for idx, item in enumerate(items):
                    if (
                        day_num == skip_day
                        and mt == skip_meal_type
                        and idx == skip_item_index
                    ):
                        continue
                    name = item.get("name")
                    if name:
                        names.append(name)
        return names

    # ── Followup eligibility ────────────────────────────────────

    async def compute_followup_eligibility(
        self, client_id: int
    ) -> Dict[str, Any]:
        """Return the eligibility envelope for the client's *next* plan.

        Cases:
          - No plans                        → next=initial (step 0), eligible
          - Last step == 3                  → series complete; next=initial (step 0), eligible
          - Last step < 3, age >= 7d        → next=followup (last.step+1), eligible
          - Last step < 3, age <  7d        → not eligible, days_until_eligible > 0
        """
        latest = await self.fetch_latest_plan(client_id)

        if latest is None:
            return {
                "current_step": None,
                "next_step": 0,
                "next_step_label": step_label(0),
                "eligible": True,
                "last_plan_id": None,
                "last_plan_created_at": None,
                "days_until_eligible": 0,
                "series_complete": False,
            }

        last_created_at = latest["created_at"]
        last_step = latest["step"]
        # `created_at` is naive IST in DB; compare against naive IST now
        now_naive = now_ist().replace(tzinfo=None)
        age = now_naive - last_created_at

        if last_step >= MAX_STEP:
            # Series complete — start a fresh initial plan whenever
            return {
                "current_step": last_step,
                "next_step": 0,
                "next_step_label": step_label(0),
                "eligible": True,
                "last_plan_id": latest["id"],
                "last_plan_created_at": last_created_at.isoformat(),
                "days_until_eligible": 0,
                "series_complete": True,
            }

        cooldown = timedelta(days=FOLLOWUP_COOLDOWN_DAYS)
        next_step = last_step + 1
        if age >= cooldown:
            return {
                "current_step": last_step,
                "next_step": next_step,
                "next_step_label": step_label(next_step),
                "eligible": True,
                "last_plan_id": latest["id"],
                "last_plan_created_at": last_created_at.isoformat(),
                "days_until_eligible": 0,
                "series_complete": False,
            }

        # Round up so "6 days 3 hours remaining" → days_left = 7 (still waiting)
        remaining = cooldown - age
        total_seconds = max(remaining.total_seconds(), 0)
        days_left = int((total_seconds + 86399) // 86400) or 1
        return {
            "current_step": last_step,
            "next_step": next_step,
            "next_step_label": step_label(next_step),
            "eligible": False,
            "last_plan_id": latest["id"],
            "last_plan_created_at": last_created_at.isoformat(),
            "days_until_eligible": days_left,
            "series_complete": False,
        }

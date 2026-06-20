"""Redis-based slot holds for the ad-funnel slot-first checkout flow.

When a user picks a slot before paying, we briefly lock that slot in
Redis so concurrent ad users see it as busy. The hold expires automatically
(15 min TTL — matches Razorpay order TTL) so abandoned checkouts release
the slot without explicit cleanup.

Keys are scoped to (date, hour-window, nutritionist) — the same granularity
the booking conflict check operates on. The value is the order_id, so
`release` is owner-checked: a stale release call can't clear a newer hold.

Both async and sync variants are exposed:
  - Async: used by Fymble slot listing (FastAPI request path).
  - Sync : used by Razorpay processor (Celery worker).
"""

from __future__ import annotations

from datetime import date as date_type, time
from typing import Union

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis

HOLD_TTL_SECONDS = 900  # 15 min — matches Razorpay order expiry


def _fmt_time(t: Union[time, str]) -> str:
    """Accept datetime.time or 'HH:MM[:SS]' string, return 'HH:MM'."""
    if isinstance(t, time):
        return t.strftime("%H:%M")
    return str(t)[:5]


def _fmt_date(d: Union[date_type, str]) -> str:
    if isinstance(d, date_type):
        return d.isoformat()
    return str(d)


def make_key(
    booking_date: Union[date_type, str],
    start_time: Union[time, str],
    end_time: Union[time, str],
    nutritionist_id: int,
) -> str:
    return (
        f"nutri_slot_hold:{_fmt_date(booking_date)}"
        f":{_fmt_time(start_time)}-{_fmt_time(end_time)}"
        f":{nutritionist_id}"
    )


# ── Async (used by Fymble slot listing) ──────────────────────────────

async def try_acquire(
    redis: AsyncRedis,
    booking_date,
    start_time,
    end_time,
    nutritionist_id: int,
    order_id: str,
) -> bool:
    """Atomic SETNX with TTL. Returns True if hold was acquired."""
    key = make_key(booking_date, start_time, end_time, nutritionist_id)
    return bool(await redis.set(key, order_id, nx=True, ex=HOLD_TTL_SECONDS))


async def release(
    redis: AsyncRedis,
    booking_date,
    start_time,
    end_time,
    nutritionist_id: int,
    order_id: str,
) -> None:
    """Owner-checked release: only deletes if value matches order_id."""
    key = make_key(booking_date, start_time, end_time, nutritionist_id)
    current = await redis.get(key)
    if current is None:
        return
    if isinstance(current, bytes):
        current = current.decode()
    if current == order_id:
        await redis.delete(key)


async def is_held(
    redis: AsyncRedis,
    booking_date,
    start_time,
    end_time,
    nutritionist_id: int,
) -> bool:
    key = make_key(booking_date, start_time, end_time, nutritionist_id)
    return bool(await redis.exists(key))


# ── Sync (used by Razorpay processor / Celery worker) ────────────────

def try_acquire_sync(
    redis: SyncRedis,
    booking_date,
    start_time,
    end_time,
    nutritionist_id: int,
    order_id: str,
) -> bool:
    key = make_key(booking_date, start_time, end_time, nutritionist_id)
    return bool(redis.set(key, order_id, nx=True, ex=HOLD_TTL_SECONDS))


def release_sync(
    redis: SyncRedis,
    booking_date,
    start_time,
    end_time,
    nutritionist_id: int,
    order_id: str,
) -> None:
    key = make_key(booking_date, start_time, end_time, nutritionist_id)
    current = redis.get(key)
    if current is None:
        return
    if isinstance(current, bytes):
        current = current.decode()
    if current == order_id:
        redis.delete(key)


def is_held_sync(
    redis: SyncRedis,
    booking_date,
    start_time,
    end_time,
    nutritionist_id: int,
) -> bool:
    key = make_key(booking_date, start_time, end_time, nutritionist_id)
    return bool(redis.exists(key))

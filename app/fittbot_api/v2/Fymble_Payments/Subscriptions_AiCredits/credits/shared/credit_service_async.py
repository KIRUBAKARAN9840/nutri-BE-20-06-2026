"""Async sweep for stale credit grants.

Mirrors the sync ``expire_stale_credits`` in credit_service.py but operates on
an ``AsyncSession``. Two entry points:

  - ``expire_stale_credits_in_session(session, client_id)``: runs on the
    caller's session, caller manages commit. Use when the caller already owns
    a transaction (e.g. inside ``fetch_credit_balance_isolated``).

  - ``expire_stale_credits_isolated(client_id, redis=None)``: opens its own
    session, commits, and (if anything actually expired) invalidates the
    home cache. Safe to call from any async path including inside
    ``asyncio.gather`` siblings on a different session.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fittbot_api.v1.payments.models.credits import CreditBalance, CreditLedger

logger = logging.getLogger("payments.credits.service.async")

EXPIRABLE_TXN_TYPES = ("trial_bonus", "subscription_bonus", "signup_bonus")


async def expire_stale_credits_in_session(
    session: AsyncSession, client_id: int
) -> int:
    """Sweep expired grants on the caller's session. Caller commits."""
    now = datetime.now(ZoneInfo("Asia/Kolkata"))

    expirable_stmt = select(CreditLedger).where(
        CreditLedger.client_id == client_id,
        CreditLedger.txn_type.in_(EXPIRABLE_TXN_TYPES),
        CreditLedger.credits > 0,
        CreditLedger.expires_at.isnot(None),
        CreditLedger.expires_at < now,
    )
    expirable = (await session.execute(expirable_stmt)).scalars().all()
    if not expirable:
        return 0

    already_stmt = select(CreditLedger.source_subscription_id).where(
        CreditLedger.client_id == client_id,
        CreditLedger.txn_type == "expired",
        CreditLedger.source_subscription_id.isnot(None),
    )
    already_expired = {row[0] for row in (await session.execute(already_stmt)).all()}

    balance_stmt = (
        select(CreditBalance)
        .where(CreditBalance.client_id == client_id)
        .with_for_update()
    )
    balance_row = (await session.execute(balance_stmt)).scalars().first()
    if balance_row is None:
        return 0

    total_expired = 0
    for grant in expirable:
        grant_source = grant.source_subscription_id or grant.id
        if grant_source in already_expired:
            continue

        credits_to_expire = min(grant.credits, balance_row.balance)
        if credits_to_expire <= 0:
            continue

        balance_row.balance -= credits_to_expire
        total_expired += credits_to_expire

        session.add(CreditLedger(
            id=f"crl_{int(now.timestamp())}_{str(uuid.uuid4())[:8]}",
            client_id=client_id,
            txn_type="expired",
            credits=-credits_to_expire,
            balance_after=balance_row.balance,
            source_subscription_id=grant_source,
            description=f"Expired {grant.txn_type} credits",
            created_at=now,
        ))

    if total_expired > 0:
        session.add(balance_row)
        await session.flush()
        logger.info(
            "CREDITS_EXPIRED_ASYNC | client=%s expired=%d new_balance=%d",
            client_id, total_expired, balance_row.balance,
        )

    return total_expired


async def expire_stale_credits_isolated(
    client_id: int, redis: Optional[object] = None
) -> int:
    """Sweep expired grants in an isolated session. Commits internally.

    If anything actually expired, also invalidates the home cache so the
    next /home/data and /sidebar reads see the updated balance.
    """
    from app.models.async_database import get_async_sessionmaker

    AsyncSessionLocal = get_async_sessionmaker()
    async with AsyncSessionLocal() as session:
        try:
            total_expired = await expire_stale_credits_in_session(session, client_id)
            if total_expired > 0:
                await session.commit()
            else:
                await session.rollback()
        except Exception:
            await session.rollback()
            raise

    if total_expired > 0 and redis is not None:
        try:
            keys = await redis.keys(f"home:data:{client_id}:*")
            ustate_keys = await redis.keys(f"home:v2:ustate:{client_id}")
            all_keys = (keys or []) + (ustate_keys or [])
            if all_keys:
                await redis.delete(*all_keys)
        except Exception:
            pass

    return total_expired

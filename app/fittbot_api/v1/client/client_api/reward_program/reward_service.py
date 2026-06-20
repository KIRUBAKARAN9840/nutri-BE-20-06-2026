"""
Reward Program Service

This service provides helper functions to add reward entries from different processors:
- DailyPass Processor
- Session Processor
- Gym Membership (Subscription) Processor
- Nutrition Package Purchase Processor (Google Play)

Entry limits per user:
- Daily Gym Pass: Up to 100 entries (1 per day booked, e.g. 3-day booking = 3 entries)
- Session Booking: Up to 100 entries (1 per session booked, e.g. 3-session booking = 3 entries)
- Fymble Subscription: Up to 8 entries (2 per month)
- Gym Membership: Up to 15 entries (1-5mo=1, 6-11mo=2, 12+mo=3)
- Nutrition Purchase: Up to 15 entries (3 per purchase)
- Referral Bonus: Up to 25 entries (1 per 3 referrals)
"""

import logging
import random
import string
from datetime import datetime, date
from typing import Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.fittbot_models import RewardProgramOptIn, RewardProgramEntry

logger = logging.getLogger("payments.reward_program")

# Program dates
PROGRAM_START_DATE = date(2026, 1, 26)
PROGRAM_END_DATE = date(2026, 8, 15)


MAX_ENTRIES = {
    "dailypass": 100,
    "session": 100,
    "subscription": 15,
    "gym_membership": 15,
    "nutrition_purchase": 15,
    "ai_scanner": 4,
    "ai_diet_coach": 4,
    "referral": 25,
}


def generate_entry_id() -> str:
    """Generate an 8-character uppercase alphanumeric entry ID."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


async def is_program_active() -> bool:
    """Check if the reward program is currently active."""
    today = date.today()
    return PROGRAM_START_DATE <= today <= PROGRAM_END_DATE


async def check_client_opted_in(db: AsyncSession, client_id: int) -> bool:
    """
    Check if a client has opted into the reward program.

    Args:
        db: Async database session
        client_id: The client ID to check

    Returns:
        True if client is opted in and active, False otherwise
    """
    try:
        result = await db.execute(
            select(RewardProgramOptIn).where(
                RewardProgramOptIn.client_id == client_id,
                RewardProgramOptIn.status == "active",
            )
        )
        opt_in = result.scalars().first()
        return opt_in is not None
    except Exception as e:
        logger.error(f"[REWARD_CHECK_OPT_IN_ERROR] client_id={client_id}, error={e}")
        return False


async def get_entry_count_by_method(db: AsyncSession, client_id: int, method: str) -> int:
    """
    Get the current count of valid entries for a client by method.

    Args:
        db: Async database session
        client_id: The client ID
        method: Entry method (dailypass, session, subscription, referral)

    Returns:
        Count of valid entries for this method
    """
    try:
        result = await db.execute(
            select(func.count(RewardProgramEntry.id)).where(
                RewardProgramEntry.client_id == client_id,
                RewardProgramEntry.method == method,
                RewardProgramEntry.status == "valid",
            )
        )
        count = result.scalar() or 0
        return count
    except Exception as e:
        logger.error(f"[REWARD_COUNT_ERROR] client_id={client_id}, method={method}, error={e}")
        return 0


async def add_reward_entry(
    db: AsyncSession,
    client_id: int,
    method: str,
    source_id: Optional[str] = None,
    entries_to_add: int = 1,
) -> Tuple[bool, int, str]:

    try:
        # Check if program is active
        if not await is_program_active():
            return False, 0, "Reward program is not active"

        # # Check if client is opted in
        # if not await check_client_opted_in(db, client_id):
        #     return False, 0, "Client has not opted into reward program"

        # Get max entries for this method
        max_entries = MAX_ENTRIES.get(method, 0)
        if max_entries == 0:
            return False, 0, f"Invalid entry method: {method}"

        # Idempotency: skip if this source_id already has entries for this method
        if source_id:
            existing = await db.execute(
                select(RewardProgramEntry.id).where(
                    RewardProgramEntry.client_id == client_id,
                    RewardProgramEntry.source_id == source_id,
                    RewardProgramEntry.method == method,
                )
            )
            if existing.scalars().first() is not None:
                return False, 0, f"Already added for source_id={source_id}"

        # Get current entry count
        current_count = await get_entry_count_by_method(db, client_id, method)

        # Calculate how many entries we can actually add
        remaining_capacity = max_entries - current_count
        if remaining_capacity <= 0:
            return False, 0, f"Max entries ({max_entries}) reached for {method}"

        # Limit entries to add based on remaining capacity
        actual_entries_to_add = min(entries_to_add, remaining_capacity)

        # Add the entries
        entries_added = 0
        max_retries = 5
        for _ in range(actual_entries_to_add):
            entry_id = None
            for attempt in range(max_retries):
                candidate = generate_entry_id()
                existing = await db.execute(
                    select(RewardProgramEntry.id).where(
                        RewardProgramEntry.entry_id == candidate
                    )
                )
                if existing.scalars().first() is None:
                    entry_id = candidate
                    break
                logger.warning(f"[REWARD_ENTRY_ID_COLLISION] entry_id={candidate}, attempt={attempt + 1}")

            if entry_id is None:
                return False, entries_added, f"Failed to generate unique entry ID after {max_retries} attempts"

            entry = RewardProgramEntry(
                entry_id=entry_id,
                client_id=client_id,
                method=method,
                source_id=source_id,
                created_at=datetime.now(),
                status="valid",
            )
            db.add(entry)
            entries_added += 1

        await db.flush()

        return True, entries_added, f"Added {entries_added} entry(ies) for {method}"

    except Exception as e:
        await db.rollback()
        logger.error(f"[REWARD_ADD_ENTRY_ERROR] client_id={client_id}, method={method}, error={e}")
        return False, 0, f"Error adding reward entry: {str(e)}"


async def add_dailypass_entry(
    db: AsyncSession,
    client_id: int,
    source_id: Optional[str] = None,
    days_count: int = 1,
) -> Tuple[bool, int, str]:
    """
    Add daily pass reward entries.
    1 entry per day booked (max 100 total).
    If user books 3 days, they get 3 entries.
    """
    return await add_reward_entry(db, client_id, "dailypass", source_id, entries_to_add=days_count)


async def add_session_entry(
    db: AsyncSession,
    client_id: int,
    source_id: Optional[str] = None,
    sessions_count: int = 1,
) -> Tuple[bool, int, str]:
    """
    Add session booking reward entries.
    1 entry per session booked (max 100 total).
    If user books 3 sessions, they get 3 entries.
    """
    return await add_reward_entry(db, client_id, "session", source_id, entries_to_add=sessions_count)


async def add_subscription_entry(
    db: AsyncSession,
    client_id: int,
    source_id: Optional[str] = None,
) -> Tuple[bool, int, str]:
    """
    Add subscription reward entries.
    2 entries per subscription month (max 8 total = 4 months).
    """
    return await add_reward_entry(db, client_id, "subscription", source_id, entries_to_add=2)


def _gym_membership_entries_for_duration(duration_months: int) -> int:
    """
    Calculate reward entries based on gym membership duration.
    1-5 months  -> 1 entry
    6-11 months -> 2 entries
    12+ months  -> 3 entries
    """
    if duration_months >= 12:
        return 3
    elif duration_months >= 6:
        return 2
    else:
        return 1


async def add_gym_membership_entry(
    db: AsyncSession,
    client_id: int,
    duration_months: int,
    source_id: Optional[str] = None,
) -> Tuple[bool, int, str]:
    """
    Add gym membership reward entries based on duration.
    1-5mo=1 entry, 6-11mo=2, 12+mo=3 (max 15 total).
    """
    entries = _gym_membership_entries_for_duration(duration_months)
    return await add_reward_entry(db, client_id, "gym_membership", source_id, entries_to_add=entries)


async def add_referral_entry(
    db: AsyncSession,
    client_id: int,
    source_id: Optional[str] = None,
) -> Tuple[bool, int, str]:
    """
    Add a referral reward entry.
    1 entry per 3 successful referrals (max 25 total).
    Note: The caller should verify the referral count before calling this.
    """
    return await add_reward_entry(db, client_id, "referral", source_id, entries_to_add=1)


def _add_purchase_entry_sync(
    session: Session,
    client_id: int,
    method: str,
    source_id: Optional[str],
    entries_to_add: int,
) -> Tuple[bool, int, str]:
    """Generic sync writer for purchase-driven reward entries.

    Enforces program window, per-method cap, and source_id idempotency. Called
    by the method-specific wrappers below.
    """
    if entries_to_add <= 0:
        return False, 0, f"entries_to_add must be > 0 (got {entries_to_add})"

    try:
        if not is_program_active_sync():
            return False, 0, "Reward program is not active"

        max_entries = MAX_ENTRIES.get(method, 0)
        if max_entries == 0:
            return False, 0, f"Invalid entry method: {method}"

        # Idempotency: skip if this source_id already has entries
        if source_id:
            existing = session.execute(
                select(RewardProgramEntry.id).where(
                    RewardProgramEntry.client_id == client_id,
                    RewardProgramEntry.source_id == source_id,
                    RewardProgramEntry.method == method,
                )
            )
            if existing.scalars().first() is not None:
                return False, 0, f"Already added for source_id={source_id}"

        # Current count
        result = session.execute(
            select(func.count(RewardProgramEntry.id)).where(
                RewardProgramEntry.client_id == client_id,
                RewardProgramEntry.method == method,
                RewardProgramEntry.status == "valid",
            )
        )
        current_count = result.scalar() or 0

        remaining_capacity = max_entries - current_count
        if remaining_capacity <= 0:
            return False, 0, f"Max entries ({max_entries}) reached for {method}"

        actual_entries_to_add = min(entries_to_add, remaining_capacity)

        entries_added = 0
        max_retries = 5
        for _ in range(actual_entries_to_add):
            entry_id = None
            for attempt in range(max_retries):
                candidate = generate_entry_id()
                existing = session.execute(
                    select(RewardProgramEntry.id).where(
                        RewardProgramEntry.entry_id == candidate
                    )
                )
                if existing.scalars().first() is None:
                    entry_id = candidate
                    break

            if entry_id is None:
                return False, entries_added, f"Failed to generate unique entry ID after {max_retries} attempts"

            entry = RewardProgramEntry(
                entry_id=entry_id,
                client_id=client_id,
                method=method,
                source_id=source_id,
                created_at=datetime.now(),
                status="valid",
            )
            session.add(entry)
            entries_added += 1

        session.flush()
        return True, entries_added, f"Added {entries_added} entry(ies) for {method}"

    except Exception as e:
        logger.error(f"[REWARD_ADD_ENTRY_ERROR] client_id={client_id}, method={method}, error={e}")
        return False, 0, f"Error adding reward entry: {str(e)}"


def add_nutrition_purchase_entry_sync(
    session: Session,
    client_id: int,
    source_id: Optional[str] = None,
    entries_to_add: int = 3,
) -> Tuple[bool, int, str]:
    """Add reward entries for a nutrition package purchase (max 15 total).

    Caller passes entries_to_add per plan (see NutritionPlan.reward_entries_count).
    """
    return _add_purchase_entry_sync(
        session, client_id, "nutrition_purchase", source_id, entries_to_add
    )


def add_ai_scanner_entry_sync(
    session: Session,
    client_id: int,
    source_id: Optional[str] = None,
    entries_to_add: int = 1,
) -> Tuple[bool, int, str]:
    """Add reward entries for an AI scanner credit pack purchase (max 4 total).

    One entry per credit_50 / credit_99 purchase; idempotent on source_id.
    """
    return _add_purchase_entry_sync(
        session, client_id, "ai_scanner", source_id, entries_to_add
    )


def add_ai_diet_coach_entry_sync(
    session: Session,
    client_id: int,
    source_id: Optional[str] = None,
    entries_to_add: int = 1,
) -> Tuple[bool, int, str]:
    """Add reward entries for an AI Diet Coach purchase (max 4 total).

    One entry per ai_diet_coach SKU purchase; idempotent on source_id.
    """
    return _add_purchase_entry_sync(
        session, client_id, "ai_diet_coach", source_id, entries_to_add
    )


def is_program_active_sync() -> bool:
    """Sync version of is_program_active."""
    today = date.today()
    return PROGRAM_START_DATE <= today <= PROGRAM_END_DATE

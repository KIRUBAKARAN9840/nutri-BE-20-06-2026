"""
Database helpers for v2 payment processing.
"""

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from typing import Any, Callable, Generator

from sqlalchemy.orm import Session

from app.models.database import get_db

logger = logging.getLogger("payments.v2.database")


# ── PaymentDatabase ────────────────────────────────────────────────

class PaymentDatabase:
    """Payment database manager — uses main app database."""

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        session = next(get_db())
        try:
            yield session
        finally:
            session.close()

    def close(self):
        pass


_payment_db: PaymentDatabase = None


def get_payment_db() -> PaymentDatabase:
    global _payment_db
    if _payment_db is None:
        _payment_db = PaymentDatabase()
    return _payment_db


def get_db_session() -> Generator[Session, None, None]:
    with get_payment_db().get_session() as session:
        yield session


# ── Thread pool for sync DB operations ─────────────────────────────

_db_executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="db_worker")


async def run_sync_db_operation(func: Callable, *args, **kwargs) -> Any:
    """Execute a blocking DB operation in the shared thread pool."""
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            _db_executor, partial(func, *args, **kwargs)
        )
    except Exception as exc:
        logger.error("DB operation failed: %s", exc, exc_info=True)
        raise


# ── ID generation ──────────────────────────────────────────────────

def generate_unique_id(prefix: str = "") -> str:
    """Generate unique ID with timestamp and UUID."""
    timestamp = int(datetime.now().timestamp())
    unique_id = str(uuid.uuid4())[:8]
    if prefix:
        return f"{prefix}_{timestamp}_{unique_id}"
    return f"{timestamp}_{unique_id}"



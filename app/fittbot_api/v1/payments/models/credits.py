"""
Credit balance and ledger models for food-scanner credits.

Design:
- credit_balances  : single row per client, fast O(1) balance lookup.
- credit_ledger    : append-only audit trail of every credit movement.

Race-condition safety:
- All writes to credit_balances use SELECT … FOR UPDATE.
- Double-grant is blocked by a UNIQUE partial index on
  (source_order_id, txn_type) WHERE source_order_id IS NOT NULL.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampMixin
from app.models.database import Base


class CreditBalance(Base, TimestampMixin):
    """Fast-lookup table: one row per client holding the current balance."""

    __tablename__ = "credit_balances"
    __table_args__ = {"schema": "payments"}

    client_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_purchased: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_bonus: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # When set and in the future, the client has an active "unlimited scans"
    # pass (e.g. credit_999) and per-scan credits are NOT deducted.
    unlimited_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<CreditBalance(client_id={self.client_id}, balance={self.balance})>"


class CreditLedger(Base):
    """Append-only audit log of every credit change."""

    __tablename__ = "credit_ledger"
    __table_args__ = (
        Index("idx_credit_ledger_client", "client_id", "created_at"),
        Index("idx_credit_ledger_source_order", "source_order_id"),
        UniqueConstraint(
            "source_order_id",
            "txn_type",
            name="uq_credit_ledger_source_txn",
        ),
        {"schema": "payments"},
    )

    sno: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    client_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    txn_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # purchase | subscription_bonus | used | expired | refunded | admin_grant | signup_bonus
    credits: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # +50 purchase, -1 scan, +5 bonus
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    source_order_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    source_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<CreditLedger(sno={self.sno}, client={self.client_id}, "
            f"txn={self.txn_type}, credits={self.credits})>"
        )

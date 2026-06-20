"""create credit_balances and credit_ledger tables

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-03-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, Sequence[str], None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create credit_balances and credit_ledger tables in payments schema."""

    # ── credit_balances ─────────────────────────────────────────────
    op.create_table(
        "credit_balances",
        sa.Column("customer_id", sa.String(100), primary_key=True),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_purchased", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        schema="payments",
    )

    # ── credit_ledger ───────────────────────────────────────────────
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("customer_id", sa.String(100), nullable=False, index=True),
        sa.Column("txn_type", sa.String(50), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("source_order_id", sa.String(100), nullable=True),
        sa.Column("source_subscription_id", sa.String(100), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema="payments",
    )

    # Composite index for customer history queries
    op.create_index(
        "idx_credit_ledger_customer",
        "credit_ledger",
        ["customer_id", "created_at"],
        schema="payments",
    )

    # Index for quick lookup by source order
    op.create_index(
        "idx_credit_ledger_source_order",
        "credit_ledger",
        ["source_order_id"],
        schema="payments",
    )

    # Unique constraint: prevents double-grant for the same order + txn_type
    op.create_unique_constraint(
        "uq_credit_ledger_source_txn",
        "credit_ledger",
        ["source_order_id", "txn_type"],
        schema="payments",
    )


def downgrade() -> None:
    """Drop credit tables."""
    op.drop_table("credit_ledger", schema="payments")
    op.drop_table("credit_balances", schema="payments")

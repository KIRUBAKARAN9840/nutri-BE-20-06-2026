"""add ad_registerations table

Revision ID: c1a5f2ad198a
Revises: b93585c1d9d0
Create Date: 2026-05-11 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1a5f2ad198a"
down_revision: Union[str, None] = "b93585c1d9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ad_registerations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ad_registerations_client_id", "ad_registerations", ["client_id"]
    )
    op.create_index(
        "ix_ad_registerations_created_at", "ad_registerations", ["created_at"]
    )
    op.create_index(
        "ix_ad_registerations_client_created",
        "ad_registerations",
        ["client_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ad_registerations_client_created", table_name="ad_registerations"
    )
    op.drop_index(
        "ix_ad_registerations_created_at", table_name="ad_registerations"
    )
    op.drop_index(
        "ix_ad_registerations_client_id", table_name="ad_registerations"
    )
    op.drop_table("ad_registerations")

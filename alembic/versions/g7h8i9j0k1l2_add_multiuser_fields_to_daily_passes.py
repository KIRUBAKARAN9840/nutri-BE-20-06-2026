"""add multiuser fields to daily_passes

Revision ID: g7h8i9j0k1l2
Revises: a1b2c3d4e5f7
Create Date: 2026-03-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import JSON as MYSQL_JSON

# revision identifiers, used by Alembic.
revision: str = 'g7h8i9j0k1l2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add selected_dates, booking_type, head_count, per_user_price to daily_passes."""
    op.add_column('daily_passes', sa.Column('selected_dates', MYSQL_JSON, nullable=True), schema='dailypass')
    op.add_column('daily_passes', sa.Column('booking_type', sa.String(16), nullable=False, server_default='single'), schema='dailypass')
    op.add_column('daily_passes', sa.Column('head_count', sa.Integer(), nullable=False, server_default='1'), schema='dailypass')
    op.add_column('daily_passes', sa.Column('per_user_price', sa.Integer(), nullable=True), schema='dailypass')


def downgrade() -> None:
    """Remove multiuser fields from daily_passes."""
    op.drop_column('daily_passes', 'per_user_price', schema='dailypass')
    op.drop_column('daily_passes', 'head_count', schema='dailypass')
    op.drop_column('daily_passes', 'booking_type', schema='dailypass')
    op.drop_column('daily_passes', 'selected_dates', schema='dailypass')

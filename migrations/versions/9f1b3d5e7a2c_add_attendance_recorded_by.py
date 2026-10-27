"""add attendances.recorded_by_id and recorded_at

Revision ID: 9f1b3d5e7a2c
Revises: 3d9296d8c3d2
Create Date: 2026-10-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '9f1b3d5e7a2c'
down_revision: Union[str, Sequence[str], None] = '3d9296d8c3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('attendances', sa.Column('recorded_by_id', sa.Integer(), sa.ForeignKey('members.id'), nullable=True))
    op.add_column('attendances', sa.Column('recorded_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('attendances', 'recorded_at')
    op.drop_column('attendances', 'recorded_by_id')

"""add civility to guest_accounts

Revision ID: e5f7a9b1c3d4
Revises: d4e6f8a0b2c3
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e5f7a9b1c3d4'
down_revision: Union[str, Sequence[str], None] = 'd4e6f8a0b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'guest_accounts',
        sa.Column('civility', sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('guest_accounts', 'civility')

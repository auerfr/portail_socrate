"""add visible_to_guests to meetings and documents

Revision ID: 8b2c4d6e9f01
Revises: 5e6f7a8b9c0d
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '8b2c4d6e9f01'
down_revision: Union[str, Sequence[str], None] = '5e6f7a8b9c0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'meetings',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'documents',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('documents', 'visible_to_guests')
    op.drop_column('meetings', 'visible_to_guests')

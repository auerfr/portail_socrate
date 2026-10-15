"""add guest grade and folder/space-level guest sharing

Revision ID: c3d5e7f9a1b2
Revises: 8b2c4d6e9f01
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c3d5e7f9a1b2'
down_revision: Union[str, Sequence[str], None] = '8b2c4d6e9f01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'guest_accounts',
        sa.Column('masonic_grade', sa.String(length=10), nullable=True),
    )
    op.add_column(
        'doc_spaces',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'doc_folders',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('doc_folders', 'visible_to_guests')
    op.drop_column('doc_spaces', 'visible_to_guests')
    op.drop_column('guest_accounts', 'masonic_grade')

"""add visible_to_guests to news_articles

Revision ID: f6a8b0c2d4e5
Revises: e5f7a9b1c3d4
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f6a8b0c2d4e5'
down_revision: Union[str, Sequence[str], None] = 'e5f7a9b1c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'news_articles',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('news_articles', 'visible_to_guests')

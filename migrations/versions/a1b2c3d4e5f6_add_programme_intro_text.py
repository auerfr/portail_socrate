"""add programme_intro_text to lodge_settings

Revision ID: a1b2c3d4e5f6
Revises: 7f67dfef9906
Create Date: 2026-08-17 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '7f67dfef9906'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('lodge_settings', sa.Column('programme_intro_text', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('lodge_settings', 'programme_intro_text')

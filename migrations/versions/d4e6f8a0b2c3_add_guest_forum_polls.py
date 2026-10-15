"""add guest access to forum and polls

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd4e6f8a0b2c3'
down_revision: Union[str, Sequence[str], None] = 'c3d5e7f9a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'forum_subjects',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'forum_messages',
        sa.Column('created_by_guest_id', sa.Integer(), sa.ForeignKey('guest_accounts.id', ondelete='SET NULL'), nullable=True),
    )
    op.add_column(
        'polls',
        sa.Column('visible_to_guests', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column(
        'poll_votes',
        sa.Column('guest_id', sa.Integer(), sa.ForeignKey('guest_accounts.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('poll_votes', 'guest_id')
    op.drop_column('polls', 'visible_to_guests')
    op.drop_column('forum_messages', 'created_by_guest_id')
    op.drop_column('forum_subjects', 'visible_to_guests')

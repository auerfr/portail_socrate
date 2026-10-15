"""add guest_push_subscriptions

Revision ID: a7c9e1f3b5d7
Revises: f6a8b0c2d4e5
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c9e1f3b5d7'
down_revision: Union[str, Sequence[str], None] = 'f6a8b0c2d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'guest_push_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('guest_account_id', sa.Integer(),
                  sa.ForeignKey('guest_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('endpoint', sa.String(length=1000), nullable=False),
        sa.Column('key_p256dh', sa.String(length=200), nullable=False),
        sa.Column('key_auth', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index(
        'ix_guest_push_subscriptions_guest_account_id',
        'guest_push_subscriptions', ['guest_account_id'],
    )


def downgrade() -> None:
    op.drop_index('ix_guest_push_subscriptions_guest_account_id', table_name='guest_push_subscriptions')
    op.drop_table('guest_push_subscriptions')

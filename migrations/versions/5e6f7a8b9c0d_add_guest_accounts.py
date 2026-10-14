"""add guest_accounts and guest_sessions (accès visiteur)

Revision ID: 5e6f7a8b9c0d
Revises: 3d9296d8c3d2
Create Date: 2026-10-14 16:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '5e6f7a8b9c0d'
down_revision: Union[str, Sequence[str], None] = '3d9296d8c3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'guest_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=200), nullable=False),
        sa.Column('home_lodge', sa.String(length=200), nullable=True),
        sa.Column('obedience', sa.String(length=200), nullable=True),
        sa.Column('password_hash', sa.String(length=200), nullable=True),
        sa.Column('totp_secret', sa.String(length=64), nullable=True),
        sa.Column('totp_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('onboarding_completed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('magic_token', sa.String(length=100), nullable=True),
        sa.Column('magic_token_expires_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('invited_by_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='SET NULL'), nullable=True),
        sa.Column('linked_visitor_id', sa.Integer(), sa.ForeignKey('visitors.id', ondelete='SET NULL'), nullable=True),
        sa.Column('promoted_to_member_id', sa.Integer(), sa.ForeignKey('members.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_guest_accounts_email', 'guest_accounts', ['email'], unique=True)
    op.create_index('ix_guest_accounts_magic_token', 'guest_accounts', ['magic_token'], unique=True)

    op.create_table(
        'guest_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('guest_account_id', sa.Integer(), sa.ForeignKey('guest_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_guest_sessions_guest_account_id', 'guest_sessions', ['guest_account_id'])
    op.create_index('ix_guest_sessions_jti', 'guest_sessions', ['jti'], unique=True)


def downgrade() -> None:
    op.drop_table('guest_sessions')
    op.drop_table('guest_accounts')

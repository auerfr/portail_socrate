"""add guest_id on lodge_group_memberships, member_id nullable

Revision ID: c1e3a5b7d9f0
Revises: b8d0f2a4c6e8
Create Date: 2026-10-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c1e3a5b7d9f0'
down_revision: Union[str, Sequence[str], None] = 'b8d0f2a4c6e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('lodge_group_memberships', schema=None) as batch_op:
        batch_op.add_column(sa.Column('guest_id', sa.Integer(), nullable=True))
        batch_op.alter_column('member_id', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint('uq_group_member', type_='unique')
        batch_op.create_foreign_key(
            'fk_lodge_group_memberships_guest_id', 'guest_accounts', ['guest_id'], ['id'], ondelete='CASCADE',
        )
        batch_op.create_index(
            'uq_group_member', ['group_id', 'member_id'], unique=True,
            sqlite_where=sa.text('member_id IS NOT NULL'),
        )
        batch_op.create_index(
            'uq_group_guest', ['group_id', 'guest_id'], unique=True,
            sqlite_where=sa.text('guest_id IS NOT NULL'),
        )


def downgrade() -> None:
    with op.batch_alter_table('lodge_group_memberships', schema=None) as batch_op:
        batch_op.drop_index('uq_group_guest')
        batch_op.drop_index('uq_group_member')
        batch_op.drop_constraint('fk_lodge_group_memberships_guest_id', type_='foreignkey')
        batch_op.create_unique_constraint('uq_group_member', ['group_id', 'member_id'])
        batch_op.alter_column('member_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('guest_id')

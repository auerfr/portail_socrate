"""add status (brouillon/publiée) to meetings

Revision ID: 3d9296d8c3d2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-14 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '3d9296d8c3d2'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'meetings',
        sa.Column(
            'status',
            sa.Enum('DRAFT', 'PUBLISHED', name='meetingstatus'),
            nullable=False,
            server_default='PUBLISHED',
        ),
    )


def downgrade() -> None:
    op.drop_column('meetings', 'status')
    sa.Enum(name='meetingstatus').drop(op.get_bind(), checkfirst=True)

"""entitlements: user plan tier and append-only usage meter

Revision ID: 9a1f7c3d05be
Revises: eccdd769b70b
Create Date: 2026-09-28 16:00:00.000000+00:00

Adds:
  users.plan    — FREE/PRO/ENTERPRISE, default FREE. Distinct from role:
                  role gates *what* an account may do, plan gates *how much*
                  (usage quotas). No payment provider is wired yet, so every
                  account is FREE; the higher tiers are enforcement-ready labels.

  usage_events  — append-only log of metered per-account actions
                  (fact_check, chat, export, ...). Quota decisions are derived
                  from this log at read time, never written back. user_id is
                  nullable and SET NULL on account deletion so historical spend
                  survives anonymized.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9a1f7c3d05be'
down_revision: Union[str, None] = 'eccdd769b70b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    plan_enum = sa.Enum('FREE', 'PRO', 'ENTERPRISE', name='plantier')
    plan_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'users',
        sa.Column('plan', plan_enum, nullable=False, server_default='FREE'),
    )

    op.create_table(
        'usage_events',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('meter', sa.String(length=64), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('meta', postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='fk_usage_events_user_id_users'),
        sa.PrimaryKeyConstraint('id', name='pk_usage_events'),
        comment=('Append-only log of metered per-account actions. user_id is '
                 'NULL after account deletion so history is kept anonymized; '
                 'quota decisions are derived from this log at read time.'),
    )
    op.create_index('ix_usage_events_user_id', 'usage_events', ['user_id'])
    op.create_index('ix_usage_events_created_at', 'usage_events', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_usage_events_created_at', table_name='usage_events')
    op.drop_index('ix_usage_events_user_id', table_name='usage_events')
    op.drop_table('usage_events')

    op.drop_column('users', 'plan')
    sa.Enum(name='plantier').drop(op.get_bind(), checkfirst=True)
"""plan assignment: append-only audit log for plan_changes

Revision ID: b5d2e4f81c92
Revises: 0a3eaaa770d3
Create Date: 2026-09-29 09:00:00.000000+00:00

Adds `plan_changes`, an append-only log of every plan assignment, so moving an
account onto PRO or ENTERPRISE is a real, audited operation instead of a silent
column edit. Registration records the opening row (from_plan NULL, to_plan FREE,
changed_by NULL); admin assignments record the previous tier, the target tier,
and the admin who did it. user_id and changed_by are SET NULL on account
deletion, mirroring usage_events, so history survives anonymized.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b5d2e4f81c92'
down_revision: Union[str, None] = '0a3eaaa770d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The plantier native enum already exists on users.plan; reference it without
# re-creating the type so the two enum columns share one PostgreSQL type.
# The dialect type is used rather than generic sa.Enum: the generic Enum's
# dialect_impl() rewraps the type via adapt(), which only forwards create_type
# when truthy, so a generic sa.Enum(create_type=False) silently re-creates the
# type on CREATE TABLE.
_PLAN_ENUM = postgresql.ENUM('FREE', 'PRO', 'ENTERPRISE', name='plantier', create_type=False)


def upgrade() -> None:
    op.create_table(
        'plan_changes',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('changed_by', sa.UUID(), nullable=True),
        sa.Column('from_plan', _PLAN_ENUM, nullable=True),
        sa.Column('to_plan', _PLAN_ENUM, nullable=False),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='fk_plan_changes_user_id_users'),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ondelete='SET NULL', name='fk_plan_changes_changed_by_users'),
        sa.PrimaryKeyConstraint('id', name='pk_plan_changes'),
        comment=('Append-only log of plan assignments. from_plan NULL means '
                 'the account was created onto to_plan; later rows record the previous '
                 'tier, the target tier, and the admin who made the change. user_id and '
                 'changed_by are NULL after account deletion, so history survives '
                 'anonymized.'),
    )
    op.create_index('ix_plan_changes_user_id', 'plan_changes', ['user_id'])
    op.create_index('ix_plan_changes_changed_by', 'plan_changes', ['changed_by'])
    op.create_index('ix_plan_changes_created_at', 'plan_changes', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_plan_changes_created_at', table_name='plan_changes')
    op.drop_index('ix_plan_changes_changed_by', table_name='plan_changes')
    op.drop_index('ix_plan_changes_user_id', table_name='plan_changes')
    op.drop_table('plan_changes')
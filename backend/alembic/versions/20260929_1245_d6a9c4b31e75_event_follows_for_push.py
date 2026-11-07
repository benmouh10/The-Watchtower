"""event follows: opt-in follows of specific events for the alert surface

Revision ID: d6a9c4b31e75
Revises: b5d2e4f81c92
Create Date: 2026-09-29 12:45:00.000000+00:00

event_follows — one row per (user, event) the user opted to follow, so the
notification surface can target individual stories without scanning preference
arrays. Unique (user_id, event_id) makes following idempotent; deleting either
the user or the event cascades the follow away.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6a9c4b31e75'
down_revision: Union[str, None] = 'b5d2e4f81c92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_follows',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_event_follows_user_id'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE', name='fk_event_follows_event_id'),
        sa.PrimaryKeyConstraint('id', name='pk_event_follows'),
        sa.UniqueConstraint('user_id', 'event_id', name='uq_event_follows_user_event'),
        comment=('Opt-in follows of specific events by users. Unique (user_id, '
                 'event_id) so a user follows a dossier at most once; cascades '
                 'with a deleted user or event.'),
    )
    op.create_index('ix_event_follows_user_id', 'event_follows', ['user_id'])
    op.create_index('ix_event_follows_event_id', 'event_follows', ['event_id'])


def downgrade() -> None:
    op.drop_index('ix_event_follows_event_id', table_name='event_follows')
    op.drop_index('ix_event_follows_user_id', table_name='event_follows')
    op.drop_table('event_follows')
"""add bookmarks so saved events persist per user

The event page had a bookmark button that only flipped React state, so a save
was lost on refresh and existed on no server. This adds the storage for it.

The unique constraint on (user_id, event_id) is what makes saving idempotent:
re-saving an event the user already has updates nothing rather than creating a
duplicate, so the endpoint can be retried without inflating the dossier.

Revision ID: f7c2d81b4a95
Revises: e4b7a2c19d63
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c2d81b4a95'
down_revision: Union[str, None] = 'e4b7a2c19d63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'bookmarks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_bookmarks_user_id_users'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], name='fk_bookmarks_event_id_events'),
        sa.PrimaryKeyConstraint('id', name='pk_bookmarks'),
        sa.UniqueConstraint('user_id', 'event_id', name='uq_bookmark_user_event'),
    )
    op.create_index('ix_bookmarks_user_id', 'bookmarks', ['user_id'])
    op.create_index('ix_bookmarks_event_id', 'bookmarks', ['event_id'])


def downgrade() -> None:
    op.drop_index('ix_bookmarks_event_id', table_name='bookmarks')
    op.drop_index('ix_bookmarks_user_id', table_name='bookmarks')
    op.drop_table('bookmarks')

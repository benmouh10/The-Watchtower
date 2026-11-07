"""reading lists: named ordered collections of events

Revision ID: eccdd769b70b
Revises: 0f76d0c799a3
Create Date: 2026-09-28 15:03:24.470251+00:00

Two tables:

  reading_lists — a named, private, user-owned collection. (user_id, title) is
  unique so a user cannot accumulate identical labels, and list membership is
  strictly owned (there is no shared/public list).

  reading_list_items — one event inside one list at a user-arranged 0-based
  position. (list_id, position) is unique so the order is deterministic and
  gap-free after compaction, and (list_id, event_id) guarantees an event can be
  listed only once no matter the order edits. A CHECK keeps positions sane.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eccdd769b70b'
down_revision: Union[str, None] = '0f76d0c799a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reading_lists',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_private', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_reading_lists_user_id_users'),
        sa.PrimaryKeyConstraint('id', name='pk_reading_lists'),
        sa.UniqueConstraint('user_id', 'title', name='uq_reading_list_user_title'),
        comment=('Named reading lists owned by a user. user_id+title is unique '
                 'so a user cannot accumulate two identically-labelled lists.'),
    )
    op.create_index('ix_reading_lists_user_id', 'reading_lists', ['user_id'])

    op.create_table(
        'reading_list_items',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('position', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('list_id', sa.UUID(), nullable=False),
        sa.Column('event_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['list_id'], ['reading_lists.id'], ondelete='CASCADE', name='fk_reading_list_items_list_id_reading_lists'),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE', name='fk_reading_list_items_event_id_events'),
        sa.PrimaryKeyConstraint('id', name='pk_reading_list_items'),
        sa.UniqueConstraint('list_id', 'position', name='uq_reading_list_item_position'),
        sa.UniqueConstraint('list_id', 'event_id', name='uq_reading_list_item_event'),
        sa.CheckConstraint('position >= 0', name='ck_reading_list_item_position'),
        comment=('An event inside a reading list at a user-arranged 0-based '
                 'position. list_id+position unique, and list_id+event_id '
                 'unique, so ordering is stable and a duplicate add is '
                 'impossible.'),
    )
    op.create_index('ix_reading_list_items_list_id', 'reading_list_items', ['list_id'])
    op.create_index('ix_reading_list_items_event_id', 'reading_list_items', ['event_id'])


def downgrade() -> None:
    op.drop_index('ix_reading_list_items_event_id', table_name='reading_list_items')
    op.drop_index('ix_reading_list_items_list_id', table_name='reading_list_items')
    op.drop_table('reading_list_items')
    op.drop_index('ix_reading_lists_user_id', table_name='reading_lists')
    op.drop_table('reading_lists')
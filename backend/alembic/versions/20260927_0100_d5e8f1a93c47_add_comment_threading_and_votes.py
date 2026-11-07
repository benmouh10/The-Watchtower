"""add comment threading and votes

Adds threaded discussions (comments.parent_id, one reply level) and a
comment_votes table for quality voting.

Revision ID: d5e8f1a93c47
Revises: c1a4e7b93d20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision: str = 'd5e8f1a93c47'
down_revision: Union[str, None] = 'c1a4e7b93d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # updated_at / is_hidden are declared on the Comment model but were never
    # created by 0001_initial_schema, so any query against Comment failed with
    # "column comments.updated_at does not exist". Added here to close that gap.
    op.add_column(
        'comments',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'comments',
        sa.Column(
            'is_hidden',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    op.add_column(
        'comments',
        sa.Column('parent_id', PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_index('ix_comments_parent_id', 'comments', ['parent_id'])
    op.create_foreign_key(
        'fk_comments_parent_id',
        'comments',
        'comments',
        ['parent_id'],
        ['id'],
        ondelete='CASCADE',
    )

    op.create_table(
        'comment_votes',
        sa.Column('id', PG_UUID(as_uuid=True), primary_key=True),
        sa.Column('value', sa.Integer(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('comment_id', PG_UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', PG_UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['comment_id'], ['comments.id'], ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.CheckConstraint('value IN (-1, 1)', name='ck_comment_vote_value'),
        # One vote per user per comment: switching direction updates the
        # existing row instead of accumulating a second vote.
        sa.UniqueConstraint('comment_id', 'user_id', name='uq_comment_vote_user'),
    )
    op.create_index('ix_comment_votes_comment_id', 'comment_votes', ['comment_id'])
    op.create_index('ix_comment_votes_user_id', 'comment_votes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_comment_votes_user_id', table_name='comment_votes')
    op.drop_index('ix_comment_votes_comment_id', table_name='comment_votes')
    op.drop_table('comment_votes')

    op.drop_constraint('fk_comments_parent_id', 'comments', type_='foreignkey')
    op.drop_index('ix_comments_parent_id', table_name='comments')
    op.drop_column('comments', 'parent_id')
    op.drop_column('comments', 'is_hidden')
    op.drop_column('comments', 'updated_at')

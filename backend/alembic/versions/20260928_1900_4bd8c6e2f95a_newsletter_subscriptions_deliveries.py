"""newsletter: digest email subscriptions and delivery queue

Revision ID: 4bd8c6e2f95a
Revises: 7e9f2d4a81c3
Create Date: 2026-09-28 19:00:00.000000+00:00

Two tables power the newsletter automation built on the digest engine:

newsletter_subscriptions — one row per user (unique user_id). unsubscribed_at
is NULL while subscribed; unsubscribe sets it (kept for audit), resubscribe
clears it. Deleting a user cascades their row.

newsletter_deliveries — one send attempt per (digest_id, user_id), unique, so
a digest can never email a subscriber twice. delivery_status traces the
attempt: 'pending' (queued), 'sent' (real SMTP delivery), 'logged'
(EMAIL_ENABLED off; captured to log instead of emailed), or 'failed' (SMTP
error; error_text keeps the reason). Deleting a user or a digest cascades.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bd8c6e2f95a'
down_revision: Union[str, None] = '7e9f2d4a81c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'newsletter_subscriptions',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('unsubscribed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_newsletter_subscriptions_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_newsletter_subscriptions'),
        sa.UniqueConstraint('user_id', name='uq_newsletter_subscriptions_user_id'),
        comment=('Digest email subscriptions. One row per user; '
                 'unsubscribed_at non-null marks an ended subscription; the '
                 'row is kept for audit and the timestamp is cleared on a '
                 'resubscribe.'),
    )
    op.create_table(
        'newsletter_deliveries',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('digest_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('delivery_status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('error_text', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['digest_id'], ['digests.id'], ondelete='CASCADE', name='fk_newsletter_deliveries_digest_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_newsletter_deliveries_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_newsletter_deliveries'),
        sa.UniqueConstraint('digest_id', 'user_id', name='uq_newsletter_delivery_digest_user'),
        comment=('Digest newsletter sends. Unique (digest_id, user_id) so a '
                 'digest never emails a subscriber twice; status traces the '
                 'send attempt (pending/sent/logged/failed) with error_text '
                 'for failures.'),
    )
    op.create_index('ix_newsletter_deliveries_status', 'newsletter_deliveries', ['delivery_status'])


def downgrade() -> None:
    op.drop_index('ix_newsletter_deliveries_status', table_name='newsletter_deliveries')
    op.drop_table('newsletter_deliveries')
    op.drop_table('newsletter_subscriptions')
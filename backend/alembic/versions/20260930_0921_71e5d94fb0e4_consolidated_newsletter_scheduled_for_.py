"""consolidated newsletter scheduled_for webhook unique video columns

Revision ID: 71e5d94fb0e4
Revises: e54bdc8c420f
Create Date: 2026-09-30 09:21:25.705657+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71e5d94fb0e4'
down_revision: Union[str, None] = 'e54bdc8c420f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Newsletter digests: per-subscriber next-send stamp added by the
    # preferred_digest_hour work. pends_filter treats NULL (legacy rows) as
    # "due immediately", so the column is nullable with no backfill.
    op.add_column(
        'newsletter_deliveries',
        sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_newsletter_deliveries_scheduled_for',
        'newsletter_deliveries',
        ['scheduled_for'],
        unique=False,
    )

    # Webhook deliveries must be one row per (webhook, event) so queue_for_event
    # is idempotent; retries ride the same row. Milk-run idempotency relies on
    # this unique pair.
    op.create_unique_constraint(
        'uq_webhook_deliveries_webhook_event',
        'webhook_deliveries',
        ['webhook_id', 'event_id'],
    )

    # Video path (roadmap #10): the publisher-declared og:video/twitter:player
    # URL plus the transcript/summary artifacts produced by enrichment.
    op.add_column('articles', sa.Column('video_url', sa.String(length=2000), nullable=True))
    op.add_column('articles', sa.Column('video_transcript', sa.Text(), nullable=True))
    op.add_column('articles', sa.Column('video_summary', sa.Text(), nullable=True))
    op.add_column('articles', sa.Column('video_summary_model', sa.String(length=200), nullable=True))
    op.add_column('articles', sa.Column('video_processed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('articles', 'video_processed_at')
    op.drop_column('articles', 'video_summary_model')
    op.drop_column('articles', 'video_summary')
    op.drop_column('articles', 'video_transcript')
    op.drop_column('articles', 'video_url')
    op.drop_constraint(
        'uq_webhook_deliveries_webhook_event',
        'webhook_deliveries',
        type_='unique',
    )
    op.drop_index(
        'ix_newsletter_deliveries_scheduled_for',
        table_name='newsletter_deliveries',
    )
    op.drop_column('newsletter_deliveries', 'scheduled_for')

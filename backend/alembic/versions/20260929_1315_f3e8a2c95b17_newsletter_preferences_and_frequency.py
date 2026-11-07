"""newsletter preferences and digest frequency

Revision ID: f3e8a2c95b17
Revises: d6a9c4b31e75
Create Date: 2026-09-29 13:15:00.000000+00:00

Adds per-subscription newsletter preferences (digest_frequency, topics,
preferred_digest_hour, last_email_sent_at) and a frequency tag on digests so
the daily brief and weekly deep-dive can be targeted and audited separately.
All new columns carry server defaults, so existing rows upgrade safely in
place with no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'f3e8a2c95b17'
down_revision: Union[str, None] = 'd6a9c4b31e75'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'newsletter_subscriptions',
        sa.Column('digest_frequency', sa.String(length=16), server_default='daily', nullable=False),
    )
    op.add_column(
        'newsletter_subscriptions',
        sa.Column('topics', postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        'newsletter_subscriptions',
        sa.Column('preferred_digest_hour', sa.Integer(), server_default='6', nullable=False),
    )
    op.add_column(
        'newsletter_subscriptions',
        sa.Column('last_email_sent_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_newsletter_subscriptions_frequency',
        'newsletter_subscriptions',
        ['digest_frequency'],
    )
    op.add_column(
        'digests',
        sa.Column('frequency', sa.String(length=16), server_default='daily', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('digests', 'frequency')
    op.drop_index('ix_newsletter_subscriptions_frequency', table_name='newsletter_subscriptions')
    op.drop_column('newsletter_subscriptions', 'last_email_sent_at')
    op.drop_column('newsletter_subscriptions', 'preferred_digest_hour')
    op.drop_column('newsletter_subscriptions', 'topics')
    op.drop_column('newsletter_subscriptions', 'digest_frequency')
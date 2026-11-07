"""push notifications: device subscriptions and digest delivery queue

Revision ID: c2a3d5e8f010
Revises: 4bd8c6e2f95a
Create Date: 2026-09-29 00:15:00.000000+00:00

Two tables, mirroring the newsletter sibling feature but per-device.

push_subscriptions — one row per browser (unique endpoint), owned by a user.
p256dh/auth are the push-service encryption keys; storing them is what lets the
app address the device. Deleting the user cascades all their subscriptions.

push_deliveries — one send attempt per (digest_id, subscription_id), unique, so
a digest never notifies the same browser twice. delivery_status traces the
attempt ('pending'/'sent'/'logged'/'failed'). Deleting a digest or a
subscription cascades.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2a3d5e8f010'
down_revision: Union[str, None] = '4bd8c6e2f95a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'push_subscriptions',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('p256dh', sa.Text(), nullable=False),
        sa.Column('auth', sa.Text(), nullable=False),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_push_subscriptions_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_push_subscriptions'),
        sa.UniqueConstraint('endpoint', name='uq_push_subscriptions_endpoint'),
        comment=('Web Push device subscriptions. One row per browser (unique '
                 'endpoint); p256dh/auth are the push-service encryption keys '
                 'the app uses to address that device.'),
    )
    op.create_index('ix_push_subscriptions_user_id', 'push_subscriptions', ['user_id'])
    op.create_table(
        'push_deliveries',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('digest_id', sa.UUID(), nullable=False),
        sa.Column('subscription_id', sa.UUID(), nullable=False),
        sa.Column('delivery_status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('error_text', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['digest_id'], ['digests.id'], ondelete='CASCADE', name='fk_push_deliveries_digest_id'),
        sa.ForeignKeyConstraint(['subscription_id'], ['push_subscriptions.id'], ondelete='CASCADE', name='fk_push_deliveries_subscription_id'),
        sa.PrimaryKeyConstraint('id', name='pk_push_deliveries'),
        sa.UniqueConstraint('digest_id', 'subscription_id', name='uq_push_delivery_digest_sub'),
        comment=('Digest push sends. Unique (digest_id, subscription_id) so a '
                 'digest never notifies a browser twice; status traces the '
                 'attempt (pending/sent/logged/failed) with error_text for '
                 'failures.'),
    )
    op.create_index('ix_push_deliveries_status', 'push_deliveries', ['delivery_status'])


def downgrade() -> None:
    op.drop_index('ix_push_deliveries_status', table_name='push_deliveries')
    op.drop_table('push_deliveries')
    op.drop_index('ix_push_subscriptions_user_id', table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
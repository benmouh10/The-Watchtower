"""digests: scheduled comprehensive briefs

Revision ID: 7e9f2d4a81c3
Revises: 2b8c4e6a10fd
Create Date: 2026-09-28 18:00:00.000000+00:00

digests stores one row per completed scheduled comprehensive-brief run. The
engine ranks PROCESSED events inside a window by importance, renders a single
self-contained HTML document, and persists it here exactly once. event_ids
plus range_start/range_end record what a brief covered so readers can audit a
digest without trusting a re-render; content_html keeps the document identical
to what was generated, even after later code changes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7e9f2d4a81c3'
down_revision: Union[str, None] = '2b8c4e6a10fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'digests',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('range_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('range_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('event_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='UUIDs of the events covered by this brief, in rendered order.'),
        sa.Column('event_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content_html', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_digests'),
        comment=('Scheduled comprehensive briefs. Each row is one completed run: '
                 'the persisted self-contained HTML plus the exact events and time '
                 'window it covered, for auditability.'),
    )
    op.create_index('ix_digests_created_at', 'digests', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_digests_created_at', table_name='digests')
    op.drop_table('digests')
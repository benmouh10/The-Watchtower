"""add_event_citation_sources_and_contradictions

Revision ID: c1a4e7b93d20
Revises: f07290eae839
Create Date: 2026-09-27 00:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c1a4e7b93d20'
down_revision: Union[str, None] = 'f07290eae839'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ordered article UUIDs as presented to the LLM; index N-1 resolves the
    # summary's [N] inline citation. Previously this mapping was never stored,
    # so citations silently pointed at whichever article a read returned first.
    op.add_column(
        'events',
        sa.Column('citation_sources', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # Structured A-vs-B contradiction pairs detected across the cluster's outlets.
    op.add_column(
        'events',
        sa.Column('contradictions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # Deliberately no backfill. Events synthesized before this migration have a
    # summary whose [N] markers were resolved against an unordered read, so the
    # true article for each index is unrecoverable. Reconstructing an order here
    # would render confident-looking but potentially wrong attribution. Legacy
    # rows keep NULL, and the API reports citation_provenance='unavailable' so
    # the UI can say so rather than guess. Rows synthesized after this migration
    # carry a real map and report 'verified'.


def downgrade() -> None:
    op.drop_column('events', 'contradictions')
    op.drop_column('events', 'citation_sources')

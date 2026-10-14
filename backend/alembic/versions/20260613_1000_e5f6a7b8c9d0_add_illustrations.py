"""add illustrations

Creates illustrations (one row per event) holding a generated image plus the
record of how it was made: prompt, provider, model, attribution and failure
reason. The unique constraint on event_id is what makes "one illustration per
event, generated once" enforceable rather than a convention.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-13 1000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS illustrations (
            id UUID NOT NULL,
            event_id UUID NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            prompt TEXT,
            negative_prompt TEXT,
            provider VARCHAR(50),
            model VARCHAR(120),
            attribution VARCHAR(255),
            storage_key VARCHAR(500),
            content_type VARCHAR(100),
            byte_size INTEGER,
            width INTEGER,
            height INTEGER,
            seed BIGINT,
            failure_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT illustrations_pkey PRIMARY KEY (id),
            CONSTRAINT illustrations_event_id_key UNIQUE (event_id),
            CONSTRAINT illustrations_event_id_fkey FOREIGN KEY (event_id)
                REFERENCES events(id) ON DELETE CASCADE
        );
        """
    )
    # No separate index on event_id: UNIQUE (event_id) already indexes it.
    op.execute("DROP INDEX IF EXISTS ix_illustrations_event_id;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_illustrations_status ON illustrations (status);")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS illustrations;")
"""add per-claim citations and confidence

Revision ID: c3e9a17d4b82
Revises: f7c2d81b4a95
Create Date: 2026-09-28

Creates `event_claims` so the product can answer "which sources contributed to
this claim" per claim, rather than only attributing citations to a summary as a
whole, and adds a native `claimstatus` enum carrying a genuine
PARTIALLY_VERIFIED state plus a null confidence meaning "not scored".

Also widens the stored contradiction JSON to include a stable `contradiction_id`
key; the value is computed deterministically in the service, so no backfill is
required and existing rows simply gain the key on their next detection run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c3e9a17d4b82"
down_revision: Union[str, None] = "f7c2d81b4a95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Native enum rather than a free-text column: these strings are rendered as
    # review badges, so an unexpected value must be rejected on write, not just
    # by the API layer.
    claimstatus = postgresql.ENUM(
        "Corroborated",
        "Partially verified",
        "Disputed",
        "Unverified",
        name="claimstatus",
        create_type=False,
    )
    claimstatus.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "event_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        # status and confidence are both nullable on purpose:
        #   status NULL     = the claim was extracted but never adjudicated
        #   confidence NULL = the model expressed no confidence, which is
        #                      different from a confidence of 0
        sa.Column("status", claimstatus, nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("citation_indices", postgresql.JSONB(), nullable=False, server_default="[]"),
        # No third-party fact-checker feed is connected, so these stay NULL and
        # the UI must not render a "reviewed by" badge from them.
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("review_url", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_event_claims_event_id", "event_claims", ["event_id"])
    # The common read is "all claims for this event in order".
    op.create_index(
        "ix_event_claims_event_ordinal", "event_claims", ["event_id", "ordinal"]
    )

    # Seed nothing. There are no real extracted claims to store yet, and writing
    # placeholder rows would present invented analysis as a real verdict.
    op.execute(
        """
        COMMENT ON TABLE event_claims IS
        'Per-claim citations and confidence. Rows are produced by claim '
        'extraction only; NULL status/confidence mean not adjudicated/not scored.'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_event_claims_event_ordinal", table_name="event_claims")
    op.drop_index("ix_event_claims_event_id", table_name="event_claims")
    op.drop_table("event_claims")
    postgresql.ENUM(name="claimstatus").drop(op.get_bind(), checkfirst=True)

"""drift cleanup: intentional schema alignment

Revision ID: 0f76d0c799a3
Revises: d4f2a91c7e38
Create Date: 2026-09-28 14:51:13.361396+00:00

Purpose
-------
This migration exists because alembic's ``compare_metadata`` reports seven ops
of drift that are all *intentional* on the DB side — the models already declare
them and this file makes the live database match:

  1. ``comment_votes.comment_id`` / ``comment_votes.user_id`` were created
     nullable in the legacy schema though the app always writes both. The table
     is empty (verified), so they can be tightened to NOT NULL now.
  2. Drop the redundant non-unique indexes ``ix_embeddings_article_id`` and
     ``ix_users_email``. Each column already has a UNIQUE *constraint* named
     ``*_key`` whose own btree index serves both uniqueness and lookups; the
     extra non-unique index was pure write/storage overhead and a perpetual
     source of autogenerate noise.
  3. Add the column comments on ``events.citation_sources`` and
     ``events.contradictions``. The models carry these comments (see
     app/entities/models.py); the DB never got them, so autogenerate kept
     proposing a comment drop every run.
  4. Add the ``uuid_generate_v4()`` default on ``refresh_tokens.id`` for
     consistency with every other table in the schema (legacy create scripts
     gave it a default; the migration that introduced refresh_tokens omitted
     it, and the model now declares it).

None of these change application behaviour; they make ``compare_metadata``
return an empty result so future autogenerate invocations are trustworthy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f76d0c799a3'
down_revision: Union[str, None] = 'd4f2a91c7e38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CITATION_SOURCES_COMMENT = ("Ordered list of article UUIDs exactly as presented to the LLM "
                             "at synthesis time. Index N-1 is the target of the summary's "
                             "[N] inline citation.")
_CONTRADICTIONS_COMMENT = ("Structured A-vs-B contradiction pairs detected across the "
                           "cluster's sources.")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. comment_votes — tighten to NOT NULL. Guard: refuse to run if NULLs
    #    exist rather than silently discarding votes.
    nulls = bind.execute(
        sa.text(
            "SELECT count(*) FROM comment_votes "
            "WHERE comment_id IS NULL OR user_id IS NULL"
        )
    ).scalar_one()
    if nulls:
        raise RuntimeError(
            f"aborting drift-cleanup migration: {nulls} comment_votes rows have "
            "NULL comment_id/user_id; resolve before tightening to NOT NULL"
        )
    op.alter_column("comment_votes", "comment_id", existing_type=sa.UUID(), nullable=False)
    op.alter_column("comment_votes", "user_id", existing_type=sa.UUID(), nullable=False)

    # 2. drop the redundant non-unique indexes (unique constraints remain).
    op.drop_index("ix_embeddings_article_id", table_name="embeddings")
    op.drop_index("ix_users_email", table_name="users")

    # 3. add column comments the model already declares.
    op.execute(
        f"COMMENT ON COLUMN events.citation_sources IS "
        f"{_sql_literal(_CITATION_SOURCES_COMMENT)}"
    )
    op.execute(
        f"COMMENT ON COLUMN events.contradictions IS "
        f"{_sql_literal(_CONTRADICTIONS_COMMENT)}"
    )

    # 4. refresh_tokens.id → uuid_generate_v4() default, like every other table.
    op.execute("ALTER TABLE refresh_tokens ALTER COLUMN id SET DEFAULT uuid_generate_v4()")


def downgrade() -> None:
    op.execute("ALTER TABLE refresh_tokens ALTER COLUMN id DROP DEFAULT")
    op.execute("COMMENT ON COLUMN events.contradictions IS NULL")
    op.execute("COMMENT ON COLUMN events.citation_sources IS NULL")
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    op.create_index("ix_embeddings_article_id", "embeddings", ["article_id"], unique=False)
    # Restoring nullable is safe even without a NULL-check guard on downgrade:
    # the schema simply returns to the legacy loose state.
    op.alter_column("comment_votes", "user_id", existing_type=sa.UUID(), nullable=True)
    op.alter_column("comment_votes", "comment_id", existing_type=sa.UUID(), nullable=True)


def _sql_literal(text: str) -> str:
    """Escape a string for embedding in a SQL literal (single quotes doubled)."""
    return "'" + text.replace("'", "''") + "'"
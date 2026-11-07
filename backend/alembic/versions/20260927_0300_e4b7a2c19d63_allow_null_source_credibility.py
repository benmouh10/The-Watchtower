"""allow an unassessed source to have no credibility score

Source.credibility_score was NOT NULL with a Python-side default of 0.5, so every
newly created publisher was silently rendered as "50% trust" in the event page's
Trust Avg metric. That is a rating nobody made, and it defeated the existing
unrated-source accounting in event_controller, which counts sources whose score
is None but could never observe one.

This mirrors 20260927_0200_b3f1c8a27d90, which did the same for bias_lean.

Existing rows keep whatever they have, so no data is rewritten here.

Revision ID: e4b7a2c19d63
Revises: b3f1c8a27d90
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e4b7a2c19d63'
down_revision: Union[str, None] = 'b3f1c8a27d90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop NOT NULL and the server DEFAULT 0.5 together. Leaving the default in
    # place would keep fabricating a neutral 50% trust rating for any insert that
    # omits the column.
    op.execute("ALTER TABLE sources ALTER COLUMN credibility_score DROP NOT NULL")
    op.execute("ALTER TABLE sources ALTER COLUMN credibility_score DROP DEFAULT")


def downgrade() -> None:
    # Any source still lacking a rating would block the NOT NULL restore, so
    # report it rather than silently inventing a neutral score for those rows.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM sources WHERE credibility_score IS NULL) THEN
                RAISE EXCEPTION
                    'Cannot restore NOT NULL: % source(s) have no credibility_score. '
                    'Assign a rating or delete those sources first.',
                    (SELECT COUNT(*) FROM sources WHERE credibility_score IS NULL);
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE sources ALTER COLUMN credibility_score SET NOT NULL")
    op.execute("ALTER TABLE sources ALTER COLUMN credibility_score SET DEFAULT 0.5")

"""allow an unassessed source to have no bias lean

Source.bias_lean was NOT NULL with a Python-side default of BiasLean.CENTER, so
every newly created publisher was silently labelled "centre" and then rendered
that way in the bias spectrum. That is a rating nobody made. Event.bias_lean was
already nullable; this brings Source in line.

Existing rows keep whatever they have, so no data is rewritten here.

Revision ID: b3f1c8a27d90
Revises: d5e8f1a93c47
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b3f1c8a27d90'
down_revision: Union[str, None] = 'd5e8f1a93c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop NOT NULL and the server DEFAULT 'CENTER' together. Leaving the
    # default in place would keep fabricating a centre rating for any insert
    # that omits the column.
    op.execute("ALTER TABLE sources ALTER COLUMN bias_lean DROP NOT NULL")
    op.execute("ALTER TABLE sources ALTER COLUMN bias_lean DROP DEFAULT")


def downgrade() -> None:
    # Any source still lacking a rating would block the NOT NULL restore, so
    # report it rather than silently inventing a lean for those rows.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM sources WHERE bias_lean IS NULL) THEN
                RAISE EXCEPTION
                    'Cannot restore NOT NULL: % source(s) have no bias_lean. '
                    'Assign a rating or delete those sources first.',
                    (SELECT COUNT(*) FROM sources WHERE bias_lean IS NULL);
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE sources ALTER COLUMN bias_lean SET NOT NULL")
    op.execute("ALTER TABLE sources ALTER COLUMN bias_lean SET DEFAULT 'CENTER'")

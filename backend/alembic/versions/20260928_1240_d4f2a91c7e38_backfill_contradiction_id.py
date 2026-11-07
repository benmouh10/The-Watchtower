"""backfill contradiction_id on stored contradictions

Rows written before `contradiction_id` existed have no stable key. The event
page uses that key as a React list key and to track a contradiction across
polls, so those rows silently lose their identity: React falls back to the array
index, and any expand/collapse or deep-link state attaches to the wrong row
whenever detection order changes.

The id is derived from the two article ids, so it is fully recoverable from the
row itself. No re-detection, and no LLM call, is needed.

Revision ID: d4f2a91c7e38
Revises: bb81141c8897
Create Date: 2026-09-28 12:40:00.000000+00:00
"""
import hashlib
import json
from typing import Any, Dict, List, Sequence, Tuple, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4f2a91c7e38'
down_revision: Union[str, None] = 'bb81141c8897'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _contradiction_id(article_id_a: str, article_id_b: str) -> str:
    """
    FROZEN COPY of ContradictionService._contradiction_id. Do not edit.

    This is duplicated rather than imported on purpose. A migration has to keep
    working years from now, and importing application code would make it break
    the first time that module is refactored. The duplication is guarded instead:
    test_contradiction_id_backfill.py imports this function and asserts it
    still agrees with the live implementation over a batch of pairs, so drift
    fails a test rather than silently changing ids.
    """
    pair = "|".join(sorted([str(article_id_a), str(article_id_b)]))
    return hashlib.sha256(pair.encode("utf-8")).hexdigest()[:16]


def _recoverable(item: Dict[str, Any]) -> bool:
    """
    True when an id can be derived from this row.

    A contradiction missing either article id has nothing to hash. Inventing an
    id from empty strings would produce a stable but wrong key -- worse than a
    missing one, because it would collide across unrelated rows and look valid.
    Those rows are left untouched and reported instead.
    """
    if not isinstance(item, dict):
        return False
    for side in ("claim_a", "claim_b"):
        claim = item.get(side)
        if not isinstance(claim, dict):
            return False
        value = claim.get("article_id")
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def backfill_contradiction_ids(connection) -> Tuple[int, int, int]:
    """
    Fill in a missing `contradiction_id` on every stored contradiction.

    Returns (events_scanned, ids_written, rows_skipped). Idempotent: an id that
    is already present is never rewritten, so an upgrade/downgrade/upgrade cycle
    is a no-op the second time.
    """
    events = connection.execute(sa.text(
        "SELECT id, contradictions FROM events "
        "WHERE contradictions IS NOT NULL "
        "  AND jsonb_typeof(contradictions) = 'array'"
    )).fetchall()

    scanned = written = skipped = 0

    for event_id, raw in events:
        items: List[Any] = (
            json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        ) or []
        if not items:
            continue
        scanned += 1

        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("contradiction_id") or "").strip():
                continue  # already has a stable key; leave it alone
            if not _recoverable(item):
                skipped += 1
                continue
            item["contradiction_id"] = _contradiction_id(
                item["claim_a"]["article_id"],
                item["claim_b"]["article_id"],
            )
            written += 1
            changed = True

        if changed:
            connection.execute(
                sa.text(
                    "UPDATE events SET contradictions = CAST(:payload AS jsonb) "
                    "WHERE id = :event_id"
                ),
                {"payload": json.dumps(items), "event_id": event_id},
            )

    return scanned, written, skipped


def upgrade() -> None:
    connection = op.get_bind()
    scanned, written, skipped = backfill_contradiction_ids(connection)
    op.execute(
        "DO $$ BEGIN RAISE NOTICE "
        "'contradiction_id backfill: % events scanned, % ids written, % rows "
        "skipped (missing article ids)'; END $$;" % (scanned, written, skipped)
    )


def downgrade() -> None:
    """
    Intentionally does nothing.

    Removing the ids again would discard recovered data to satisfy a code
    version that does not happen to read it. Nothing breaks if the extra field
    is present on older code, so the only thing a destructive downgrade could
    accomplish here is lose information.
    """
    pass

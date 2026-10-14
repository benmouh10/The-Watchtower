"""add blocked_ips

Durable record of Threat Lens block decisions. The live block marker lives in
Redis (the middleware reads it per request); this table is the audit/ops row an
operator can query, and survives a Redis flush. `ip` is unique so re-blocking
upserts rather than piling up expired rows.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-07 1200
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_ips (
            id UUID NOT NULL PRIMARY KEY,
            ip VARCHAR(45) NOT NULL,
            source VARCHAR(20) NOT NULL DEFAULT 'THREAT_LENS',
            reason TEXT,
            risk_score FLOAT,
            blocked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ,
            CONSTRAINT uq_blocked_ips_ip UNIQUE (ip)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_blocked_ips_ip ON blocked_ips (ip)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_blocked_ips_ip")
    op.execute("DROP TABLE IF EXISTS blocked_ips")
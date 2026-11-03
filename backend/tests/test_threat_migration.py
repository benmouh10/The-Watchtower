"""Hermetic tests for the blocked_ips Alembic migration and BlockedIp entity."""
from pathlib import Path
from datetime import datetime, timezone
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.entities.models import BlockedIp, BlockSource


def test_alembic_head_lands_on_new_revision():
    """Verify that the Alembic head lands on the blocked_ips revision chained on e5f6a7b8c9d0."""
    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()

    assert len(heads) == 1, f"Expected single head, got: {heads}"
    head_rev = heads[0]
    assert head_rev == "f6a7b8c9d0e1", f"Expected head f6a7b8c9d0e1, got {head_rev}"

    revision = script.get_revision(head_rev)
    assert revision.down_revision == "e5f6a7b8c9d0", (
        f"Expected down_revision e5f6a7b8c9d0, got {revision.down_revision}"
    )


def test_blocked_ip_entity_attributes_and_aliases():
    """Verify BlockedIp entity columns, properties, and constructors."""
    now = datetime.now(timezone.utc)
    entry = BlockedIp(
        ip="203.0.113.100",
        reason="anomalous_attack",
        risk=0.98,
        blocked_by="auto",
        created_at=now,
    )
    assert entry.ip == "203.0.113.100"
    assert entry.reason == "anomalous_attack"
    assert entry.risk == 0.98
    assert entry.risk_score == 0.98
    assert entry.blocked_by == "auto"
    assert entry.source == BlockSource.THREAT_LENS.value
    assert entry.created_at == now
    assert entry.blocked_at == now

    # Test admin / manual block
    admin_entry = BlockedIp(
        ip="198.51.100.200",
        reason="admin_action",
        risk=1.0,
        blocked_by="admin",
    )
    assert admin_entry.blocked_by == "admin"
    assert admin_entry.source == BlockSource.MANUAL.value

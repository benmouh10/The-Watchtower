"""api keys: hashed long-lived bearer credentials with scopes

Revision ID: 2b8c4e6a10fd
Revises: 9a1f7c3d05be
Create Date: 2026-09-28 17:00:00.000000+00:00

api_keys stores machine credentials. Only the SHA-256 hex of a key is kept
(key_hash, unique), plus a short display prefix and the key's scope set.
revoked_at disables a key (mirrors refresh_tokens), expires_at offers optional
rotation, last_used_at records the most recent successful request. Deleting a
user cascades their keys.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2b8c4e6a10fd'
down_revision: Union[str, None] = '9a1f7c3d05be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'api_keys',
        sa.Column('id', sa.UUID(), nullable=False, server_default=sa.text('uuid_generate_v4()')),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('key_prefix', sa.String(length=16), nullable=False),
        sa.Column('scopes', postgresql.ARRAY(sa.String()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='fk_api_keys_user_id_users'),
        sa.PrimaryKeyConstraint('id', name='pk_api_keys'),
        sa.UniqueConstraint('key_hash', name='uq_api_keys_key_hash'),
        comment=('Long-lived bearer API keys. Only SHA-256 hex is stored; '
                 'key_prefix is the display prefix, scopes bound the '
                 'credential, and revoked_at non-null disables the key.'),
    )
    op.create_index('ix_api_keys_user_id', 'api_keys', ['user_id'])
    op.create_index('ix_api_keys_created_at', 'api_keys', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_api_keys_created_at', table_name='api_keys')
    op.drop_index('ix_api_keys_user_id', table_name='api_keys')
    op.drop_table('api_keys')
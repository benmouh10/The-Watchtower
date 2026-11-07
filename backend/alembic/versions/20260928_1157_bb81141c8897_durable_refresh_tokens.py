"""durable refresh tokens

Adds `refresh_tokens`, the server-side record that makes rotation and logout
real operations rather than announcements.

This file was generated with --autogenerate and then trimmed. Autogenerate also
emitted the project's long-standing model/schema drift -- server defaults,
unnamed foreign keys, a unique index on users.email, and the pgvector ivfflat
index -- and applying that verbatim would have DROPPED ix_embeddings_vector_ivfflat
and the embeddings unique constraint. That drift is a separate concern with its
own review; it is not smuggled into a migration about session tokens.

Revision ID: bb81141c8897
Revises: c3e9a17d4b82
Create Date: 2026-09-28 11:57:45.018786+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'bb81141c8897'
down_revision: Union[str, None] = 'c3e9a17d4b82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('user_id', sa.UUID(), nullable=False),
        # SHA-256 hex digest. Unique, which is also the lookup path used on
        # every refresh, so this constraint doubles as the index.
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('family_id', sa.UUID(), nullable=False),
        sa.Column('jti', sa.String(length=64), nullable=False),
        sa.Column(
            'issued_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('replaced_by_id', sa.UUID(), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        # SET NULL, not CASCADE: deleting a rotation chain should not delete the
        # whole family, or revoking a single token would orphan its siblings.
        sa.ForeignKeyConstraint(
            ['replaced_by_id'], ['refresh_tokens.id'], ondelete='SET NULL'
        ),
        # CASCADE: deleting a user must take their sessions with it, or the
        # tokens would outlive the account they authenticate.
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_refresh_tokens_family_id', 'refresh_tokens', ['family_id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_jti'), 'refresh_tokens', ['jti'], unique=False)
    # Supports the "revoke everything still live for this user" sweep, which
    # filters on exactly these two columns.
    op.create_index('ix_refresh_tokens_user_active', 'refresh_tokens', ['user_id', 'revoked_at'], unique=False)
    op.create_index('ix_refresh_tokens_user_id', 'refresh_tokens', ['user_id'], unique=False)

    op.create_table_comment(
        'refresh_tokens',
        'Server-side record of issued refresh tokens. Stores only a SHA-256 '
        'digest, never the token. family_id groups one login rotation chain; '
        'revoked_at non-null means spent or revoked, and replaying such a token '
        'revokes the whole family.',
        existing_comment=None,
        schema=None,
    )


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_user_id', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_user_active', table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_jti'), table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_family_id', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')

"""multi-modal media columns on articles

Revision ID: 0a3eaaa770d3
Revises: c2a3d5e8f010
Create Date: 2026-09-29 08:30:00.000000+00:00

Attach source-published media to an article: the image URL, its caption/alt
text, and a coarse media_type ("image"/null). All nullable — an article scraped
without an image is simply null, never a placeholder. The scraper stores only
what the source actually published (og:image on the hydrated page, or the feed's
own thumbnail), so no row asserts media a site never provided.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a3eaaa770d3'
down_revision: Union[str, None] = 'c2a3d5e8f010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'articles',
        sa.Column(
            'image_url',
            sa.String(length=2000),
            nullable=True,
            comment=('Image URL the source published for this story (og:image '
                     'from the hydrated page, or the feed thumbnail). Null when '
                     'the article was ingested without one.'),
        ),
    )
    op.add_column(
        'articles',
        sa.Column(
            'image_caption',
            sa.Text(),
            nullable=True,
            comment=('Alt/caption text the source attached to that image '
                     '(og:image:alt). Null when the source gave none.'),
        ),
    )
    op.add_column(
        'articles',
        sa.Column(
            'media_type',
            sa.String(length=20),
            nullable=True,
            comment=('Kind of media stored: "image" when image_url is present, '
                     'null when none was captured.'),
        ),
    )


def downgrade() -> None:
    op.drop_column('articles', 'media_type')
    op.drop_column('articles', 'image_caption')
    op.drop_column('articles', 'image_url')
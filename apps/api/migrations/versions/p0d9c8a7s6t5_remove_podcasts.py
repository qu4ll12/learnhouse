"""Remove podcasts and podcast episodes

Revision ID: p0d9c8a7s6t5
Revises: c7h4n5e6l7r8
Create Date: 2026-11-19 12:00:00.000000

Removes the podcastepisode and podcast tables and cleans active shared
references (slug redirects, folder contents, usergroup resources,
resource authors) that reference podcasts.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = 'p0d9c8a7s6t5'
down_revision: Union[str, None] = 'c7h4n5e6l7r8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Dropping a table removes its indexes. Drop episodes first because they reference podcasts.
    if inspector.has_table('podcastepisode'):
        op.drop_table('podcastepisode')

    if inspector.has_table('podcast'):
        op.drop_table('podcast')

    # Clean active shared references that would dangle
    if inspector.has_table('resource_slug_redirect'):
        conn.execute(sa.text("DELETE FROM resource_slug_redirect WHERE resource_type = 'podcast' OR resource_uuid LIKE 'podcast_%'"))

    if inspector.has_table('foldercontent'):
        conn.execute(sa.text("DELETE FROM foldercontent WHERE resource_uuid LIKE 'podcast_%'"))

    if inspector.has_table('usergroupresource'):
        conn.execute(sa.text("DELETE FROM usergroupresource WHERE resource_uuid LIKE 'podcast_%'"))

    if inspector.has_table('resourceauthor'):
        conn.execute(sa.text("DELETE FROM resourceauthor WHERE resource_uuid LIKE 'podcast_%'"))


def downgrade() -> None:
    # 1. Recreate podcast table
    op.create_table(
        'podcast',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('podcast_uuid', sa.String(), nullable=False, unique=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('about', sa.String(), nullable=True),
        sa.Column('tags', sa.String(), nullable=True),
        sa.Column('thumbnail_image', sa.String(), nullable=True, server_default=''),
        sa.Column('public', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('seo', JSONB(), nullable=True),
        sa.Column('slug', sa.String(length=100), nullable=True),
        sa.Column('creation_date', sa.String(), nullable=False),
        sa.Column('update_date', sa.String(), nullable=False),
    )
    op.create_index('ix_podcast_org_id', 'podcast', ['org_id'])
    op.create_index('ix_podcast_podcast_uuid', 'podcast', ['podcast_uuid'])
    op.create_index(
        'uq_podcast_org_slug',
        'podcast',
        ['org_id', 'slug'],
        unique=True,
        postgresql_where=sa.text('slug IS NOT NULL'),
    )

    # 2. Recreate podcastepisode table
    op.create_table(
        'podcastepisode',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('podcast_id', sa.Integer(), sa.ForeignKey('podcast.id', ondelete='CASCADE'), nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('episode_uuid', sa.String(), nullable=False, unique=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('audio_file', sa.String(), nullable=True, server_default=''),
        sa.Column('duration_seconds', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('episode_number', sa.Integer(), nullable=True, server_default='1'),
        sa.Column('thumbnail_image', sa.String(), nullable=True, server_default=''),
        sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('creation_date', sa.String(), nullable=False),
        sa.Column('update_date', sa.String(), nullable=False),
    )
    op.create_index('ix_podcastepisode_podcast_id', 'podcastepisode', ['podcast_id'])
    op.create_index('ix_podcastepisode_org_id', 'podcastepisode', ['org_id'])
    op.create_index('ix_podcastepisode_episode_uuid', 'podcastepisode', ['episode_uuid'])
    op.create_index('ix_podcastepisode_order', 'podcastepisode', ['podcast_id', 'order'])

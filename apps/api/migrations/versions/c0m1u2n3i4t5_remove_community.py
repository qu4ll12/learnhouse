"""Remove communities and discussions

Revision ID: c0m1u2n3i4t5
Revises: p0d9c8a7s6t5
Create Date: 2026-11-19 15:00:00.000000

Drops community and discussion tables (discussioncommentvote, discussionvote,
discussionreaction, discussioncomment, discussion, community) in FK-safe order.
Cleans shared references (resource_slug_redirect, foldercontent, usergroupresource,
resourceauthor, notification, webhook_endpoint events, role rights JSON, and
organization config JSON).
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'c0m1u2n3i4t5'
down_revision: Union[str, None] = 'p0d9c8a7s6t5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Drop discussion tables then community in FK-safe order
    if inspector.has_table('discussioncommentvote'):
        op.drop_table('discussioncommentvote')

    if inspector.has_table('discussionvote'):
        op.drop_table('discussionvote')

    if inspector.has_table('discussionreaction'):
        op.drop_table('discussionreaction')

    if inspector.has_table('discussioncomment'):
        op.drop_table('discussioncomment')

    if inspector.has_table('discussion'):
        op.drop_table('discussion')

    if inspector.has_table('community'):
        op.drop_table('community')

    # 2. Clean active shared references
    if inspector.has_table('resource_slug_redirect'):
        conn.execute(sa.text("DELETE FROM resource_slug_redirect WHERE resource_type = 'community' OR resource_uuid LIKE 'community_%' OR resource_uuid LIKE 'discussion_%'"))

    if inspector.has_table('foldercontent'):
        conn.execute(sa.text("DELETE FROM foldercontent WHERE resource_uuid LIKE 'community_%' OR resource_uuid LIKE 'discussion_%'"))

    if inspector.has_table('usergroupresource'):
        conn.execute(sa.text("DELETE FROM usergroupresource WHERE resource_uuid LIKE 'community_%' OR resource_uuid LIKE 'discussion_%'"))

    if inspector.has_table('resourceauthor'):
        conn.execute(sa.text("DELETE FROM resourceauthor WHERE resource_uuid LIKE 'community_%' OR resource_uuid LIKE 'discussion_%'"))

    if inspector.has_table('notification'):
        conn.execute(sa.text("DELETE FROM notification WHERE category = 'community' OR type IN ('discussion_reply', 'discussion_mention')"))

    # 3. Clean role rights JSON: remove 'communities' and 'discussions' keys
    if inspector.has_table('role'):
        if conn.dialect.name == "postgresql":
            conn.execute(sa.text("UPDATE role SET rights = (rights::jsonb - 'communities' - 'discussions') WHERE rights IS NOT NULL"))
        else:
            rows = conn.execute(sa.text("SELECT id, rights FROM role WHERE rights IS NOT NULL")).fetchall()
            for r_id, rights_data in rows:
                if isinstance(rights_data, str):
                    try:
                        rights_dict = json.loads(rights_data)
                    except Exception:
                        continue
                elif isinstance(rights_data, dict):
                    rights_dict = dict(rights_data)
                else:
                    continue
                modified = False
                for k in ("communities", "discussions"):
                    if k in rights_dict:
                        del rights_dict[k]
                        modified = True
                if modified:
                    conn.execute(sa.text("UPDATE role SET rights = :rights WHERE id = :id"), {"rights": json.dumps(rights_dict), "id": r_id})

    # 4. Clean webhook_endpoint events list
    if inspector.has_table('webhook_endpoint'):
        rows = conn.execute(sa.text("SELECT id, events FROM webhook_endpoint WHERE events IS NOT NULL")).fetchall()
        for wh_id, ev_data in rows:
            events_list = json.loads(ev_data) if isinstance(ev_data, str) else ev_data
            if isinstance(events_list, list):
                cleaned = [
                    e for e in events_list
                    if not (e in ('discussion_posted', 'comment_created', 'discussion_pinned', 'discussion_locked', 'discussion_vote_cast') or str(e).startswith(('discussion_', 'community_')))
                ]
                if len(cleaned) != len(events_list):
                    if conn.dialect.name == "postgresql":
                        conn.execute(sa.text("UPDATE webhook_endpoint SET events = CAST(:ev AS json) WHERE id = :id"), {"ev": json.dumps(cleaned), "id": wh_id})
                    else:
                        conn.execute(sa.text("UPDATE webhook_endpoint SET events = :ev WHERE id = :id"), {"ev": json.dumps(cleaned), "id": wh_id})

    # 5. Clean organizationconfig JSON: feature & admin toggles, seo settings, customization menu
    if inspector.has_table('organizationconfig'):
        rows = conn.execute(sa.text("SELECT id, config FROM organizationconfig WHERE config IS NOT NULL")).fetchall()
        for cfg_id, cfg_data in rows:
            if isinstance(cfg_data, str):
                try:
                    cfg = json.loads(cfg_data)
                except Exception:
                    continue
            elif isinstance(cfg_data, dict):
                cfg = dict(cfg_data)
            else:
                continue

            modified = False
            for k in ("communities", "discussions"):
                if k in cfg:
                    del cfg[k]
                    modified = True
            if "features" in cfg and isinstance(cfg["features"], dict):
                for k in ("communities", "discussions"):
                    if k in cfg["features"]:
                        del cfg["features"][k]
                        modified = True
            if "admin_toggles" in cfg and isinstance(cfg["admin_toggles"], dict):
                for k in ("communities", "discussions"):
                    if k in cfg["admin_toggles"]:
                        del cfg["admin_toggles"][k]
                        modified = True
            if "seo" in cfg and isinstance(cfg["seo"], dict):
                if "noindex_communities" in cfg["seo"]:
                    del cfg["seo"]["noindex_communities"]
                    modified = True
                if "thin_thread_noindex_days" in cfg["seo"]:
                    del cfg["seo"]["thin_thread_noindex_days"]
                    modified = True
                if "indexable_features" in cfg["seo"] and isinstance(cfg["seo"]["indexable_features"], dict):
                    if "communities" in cfg["seo"]["indexable_features"]:
                        del cfg["seo"]["indexable_features"]["communities"]
                        modified = True
            customization = cfg.get("customization")
            if isinstance(customization, dict):
                menu = customization.get("menu")
                if isinstance(menu, dict) and isinstance(menu.get("items"), list):
                    old_len = len(menu["items"])
                    menu["items"] = [item for item in menu["items"] if isinstance(item, dict) and item.get("type") not in ("communities", "community")]
                    if len(menu["items"]) != old_len:
                        modified = True
            navigation = cfg.get("navigation")
            if isinstance(navigation, dict) and isinstance(navigation.get("items"), list):
                old_len = len(navigation["items"])
                navigation["items"] = [item for item in navigation["items"] if isinstance(item, dict) and item.get("type") not in ("communities", "community")]
                if len(navigation["items"]) != old_len:
                    modified = True

            if modified:
                if conn.dialect.name == "postgresql":
                    conn.execute(sa.text("UPDATE organizationconfig SET config = CAST(:cfg AS json) WHERE id = :id"), {"cfg": json.dumps(cfg), "id": cfg_id})
                else:
                    conn.execute(sa.text("UPDATE organizationconfig SET config = :cfg WHERE id = :id"), {"cfg": json.dumps(cfg), "id": cfg_id})


def downgrade() -> None:
    """Recreate community and discussion tables empty (data loss accepted)."""
    # 1. Recreate community table
    op.create_table(
        'community',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('course.id', ondelete='SET NULL'), nullable=True),
        sa.Column('community_uuid', sa.String(), nullable=False, server_default=''),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('public', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('thumbnail_image', sa.String(), nullable=True, server_default=''),
        sa.Column('slug', sa.String(length=100), nullable=True),
        sa.Column('seo', JSONB(), nullable=True),
        sa.Column('moderation_words', sa.JSON(), nullable=True),
        sa.Column('moderation_settings', sa.JSON(), nullable=True),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.Column('update_date', sa.String(), nullable=False, server_default=''),
    )
    op.create_index('ix_community_org_id', 'community', ['org_id'])
    op.create_index('ix_community_course_id', 'community', ['course_id'])
    op.create_index('ix_community_community_uuid', 'community', ['community_uuid'])
    op.create_index(
        'uq_community_org_slug',
        'community',
        ['org_id', 'slug'],
        unique=True,
        postgresql_where=sa.text('slug IS NOT NULL'),
    )

    # 2. Recreate discussion table
    op.create_table(
        'discussion',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('community_id', sa.Integer(), sa.ForeignKey('community.id', ondelete='CASCADE'), nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('discussion_uuid', sa.String(), nullable=False, server_default=''),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('label', sa.String(length=50), nullable=True, server_default='general'),
        sa.Column('emoji', sa.String(length=50), nullable=True),
        sa.Column('upvote_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('edit_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_locked', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('answer_comment_id', sa.Integer(), nullable=True),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.Column('update_date', sa.String(), nullable=False, server_default=''),
    )
    op.create_index('ix_discussion_community_id', 'discussion', ['community_id'])
    op.create_index('ix_discussion_discussion_uuid', 'discussion', ['discussion_uuid'])
    op.create_index('ix_discussion_author_id', 'discussion', ['author_id'])
    op.create_index(
        'ix_discussion_community_pinned_date',
        'discussion',
        ['community_id', 'is_pinned', 'creation_date'],
    )

    # 3. Recreate discussioncomment table
    op.create_table(
        'discussioncomment',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('discussion_id', sa.Integer(), sa.ForeignKey('discussion.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('comment_uuid', sa.String(), nullable=False, server_default=''),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('upvote_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.Column('update_date', sa.String(), nullable=False, server_default=''),
    )
    op.create_index('ix_discussioncomment_discussion_id', 'discussioncomment', ['discussion_id'])
    op.create_index('ix_discussioncomment_author_id', 'discussioncomment', ['author_id'])
    op.create_index('ix_discussioncomment_comment_uuid', 'discussioncomment', ['comment_uuid'])

    # 4. Recreate discussionvote table
    op.create_table(
        'discussionvote',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('discussion_id', sa.Integer(), sa.ForeignKey('discussion.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vote_uuid', sa.String(), nullable=False, server_default=''),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('discussion_id', 'user_id', name='unique_discussion_user_vote'),
    )
    op.create_index('ix_discussionvote_discussion_id', 'discussionvote', ['discussion_id'])
    op.create_index('ix_discussionvote_user_id', 'discussionvote', ['user_id'])

    # 5. Recreate discussioncommentvote table
    op.create_table(
        'discussioncommentvote',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('comment_id', sa.Integer(), sa.ForeignKey('discussioncomment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vote_uuid', sa.String(), nullable=False, server_default=''),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('comment_id', 'user_id', name='unique_comment_user_vote'),
    )
    op.create_index('ix_discussioncommentvote_comment_id', 'discussioncommentvote', ['comment_id'])
    op.create_index('ix_discussioncommentvote_user_id', 'discussioncommentvote', ['user_id'])

    # 6. Recreate discussionreaction table
    op.create_table(
        'discussionreaction',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('discussion_id', sa.Integer(), sa.ForeignKey('discussion.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('emoji', sa.String(length=50), nullable=False),
        sa.Column('reaction_uuid', sa.String(), nullable=False, server_default=''),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('discussion_id', 'user_id', 'emoji', name='unique_discussion_user_emoji'),
    )

"""Anonymous-safe public content for search engines, feeds and AI search.

Everything here answers as an anonymous visitor would see the org: only
public, published, not-noindexed content, never anything sold behind a
paywall, never drafts. Each generator (thread lookup, feeds, llms.txt data,
Markdown alternates) does its own checks server-side; the web app only
formats what comes back.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.ee_hooks import get_ee_paid_locked_activity_ids, is_ee_resource_locked
from src.db.courses.activities import Activity, ActivityLockType, ActivityTypeEnum
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.course_updates import CourseUpdate
from src.db.courses.courses import Course
from src.db.folders.folders import Folder
from src.db.organizations import Organization
from src.db.paths import Path, PathCourse
from src.db.resource_authors import ResourceAuthor, ResourceAuthorshipStatusEnum
from src.db.users import AnonymousUser, User
from src.services.seo.seo import _get_org_by_slug, _noindexed, org_seo_config, public_key
from src.services.seo.slugs import find_resource

FEED_LIMIT = 50
LLMS_LIMIT = 500
LLMS_FULL_ACTIVITY_LIMIT = 2000
TOP_COMMENTS = 10


# ----------------------------------------------------------------------------
# Org-level switches
# ----------------------------------------------------------------------------

def feature_indexable(seo: dict, feature: str) -> bool:
    """Whether a public section may be indexed / listed for crawlers."""
    if seo.get("indexing_disabled"):
        return False
    return (seo.get("indexable_features") or {}).get(feature, True) is not False


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _org(db_session: AsyncSession, org_slug: str) -> tuple[Organization, dict]:
    org = await _get_org_by_slug(db_session, org_slug)
    return org, await org_seo_config(db_session, org.id)


async def _anon_locked(request: Request, db_session: AsyncSession, resource_uuid: str) -> bool:
    """Whether the resource's content is sold (an anonymous visitor gets a 402)."""
    try:
        return await is_ee_resource_locked(request, db_session, AnonymousUser(), resource_uuid)
    except HTTPException:
        return True


def _author_name(user: Optional[User]) -> str:
    if not user:
        return ""
    return " ".join(p for p in [user.first_name, user.last_name] if p).strip() or user.username or ""


async def _users(db_session: AsyncSession, ids: set[int]) -> dict[int, User]:
    if not ids:
        return {}
    rows = (await db_session.execute(select(User).where(User.id.in_(list(ids))))).scalars().all()
    return {u.id: u for u in rows}


# ----------------------------------------------------------------------------
# Readable content (feeds, llms.txt, Markdown)
# ----------------------------------------------------------------------------

def _course_entry(course: Course, authors: list[str]) -> dict:
    seo = course.seo or {}
    return {
        "type": "course",
        "key": public_key("course", course),
        "uuid": course.course_uuid,
        "name": course.name,
        "description": seo.get("description") or course.description or "",
        "about": course.about or "",
        "learnings": course.learnings or "",
        "tags": course.tags or "",
        "image": course.thumbnail_image or "",
        "language": course.default_locale or "",
        "authors": authors,
        "published": course.creation_date or "",
        "updated": course.update_date or course.creation_date or "",
    }


async def _listed_courses(db_session: AsyncSession, org_id: int, seo: dict, limit: int) -> list[Course]:
    if not feature_indexable(seo, "courses"):
        return []
    rows = (
        await db_session.execute(
            select(Course)
            .where(Course.org_id == org_id, Course.public == True, Course.published == True)  # noqa: E712
            .order_by(Course.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [c for c in rows if not _noindexed(c.seo)]


async def _course_authors(db_session: AsyncSession, uuids: list[str]) -> dict[str, list[str]]:
    if not uuids:
        return {}
    rows = (
        await db_session.execute(
            select(ResourceAuthor.resource_uuid, User)
            .join(User, User.id == ResourceAuthor.user_id)
            .where(
                ResourceAuthor.resource_uuid.in_(uuids),
                ResourceAuthor.authorship_status == ResourceAuthorshipStatusEnum.ACTIVE,
            )
            .order_by(ResourceAuthor.id)
        )
    ).all()
    out: dict[str, list[str]] = {}
    for resource_uuid, user in rows:
        name = _author_name(user)
        if name and name not in out.setdefault(resource_uuid, []):
            out[resource_uuid].append(name)
    return out


async def _public_activities(request, db_session, course: Course, limit: int = LLMS_FULL_ACTIVITY_LIMIT) -> list[dict]:
    """Ordered outline (chapters → lessons) of the lessons anonymous visitors can open, and only those."""
    rows = (
        await db_session.execute(
            select(Chapter, Activity, CourseChapter.order, ChapterActivity.order)
            .join(CourseChapter, CourseChapter.chapter_id == Chapter.id)
            .join(ChapterActivity, ChapterActivity.chapter_id == Chapter.id)
            .join(Activity, Activity.id == ChapterActivity.activity_id)
            .where(CourseChapter.course_id == course.id, Activity.course_id == course.id)
            .order_by(CourseChapter.order, ChapterActivity.order)
            .limit(limit)
        )
    ).all()
    locked = await get_ee_paid_locked_activity_ids(request, course.id, AnonymousUser(), db_session)
    course_locked = locked is None and await _anon_locked(request, db_session, course.course_uuid)
    out: list[dict] = []
    for chapter, activity, _, _ in rows:
        public = (
            activity.published
            and activity.lock_type == ActivityLockType.PUBLIC
            and not course_locked
            and not (locked and activity.id in locked)
        )
        if not public:
            # Drafts, members-only, group-restricted and sold lessons are left
            # out entirely: not even their names reach public outputs.
            continue
        out.append(
            {
                "chapter": chapter.name,
                "chapter_uuid": chapter.chapter_uuid,
                "key": activity.activity_uuid.replace("activity_", "", 1),
                "name": activity.name,
                "type": activity.activity_type.value if hasattr(activity.activity_type, "value") else str(activity.activity_type),
                "public": bool(public),
                "updated": activity.update_date or activity.creation_date or "",
            }
        )
    return out


async def _content_size(org_uuid: str, directory: str, filename: str) -> int:
    """Byte size of a stored file (podcast enclosures need it), 0 when unknown."""
    from src.services.utils.upload_content import _safe_content_path
    from config.config import get_learnhouse_config

    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return 0
    key = f"{org_uuid}/{directory}/{filename}"
    if key in _size_cache:
        return _size_cache[key]
    size = 0
    try:
        cfg = get_learnhouse_config().hosting_config.content_delivery
        if cfg.type == "s3api":
            import boto3

            s3 = boto3.client("s3", endpoint_url=cfg.s3api.endpoint_url)
            head = await asyncio.to_thread(
                s3.head_object, Bucket=cfg.s3api.bucket_name or "learnhouse-media", Key=f"content/orgs/{key}"
            )
            size = int(head.get("ContentLength") or 0)
        else:
            path = _safe_content_path("orgs", org_uuid, directory, filename)
            size = os.path.getsize(path) if os.path.exists(path) else 0
    except Exception:
        size = 0
    if size:
        _size_cache[key] = size
    return size


_size_cache: dict[str, int] = {}


def _org_info(org: Organization, seo: dict) -> dict:
    return {
        "slug": org.slug,
        "org_uuid": org.org_uuid,
        "name": org.name,
        "description": getattr(org, "description", "") or "",
        "about": getattr(org, "about", "") or "",
        "logo": getattr(org, "logo_image", "") or "",
        "thumbnail": getattr(org, "thumbnail_image", "") or "",
        "indexing_disabled": bool(seo.get("indexing_disabled")),
        "feeds_enabled": seo.get("feeds_enabled", True) is not False,
        "llms_txt_enabled": seo.get("llms_txt_enabled", True) is not False,
        "ai_crawler_policy": seo.get("ai_crawler_policy") or "allow",
    }


FEED_KINDS = ("org", "courses", "course_updates")


async def feed_data(
    request: Request,
    db_session: AsyncSession,
    org_slug: str,
    kind: str,
    key: Optional[str] = None,
    sort: str = "new",
    label: Optional[str] = None,
) -> dict:
    """Items of one public feed, newest first (or top, for threads).

    404 when the feed would expose nothing public: unknown kind/key, private,
    sold or noindexed resource, or the org turned feeds (or indexing) off.
    """
    if kind not in FEED_KINDS:
        raise HTTPException(status_code=404, detail="Not found")
    org, seo = await _org(db_session, org_slug)
    info = _org_info(org, seo)
    if not info["feeds_enabled"]:
        raise HTTPException(status_code=404, detail="Feeds are turned off")
    sort = "top" if sort == "top" else "new"
    result: dict[str, Any] = {"org": info, "kind": kind, "sort": sort, "items": []}

    if kind in ("org", "courses"):
        if info["indexing_disabled"]:
            raise HTTPException(status_code=404, detail="Not found")
        courses = await _listed_courses(db_session, org.id, seo, FEED_LIMIT)
        authors = await _course_authors(db_session, [c.course_uuid for c in courses])
        items = [_course_entry(c, authors.get(c.course_uuid, [])) for c in courses]
        if kind == "org":
            # New learning paths and podcasts belong in "what's new" too.
            if feature_indexable(seo, "paths"):
                paths = (
                    await db_session.execute(
                        select(Path)
                        .where(Path.org_id == org.id, Path.public == True, Path.published == True)  # noqa: E712
                        .order_by(Path.id.desc())
                        .limit(FEED_LIMIT)
                    )
                ).scalars().all()
                items += [
                    {
                        "type": "path", "key": public_key("path", p), "uuid": p.path_uuid, "name": p.name,
                        "description": (p.seo or {}).get("description") or p.description or "", "image": p.thumbnail_image or "",
                        "published": p.creation_date or "", "updated": p.update_date or p.creation_date or "",
                    }
                    for p in paths if not _noindexed(p.seo)
                ]
            items.sort(key=lambda i: _parse_date(i["published"]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        result["items"] = items[:FEED_LIMIT]
        return result

    # course_updates
    course, _ = await find_resource(db_session, "course", org.id, key or "")
    if course is None or not (course.public and course.published) or _noindexed(course.seo):
        raise HTTPException(status_code=404, detail="Not found")
    updates = (
        await db_session.execute(
            select(CourseUpdate).where(CourseUpdate.course_id == course.id).order_by(CourseUpdate.id.desc()).limit(FEED_LIMIT)
        )
    ).scalars().all()
    result["resource"] = {"key": public_key("course", course), "uuid": course.course_uuid, "name": course.name, "description": course.description or "", "image": course.thumbnail_image or ""}
    result["items"] = [
        {
            "uuid": u.courseupdate_uuid, "title": u.title, "content": u.content or "",
            "published": u.creation_date or "", "updated": u.update_date or u.creation_date or "",
        }
        for u in updates
    ]
    return result


async def llms_data(request: Request, db_session: AsyncSession, org_slug: str, full: bool = False) -> dict:
    """The org and its public, indexable content, for llms.txt / llms-full.txt.

    ``full`` adds course outlines (chapters and lessons anyone can open),
    episode lists and recent answered threads. Respects the AI crawler
    policy: ``block_all`` and disabled indexing return 404 (no llms.txt).
    """
    org, seo = await _org(db_session, org_slug)
    info = _org_info(org, seo)
    if info["indexing_disabled"] or not info["llms_txt_enabled"] or info["ai_crawler_policy"] == "block_all":
        raise HTTPException(status_code=404, detail="Not found")
    courses = await _listed_courses(db_session, org.id, seo, LLMS_LIMIT)
    authors = await _course_authors(db_session, [c.course_uuid for c in courses])
    course_items = []
    for c in courses:
        entry = _course_entry(c, authors.get(c.course_uuid, []))
        if full:
            entry["outline"] = await _public_activities(request, db_session, c) if feature_indexable(seo, "activities") else []
        course_items.append(entry)

    paths = []
    if feature_indexable(seo, "paths"):
        rows = (
            await db_session.execute(
                select(Path).where(Path.org_id == org.id, Path.public == True, Path.published == True).order_by(Path.id).limit(LLMS_LIMIT)  # noqa: E712
            )
        ).scalars().all()
        listed_course_ids = {c.id: c for c in courses}
        for p in rows:
            if _noindexed(p.seo):
                continue
            entry = {"key": public_key("path", p), "name": p.name, "description": (p.seo or {}).get("description") or p.description or "", "level": p.level or ""}
            if full:
                pcs = (await db_session.execute(select(PathCourse).where(PathCourse.path_id == p.id).order_by(PathCourse.position))).scalars().all()
                entry["courses"] = [
                    {"key": public_key("course", listed_course_ids[pc.course_id]), "name": listed_course_ids[pc.course_id].name, "required": pc.required}
                    for pc in pcs if pc.course_id in listed_course_ids
                ]
            paths.append(entry)

    podcasts: list = []

    folders = []
    if feature_indexable(seo, "library"):
        rows = (
            await db_session.execute(select(Folder).where(Folder.org_id == org.id, Folder.public == True).order_by(Folder.id).limit(LLMS_LIMIT))  # noqa: E712
        ).scalars().all()
        folders = [
            {"key": public_key("folder", f), "name": f.name, "description": (f.seo or {}).get("description") or f.description or ""}
            for f in rows if not _noindexed(f.seo)
        ]

    return {
        "org": info,
        "seo": {"default_meta_description": seo.get("default_meta_description") or ""},
        "courses": course_items,
        "paths": paths,
        "podcasts": podcasts,
        "folders": folders,
    }


MARKDOWN_TYPES = ("course", "activity", "path")


async def markdown_data(
    request: Request,
    db_session: AsyncSession,
    org_slug: str,
    resource_type: str,
    key: str,
    sub: Optional[str] = None,
) -> dict:
    """One public page as structured data for its Markdown alternate.

    Only pages that are indexable for an anonymous visitor: anything else is a
    404, so a Markdown URL never reveals more than the HTML page would.
    """
    if resource_type not in MARKDOWN_TYPES:
        raise HTTPException(status_code=404, detail="Not found")
    org, seo = await _org(db_session, org_slug)
    info = _org_info(org, seo)
    if info["indexing_disabled"] or not info["llms_txt_enabled"] or info["ai_crawler_policy"] == "block_all":
        raise HTTPException(status_code=404, detail="Not found")
    base: dict[str, Any] = {"org": info, "type": resource_type}

    if resource_type in ("course", "activity"):
        course, _ = await find_resource(db_session, "course", org.id, key)
        if course is None or not (course.public and course.published) or _noindexed(course.seo) or not feature_indexable(seo, "courses"):
            raise HTTPException(status_code=404, detail="Not found")
        authors = (await _course_authors(db_session, [course.course_uuid])).get(course.course_uuid, [])
        outline = await _public_activities(request, db_session, course) if feature_indexable(seo, "activities") else []
        base["course"] = {**_course_entry(course, authors), "outline": outline}
        if resource_type == "course":
            return base
        target = (sub or "").replace("activity_", "", 1)
        entry = next((a for a in outline if a["key"] == target), None)
        if entry is None or not entry["public"]:
            raise HTTPException(status_code=404, detail="Not found")
        activity = (
            await db_session.execute(select(Activity).where(Activity.activity_uuid == f"activity_{target}", Activity.course_id == course.id))
        ).scalars().first()
        content: Any = None
        if activity is not None and activity.activity_type == ActivityTypeEnum.TYPE_DYNAMIC:
            content = activity.content
        base["activity"] = {**entry, "content": content, "details": None}
        return base

    if resource_type == "path":
        path, _ = await find_resource(db_session, "path", org.id, key)
        if path is None or not (path.public and path.published) or _noindexed(path.seo) or not feature_indexable(seo, "paths"):
            raise HTTPException(status_code=404, detail="Not found")
        courses = (
            await db_session.execute(
                select(Course, PathCourse.required)
                .join(PathCourse, PathCourse.course_id == Course.id)
                .where(PathCourse.path_id == path.id)
                .order_by(PathCourse.position)
            )
        ).all()
        base["path"] = {
            "key": public_key("path", path), "name": path.name, "description": path.description or "", "about": path.about or "",
            "level": path.level or "",
            "courses": [
                {"key": public_key("course", c), "name": c.name, "description": c.description or "", "required": required}
                for c, required in courses if c.public and c.published and not _noindexed(c.seo)
            ],
        }
        return base

    raise HTTPException(status_code=404, detail="Not found")

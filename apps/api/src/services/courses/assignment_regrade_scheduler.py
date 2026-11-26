"""Every 10 minutes, retries auto-grades that were deferred because an external
checker (STACK for MATH_STEPS, Judge0 for CODE) was down at submit time.

Same shape as the other in-app schedulers: a Redis lock per slot keeps several
API workers from each running it. Running twice is harmless anyway: a row that
finalizes leaves the SUBMITTED set, and one that stays deferred is just retried.

shortcut: the batch is the oldest BATCH_SIZE candidates by id, so more than
BATCH_SIZE rows stuck behind a long outage can starve newer ones; upgrade by
tracking a last-attempt timestamp if that ever happens.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import exists
from sqlmodel import select

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 10 * 60
STARTUP_DELAY_SECONDS = 120
BATCH_SIZE = 50
_LOCK_TTL_SECONDS = 9 * 60

_task: Optional[asyncio.Task] = None


def _slot(now: datetime) -> str:
    return f"learnhouse:assignments:regrade:{now.strftime('%Y-%m-%dT%H')}:{now.minute // 10}"


async def _claim(key: str, ttl: int) -> bool:
    try:
        from src.core.redis import get_redis_client

        client = get_redis_client()
        if client is None:
            return True
        return bool(await asyncio.to_thread(client.set, key, "1", nx=True, ex=ttl))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Assignment regrade lock unavailable, running anyway: %s", exc)
        return True


async def run_once(db) -> int:
    """Retry up to BATCH_SIZE deferred submissions, oldest first. Returns how
    many finalized. Never raises."""
    from src.db.courses.assignments import (
        Assignment,
        AssignmentTask,
        AssignmentTaskSubmission,
        AssignmentTaskTypeEnum,
        AssignmentUserSubmission,
        AssignmentUserSubmissionStatus,
    )
    from src.db.courses.courses import Course
    from src.services.courses.activities.assignments import (
        AUTO_GRADABLE_TASK_TYPES,
        _apply_grade_and_finalize,
    )
    from src.services.courses.certifications import (
        check_course_completion_and_create_certificate,
    )

    checked_types = (AssignmentTaskTypeEnum.CODE, AssignmentTaskTypeEnum.MATH_STEPS)
    done = 0
    try:
        ids = (await db.execute(
            select(AssignmentUserSubmission.id)
            .join(Assignment, Assignment.id == AssignmentUserSubmission.assignment_id)
            .where(
                AssignmentUserSubmission.submission_status == AssignmentUserSubmissionStatus.SUBMITTED,
                Assignment.auto_grading == True,  # noqa: E712
                Assignment.ungraded == False,  # noqa: E712
                exists().where(
                    AssignmentTask.assignment_id == Assignment.id,
                    AssignmentTask.assignment_type.in_(checked_types),
                ),
            )
            .order_by(AssignmentUserSubmission.id)
            .limit(BATCH_SIZE)
        )).scalars().all()
    except Exception:
        logger.exception("Assignment regrade: could not list deferred submissions")
        return 0

    for submission_id in ids:
        try:
            submission = await db.get(AssignmentUserSubmission, submission_id)
            assignment = await db.get(Assignment, submission.assignment_id)
            course = await db.get(Course, assignment.course_id)
            tasks = (await db.execute(
                select(AssignmentTask).where(AssignmentTask.assignment_id == assignment.id)
            )).scalars().all()
            checked = [
                t for t in tasks
                if t.assignment_type == AssignmentTaskTypeEnum.CODE
                or (
                    t.assignment_type == AssignmentTaskTypeEnum.MATH_STEPS
                    and isinstance(t.contents, dict)
                    and t.contents.get("checker_version") is not None
                )
            ]
            # Mirror the submit path's all-auto-gradable gate, and leave alone
            # rows whose checked tasks a teacher already graded by hand.
            if not checked or submission.user_id is None or not all(
                t.assignment_type in AUTO_GRADABLE_TASK_TYPES for t in tasks
            ):
                continue
            pending = (await db.execute(
                select(AssignmentTaskSubmission.id).where(
                    AssignmentTaskSubmission.user_id == submission.user_id,
                    AssignmentTaskSubmission.assignment_task_id.in_([t.id for t in checked]),
                    AssignmentTaskSubmission.manually_graded == False,  # noqa: E712
                )
            )).first()
            if pending is None:
                continue
            result = await _apply_grade_and_finalize(
                assignment=assignment,
                course=course,
                user_id=int(submission.user_id),
                assignment_user_submission=submission,
                db_session=db,
                overall_feedback=None,
                auto_graded=True,
                assignment_tasks=tasks,
                # The learner left the submit screen long ago: tell them.
                notify_learner=True,
            )
            if result.get("finalized") is False:
                continue
            done += 1
            # Same follow-up as submit: the grade may complete the course and
            # earn the certificate. No request exists here; the certificate
            # path does not use it without a current_user.
            try:
                await check_course_completion_and_create_certificate(
                    None, int(submission.user_id), course.id, db  # type: ignore[arg-type]
                )
            except Exception:
                logger.exception("Assignment regrade: certificate check failed for submission %s", submission_id)
        except Exception:
            await db.rollback()
            logger.exception("Assignment regrade failed for submission %s", submission_id)
    return done


async def tick(now: Optional[datetime] = None) -> None:
    """One scheduled run. Never raises."""
    try:
        now = now or datetime.now(timezone.utc)
        if not await _claim(_slot(now), _LOCK_TTL_SECONDS):
            return
        from src.core.events.database import _async_session_factory

        async with _async_session_factory() as db:
            done = await run_once(db)
        if done:
            logger.info("Assignment regrade: finalized %s deferred submission(s)", done)
    except asyncio.CancelledError:  # pragma: no cover
        raise
    except Exception as exc:
        logger.exception("Assignment regrade failed, will retry: %s", exc)


async def _loop() -> None:  # pragma: no cover - timing loop
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        await tick()
        await asyncio.sleep(INTERVAL_SECONDS)


def start_scheduler() -> None:
    """Never raises. Idle under tests and with LEARNHOUSE_ASSIGNMENT_REGRADE_DISABLED."""
    global _task
    try:
        if os.environ.get("TESTING") == "true" or os.environ.get("LEARNHOUSE_ASSIGNMENT_REGRADE_DISABLED"):
            return
        _task = asyncio.create_task(_loop())  # pragma: no cover
    except Exception as exc:  # pragma: no cover
        logger.warning("Assignment regrade not started: %s", exc)


async def stop_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()  # pragma: no cover
    try:  # pragma: no cover
        await _task
    except (asyncio.CancelledError, Exception):  # pragma: no cover
        pass
    finally:  # pragma: no cover
        _task = None

"""The sweep re-grades auto-grades deferred while STACK was down."""

from unittest.mock import AsyncMock, patch

from sqlmodel import select

from src.db.courses.assignments import (
    AssignmentTaskSubmission,
    AssignmentUserSubmissionStatus,
)
from src.services.courses import assignment_regrade_scheduler as sched
from src.services.courses.activities.math_steps.stack_v1 import StackCheck, StackTechnicalError
from src.tests.services.math_steps.test_math_steps_grading import _create_stack_submission

_SVC = "src.services.courses.activities.assignments"


def _grader(**kw):
    return patch(f"{_SVC}.grade_stack_steps", new_callable=AsyncMock, **kw)


async def _sweep(db):
    with patch(f"{_SVC}.dispatch_webhooks", new_callable=AsyncMock) as hook, patch(
        "src.services.notifications.events.notify_assignment_graded", new_callable=AsyncMock
    ) as notify, patch(
        "src.services.courses.certifications.check_course_completion_and_create_certificate",
        new_callable=AsyncMock,
    ) as cert:
        done = await sched.run_once(db)
    return done, hook, notify, cert


async def test_deferred_submission_is_finalized_with_side_effects(
    db, org, course, chapter, activity, regular_user
):
    _, sub = await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    with _grader(return_value=StackCheck("valid", terminal=True)):
        done, hook, notify, cert = await _sweep(db)
    assert done == 1
    await db.refresh(sub)
    assert sub.submission_status == AssignmentUserSubmissionStatus.GRADED
    assert sub.grade == 100
    hook.assert_awaited()
    notify.assert_awaited_once()
    cert.assert_awaited_once()
    assert cert.await_args.args[1:3] == (regular_user.id, course.id)


async def test_stays_submitted_while_stack_is_down(db, org, course, chapter, activity, regular_user):
    _, sub = await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    with _grader(side_effect=StackTechnicalError("down")):
        done, hook, notify, cert = await _sweep(db)
    assert done == 0
    await db.refresh(sub)
    assert sub.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
    hook.assert_not_called()
    cert.assert_not_called()


async def test_graded_rows_are_not_touched(db, org, course, chapter, activity, regular_user):
    _, sub = await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    sub.submission_status = AssignmentUserSubmissionStatus.GRADED
    db.add(sub)
    await db.commit()
    with _grader(return_value=StackCheck("valid", terminal=True)) as grade:
        done, *_ = await _sweep(db)
    assert done == 0
    grade.assert_not_called()


async def test_non_auto_graded_assignment_is_not_touched(db, org, course, chapter, activity, regular_user):
    assignment, sub = await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    assignment.auto_grading = False
    db.add(assignment)
    await db.commit()
    with _grader(return_value=StackCheck("valid", terminal=True)) as grade:
        done, *_ = await _sweep(db)
    assert done == 0
    grade.assert_not_called()
    await db.refresh(sub)
    assert sub.submission_status == AssignmentUserSubmissionStatus.SUBMITTED


async def test_manually_graded_task_is_not_regraded(db, org, course, chapter, activity, regular_user):
    await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    ts = (await db.execute(select(AssignmentTaskSubmission))).scalars().first()
    ts.manually_graded = True
    db.add(ts)
    await db.commit()
    with _grader(return_value=StackCheck("valid", terminal=True)) as grade:
        done, *_ = await _sweep(db)
    assert done == 0
    grade.assert_not_called()

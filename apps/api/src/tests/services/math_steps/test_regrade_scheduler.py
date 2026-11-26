"""The sweep re-grades auto-grades deferred while STACK was down."""

from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlmodel import select

from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
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


async def test_skippable_rows_do_not_starve_a_real_deferred_row(
    db, org, course, chapter, activity, regular_user, monkeypatch
):
    monkeypatch.setattr(sched, "BATCH_SIZE", 2)
    now = str(datetime.now())
    # More skippable rows than the batch, all older than the real one: v1-only
    # MATH_STEPS (no checker), and one with a non-auto-gradable task.
    for i in range(4):
        assignment = Assignment(
            title=f"skip{i}", description="", published=True, grading_type=GradingTypeEnum.NUMERIC,
            auto_grading=True, org_id=org.id, course_id=course.id, chapter_id=chapter.id,
            activity_id=activity.id, assignment_uuid=f"assignment_skip{i}",
            creation_date=now, update_date=now,
        )
        db.add(assignment)
        await db.commit()
        await db.refresh(assignment)
        kinds = [(AssignmentTaskTypeEnum.MATH_STEPS, {"equation": "2x=4"})]
        if i % 2:
            kinds.append((AssignmentTaskTypeEnum.FILE_SUBMISSION, {}))
        for j, (kind, contents) in enumerate(kinds):
            db.add(AssignmentTask(
                title="t", description="", hint="", assignment_type=kind, contents=contents,
                max_grade_value=100, assignment_id=assignment.id, org_id=org.id,
                course_id=course.id, chapter_id=chapter.id, activity_id=activity.id,
                assignment_task_uuid=f"assignmenttask_skip{i}_{j}", creation_date=now, update_date=now,
            ))
        db.add(AssignmentUserSubmission(
            user_id=regular_user.id, assignment_id=assignment.id, grade=0,
            submission_status=AssignmentUserSubmissionStatus.SUBMITTED, attempt_number=1,
            assignmentusersubmission_uuid=f"aus_skip{i}", creation_date=now, update_date=now,
        ))
        await db.commit()
    _, sub = await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    with _grader(return_value=StackCheck("valid", terminal=True)):
        done, *_ = await _sweep(db)
    assert done == 1
    await db.refresh(sub)
    assert sub.submission_status == AssignmentUserSubmissionStatus.GRADED

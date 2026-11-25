"""MATH_STEPS: learner-safe live check, exercise validation, and the
all-or-nothing server-side grade."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.services.courses.activities.assignments import (
    AUTO_GRADABLE_TASK_TYPES,
    SERVER_VERIFIED_TASK_TYPES,
    _apply_grade_and_finalize,
    _server_verified_task_grade,
)
from src.services.courses.activities.math_steps import (
    MAX_STEPS,
    is_valid_exercise,
    public_check,
    steps_completed,
)
from src.services.ai.tools.assignments import MathStepsContent, _storage_contents
from src.services.courses.activities.math_steps.stack_v1 import StackCheck, StackTechnicalError


def _task(equation):
    return SimpleNamespace(
        assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
        contents={"equation": equation},
        max_grade_value=100,
    )


class TestPublicCheck:
    def test_valid_step_never_exposes_solution_sets(self):
        out = public_check("2(x+3)=10", "2x+6=10")
        assert out["status"] == "valid"
        assert out["terminal"] is False
        assert set(out) == {"status", "feedback", "reason", "example", "terminal"}

    def test_final_answer_is_terminal(self):
        assert public_check("2x=4", "x=2")["terminal"] is True

    def test_invalid_and_unsupported(self):
        assert public_check("2(x+3)=10", "2x+3=10")["status"] == "invalid"
        out = public_check("2(x+3)=10", "x^2=4")
        assert out["status"] == "unsupported" and out["reason"] == "unsupported_notation"
        assert out["terminal"] is False


class TestExerciseValidation:
    def test_accepts_solvable_equation(self):
        assert is_valid_exercise("2(x+3)=10")

    def test_rejects_answers_and_unsupported(self):
        for eq in ("x=2", "0=0", "x*x=4", "2x+1"):
            assert not is_valid_exercise(eq), eq

    def test_ai_tool_spec_validates_equation(self):
        with pytest.raises(ValueError):
            MathStepsContent(type="math_steps", equation="x=2")
        content = MathStepsContent(type="math_steps", equation="3x-5=7", explanation="x=4")
        assert _storage_contents(content, {}) == {"equation": "3x-5=7", "explanation": "x=4"}


class TestStepsCompleted:
    def test_full_valid_path(self):
        assert steps_completed("2(x+3)=10", ["2x+6=10", "2x=4", "x=2"])

    def test_alternative_route_and_reversed_answer(self):
        assert steps_completed("2(x+3)=10", ["x+3=5", "2=x"])

    def test_one_invalid_step_fails_even_if_answer_right(self):
        assert not steps_completed("2(x+3)=10", ["2x+3=10", "x=2"])

    def test_unfinished_path_fails(self):
        assert not steps_completed("2(x+3)=10", ["2x+6=10", "2x=4"])

    def test_special_cases(self):
        assert steps_completed("2x+3=2x+7", ["0=4"])
        assert steps_completed("3x+6=3(x+2)", ["3x+6=3x+6", "0=0"])
        assert not steps_completed("2x+3=2x+7", ["0=0"])

    def test_malformed_submissions(self):
        for steps in (None, [], "x=2", [1, 2], ["x=2"] * (MAX_STEPS + 1)):
            assert not steps_completed("2x=4", steps), steps
        assert not steps_completed(None, ["x=2"])


class TestServerVerifiedGrade:
    def test_registered_as_auto_graded_and_server_verified(self):
        assert AssignmentTaskTypeEnum.MATH_STEPS in AUTO_GRADABLE_TASK_TYPES
        assert AssignmentTaskTypeEnum.MATH_STEPS in SERVER_VERIFIED_TASK_TYPES

    @pytest.mark.asyncio
    async def test_completed_full_marks(self):
        ts = SimpleNamespace(task_submission={"steps": ["3x=12", "x=4"]}, grade=0)
        assert await _server_verified_task_grade(_task("3x-5=7"), ts) == 100

    @pytest.mark.asyncio
    async def test_client_claimed_grade_is_ignored(self):
        ts = SimpleNamespace(task_submission={"steps": ["x=4"], "completed": True}, grade=100)
        assert await _server_verified_task_grade(_task("3x-5=8"), ts) == 0

    @pytest.mark.asyncio
    async def test_no_submission_zero(self):
        assert await _server_verified_task_grade(_task("3x-5=7"), None) == 0

    @pytest.mark.asyncio
    async def test_stack_requires_both_a_valid_chain_and_final_root_set(self):
        task = SimpleNamespace(
            assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
            contents={
                "equation": "x^2-5*x+6=0",
                "checker_version": "stack_v1",
                "stack_coefficients": [1, -5, 6],
            },
            max_grade_value=100,
        )
        submission = SimpleNamespace(task_submission={"steps": ["x=2 or x=3"]})
        with patch(
            "src.services.courses.activities.assignments.grade_stack_steps",
            new_callable=AsyncMock,
            return_value=StackCheck("valid", terminal=True),
        ):
            assert await _server_verified_task_grade(task, submission) == 100
        with patch(
            "src.services.courses.activities.assignments.grade_stack_steps",
            new_callable=AsyncMock,
            return_value=StackCheck("valid", terminal=False),
        ):
            assert await _server_verified_task_grade(task, submission) == 0


async def _create_stack_submission(db, org, course, chapter, activity, learner):
    assignment = Assignment(
        title="STACK pilot",
        description="",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_stack_pilot",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)

    task = AssignmentTask(
        title="Quadratic",
        description="",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
        contents={
            "equation": "x^2-5*x+6=0",
            "checker_version": "stack_v1",
            "stack_coefficients": [1, -5, 6],
        },
        max_grade_value=100,
        assignment_id=assignment.id,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_task_uuid="assignmenttask_stack_pilot",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    submission = AssignmentUserSubmission(
        user_id=learner.id,
        assignment_id=assignment.id,
        grade=0,
        submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
        attempt_number=1,
        assignmentusersubmission_uuid="aus_stack_pilot",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    task_submission = AssignmentTaskSubmission(
        assignment_task_submission_uuid="ats_stack_pilot",
        task_submission={"steps": ["(x-2)*(x-3)=0", "x=2 or x=3"]},
        grade=0,
        manually_graded=False,
        task_submission_grade_feedback="",
        assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
        user_id=learner.id,
        activity_id=activity.id,
        course_id=course.id,
        chapter_id=chapter.id,
        assignment_task_id=task.id,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(submission)
    db.add(task_submission)
    await db.commit()
    await db.refresh(submission)
    return assignment, submission


class TestStackOutageDefersAutoGrade:
    async def test_stack_outage_leaves_auto_grade_pending(
        self, db, org, course, chapter, activity, regular_user
    ):
        assignment, submission = await _create_stack_submission(
            db, org, course, chapter, activity, regular_user
        )
        with patch(
            "src.services.courses.activities.assignments.grade_stack_steps",
            new_callable=AsyncMock,
            side_effect=StackTechnicalError("private upstream error"),
        ), patch(
            "src.services.courses.activities.assignments.dispatch_webhooks",
            new_callable=AsyncMock,
        ) as dispatch:
            result = await _apply_grade_and_finalize(
                assignment=assignment,
                course=course,
                user_id=regular_user.id,
                assignment_user_submission=submission,
                db_session=db,
                auto_graded=True,
            )

        assert result == {"finalized": False, "reason": "math_steps_unverifiable"}
        assert submission.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
        assert submission.grade == 0
        dispatch.assert_not_called()

    async def test_stack_outage_blocks_manual_finalize(
        self, db, org, course, chapter, activity, regular_user
    ):
        assignment, submission = await _create_stack_submission(
            db, org, course, chapter, activity, regular_user
        )
        with patch(
            "src.services.courses.activities.assignments.grade_stack_steps",
            new_callable=AsyncMock,
            side_effect=StackTechnicalError("private upstream error"),
        ), patch(
            "src.services.courses.activities.assignments.dispatch_webhooks",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await _apply_grade_and_finalize(
                    assignment=assignment,
                    course=course,
                    user_id=regular_user.id,
                    assignment_user_submission=submission,
                    db_session=db,
                    auto_graded=False,
                )

        assert exc.value.status_code == 503
        assert submission.submission_status != AssignmentUserSubmissionStatus.GRADED

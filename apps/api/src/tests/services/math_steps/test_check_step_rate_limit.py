"""Live STACK checks are rate-limited per user; local v1 checks are not."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.courses.assignments import AssignmentTask, AssignmentTaskTypeEnum
from src.services.courses.activities import assignments as svc
from src.services.courses.activities.math_steps.stack_v1 import StackCheck
from src.tests.services.math_steps.test_math_steps_grading import _create_stack_submission

_SVC = "src.services.courses.activities.assignments"
_TASK = "assignmenttask_stack_pilot"


def _fake_limiter():
    """In-memory stand-in for check_rate_limit: (allowed, count, retry_after)."""
    counts: dict[str, int] = {}

    def fake(key, max_attempts, window_seconds, r=None):
        counts[key] = counts.get(key, 0) + 1
        return counts[key] <= max_attempts, counts[key], 42

    return fake


async def _check(db, user, task_uuid=_TASK):
    return await svc.check_math_step(None, task_uuid, "x^2-5*x+6=0", "(x-2)*(x-3)=0", user, db)


@pytest.fixture
def stack_patches():
    read = AsyncMock(return_value=SimpleNamespace(assignment_type=AssignmentTaskTypeEnum.MATH_STEPS))
    with patch(f"{_SVC}.read_assignment_task", read), patch(
        f"{_SVC}.check_stack_v1_step", new_callable=AsyncMock, return_value=StackCheck("valid")
    ) as stack, patch(f"{_SVC}.check_rate_limit", side_effect=_fake_limiter()) as limiter:
        yield stack, limiter


async def test_stack_checks_hit_429_with_retry_after(
    db, org, course, chapter, activity, regular_user, stack_patches
):
    await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    stack, _ = stack_patches
    for _i in range(svc.STACK_CHECKS_PER_MINUTE):
        await _check(db, regular_user)
    with pytest.raises(HTTPException) as exc:
        await _check(db, regular_user)
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "42"}
    assert stack.await_count == svc.STACK_CHECKS_PER_MINUTE  # the 31st never reached STACK


async def test_limit_is_per_user(
    db, org, course, chapter, activity, regular_user, admin_user, stack_patches
):
    await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    _, limiter = stack_patches
    for _i in range(svc.STACK_CHECKS_PER_MINUTE):
        await _check(db, regular_user)
    await _check(db, admin_user)  # another learner is unaffected
    keys = {c.kwargs["key"] for c in limiter.call_args_list}
    assert keys == {f"math_step_check:{regular_user.id}", f"math_step_check:{admin_user.id}"}


async def test_v1_tasks_are_not_limited(
    db, org, course, chapter, activity, regular_user, stack_patches
):
    await _create_stack_submission(db, org, course, chapter, activity, regular_user)
    _, limiter = stack_patches
    task = (await db.execute(select(AssignmentTask))).scalars().first()
    task.contents = {"equation": "2(x+3)=10"}
    db.add(task)
    await db.commit()
    for _i in range(svc.STACK_CHECKS_PER_MINUTE + 5):
        result = await svc.check_math_step(
            None, _TASK, "2(x+3)=10", "2x+6=10", regular_user, db
        )
    assert result["status"] == "valid"
    limiter.assert_not_called()

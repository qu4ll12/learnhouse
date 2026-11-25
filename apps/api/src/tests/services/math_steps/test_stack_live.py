"""Live end-to-end smoke test of the STACK pilot through the real assignments
router and a real STACK + Maxima. Skipped unless STACK_LIVE_URL is set, e.g.

    STACK_LIVE_URL=http://127.0.0.1:3080/grade uv run pytest \
        src/tests/services/math_steps/test_stack_live.py -q -s

Only auth/RBAC, webhooks, analytics, search indexing and certificate side
effects are patched; the math_steps and grading paths run for real.
"""

import os
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.courses.assignments import Assignment, GradingTypeEnum
from src.routers.courses.assignments import router as assignments_router
from src.security.auth import get_current_user

pytestmark = pytest.mark.skipif(
    not os.environ.get("STACK_LIVE_URL"), reason="STACK_LIVE_URL not set"
)

_SVC = "src.services.courses.activities.assignments"
_EQ = "x^2-5*x+6=0"
_STACK = {
    "checker_version": "stack_v1",
    "equation": _EQ,
    "stack_coefficients": [1, -5, 6],
}
_GOOD_CHAIN = ["(x-2)*(x-3)=0", "x=2 or x=3"]
_MAX = 10


@pytest.fixture
def stack_url(monkeypatch):
    monkeypatch.setenv("STACK_API_URL", os.environ["STACK_LIVE_URL"])


@pytest.fixture
def who():
    """Mutable holder for the acting user and whether they are a teacher."""
    return {"user": None, "teacher": False}


@pytest.fixture
async def http(db, who, stack_url):
    app = FastAPI()
    app.include_router(assignments_router, prefix="/api/v1/assignments")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: who["user"]

    async def is_teacher(request, user_id, action, *args, **kwargs):
        # Enrolled learners may read; only the teacher may update.
        return who["teacher"] or action == "read"

    with ExitStack() as stack:
        for name in (
            "authorize_assignment_access",
            "check_resource_access",
            "verify_activity_reader_access",
            "track",
            "dispatch_webhooks",
            "record_audit_event",
            "check_course_completion_and_create_certificate",
        ):
            stack.enter_context(patch(f"{_SVC}.{name}", new_callable=AsyncMock))
        stack.enter_context(
            patch(f"{_SVC}.authorization_verify_based_on_roles", side_effect=is_teacher)
        )
        stack.enter_context(patch(f"{_SVC}.index_activity"))
        stack.enter_context(patch(f"{_SVC}.notify_ee_learner_progress"))
        stack.enter_context(
            patch(
                "src.services.feature_notifications.notify_submission_to_grade",
                new_callable=AsyncMock,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


async def _assignment(db, org, course, chapter, activity, uuid):
    assignment = Assignment(
        title=uuid,
        description="",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid=uuid,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()
    return uuid


def _body(contents):
    return {
        "title": "Task",
        "description": "",
        "hint": "",
        "assignment_type": "MATH_STEPS",
        "contents": contents,
        "max_grade_value": _MAX,
    }


class _Flow:
    def __init__(self, http, who, teacher, learner, assignment_uuid):
        self.http, self.who = http, who
        self.teacher, self.learner, self.a = teacher, learner, assignment_uuid

    def as_(self, role):
        self.who["teacher"] = role == "teacher"
        self.who["user"] = self.teacher if role == "teacher" else self.learner

    async def create(self, contents):
        self.as_("teacher")
        return await self.http.post(
            f"/api/v1/assignments/{self.a}/tasks", json=_body(contents)
        )

    async def check(self, task, previous, current):
        self.as_("learner")
        response = await self.http.post(
            f"/api/v1/assignments/{self.a}/tasks/{task}/check_step",
            json={"previous": previous, "current": current},
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def save(self, task, steps):
        self.as_("learner")
        # The web client never sends a grade.
        return await self.http.put(
            f"/api/v1/assignments/{self.a}/tasks/{task}/submissions",
            json={"task_submission": {"steps": steps}, "grade": 0},
        )

    async def submit(self):
        self.as_("learner")
        response = await self.http.post(f"/api/v1/assignments/{self.a}/submissions")
        assert response.status_code == 200, response.text
        return response.json()

    async def task_submission(self, task):
        self.as_("learner")
        response = await self.http.get(
            f"/api/v1/assignments/{self.a}/tasks/{task}/submissions/me"
        )
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def flow_factory(http, who, db, org, course, chapter, activity, admin_user, regular_user):
    async def make(uuid):
        await _assignment(db, org, course, chapter, activity, uuid)
        return _Flow(http, who, admin_user, regular_user, uuid)

    return make


def _chain(*steps):
    return "\n".join((_EQ, *steps))


class TestStackLive:
    async def test_stack_flow_end_to_end(self, flow_factory, http, who):
        flow = await flow_factory("assignment_live_good")

        # 1. Teacher creates; a complex-root quadratic is rejected specifically.
        bad = await flow.create(
            {**_STACK, "equation": "x^2+1=0", "stack_coefficients": [1, 0, 1]}
        )
        assert bad.status_code == 400 and "real roots" in bad.json()["detail"]
        created = await flow.create(_STACK)
        assert created.status_code == 200, created.text
        task = created.json()["assignment_task_uuid"]

        # 2. Learner payload keeps checker_version, hides the coefficients.
        flow.as_("learner")
        read = await http.get(f"/api/v1/assignments/task/{task}")
        assert read.status_code == 200, read.text
        contents = read.json()["contents"]
        assert contents["checker_version"] == "stack_v1"
        assert contents["equation"] == _EQ
        assert not any(key.startswith("stack_") for key in contents)

        # 3. check_step with the web client's contract (full chain as previous).
        result = await flow.check(task, _EQ, "(x-2)*(x-3)=0")
        assert (result["status"], result["terminal"]) == ("valid", False)
        previous = _chain("(x-2)*(x-3)=0")
        assert (await flow.check(task, previous, "x=2"))["status"] == "invalid"
        result = await flow.check(task, previous, "x=2 or x=3")
        assert (result["status"], result["terminal"]) == ("valid", True)
        assert (await flow.check(task, _EQ, "x^^2=)"))["status"] == "unsupported"

        # 4. Save progress, re-open, restore check.
        saved = await flow.save(task, _GOOD_CHAIN)
        assert saved.status_code == 200, saved.text
        assert (await flow.task_submission(task))["task_submission"] == {
            "steps": _GOOD_CHAIN
        }
        result = await flow.check(task, _chain(_GOOD_CHAIN[0]), _GOOD_CHAIN[-1])
        assert (result["status"], result["terminal"]) == ("valid", True)

        # 5. Submit: graded server-side at full marks despite the client's grade 0.
        submitted = await flow.submit()
        assert submitted["submission_status"] == "GRADED", submitted
        assert (await flow.task_submission(task))["grade"] == _MAX

    async def test_wrong_chain_scores_zero(self, flow_factory):
        flow = await flow_factory("assignment_live_wrong")
        task = (await flow.create(_STACK)).json()["assignment_task_uuid"]
        assert (await flow.save(task, ["(x-2)*(x-3)=0", "x=2"])).status_code == 200
        submitted = await flow.submit()
        assert submitted["submission_status"] == "GRADED", submitted
        assert (await flow.task_submission(task))["grade"] == 0

    async def test_stack_outage_leaves_submission_pending_for_regrade(
        self, flow_factory, monkeypatch
    ):
        flow = await flow_factory("assignment_live_outage")
        task = (await flow.create(_STACK)).json()["assignment_task_uuid"]
        assert (await flow.save(task, _GOOD_CHAIN)).status_code == 200

        monkeypatch.setenv("STACK_API_URL", "http://127.0.0.1:9/grade")
        result = await flow.check(task, _EQ, "(x-2)*(x-3)=0")
        assert result["status"] == "check_failed"
        submitted = await flow.submit()
        assert submitted["submission_status"] == "SUBMITTED", submitted

    async def test_linear_v1_still_works(self, flow_factory):
        flow = await flow_factory("assignment_live_linear")
        bad = await flow.create({"equation": "x=4"})
        assert bad.status_code == 400
        assert bad.json()["detail"].startswith("Invalid MATH_STEPS equation")

        created = await flow.create({"equation": "2(x+3)=10"})
        assert created.status_code == 200, created.text
        task = created.json()["assignment_task_uuid"]
        assert (await flow.check(task, "2(x+3)=10", "2x+6=10"))["status"] == "valid"
        assert (await flow.save(task, ["2x+6=10", "2x=4", "x=2"])).status_code == 200
        submitted = await flow.submit()
        assert submitted["submission_status"] == "GRADED", submitted
        assert (await flow.task_submission(task))["grade"] == _MAX

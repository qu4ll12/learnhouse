"""STACK pilot: bounded author config and learner-safe REST grading."""

from fractions import Fraction

import httpx
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

from src.db.courses.assignments import (
    Assignment,
    AssignmentTaskCreate,
    AssignmentTaskTypeEnum,
    AssignmentTaskUpdate,
)
from src.services.courses.activities.assignments import _strip_answer_key
from src.services.courses.activities.assignments import (
    create_assignment_task,
    read_assignment_task,
    read_assignment_tasks,
    update_assignment_task,
)
from src.services.courses.activities.math_steps import stack_v1


_CONTENTS = {
    "equation": "x^2 - 5*x + 6 = 0",
    "checker_version": "stack_v1",
    "stack_coefficients": [1, -5, 6],
}
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_stack_response(monkeypatch, payload=None, *, exception=None):
    real_client = _REAL_ASYNC_CLIENT
    seen = []

    def handler(request):
        seen.append((request.url.path, httpx.Request("POST", request.url, content=request.content)))
        if exception:
            raise exception
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        stack_v1.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setenv("STACK_API_URL", "http://stack.test/grade")
    return seen


@pytest.fixture(autouse=True)
def _stack_url(monkeypatch):
    monkeypatch.setenv("STACK_API_URL", "http://stack.test/grade")


class TestStackConfiguration:
    def test_integer_triple_builds_a_bounded_real_quadratic_and_exact_roots(self):
        coefficients = stack_v1.validate_stack_task_contents(_CONTENTS)

        assert coefficients == (1, -5, 6)
        assert stack_v1.quadratic_equation(coefficients) == "x^2-5*x+6=0"
        assert stack_v1.quadratic_roots(coefficients) == (Fraction(2), Fraction(3))

    @pytest.mark.parametrize(
        "contents",
        [
            {**_CONTENTS, "stack_coefficients": [1, -5, 7]},
            {**_CONTENTS, "stack_coefficients": [1, -4, 1]},  # irrational roots
            {**_CONTENTS, "stack_coefficients": [0, -5, 6]},
            {**_CONTENTS, "stack_coefficients": [1001, -5, 6]},
            {**_CONTENTS, "stack_coefficients": [True, -5, 6]},
            {**_CONTENTS, "stack_question_definition": "questionvariables: unsafe"},
            {**_CONTENTS, "equation": "x^2-5*x+6=0\nquestionvariables: 'bad'"},
        ],
    )
    def test_rejects_untrusted_or_mismatched_author_config(self, contents):
        with pytest.raises(stack_v1.StackConfigurationError):
            stack_v1.validate_stack_task_contents(contents)

    def test_task_config_validation_preserves_legacy_and_rejects_unknown_versions(self):
        assert stack_v1.validate_math_steps_task_config(
            AssignmentTaskTypeEnum.MATH_STEPS, {"equation": "2x+6=10"}
        ) is None
        assert stack_v1.validate_math_steps_task_config(
            AssignmentTaskTypeEnum.CUSTOM, {"checker_version": "custom", "stack_layout": []}
        ) is None
        assert stack_v1.validate_math_steps_task_config(
            AssignmentTaskTypeEnum.MATH_STEPS, _CONTENTS
        ) == (1, -5, 6)
        with pytest.raises(stack_v1.StackConfigurationError):
            stack_v1.validate_math_steps_task_config(
                AssignmentTaskTypeEnum.MATH_STEPS,
                {**_CONTENTS, "checker_version": "unknown"},
            )
        with pytest.raises(stack_v1.StackConfigurationError):
            stack_v1.validate_math_steps_task_config(
                AssignmentTaskTypeEnum.MATH_STEPS,
                {"equation": _CONTENTS["equation"], "stack_coefficients": [1, -5, 6]},
            )


async def _create_parent_assignment(db, org, course, chapter, activity):
    assignment = Assignment(
        title="STACK pilot",
        description="",
        published=True,
        grading_type="NUMERIC",
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_stack_config_test",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


class TestNormalTaskCrudValidation:
    async def test_create_accepts_valid_stack_config_and_rejects_mismatch(
        self, db, org, course, chapter, activity, admin_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        valid = AssignmentTaskCreate(
            title="Quadratic",
            description="",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
            contents=_CONTENTS,
        )
        invalid = valid.model_copy(
            update={"contents": {**_CONTENTS, "equation": "x^2-5*x+7=0"}}
        )
        with patch(
            "src.services.courses.activities.assignments.authorize_assignment_access",
            new_callable=AsyncMock,
        ), patch("src.services.courses.activities.assignments.index_activity"):
            created = await create_assignment_task(
                None, "assignment_stack_config_test", valid, admin_user, db
            )
            assert created.contents == _CONTENTS
            with pytest.raises(HTTPException) as error:
                await create_assignment_task(
                    None, "assignment_stack_config_test", invalid, admin_user, db
                )
        assert error.value.status_code == 400

    async def test_learner_task_reads_hide_stack_config(
        self, db, org, course, chapter, activity, admin_user, regular_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        task_data = AssignmentTaskCreate(
            title="Quadratic",
            description="",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
            contents=_CONTENTS,
        )
        with patch(
            "src.services.courses.activities.assignments.authorize_assignment_access",
            new_callable=AsyncMock,
        ), patch("src.services.courses.activities.assignments.index_activity"):
            created = await create_assignment_task(
                None, "assignment_stack_config_test", task_data, admin_user, db
            )
        with patch(
            "src.services.courses.activities.assignments.authorize_assignment_access",
            new_callable=AsyncMock,
        ), patch(
            "src.services.courses.activities.assignments._is_assignment_instructor",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "src.services.courses.activities.assignments._student_may_see_answer_key",
            new_callable=AsyncMock,
            return_value=False,
        ):
            single = await read_assignment_task(
                None, created.assignment_task_uuid, regular_user, db
            )
            listed = await read_assignment_tasks(
                None, "assignment_stack_config_test", regular_user, db
            )

        learner_contents = {
            "equation": _CONTENTS["equation"],
            "checker_version": "stack_v1",
        }
        assert single.contents == learner_contents
        assert listed[0].contents == learner_contents

    async def test_partial_update_keeps_config_and_rejects_detached_equation(
        self, db, org, course, chapter, activity, admin_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        task_data = AssignmentTaskCreate(
            title="Quadratic",
            description="",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
            contents=_CONTENTS,
        )
        with patch(
            "src.services.courses.activities.assignments.authorize_assignment_access",
            new_callable=AsyncMock,
        ), patch("src.services.courses.activities.assignments.index_activity"):
            created = await create_assignment_task(
                None, "assignment_stack_config_test", task_data, admin_user, db
            )
            updated = await update_assignment_task(
                None,
                created.assignment_task_uuid,
                AssignmentTaskUpdate(title="Renamed"),
                admin_user,
                db,
            )
            assert updated.title == "Renamed" and updated.contents == _CONTENTS

            with pytest.raises(HTTPException) as error:
                await update_assignment_task(
                    None,
                    created.assignment_task_uuid,
                    AssignmentTaskUpdate(
                        contents={**_CONTENTS, "equation": "x^2-5*x+7=0"}
                    ),
                    admin_user,
                    db,
                )
        assert error.value.status_code == 400


class TestStackRequests:
    def test_prt_node_notes_can_be_flat_or_nested(self):
        result = stack_v1._parse_grade_response(
            {
                "isgradable": True,
                "prtresults": {
                    "prt1": {
                        "prtanswernotes": [["chain-valid"], ["final-answer"]]
                    }
                },
            }
        )
        assert result == stack_v1.StackCheck("valid", terminal=True)

    @pytest.mark.asyncio
    async def test_live_check_replays_original_and_prior_chain_only_in_answers(self, monkeypatch):
        seen = _install_stack_response(
            monkeypatch,
            {
                "isgradable": True,
                "prtresults": {
                    "prt1": {"prtanswernotes": ["chain-valid", "final-answer"]}
                },
            },
        )

        result = await stack_v1.check_stack_step(
            _CONTENTS["equation"],
            (1, -5, 6),
            "x^2 - 5*x + 6 = 0\n(x-2)*(x-3)=0",
            "x=3 or x=2",
        )

        assert result.status == "valid" and result.terminal
        assert len(seen) == 1 and seen[0][0] == "/grade"
        request = seen[0][1]
        import json

        payload = json.loads(request.content)
        assert payload["answers"] == {
            "ans1": "x^2 - 5*x + 6 = 0\n(x-2)*(x-3)=0\nx=3 or x=2"
        }
        assert "x^2-5*x+6=0" in payload["questionDefinition"]
        assert "x=2 nounor x=3" in payload["questionDefinition"]
        assert "x=3 or x=2" not in payload["questionDefinition"]
        assert "truenextnode: '1'" in payload["questionDefinition"]

    @pytest.mark.asyncio
    async def test_persisted_replay_anchors_on_the_stored_problem(self, monkeypatch):
        seen = _install_stack_response(
            monkeypatch,
            {
                "isgradable": True,
                "prtresults": {
                    "prt1": {"prtanswernotes": ["chain-valid", "final-answer"]}
                },
            },
        )

        result = await stack_v1.grade_stack_steps(
            _CONTENTS["equation"],
            (1, -5, 6),
            ["(x-2)*(x-3)=0", "x=2 or x=3"],
        )

        assert result.status == "valid" and result.terminal
        import json

        payload = json.loads(seen[0][1].content)
        assert payload["answers"]["ans1"] == (
            "x^2 - 5*x + 6 = 0\n(x-2)*(x-3)=0\nx=2 or x=3"
        )

    @pytest.mark.asyncio
    async def test_bad_math_is_not_a_technical_error(self, monkeypatch):
        _install_stack_response(
            monkeypatch,
            {
                "isgradable": True,
                "prtresults": {
                    "prt1": {"prtanswernotes": ["chain-valid", "continue-working"]}
                },
            },
        )
        result = await stack_v1.check_stack_step(
            _CONTENTS["equation"], (1, -5, 6), "x^2-5*x+6=0", "(x-2)*(x-3)=0"
        )
        assert result.status == "valid" and not result.terminal

        _install_stack_response(
            monkeypatch,
            {
                "isgradable": True,
                "prtresults": {"prt1": {"prtanswernotes": ["chain-invalid"]}},
            },
        )
        result = await stack_v1.check_stack_step(
            _CONTENTS["equation"], (1, -5, 6), "x^2-5*x+6=0", "x=4"
        )
        assert result.status == "invalid"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("prtresults", [[], {}])
    async def test_ungradable_student_input_is_not_a_stack_outage(self, monkeypatch, prtresults):
        seen = _install_stack_response(
            monkeypatch, {"isgradable": False, "prtresults": prtresults}
        )
        result = await stack_v1.check_stack_step(
            _CONTENTS["equation"], (1, -5, 6), "x^2-5*x+6=0", "x=("
        )
        assert result.status == "unsupported"
        assert len(seen) == 1

    @pytest.mark.asyncio
    async def test_timeout_and_malformed_upstream_are_technical_errors(self, monkeypatch):
        _install_stack_response(
            monkeypatch,
            exception=httpx.ConnectTimeout("private upstream text"),
        )
        with pytest.raises(stack_v1.StackTechnicalError):
            await stack_v1.check_stack_step(
                _CONTENTS["equation"], (1, -5, 6), _CONTENTS["equation"], "x=2"
            )

        _install_stack_response(monkeypatch, {"isgradable": "yes"})
        with pytest.raises(stack_v1.StackTechnicalError):
            await stack_v1.check_stack_step(
                _CONTENTS["equation"], (1, -5, 6), _CONTENTS["equation"], "x=2"
            )

    def test_student_lines_are_bounded_and_cannot_add_lines(self):
        for steps in (
            ["x=2\nquestionvariables: 'bad'"],
            ["x" * (stack_v1.MAX_STACK_LINE_CHARS + 1)],
            ["x=2"] * (stack_v1.MAX_STACK_STEPS + 1),
        ):
            with pytest.raises(stack_v1.StackStudentInputError):
                stack_v1.validate_stack_steps(_CONTENTS["equation"], steps)

    def test_public_result_and_learner_task_contents_hide_internal_config(self):
        result = stack_v1.StackCheck("valid", terminal=True)
        public = stack_v1.public_stack_check(result)
        assert public == {
            "status": "valid",
            "feedback": public["feedback"],
            "reason": None,
            "example": None,
            "terminal": True,
        }

        learner = _strip_answer_key(
            {**_CONTENTS, "stack_question_definition": "internal gold"},
            assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
        )
        assert learner == {
            "equation": _CONTENTS["equation"],
            "checker_version": "stack_v1",
        }
        assert _CONTENTS["stack_coefficients"] == [1, -5, 6]


_AUTH = "src.services.courses.activities.assignments.authorize_assignment_access"
_INDEX = "src.services.courses.activities.assignments.index_activity"


def _math_task(contents):
    return AssignmentTaskCreate(
        title="T",
        description="",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
        contents=contents,
    )


class TestStackNotConfigured:
    async def test_create_rejects_stack_task_without_stack_url(
        self, db, org, course, chapter, activity, admin_user, monkeypatch
    ):
        monkeypatch.delenv("STACK_API_URL", raising=False)
        await _create_parent_assignment(db, org, course, chapter, activity)
        task = AssignmentTaskCreate(
            title="Quadratic",
            description="",
            hint="",
            assignment_type=AssignmentTaskTypeEnum.MATH_STEPS,
            contents=_CONTENTS,
        )
        with patch(
            "src.services.courses.activities.assignments.authorize_assignment_access",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as exc:
                await create_assignment_task(
                    None, "assignment_stack_config_test", task, admin_user, db
                )
        assert exc.value.status_code == 400
        assert exc.value.detail == (
            "Invalid MATH_STEPS checker configuration: STACK is not configured on this server"
        )


class TestSpecificValidationErrors:
    async def test_create_and_update_report_the_stack_config_message(
        self, db, org, course, chapter, activity, admin_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        bad = {**_CONTENTS, "equation": "x^2+1=0", "stack_coefficients": [1, 0, 1]}
        with patch(_AUTH, new_callable=AsyncMock), patch(_INDEX):
            with pytest.raises(HTTPException) as error:
                await create_assignment_task(
                    None, "assignment_stack_config_test", _math_task(bad), admin_user, db
                )
            assert error.value.status_code == 400
            assert "real roots" in error.value.detail

            created = await create_assignment_task(
                None, "assignment_stack_config_test", _math_task(_CONTENTS), admin_user, db
            )
            with pytest.raises(HTTPException) as error:
                await update_assignment_task(
                    None,
                    created.assignment_task_uuid,
                    AssignmentTaskUpdate(contents=bad),
                    admin_user,
                    db,
                )
        assert error.value.status_code == 400
        assert "real roots" in error.value.detail


class TestLinearEquationValidation:
    async def _update(self, db, uuid, admin_user, **fields):
        with patch(_AUTH, new_callable=AsyncMock), patch(_INDEX):
            return await update_assignment_task(
                None, uuid, AssignmentTaskUpdate(**fields), admin_user, db
            )

    async def test_create_checks_equation_but_allows_empty_contents(
        self, db, org, course, chapter, activity, admin_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        with patch(_AUTH, new_callable=AsyncMock), patch(_INDEX):
            await create_assignment_task(
                None, "assignment_stack_config_test", _math_task({}), admin_user, db
            )
            with pytest.raises(HTTPException) as error:
                await create_assignment_task(
                    None,
                    "assignment_stack_config_test",
                    _math_task({"equation": "x=4"}),
                    admin_user,
                    db,
                )
        assert error.value.status_code == 400
        assert error.value.detail.startswith("Invalid MATH_STEPS equation")

    async def test_update_validates_changed_equation_only(
        self, db, org, course, chapter, activity, admin_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        with patch(_AUTH, new_callable=AsyncMock), patch(_INDEX):
            # Legacy task whose stored equation is no longer valid.
            created = await create_assignment_task(
                None, "assignment_stack_config_test", _math_task({}), admin_user, db
            )
        uuid = created.assignment_task_uuid
        for bad in ("x=4", "x^2=4"):
            with pytest.raises(HTTPException) as error:
                await self._update(db, uuid, admin_user, contents={"equation": bad})
            assert error.value.status_code == 400

        updated = await self._update(db, uuid, admin_user, contents={"equation": "2(x+3)=10"})
        assert updated.contents == {"equation": "2(x+3)=10"}

        from sqlalchemy import update
        from src.db.courses.assignments import AssignmentTask

        await db.execute(
            update(AssignmentTask)
            .where(AssignmentTask.assignment_task_uuid == uuid)
            .values(contents={"equation": "x=4"})
        )
        await db.commit()
        renamed = await self._update(db, uuid, admin_user, title="Renamed")
        assert renamed.title == "Renamed" and renamed.contents == {"equation": "x=4"}

    async def test_switching_stack_task_back_to_linear(
        self, db, org, course, chapter, activity, admin_user
    ):
        await _create_parent_assignment(db, org, course, chapter, activity)
        with patch(_AUTH, new_callable=AsyncMock), patch(_INDEX):
            created = await create_assignment_task(
                None, "assignment_stack_config_test", _math_task(_CONTENTS), admin_user, db
            )
        # Dropping checker_version but keeping the quadratic is a new v1 equation.
        with pytest.raises(HTTPException) as error:
            await self._update(
                db,
                created.assignment_task_uuid,
                admin_user,
                contents={"equation": _CONTENTS["equation"]},
            )
        assert error.value.status_code == 400
        updated = await self._update(
            db,
            created.assignment_task_uuid,
            admin_user,
            contents={"equation": "2(x+3)=10"},
        )
        assert updated.contents == {"equation": "2(x+3)=10"}

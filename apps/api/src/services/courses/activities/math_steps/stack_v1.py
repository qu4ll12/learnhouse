"""Small, opt-in STACK REST adapter for bounded quadratic MATH_STEPS tasks.

Set ``STACK_API_URL`` to the trusted STACK REST ``/grade`` endpoint.
"""

import asyncio
from dataclasses import dataclass
from fractions import Fraction
import httpx
import math
import os
from typing import Literal


STACK_VERSION = "stack_v1"
MAX_STACK_COEFFICIENT = 1000
MAX_STACK_LINE_CHARS = 1024
MAX_STACK_STEPS = 50
MAX_STACK_ANSWER_CHARS = 10_240
MAX_STACK_RESPONSE_BYTES = 65_536
STACK_TIMEOUT_SECONDS = 8.0


def _read_max_concurrency() -> int:
    try:
        value = int(os.getenv("STACK_MAX_CONCURRENCY", ""))
    except ValueError:
        return 10
    return value if value > 0 else 10


# Per API worker process, so total STACK load is workers x this cap.
STACK_MAX_CONCURRENCY = _read_max_concurrency()
_stack_slots: asyncio.Semaphore | None = None

_LINE_SEPARATORS = ("\r", "\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


class StackConfigurationError(ValueError):
    """The persisted teacher-authored STACK pilot config is not supported."""


class StackStudentInputError(ValueError):
    """A student answer exceeds the bounded single-line submission shape."""


class StackTechnicalError(RuntimeError):
    """STACK was unavailable or returned a response outside its REST contract."""


@dataclass(frozen=True)
class StackCheck:
    status: Literal["valid", "invalid", "unsupported", "check_failed"]
    terminal: bool = False


def _coefficients(values) -> tuple[int, int, int]:
    if (
        not isinstance(values, (list, tuple))
        or len(values) != 3
        or any(type(value) is not int or abs(value) > MAX_STACK_COEFFICIENT for value in values)
    ):
        raise StackConfigurationError("STACK coefficients must be three bounded integers")
    a, b, c = values
    if a == 0:
        raise StackConfigurationError("STACK pilot tasks must be quadratic")
    return a, b, c


def quadratic_equation(coefficients) -> str:
    """Return a canonical Maxima equation built only from bounded integers."""
    a, b, c = _coefficients(coefficients)
    terms = []
    for coefficient, variable in ((a, "x^2"), (b, "x"), (c, "")):
        if not coefficient:
            continue
        sign = "-" if coefficient < 0 else "+"
        magnitude = abs(coefficient)
        term = (
            variable
            if variable and magnitude == 1
            else f"{magnitude}*{variable}"
            if variable
            else str(magnitude)
        )
        if not terms:
            terms.append(("-" if coefficient < 0 else "") + term)
        else:
            terms.append(sign + term)
    return "".join(terms) + "=0"


def quadratic_roots(coefficients) -> tuple[Fraction, ...]:
    """Return exact rational roots for a real, rational-root quadratic."""
    a, b, c = _coefficients(coefficients)
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        raise StackConfigurationError("STACK pilot tasks must have real roots")
    square_root = math.isqrt(discriminant)
    if square_root * square_root != discriminant:
        raise StackConfigurationError("STACK pilot tasks must have exact rational roots")
    roots = {
        Fraction(-b - square_root, 2 * a),
        Fraction(-b + square_root, 2 * a),
    }
    return tuple(sorted(roots))


def validate_stack_task_contents(contents) -> tuple[int, int, int]:
    """Validate the complete persisted pilot definition before every use."""
    if not isinstance(contents, dict) or contents.get("checker_version") != STACK_VERSION:
        raise StackConfigurationError("Missing STACK pilot version")
    if any(
        key in contents
        for key in (
            "questionDefinition",
            "question_definition",
            "stack_question_definition",
            "teacher_answer",
            "trusted_solution",
            "gold_answer",
        )
    ):
        raise StackConfigurationError("STACK definitions and gold answers are generated internally")
    coefficients = _coefficients(contents.get("stack_coefficients"))
    roots = quadratic_roots(coefficients)
    equation = contents.get("equation")
    if (
        not isinstance(equation, str)
        or not equation
        or len(equation) > MAX_STACK_LINE_CHARS
        or any(separator in equation for separator in _LINE_SEPARATORS)
        or equation.replace(" ", "") != quadratic_equation(coefficients)
    ):
        raise StackConfigurationError(
            "The learner-facing equation must match the configured quadratic"
        )
    if not roots:
        raise StackConfigurationError("STACK pilot tasks need at least one real root")
    return coefficients


def validate_math_steps_task_config(assignment_type, contents) -> tuple[int, int, int] | None:
    """Validate create/update data while leaving unconfigured legacy tasks alone."""
    if getattr(assignment_type, "value", assignment_type) != "MATH_STEPS":
        return None
    if not isinstance(contents, dict):
        return None
    version = contents.get("checker_version")
    if version is None:
        if any(key.startswith("stack_") for key in contents):
            raise StackConfigurationError("STACK fields require checker_version")
        return None
    if version != STACK_VERSION:
        raise StackConfigurationError("Unknown MATH_STEPS checker version")
    return validate_stack_task_contents(contents)


def _validate_line(line, *, problem: bool = False) -> str:
    if (
        not isinstance(line, str)
        or not line.strip()
        or len(line) > MAX_STACK_LINE_CHARS
        or any(separator in line for separator in _LINE_SEPARATORS)
        or any(ord(character) < 32 or ord(character) == 127 for character in line)
    ):
        error = StackConfigurationError if problem else StackStudentInputError
        raise error("Each STACK answer must be one non-empty bounded line")
    return line


def _validate_stack_problem(problem: str, coefficients) -> str:
    _coefficients(coefficients)
    problem = _validate_line(problem, problem=True)
    if problem.replace(" ", "") != quadratic_equation(coefficients):
        raise StackConfigurationError("The STACK problem does not match its coefficients")
    return problem


def _validate_step_lines(problem: str, steps, *, allow_empty: bool = False) -> tuple[str, ...]:
    _validate_line(problem, problem=True)
    if not isinstance(steps, list):
        raise StackStudentInputError("STACK steps must be a list of single lines")
    if not steps and allow_empty:
        return ()
    if not steps or len(steps) > MAX_STACK_STEPS:
        raise StackStudentInputError("STACK submissions must contain between 1 and 50 lines")
    lines = tuple(_validate_line(step) for step in steps)
    answer_size = len(problem) + sum(len(line) for line in lines) + len(lines)
    if answer_size > MAX_STACK_ANSWER_CHARS:
        raise StackStudentInputError("STACK answer is too large")
    return lines


def validate_stack_steps(problem: str, steps) -> tuple[str, ...]:
    """Validate saved student lines before replay or persistence."""
    return _validate_step_lines(problem, steps)


def validate_stack_submission(contents, submission) -> tuple[str, ...]:
    """Validate STACK answer data at the normal assignment submission boundary."""
    validate_stack_task_contents(contents)
    if not isinstance(submission, dict):
        raise StackStudentInputError("STACK submission must be an object")
    return _validate_step_lines(
        contents["equation"], submission.get("steps", []), allow_empty=True
    )


def _teacher_solution(coefficients) -> str:
    roots = quadratic_roots(coefficients)
    answers = [f"x={root.numerator}" if root.denominator == 1 else f"x={root}" for root in roots]
    return " nounor ".join(answers)


def _question_definition(coefficients) -> str:
    equation = quadratic_equation(coefficients)
    solution = _teacher_solution(coefficients)
    # This fixed YAML is the only definition sent to STACK. Student text is
    # placed solely in answers. Both equation and exact roots come from the
    # validated integer triple, never from arbitrary author strings.
    return f"""name: LearnHouse MATH_STEPS pilot
questiontext: '[[input:ans1]] [[validation:ans1]]'
questionvariables: 'ta:[{equation},{solution}];'
questionsimplify: '0'
assumereal: '1'
specificfeedback: '[[feedback:prt1]]'
input:
  - name: ans1
    type: equiv
    tans: ta
    insertstars: '1'
    mustverify: '0'
    showvalidation: '1'
    options: 'firstline,assume_real'
prt:
  - name: prt1
    autosimplify: '0'
    node:
      - name: '0'
        answertest: EquivFirst
        testoptions: assume_real
        sans: ans1
        tans: ta
        quiet: '0'
        truescore: '0'
        truenextnode: '1'
        falsescore: '0'
        trueanswernote: chain-valid
        falseanswernote: chain-invalid
      - name: '1'
        answertest: EqualComAss
        sans: last(ans1)
        tans: last(ta)
        quiet: '1'
        truescore: '1'
        falsescore: '0'
        trueanswernote: final-answer
        falseanswernote: continue-working
"""


def _parse_grade_response(payload) -> StackCheck:
    if not isinstance(payload, dict) or type(payload.get("isgradable")) is not bool:
        raise StackTechnicalError("Malformed STACK response")
    if not payload["isgradable"]:
        if payload.get("prtresults") not in ([], {}):
            raise StackTechnicalError("Malformed STACK response")
        return StackCheck("unsupported")

    results = payload.get("prtresults")
    prt = results.get("prt1") if isinstance(results, dict) else None
    if not isinstance(prt, dict) or prt.get("errors") or prt.get("fverrors"):
        raise StackTechnicalError("Malformed STACK response")
    notes = prt.get("prtanswernotes")
    if not isinstance(notes, list):
        raise StackTechnicalError("Malformed STACK response")
    flat_notes = []
    pending = list(notes)
    while pending:
        note = pending.pop()
        if isinstance(note, str):
            flat_notes.append(note)
        elif isinstance(note, list):
            pending.extend(note)
        else:
            raise StackTechnicalError("Malformed STACK response")

    chain_notes = {note for note in flat_notes if note in ("chain-valid", "chain-invalid")}
    final_notes = {note for note in flat_notes if note in ("final-answer", "continue-working")}
    if len(chain_notes) != 1:
        raise StackTechnicalError("Malformed STACK response")
    if "chain-invalid" in chain_notes:
        if final_notes:
            raise StackTechnicalError("Malformed STACK response")
        return StackCheck("invalid")
    if len(final_notes) != 1:
        raise StackTechnicalError("Malformed STACK response")
    return StackCheck("valid", terminal="final-answer" in final_notes)


async def _grade_lines(
    problem: str, coefficients, lines: tuple[str, ...], *, wait_for_slot: bool
) -> StackCheck:
    _validate_stack_problem(problem, coefficients)
    url = (os.getenv("STACK_API_URL") or "").strip()
    if not url:
        raise StackTechnicalError("STACK endpoint is not configured")
    request_body = {
        "questionDefinition": _question_definition(coefficients),
        "answers": {"ans1": "\n".join((problem, *lines))},
    }
    global _stack_slots
    if _stack_slots is None:
        _stack_slots = asyncio.Semaphore(STACK_MAX_CONCURRENCY)
    slots = _stack_slots
    if wait_for_slot:
        try:
            await asyncio.wait_for(slots.acquire(), STACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise StackTechnicalError("STACK is busy") from None
    elif slots.locked():
        raise StackTechnicalError("STACK is busy")
    else:
        await slots.acquire()  # free slot: returns without yielding
    try:
        async with httpx.AsyncClient(timeout=STACK_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=request_body)
        if response.status_code != 200 or len(response.content) > MAX_STACK_RESPONSE_BYTES:
            raise StackTechnicalError("STACK returned an unexpected response")
        payload = response.json()
    except StackTechnicalError:
        raise
    except Exception:
        # Do not propagate response bodies, URLs, or transport exception text.
        raise StackTechnicalError("STACK request failed") from None
    finally:
        slots.release()
    return _parse_grade_response(payload)


def _live_lines(problem: str, previous: str, current: str) -> tuple[str, ...]:
    problem = _validate_line(problem, problem=True)
    if not isinstance(previous, str) or not previous or len(previous) > MAX_STACK_ANSWER_CHARS:
        raise StackStudentInputError("Previous STACK work is empty or too large")
    if any(separator != "\n" and separator in previous for separator in _LINE_SEPARATORS):
        raise StackStudentInputError("Previous STACK work contains an invalid line break")
    prior = previous.split("\n")
    prior = [_validate_line(line) for line in prior]
    current = _validate_line(current)
    same_problem = prior[0].replace(" ", "") == problem.replace(" ", "")
    lines = tuple((*prior[1:], current) if same_problem else (*prior, current))
    if len(lines) > MAX_STACK_STEPS:
        raise StackStudentInputError("STACK submissions may contain at most 50 steps")
    if len(problem) + sum(map(len, lines)) + len(lines) > MAX_STACK_ANSWER_CHARS:
        raise StackStudentInputError("STACK answer is too large")
    return lines


async def check_stack_step(
    problem: str, coefficients, previous: str, current: str
) -> StackCheck:
    try:
        lines = _live_lines(problem, previous, current)
        _validate_stack_problem(problem, coefficients)
    except StackStudentInputError:
        return StackCheck("unsupported")
    result = await _grade_lines(problem, coefficients, lines, wait_for_slot=False)
    return result


async def grade_stack_steps(problem: str, coefficients, steps) -> StackCheck:
    try:
        lines = _validate_step_lines(problem, steps)
        _validate_stack_problem(problem, coefficients)
    except StackStudentInputError:
        return StackCheck("unsupported")
    return await _grade_lines(problem, coefficients, lines, wait_for_slot=True)


def public_stack_check(result: StackCheck) -> dict:
    """Return only local status and feedback; never STACK's HTML or answer data."""
    messages = {
        "valid": ("Bước biến đổi hợp lệ.", None),
        "invalid": ("Bước biến đổi chưa đúng.", "invalid_transformation"),
        "unsupported": ("Chưa thể kiểm tra đầu vào này.", "unsupported_input"),
        "check_failed": (
            "Hiện chưa thể kiểm tra do lỗi kỹ thuật. Vui lòng thử lại.",
            "checker_unavailable",
        ),
    }
    feedback, reason = messages[result.status]
    return {
        "status": result.status,
        "feedback": feedback,
        "reason": reason,
        "example": None,
        "terminal": result.status == "valid" and result.terminal,
    }

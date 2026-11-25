"""Step-by-step math checking for MATH_STEPS assignment tasks.

v1_checker.py and terminal.py are verbatim copies of MATHCDT
server/src/modules/mathematics (commit 9a92438); re-copy them rather than
editing here so the two checkers stay identical. STACK is an explicit,
bounded opt-in for quadratic tasks; tasks without a checker version use v1.
"""

from . import v1_checker as checker
from .terminal import is_terminal_answer
from .v1_checker import CheckResult, CheckStatus, EquationAnalysis, SolutionSet, check_step

MAX_STEPS = 50

__all__ = [
    "CheckResult",
    "CheckStatus",
    "EquationAnalysis",
    "SolutionSet",
    "check_step",
    "is_terminal_answer",
    "public_check",
    "is_valid_exercise",
    "steps_completed",
]


def public_check(previous: str, current: str) -> dict:
    """The learner-safe view of one check: never the solution sets, which
    would hand out the answer."""
    result = check_step(previous, current)
    return {
        "status": result.status,
        "feedback": result.feedback,
        "reason": result.reason,
        "example": result.example,
        "terminal": result.status == "valid" and is_terminal_answer(current),
    }


def is_valid_exercise(equation: str) -> bool:
    """Supported, solvable-by-steps equation that is not already an answer
    (same rule as MATHCDT's exercise validation)."""
    return check_step(equation, equation).status == "valid" and not is_terminal_answer(equation)


def steps_completed(equation, steps) -> bool:
    """All-or-nothing grade: every transition from the exercise equation is
    valid and the last step is a final-answer form."""
    if not isinstance(equation, str) or not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        return False
    if not all(isinstance(s, str) for s in steps):
        return False
    chain = [equation, *steps]
    return all(
        check_step(prev, cur).status == "valid" for prev, cur in zip(chain, chain[1:])
    ) and is_terminal_answer(steps[-1])

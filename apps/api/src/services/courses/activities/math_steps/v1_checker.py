"""Exact step checking for the documented one-variable linear-equation domain.

The parser is deliberately restricted: it never executes student input. Each
expression is reduced to a*x + b while retaining whether it contained x, so
cancellation cannot hide an unsupported variable product or denominator.
"""

from dataclasses import dataclass
from fractions import Fraction
import logging
import re
from typing import Literal


CheckStatus = Literal["valid", "invalid", "unsupported", "check_failed"]
SolutionKind = Literal["singleton", "all_real", "empty"]
Coefficients = tuple[Fraction, Fraction]

MAX_INPUT_LENGTH = 1024
MAX_LITERAL_DIGITS = 64
MAX_NESTING = 32

_TOKEN = re.compile(r"[0-9]+(?:\.[0-9]+)?|\.[0-9]+|x|[()+*/=\-]")
_EXAMPLE = "2(x + 3) = 10"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SolutionSet:
    """A singleton rational value, all real numbers, or the empty set."""

    kind: SolutionKind
    value: Fraction | None = None


@dataclass(frozen=True)
class EquationAnalysis:
    """Preserved input, explicit interpretation, and exact mathematical evidence."""

    text: str
    interpreted: str
    left: Coefficients
    right: Coefficients
    solution_set: SolutionSet


@dataclass(frozen=True)
class CheckResult:
    """One transition judgment, not a judgment of completion or understanding.

    Analysis values are internal evidence; callers should not expose answers as
    unsolicited hints. Callers must bind this result to their revision IDs.
    """

    status: CheckStatus
    feedback: str
    previous_text: str
    current_text: str
    previous: EquationAnalysis | None = None
    current: EquationAnalysis | None = None
    reason: str | None = None
    unsupported_side: Literal["previous", "current"] | None = None
    example: str | None = None


class _Unsupported(ValueError):
    def __init__(self, reason: str, feedback: str, example: str = _EXAMPLE):
        super().__init__(feedback)
        self.reason = reason
        self.feedback = feedback
        self.example = example


@dataclass(frozen=True)
class _Expression:
    a: Fraction
    b: Fraction
    contains_x: bool = False


def _tokenize(text: str) -> list[str]:
    if not isinstance(text, str):
        raise _Unsupported("input_type", "Hãy nhập phương trình dưới dạng văn bản.")
    if len(text) > MAX_INPUT_LENGTH:
        raise _Unsupported(
            "input_limit", f"Phương trình chỉ được dài tối đa {MAX_INPUT_LENGTH} ký tự."
        )
    tokens = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(text, position)
        if match is None:
            raise _Unsupported(
                "unsupported_notation",
                "Chỉ hỗ trợ số, biến x, dấu ngoặc và các phép +, -, *, /, =; "
                "dùng dấu chấm cho số thập phân.",
            )
        token = match.group()
        if sum(character.isdigit() for character in token) > MAX_LITERAL_DIGITS:
            raise _Unsupported(
                "literal_limit", f"Mỗi số chỉ được có tối đa {MAX_LITERAL_DIGITS} chữ số."
            )
        tokens.append(token)
        position = match.end()
    if tokens.count("=") != 1:
        raise _Unsupported("equation_syntax", "Hãy nhập một phương trình có đúng một dấu =.")
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.position = 0
        self.interpreted: list[str] = []

    def peek(self) -> str:
        return self.tokens[self.position] if self.position < len(self.tokens) else ""

    def take(self) -> str:
        token = self.peek()
        self.position += 1
        self.interpreted.append(token)
        return token

    def require(self, token: str) -> None:
        if self.peek() != token:
            raise _Unsupported(
                "equation_syntax", "Hãy kiểm tra toán hạng, dấu phép tính và dấu ngoặc."
            )
        self.take()

    def expression(self, depth: int = 0) -> _Expression:
        left = self.term(depth)
        while self.peek() in ("+", "-"):
            operator = self.take()
            right = self.term(depth)
            sign = 1 if operator == "+" else -1
            left = _Expression(
                left.a + sign * right.a,
                left.b + sign * right.b,
                left.contains_x or right.contains_x,
            )
        return left

    def term(self, depth: int) -> _Expression:
        left = self.unary(depth)
        last_operator = ""
        while True:
            operator = self.peek()
            previous_token = self.tokens[self.position - 1]
            implicit = operator == "(" or (
                operator == "x" and previous_token not in ("x", "")
            )
            if operator in ("*", "/"):
                self.take()
            elif implicit:
                # 1/2x has competing classroom interpretations. Require grouping.
                if last_operator == "/":
                    raise _Unsupported(
                        "ambiguous_division",
                        "Hãy dùng dấu ngoặc hoặc dấu * để làm rõ phép nhân sau phép chia.",
                        "(1/2)*x = 3",
                    )
                operator = "*"
                self.interpreted.append("*")
            else:
                break
            right = self.unary(depth)
            if operator == "*":
                if left.contains_x and right.contains_x:
                    raise _Unsupported(
                        "variable_product", "Phép nhân phải có ít nhất một toán hạng không chứa x."
                    )
                left = _Expression(
                    left.a * right.b + left.b * right.a,
                    left.b * right.b,
                    left.contains_x or right.contains_x,
                )
            else:
                if right.contains_x:
                    raise _Unsupported("variable_denominator", "Mẫu số không được chứa x.")
                if right.b == 0:
                    raise _Unsupported("division_by_zero", "Không hỗ trợ phép chia cho 0.")
                left = _Expression(left.a / right.b, left.b / right.b, left.contains_x)
            last_operator = operator
        return left

    def unary(self, depth: int) -> _Expression:
        if depth > MAX_NESTING:
            raise _Unsupported(
                "nesting_limit", f"Chỉ hỗ trợ tối đa {MAX_NESTING} mức dấu ngoặc hoặc dấu âm/dương."
            )
        token = self.peek()
        if token in ("+", "-"):
            self.take()
            value = self.unary(depth + 1)
            sign = 1 if token == "+" else -1
            return _Expression(sign * value.a, sign * value.b, value.contains_x)
        if token == "(":
            self.take()
            value = self.expression(depth + 1)
            self.require(")")
            return value
        if token == "x":
            self.take()
            return _Expression(Fraction(1), Fraction(0), True)
        if token and (token[0].isdigit() or token[0] == "."):
            self.take()
            return _Expression(Fraction(0), Fraction(token))
        raise _Unsupported(
            "equation_syntax", "Thiếu số, biến x hoặc biểu thức hợp lệ trong phương trình."
        )


def _analyze(text: str) -> EquationAnalysis:
    parser = _Parser(_tokenize(text))
    left = parser.expression()
    parser.require("=")
    right = parser.expression()
    if parser.peek():
        raise _Unsupported(
            "equation_syntax", "Hãy kiểm tra các phép tính và dấu ngoặc; dùng * khi cần."
        )
    a, b = left.a - right.a, left.b - right.b
    if a:
        solutions = SolutionSet("singleton", -b / a)
    elif b == 0:
        solutions = SolutionSet("all_real")
    else:
        solutions = SolutionSet("empty")
    return EquationAnalysis(
        text, " ".join(parser.interpreted), (left.a, left.b), (right.a, right.b), solutions
    )


def check_step(previous: str, current: str) -> CheckResult:
    """Compare exact real solution sets of two supported equation strings.

    Unsupported input is never called mathematically invalid. Unexpected engine
    errors are logged and become check_failed, never a validity judgment.
    """
    previous_analysis = None
    current_analysis = None
    side: Literal["previous", "current"] = "previous"
    try:
        previous_analysis = _analyze(previous)
        side = "current"
        current_analysis = _analyze(current)
        equivalent = previous_analysis.solution_set == current_analysis.solution_set
        return CheckResult(
            status="valid" if equivalent else "invalid",
            feedback=(
                "Bước biến đổi hợp lệ: hai phương trình có cùng tập nghiệm."
                if equivalent
                else "Bước biến đổi chưa đúng: hai phương trình không có cùng tập nghiệm."
            ),
            previous_text=previous,
            current_text=current,
            previous=previous_analysis,
            current=current_analysis,
        )
    except _Unsupported as error:
        return CheckResult(
            status="unsupported",
            feedback=f"Chưa thể kiểm tra đầu vào này. {error.feedback}",
            previous_text=previous,
            current_text=current,
            previous=previous_analysis,
            current=current_analysis,
            reason=error.reason,
            unsupported_side=side,
            example=error.example,
        )
    except Exception:
        _LOGGER.exception("Mathematical checking failed")
        return CheckResult(
            status="check_failed",
            feedback="Hiện chưa thể kiểm tra do lỗi kỹ thuật. Vui lòng thử lại.",
            previous_text=previous,
            current_text=current,
            previous=previous_analysis,
            current=current_analysis,
            reason="internal_error",
        )


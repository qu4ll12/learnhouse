"""Copied from MATHCDT server/src/modules/mathematics/tests (commit 9a92438)."""

from fractions import Fraction
import unittest
from unittest.mock import patch

from src.services.courses.activities.math_steps import SolutionSet, check_step
from src.services.courses.activities.math_steps import checker


class MathematicsTests(unittest.TestCase):
    def test_expansion_and_alternative_routes(self):
        for step in ("2x + 6 = 10", "x + 3 = 5", "2x = 4", "x = 2", "2 = x"):
            with self.subTest(step=step):
                result = check_step("2(x + 3) = 10", step)
                self.assertEqual(result.status, "valid")
                self.assertEqual(result.current.solution_set, SolutionSet("singleton", Fraction(2)))

    def test_incorrect_expansion(self):
        result = check_step("2(x + 3) = 10", "2x + 3 = 10")
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.previous.solution_set.value, Fraction(2))
        self.assertEqual(result.current.solution_set.value, Fraction(7, 2))

    def test_precedence_parentheses_and_unary_signs(self):
        cases = [
            ("2 + 3*x = 11", "x = 3"),
            ("(2 + 3)*x = 10", "x = 2"),
            ("-(x + 3) = -5", "x = 2"),
            ("-2(-x + 3) = -2", "x = 2"),
            ("x--2 = 4", "x = 2"),
            ("+x = --2", "x = 2"),
            ("2*-x = -4", "x = 2"),
            ("x/-2 = -1", "x = 2"),
            ("x = 8/2/2", "x = 2"),
            ("x = 8/(2/2)", "x = 8"),
            ("x = 8-3-2", "x = 3"),
            ("2(x/3 + 1) = 6", "x = 6"),
        ]
        for previous, current in cases:
            with self.subTest(previous=previous):
                self.assertEqual(check_step(previous, current).status, "valid")

    def test_fractions_and_decimals_are_exact(self):
        for equation, answer in (
            ("0.1x + 0.2 = 0.3", "x = 1"),
            (".5x = 1.5", "x = 3"),
            ("(1/3)x = 1", "x = 3"),
            ("2/3*x = 4", "x = 6"),
            ("3x = 1", "x = 1/3"),
            ("x/(2 + 1) = 1", "x = 3"),
        ):
            with self.subTest(equation=equation):
                self.assertEqual(check_step(equation, answer).status, "valid")
        self.assertEqual(check_step("3x = 1", "x = 0.3333333333333333").status, "invalid")
        self.assertEqual(
            check_step("x = 9007199254740992", "x = 9007199254740993").status, "invalid"
        )

    def test_implicit_multiplication_is_shown_explicitly(self):
        for text in ("2(x+3)=10", "2*(x+3)=10", "(2)(x+3)=10", "(x+3)(2)=10"):
            with self.subTest(text=text):
                result = check_step(text, "x=2")
                self.assertEqual(result.status, "valid")
                self.assertIn("*", result.previous.interpreted)
                self.assertEqual(result.previous.left, (Fraction(2), Fraction(6)))
        self.assertEqual(check_step("x(2)=4", "x=2").status, "valid")

    def test_original_input_and_interpretation_are_preserved(self):
        previous = "  2(x + 3) = 10\n"
        current = "x = 2"
        result = check_step(previous, current)
        self.assertEqual(result.previous_text, previous)
        self.assertEqual(result.current_text, current)
        self.assertEqual(result.previous.text, previous)
        self.assertEqual(result.current.text, current)
        self.assertEqual(result.previous.interpreted, "2 * ( x + 3 ) = 10")
        self.assertNotIn("x = 2", result.feedback)

    def test_all_real_solution_sets(self):
        result = check_step("2(x+1)=2x+2", "0=0")
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.previous.solution_set, SolutionSet("all_real"))
        self.assertEqual(check_step("x=x", "3=3").status, "valid")

    def test_empty_solution_sets(self):
        result = check_step("2x+1=2x+3", "0=2")
        self.assertEqual(result.status, "valid")
        self.assertEqual(result.previous.solution_set, SolutionSet("empty"))
        self.assertEqual(check_step("0=1", "2=3").status, "valid")

    def test_solution_classes_are_distinct(self):
        for previous, current in (("x=2", "0=0"), ("x=2", "0=1"), ("0=0", "0=1")):
            with self.subTest(previous=previous, current=current):
                self.assertEqual(check_step(previous, current).status, "invalid")
                self.assertEqual(check_step(current, previous).status, "invalid")

    def test_multiplying_both_sides_by_zero_can_lose_information(self):
        self.assertEqual(check_step("x=2", "0*x=0*2").status, "invalid")

    def test_locally_valid_transition_does_not_repair_an_invalid_path(self):
        self.assertEqual(check_step("2(x+3)=10", "2x+3=10").status, "invalid")
        self.assertEqual(check_step("2x+3=10", "x=7/2").status, "valid")
        self.assertEqual(check_step("2(x+3)=10", "x=7/2").status, "invalid")

    def test_unsupported_notation_and_malformed_equations(self):
        inputs = [
            "", " ", "x", "x=", "=2", "x=2=3", "x==2", "x+ = 2", "()=2",
            "(x+1=2", "x+1)=2", "x=2)", "2 3x=6", "x2=2", "xx=2", "(2)3=6",
            "x=1.2.3", "x=1.", "x=1,5", "x=1e2", "x=NaN", "x=inf", "x=0x10",
            "y=2", "x+y=2", "X=2", "x^2=4", "x**2=4", "sin(x)=0", "sqrt(x)=2",
            "x<=2", "x!=2", "x//2=1", "x%2=0", "[x]=2", "x=2;", "x=２",
            "__import__('os').system('echo should_not_run')=0",
        ]
        for text in inputs:
            with self.subTest(text=text):
                result = check_step("x=2", text)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.unsupported_side, "current")
                self.assertTrue(result.reason)
                self.assertTrue(result.example)

    def test_variable_products_are_rejected_even_after_cancellation(self):
        for text in ("x*x=4", "(x+1)(x-1)=0", "(x-x)*x=0", "0*(x*x)=0"):
            with self.subTest(text=text):
                result = check_step("x=2", text)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.reason, "variable_product")

    def test_variable_denominators_are_rejected_even_after_cancellation(self):
        for text in ("1/x=2", "x/x=1", "x/(x-x+1)=2", "0/x=0"):
            with self.subTest(text=text):
                result = check_step("x=2", text)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.reason, "variable_denominator")

    def test_division_by_zero_is_unsupported(self):
        for text in ("x/0=1", "x/(2-2)=1", "0/0=0", "x/(-0)=1"):
            with self.subTest(text=text):
                result = check_step("x=2", text)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.reason, "division_by_zero")

    def test_ambiguous_division_requires_explicit_grouping(self):
        for text in ("1/2x=1", "x/2(3)=1", "1/-2x=1", "1/(2)x=1"):
            with self.subTest(text=text):
                result = check_step("x=2", text)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.reason, "ambiguous_division")
        self.assertEqual(check_step("(1/2)x=1", "x=2").status, "valid")
        self.assertEqual(check_step("1/2*x=1", "x=2").status, "valid")

    def test_unsupported_previous_is_identified(self):
        result = check_step("x^2=4", "x=2")
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.unsupported_side, "previous")
        self.assertEqual(result.previous_text, "x^2=4")
        self.assertIsNone(result.previous)
        self.assertIsNone(result.current)

    def test_limits_are_unsupported_not_execution_failures(self):
        cases = [
            (" " * 1025, "input_limit"),
            ("x=" + "1" * 65, "literal_limit"),
            ("x=0." + "1" * 64, "literal_limit"),
            ("(" * 33 + "x" + ")" * 33 + "=2", "nesting_limit"),
            ("-" * 33 + "x=2", "nesting_limit"),
        ]
        for text, reason in cases:
            with self.subTest(reason=reason):
                result = check_step("x=2", text)
                self.assertEqual(result.status, "unsupported")
                self.assertEqual(result.reason, reason)

    def test_inputs_at_documented_limits(self):
        for text in (
            "x=2" + " " * 1021,
            "x=" + "1" * 64,
            "(" * 32 + "x" + ")" * 32 + "=2",
            "-" * 32 + "x=2",
        ):
            with self.subTest(text=text):
                self.assertEqual(check_step(text, text).status, "valid")

    def test_generated_linear_equations_against_known_solutions(self):
        # Construct a*x+b=c*x+d with d=(a-c)*root+b independently
        # of the checker. Both sides contain x; coefficients include zero/signs.
        for a in range(-3, 4):
            for c in range(-3, 4):
                if a == c:
                    continue
                for b in range(-2, 3):
                    for root in (Fraction(-3, 2), Fraction(0), Fraction(2, 3)):
                        d = (a - c) * root + b
                        equation = f"({a})*x+({b})=({c})*x+({d})"
                        with self.subTest(equation=equation, root=root):
                            self.assertEqual(check_step(equation, f"x={root}").status, "valid")
                            self.assertEqual(check_step(equation, f"x={root+1}").status, "invalid")

    def test_equivalent_operations_on_both_sides(self):
        for equation in ("2x+3=7", "x=x", "x=x+1"):
            left, right = equation.split("=")
            for constant in (-3, -1, 1, 2):
                for operation in ("+", "-", "*", "/"):
                    transformed = f"({left}){operation}({constant})=({right}){operation}({constant})"
                    with self.subTest(equation=equation, transformed=transformed):
                        self.assertEqual(check_step(equation, transformed).status, "valid")

    def test_unexpected_failure_has_no_mathematical_judgment(self):
        with patch.object(checker, "_analyze", side_effect=RuntimeError("private error")):
            with self.assertLogs(checker.__name__, level="ERROR"):
                result = check_step("x=2", "x=2")
        self.assertEqual(result.status, "check_failed")
        self.assertEqual(result.reason, "internal_error")
        self.assertNotIn("private error", result.feedback)
        self.assertIsNone(result.unsupported_side)

    def test_failure_on_current_preserves_previous_analysis(self):
        analyze = checker._analyze
        with patch.object(checker, "_analyze", side_effect=[analyze("x=2"), RuntimeError("boom")]):
            with self.assertLogs(checker.__name__, level="ERROR"):
                result = check_step("x=2", "2x=4")
        self.assertEqual(result.status, "check_failed")
        self.assertEqual(result.previous.solution_set.value, Fraction(2))
        self.assertIsNone(result.current)


if __name__ == "__main__":
    unittest.main()

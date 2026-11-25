"""Tests for reviewed final-answer forms."""

import unittest

from src.services.courses.activities.math_steps import is_terminal_answer


class TerminalAnswerTests(unittest.TestCase):
    def test_isolated_variable_is_terminal(self):
        for equation in ("x = 2", "2 = x", "x=-3/2", "1 + 1 = x"):
            with self.subTest(equation=equation):
                self.assertTrue(is_terminal_answer(equation))

    def test_equivalent_intermediate_equations_are_not_terminal(self):
        for equation in ("2x = 4", "x + 0 = 2", "2(x + 3) = 10"):
            with self.subTest(equation=equation):
                self.assertFalse(is_terminal_answer(equation))

    def test_constant_solution_class_forms_are_terminal(self):
        for equation in ("0 = 0", "3 = 3", "0 = 1", "2 + 2 = 5"):
            with self.subTest(equation=equation):
                self.assertTrue(is_terminal_answer(equation))

    def test_variable_forms_are_not_constant_terminal_forms(self):
        for equation in ("x = x", "x - x = 0", "0*x = 0", "x + 1 = x + 2"):
            with self.subTest(equation=equation):
                self.assertFalse(is_terminal_answer(equation))

    def test_unsupported_input_is_not_terminal(self):
        for equation in ("", "x", "x =", "x^2 = 4", "x = 1/0"):
            with self.subTest(equation=equation):
                self.assertFalse(is_terminal_answer(equation))


if __name__ == "__main__":
    unittest.main()

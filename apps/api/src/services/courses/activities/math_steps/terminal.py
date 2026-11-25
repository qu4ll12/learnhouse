"""Recognition of supported final-answer equation forms."""

from .v1_checker import _Unsupported, _analyze, _tokenize


def is_terminal_answer(equation: str) -> bool:
    """Return whether a supported equation has a reviewed final-answer form.

    Singleton answers must have ``x`` alone on one side and a constant
    expression on the other. All-real and empty-set answers must be written as
    constant equalities or contradictions.
    """
    try:
        tokens = _tokenize(equation)
        analysis = _analyze(equation)
    except _Unsupported:
        return False

    equals = tokens.index("=")
    left_tokens = tokens[:equals]
    right_tokens = tokens[equals + 1 :]

    if analysis.solution_set.kind == "singleton":
        return (left_tokens == ["x"] and "x" not in right_tokens) or (
            right_tokens == ["x"] and "x" not in left_tokens
        )

    return "x" not in tokens

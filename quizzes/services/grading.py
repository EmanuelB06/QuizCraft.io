"""Answer checking for every question type.

This module is a thin façade over the per-type handlers in `question_types`. It
exists because grading is called from several places and because the previous
single-function version branched on `question_type` in one long conditional.
All of that branching now lives with the type that owns it.

Kept free of Django imports so it can be unit tested directly.
"""

from decimal import Decimal

from ..question_types import get_handler
from ..question_types.normalize import normalize, to_bool

__all__ = ["format_answer", "grade", "normalize", "to_bool"]


def grade(question, user_answer, partial_credit=None):
    """Return (is_correct, points_earned) for one question.

    `user_answer` is whatever the type's handler produced from the submitted
    form: a string for single-answer types, a list of positions for
    multiple-choice. `partial_credit` defaults to the quiz's own setting.
    """
    if partial_credit is None:
        # Kept for callers holding a bare question, e.g. the unit tests. The
        # view passes the quiz's setting explicitly to avoid a lazy load.
        partial_credit = getattr(getattr(question, "quiz", None), "partial_credit", False)
    return get_handler(question.question_type).grade(
        question, user_answer, bool(partial_credit)
    )


def format_answer(question, user_answer):
    """Render a stored answer for display in the results table."""
    if user_answer in (None, "", []):
        return "No answer"
    return get_handler(question.question_type).format_answer(question, user_answer)


def score_attempt(questions, extract_answer, partial_credit=False):
    """Grade a whole attempt.

    `extract_answer` maps a question to the answer it should be graded against,
    which keeps HTTP concerns (reading the POST) out of this module.
    Returns a list of (question, user_answer, is_correct, points_earned).
    """
    rows = []
    for question in questions:
        submission = extract_answer(question)
        is_correct, points = grade(question, submission, partial_credit)
        rows.append((question, submission, is_correct, Decimal(points)))
    return rows

"""Leaf module holding the persisted enum values.

It deliberately imports nothing from the rest of the app. The question-type
handlers, the models and the forms all need these names, so keeping the enum in
its own module is what lets `models` depend on `question_types` without a cycle.
"""

from django.db import models


class QuestionType(models.TextChoices):
    """The question formats the app can store and render.

    Note: Django's TextChoices metaclass does not keep extra classmethods on the
    resulting enum, so helpers for these values live in
    `question_types.registry`, not on the enum itself.
    """

    MCQ_SINGLE = "mcq_single", "Multiple choice (one answer)"
    MCQ_MULTI = "mcq_multi", "Multiple answer"
    TRUE_FALSE = "true_false", "True / False"
    SHORT_ANSWER = "short_answer", "Short answer"
    FILL_BLANK = "fill_blank", "Fill in the blank"


class Difficulty(models.TextChoices):
    EASY = "easy", "Easy"
    MEDIUM = "medium", "Medium"
    HARD = "hard", "Hard"

"""Form surface, re-exported so `quizzes.forms` keeps working."""

from .question_edit import NEW_CHOICES, NEW_SLOTS, QuestionEditForm
from .upload import (
    DEFAULT_QUIZ_QUESTIONS,
    MAX_QUIZ_QUESTIONS,
    MIN_QUIZ_QUESTIONS,
    QuizUploadForm,
)

__all__ = [
    "DEFAULT_QUIZ_QUESTIONS",
    "MAX_QUIZ_QUESTIONS",
    "MIN_QUIZ_QUESTIONS",
    "NEW_CHOICES",
    "NEW_SLOTS",
    "QuestionEditForm",
    "QuizUploadForm",
]

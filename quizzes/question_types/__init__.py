"""The question-type registry: the one place a new format is registered.

Everything else in the app asks this module what a given `question_type` means
instead of branching on the value itself. Adding a sixth type is therefore a
new handler plus one line in `REGISTRY` -- no edits to the grader, the PDF
writer, the edit form, the take page or the views.
"""

from .base import (
    AnswerView,
    ChoiceOption,
    DescribesAnswer,
    GradesAnswers,
    QuestionHandler,
    ValidatesModelOutput,
)
from .choice import MultiChoiceHandler, SingleChoiceHandler
from .kinds import Difficulty, QuestionType
from .text import FillBlankHandler, ShortAnswerHandler, TrueFalseHandler

#: Persisted value -> handler instance. Handlers are stateless, so one shared
#: instance per type is enough.
REGISTRY: dict = {
    SingleChoiceHandler.type_value: SingleChoiceHandler(),
    MultiChoiceHandler.type_value: MultiChoiceHandler(),
    TrueFalseHandler.type_value: TrueFalseHandler(),
    ShortAnswerHandler.type_value: ShortAnswerHandler(),
    FillBlankHandler.type_value: FillBlankHandler(),
}

DEFAULT = SingleChoiceHandler.type_value


def get_handler(question_type: str) -> QuestionHandler:
    """Return the handler for `question_type`.

    Falls back to the single-choice handler for an unknown or missing value so
    that a row written by a future version of the app can still be displayed
    rather than raising deep inside a template.
    """
    return REGISTRY.get(question_type, REGISTRY[DEFAULT])


def all_types() -> list:
    """Persisted values, in declaration order."""
    return list(REGISTRY)


def all_handlers() -> list:
    return list(REGISTRY.values())


def choice_based_types() -> set:
    """Types whose answer is picked from an option list in the edit screen."""
    return {h.type_value for h in REGISTRY.values() if h.is_choice_based}


def label_for(question_type: str) -> str:
    """Human label for a persisted value.

    A module-level function rather than a method on the enum: Django's
    TextChoices metaclass drops extra classmethods from the resulting enum.
    """
    handler = REGISTRY.get(question_type)
    return handler.label if handler else question_type


def labels() -> list:
    """`(value, label)` pairs for form choices and `<select>` tags."""
    return [(h.type_value, h.label) for h in REGISTRY.values()]


__all__ = [
    "AnswerView",
    "ChoiceOption",
    "DEFAULT",
    "DescribesAnswer",
    "Difficulty",
    "GradesAnswers",
    "QuestionHandler",
    "QuestionType",
    "REGISTRY",
    "ValidatesModelOutput",
    "all_handlers",
    "all_types",
    "choice_based_types",
    "get_handler",
    "label_for",
    "labels",
]

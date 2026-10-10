"""The contract every question type implements.

Adding a sixth question type should mean adding one handler here and one entry in
the registry. It should not mean editing the grader, the PDF writer, the edit
form, the take page and the view layer. That is the whole point of this module.

Three separate narrow protocols describe what each consumer actually needs, so
the PDF writer cannot accidentally grow a dependency on model-output validation
and vice versa (interface segregation). `QuestionHandler` is the concrete base
that satisfies all three and is what the registry stores.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Optional, Protocol, Sequence, runtime_checkable

BLANK_MARKER = "___"


@dataclass(frozen=True)
class ChoiceOption:
    """One selectable option, resolved to its final display position."""

    position: int
    text: str
    is_correct: bool = False


@dataclass(frozen=True)
class AnswerView:
    """A presentation-neutral description of how a question is answered.

    Handlers describe the answer area; templates and the PDF writer each render
    it in their own medium. That keeps reportlab and template syntax out of the
    domain layer, so neither leaks into grading.
    """

    kind: str
    options: tuple = ()
    accepted: tuple = ()
    placeholder: str = ""


# --- narrow consumer contracts -------------------------------------------


@runtime_checkable
class ValidatesModelOutput(Protocol):
    """Turns one raw item from the model into Question field values."""

    def validate(self, raw: dict) -> Optional[dict]: ...


@runtime_checkable
class GradesAnswers(Protocol):
    """Compares a submitted answer against the stored key."""

    def grade(
        self, question, submission, partial_credit: bool
    ) -> tuple[bool, Decimal]: ...

    def format_answer(self, question, user_answer) -> str: ...

    def correct_texts(self, question) -> list[str]: ...


@runtime_checkable
class DescribesAnswer(Protocol):
    """Describes the answer area for a template or the PDF writer."""

    def answer_view(self, question, reveal: bool = False) -> AnswerView: ...


# --- shared implementation ------------------------------------------------


class QuestionHandler(ABC):
    """Base class for a question type.

    Subclasses declare their persisted value and behaviour; the abstract methods
    force every type to be substitutable wherever the registry hands one out.
    """

    #: The value stored in `Question.question_type`.
    type_value: ClassVar[str] = ""
    #: Human label shown in the UI.
    label: ClassVar[str] = ""
    #: How the take page should collect the answer; pairs with `input_template`.
    input_kind: ClassVar[str] = "text"
    input_template: ClassVar[str] = ""
    #: Whether a typed answer wants a textarea rather than a one-line input.
    multiline: ClassVar[bool] = True
    #: True when this type arrives as several submitted values (checkbox group)
    #: rather than one. The submit handler needs it to read the form correctly.
    expects_many: ClassVar[bool] = False
    #: True when the answer is picked from `Question.choices`.
    is_choice_based: ClassVar[bool] = False
    #: Option-count bounds enforced when validating model output.
    min_choices: ClassVar[int] = 0
    max_choices: ClassVar[int] = 0
    #: Fill-in-the-blank questions are only answerable with a visible gap.
    requires_blank_marker: ClassVar[bool] = False

    @abstractmethod
    def validate(self, raw: dict) -> Optional[dict]:
        """Return Question field values for `raw`, or None if unusable."""

    @abstractmethod
    def extract_submission(self, submitted):
        """Pull this type's answer out of a submitted form."""

    @abstractmethod
    def grade(self, question, submission, partial_credit: bool) -> tuple:
        """Return (is_correct, points_earned)."""

    @abstractmethod
    def format_answer(self, question, user_answer) -> str:
        """Render a stored answer as display text."""

    @abstractmethod
    def correct_texts(self, question) -> list:
        """Human-readable correct answers, for keys and results pages."""

    @abstractmethod
    def answer_view(self, question, reveal: bool = False) -> AnswerView:
        """Describe the answer area for a renderer."""

    def apply_edited(self, question, values: dict) -> bool:
        """Write an answer the user changed on the review screen.

        `values` carries whatever the edit form collected: `answer` and
        `alternatives` for a typed answer, `choices` and `correct` for an option
        question. Each handler reads the keys it needs, so the code that saves an
        edit never has to know how a given type stores its key.

        Returns False when the question is no longer answerable and should be
        dropped. The default here suits the typed-text types.
        """
        answer = (values.get("answer") or "").strip()
        alternatives = [
            part.strip()
            for part in (values.get("alternatives") or "").split(",")
            if part.strip()
        ]
        question.choices = []
        # One code path for grading: the key is always at position 0.
        question.correct_choices = [0] if answer else []
        question.acceptable_answers = (
            [answer, *alternatives] if answer else alternatives
        )
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.type_value}>"


def dedupe_choices(values: Sequence[str]) -> tuple:
    """Drop empty and duplicate options, keeping a position remap.

    Correct answers are stored as indexes into the option list, so removing an
    option shifts every later index. The returned mapping is what callers need in
    order to keep the key pointing at the right option: a dropped duplicate
    resolves to the surviving twin rather than to nothing.
    """
    kept, old_to_new = [], {}
    for index, value in enumerate(values):
        if not value:
            continue
        if value in kept:
            old_to_new[index] = kept.index(value)
        else:
            old_to_new[index] = len(kept)
            kept.append(value)
    return tuple(kept), old_to_new


def remap_indexes(indexes, old_to_new: dict) -> list:
    """Translate original option positions onto a de-duplicated list."""
    return sorted({old_to_new[i] for i in indexes if i in old_to_new})


def resolve_choice_selection(values: Sequence[str], ticked) -> tuple:
    """Turn submitted option strings plus ticked positions into (options, key).

    This is the single place option positions are re-keyed. Model-output
    validation, the edit screen and the new-question slots all go through it, so
    a dropped option can never leave the key pointing at a different option.
    """
    options, old_to_new = dedupe_choices(values)
    return list(options), remap_indexes(ticked, old_to_new)


def clean_text(value, limit: int = 2000) -> str:
    """Collapse whitespace and cap the length of model-supplied text."""
    if not value:
        return ""
    return " ".join(str(value).split())[:limit]


__all__ = [
    "AnswerView",
    "BLANK_MARKER",
    "ChoiceOption",
    "DescribesAnswer",
    "GradesAnswers",
    "QuestionHandler",
    "ValidatesModelOutput",
    "clean_text",
    "dedupe_choices",
    "remap_indexes",
    "resolve_choice_selection",
]

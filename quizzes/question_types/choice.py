"""Handlers for the two multiple-choice formats.

Both pick an answer from `Question.choices` and both key it by position, so the
option-list plumbing lives here once. They differ in how many keys a valid
question has and in how a partial answer is scored, which is the only reason they
are separate classes.
"""

from decimal import Decimal

from .base import (
    AnswerView,
    ChoiceOption,
    QuestionHandler,
    clean_text,
    dedupe_choices,
    remap_indexes,
)
from .kinds import QuestionType

MIN_CHOICES = 2
MAX_CHOICES = 6
PROMPT_MIN_CHARS = 8
CHOICE_MAX_CHARS = 300


class ChoiceHandler(QuestionHandler):
    """Shared behaviour for question types answered by choosing an option."""

    is_choice_based = True
    min_choices = MIN_CHOICES
    max_choices = MAX_CHOICES

    def validate(self, raw):
        if not isinstance(raw, dict):
            return None

        prompt = clean_text(raw.get("prompt"), 1000)
        if len(prompt) < PROMPT_MIN_CHARS:
            return None

        options, old_to_new = dedupe_choices(
            [clean_text(c, CHOICE_MAX_CHARS) for c in (raw.get("choices") or [])]
        )
        if not (self.min_choices <= len(options) <= self.max_choices):
            return None

        try:
            keyed = {int(i) for i in (raw.get("correct_indexes") or [])}
        except (TypeError, ValueError):
            return None

        correct = remap_indexes(keyed, old_to_new)
        if not self.is_valid_key_set(correct):
            return None

        return {
            "question_type": self.type_value,
            "prompt": prompt,
            "choices": list(options),
            "correct_choices": correct,
            "acceptable_answers": [],
            "explanation": clean_text(raw.get("explanation"), 600),
        }

    def is_valid_key_set(self, correct) -> bool:
        """Whether a question with these keys would be answerable."""
        raise NotImplementedError

    # --- shared option handling -------------------------------------------

    def _options(self, question, reveal: bool = False) -> tuple:
        correct = set(question.correct_choices or [])
        return tuple(
            ChoiceOption(
                position=index,
                text=text,
                is_correct=reveal and index in correct,
            )
            for index, text in enumerate(question.choices or [])
        )

    def _resolve(self, question, submission) -> set:
        """Turn whatever was submitted into a set of chosen positions."""
        if isinstance(submission, (list, tuple, set)):
            raw = list(submission)
        elif submission in (None, ""):
            raw = []
        else:
            raw = [submission]
        try:
            return {int(i) for i in raw}
        except (TypeError, ValueError):
            return set()

    def _score(self, question, chosen: set, partial_credit: bool):
        """Compare chosen positions with the key and return (is_correct, points)."""
        points = Decimal(question.points)
        correct = {int(i) for i in (question.correct_choices or [])}
        if not correct or not chosen:
            return False, Decimal("0")
        if chosen == correct:
            return True, points
        if partial_credit:
            # Correct picks earn, wrong picks cancel them out, so selecting every
            # option scores nothing rather than a full mark.
            net = len(chosen & correct) - len(chosen - correct)
            if net <= 0:
                return False, Decimal("0")
            return False, points * Decimal(net) / Decimal(len(correct))
        return False, Decimal("0")

    def correct_texts(self, question) -> list:
        return [
            question.choices[i]
            for i in (question.correct_choices or [])
            if 0 <= i < len(question.choices or [])
        ]

    def apply_edited(self, question, values: dict) -> bool:
        """Store the options the user kept, with the key already remapped.

        Blanking some options is fine; blanking all of them leaves a question
        that cannot be answered, so it is reported as unkeepable and the caller
        drops it.
        """
        choices = [value for value in (values.get("choices") or []) if value]
        if not choices:
            return False
        question.choices = choices
        correct = [int(i) for i in (values.get("correct") or [])]
        question.correct_choices = correct or [0]
        question.acceptable_answers = []
        return True

    def format_answer(self, question, user_answer) -> str:
        if user_answer in (None, "", []):
            return "No answer"
        chosen = sorted(self._resolve(question, user_answer))
        options = question.choices or []
        texts = [options[i] for i in chosen if 0 <= i < len(options)]
        return ", ".join(texts) if texts else "No answer"


class SingleChoiceHandler(ChoiceHandler):
    """One right answer out of several."""

    type_value = QuestionType.MCQ_SINGLE
    label = "Multiple choice (one answer)"
    input_kind = "single"
    input_template = "partials/answer_single.html"

    def is_valid_key_set(self, correct) -> bool:
        return len(correct) == 1

    def extract_submission(self, submitted):
        try:
            return int(submitted)
        except (TypeError, ValueError):
            return ""

    def grade(self, question, submission, partial_credit: bool = False):
        return self._score(question, self._resolve(question, submission), False)

    def answer_view(self, question, reveal: bool = False) -> AnswerView:
        return AnswerView(kind="single", options=self._options(question, reveal))


class MultiChoiceHandler(ChoiceHandler):
    """Several right answers, optionally earning partial credit."""

    type_value = QuestionType.MCQ_MULTI
    label = "Multiple answer"
    input_kind = "multi"
    input_template = "partials/answer_multi.html"
    # A checkbox group, so the browser posts a list rather than one value.
    expects_many = True

    def is_valid_key_set(self, correct) -> bool:
        return len(correct) >= 2

    def extract_submission(self, submitted):
        if isinstance(submitted, (list, tuple)):
            values = submitted
        elif submitted in (None, ""):
            values = []
        else:
            values = [submitted]
        chosen = []
        for value in values:
            try:
                chosen.append(int(value))
            except (TypeError, ValueError):
                continue
        return chosen
    def grade(self, question, submission, partial_credit: bool = False):
        return self._score(question, self._resolve(question, submission), partial_credit)

    def answer_view(self, question, reveal: bool = False) -> AnswerView:
        return AnswerView(kind="multi", options=self._options(question, reveal))

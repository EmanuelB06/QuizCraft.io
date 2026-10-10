"""Handlers for true/false and the two typed-text formats.

True/false is kept apart from short answer even though both compare text,
because its stored key is an option list and its submitted value is a word, which
changes extraction and rendering.
"""

from decimal import Decimal

from .base import AnswerView, ChoiceOption, QuestionHandler, BLANK_MARKER, clean_text
from .kinds import QuestionType
from .normalize import normalize, to_bool

PROMPT_MIN_CHARS = 8
ANSWER_MAX_CHARS = 300
EXPLANATION_MAX_CHARS = 600


class TextHandler(QuestionHandler):
    """Shared behaviour for question types answered by typing.

    Grading is deliberately forgiving: exact match after normalization, or the
    accepted answer appearing inside a longer reply, which is what a student
    writes when they explain themselves.
    """

    input_kind = "text"
    input_template = "partials/answer_text.html"
    placeholder = "Your answer"

    def validate(self, raw):
        if not isinstance(raw, dict):
            return None

        prompt = clean_text(raw.get("prompt"), 1000)
        if len(prompt) < PROMPT_MIN_CHARS:
            return None

        answer = clean_text(raw.get("correct_answer"), ANSWER_MAX_CHARS)
        if not answer:
            return None
        if self.requires_blank_marker and BLANK_MARKER not in prompt:
            return None

        alternatives = [
            clean_text(a, ANSWER_MAX_CHARS)
            for a in (raw.get("acceptable_answers") or [])
            if clean_text(a, ANSWER_MAX_CHARS)
        ]

        return {
            "question_type": self.type_value,
            "prompt": prompt,
            "choices": [],
            # One code path for grading: the key is always at position 0.
            "correct_choices": [0],
            "acceptable_answers": [answer]
            + [a for a in alternatives if normalize(a) != normalize(answer)],
            "explanation": clean_text(raw.get("explanation"), EXPLANATION_MAX_CHARS),
        }

    def extract_submission(self, submitted):
        return (submitted or "").strip() if isinstance(submitted, str) else submitted

    def grade(self, question, submission, partial_credit: bool = False):
        points = Decimal(question.points)
        given = normalize(self.extract_submission(submission))
        if not given:
            return False, Decimal("0")

        accepted = [a for a in (question.acceptable_answers or []) if normalize(a)]
        if not accepted:
            return False, Decimal("0")
        if any(given == normalize(a) for a in accepted):
            return True, points
        if any(normalize(a) in given for a in accepted):
            return True, points
        return False, Decimal("0")

    def correct_texts(self, question) -> list:
        return [str(question.acceptable_answers[0])] if question.acceptable_answers else [""]

    def format_answer(self, question, user_answer) -> str:
        if user_answer in (None, "", []):
            return "No answer"
        return str(user_answer)

    def answer_view(self, question, reveal: bool = False) -> AnswerView:
        accepted = tuple(question.acceptable_answers or ())
        return AnswerView(
            kind="text",
            accepted=accepted,
            placeholder=", ".join(accepted) if reveal else self.placeholder,
        )


class ShortAnswerHandler(TextHandler):
    type_value = QuestionType.SHORT_ANSWER
    label = "Short answer"


class FillBlankHandler(TextHandler):
    type_value = QuestionType.FILL_BLANK
    label = "Fill in the blank"
    placeholder = "Type the missing words"
    # A blank question with no visible gap cannot be answered, so the model
    # output is rejected rather than saved as a broken question.
    requires_blank_marker = True
    # The answer is one term, not a sentence, so a one-line input is enough.
    multiline = False


class TrueFalseHandler(QuestionHandler):
    """Answered with a word, but keyed as the option list ['True', 'False'].

    `is_choice_based` stays False: that flag means "the edit screen offers
    option boxes", and true/false is authored as a text answer here. This
    handler owns its fixed option list instead of borrowing the MCQ one.
    """

    type_value = QuestionType.TRUE_FALSE
    label = "True / False"
    input_kind = "boolean"
    input_template = "partials/answer_boolean.html"
    is_choice_based = False

    CHOICES = ("True", "False")

    def validate(self, raw):
        if not isinstance(raw, dict):
            return None

        prompt = clean_text(raw.get("prompt"), 1000)
        if len(prompt) < PROMPT_MIN_CHARS:
            return None

        answer = clean_text(raw.get("correct_answer"), ANSWER_MAX_CHARS).lower()
        if answer not in {"true", "false"}:
            return None

        return {
            "question_type": self.type_value,
            "prompt": prompt,
            "choices": list(self.CHOICES),
            "correct_choices": [0 if answer == "true" else 1],
            "acceptable_answers": [answer],
            "explanation": clean_text(raw.get("explanation"), EXPLANATION_MAX_CHARS),
        }

    def extract_submission(self, submitted):
        return submitted if isinstance(submitted, str) else ""

    def grade(self, question, submission, partial_credit: bool = False):
        points = Decimal(question.points)
        given = to_bool(self.extract_submission(submission))
        expected = to_bool(self.correct_texts(question)[0] if question.acceptable_answers else "")
        if given is not None and expected is not None and given == expected:
            return True, points
        return False, Decimal("0")

    def correct_texts(self, question) -> list:
        values = question.acceptable_answers or []
        return [str(values[0])] if values else [""]

    def format_answer(self, question, user_answer) -> str:
        if user_answer in (None, "", []):
            return "No answer"
        parsed = to_bool(user_answer)
        if parsed is True:
            return "True"
        if parsed is False:
            return "False"
        return str(user_answer)

    def answer_view(self, question, reveal: bool = False) -> AnswerView:
        correct = set(question.correct_choices or [])
        options = tuple(
            ChoiceOption(
                position=index,
                text=text,
                is_correct=reveal and index in correct,
            )
            for index, text in enumerate(self.CHOICES)
        )
        return AnswerView(kind="boolean", options=options)

    def apply_edited(self, question, values: dict) -> bool:
        """Normalise the typed answer onto the fixed True/False option pair.

        The edit screen offers a text box, but the stored key is positional, so
        anything that is not recognisably true or false falls back to `true`
        rather than producing a question with no correct option.
        """
        answer = (values.get("answer") or "").strip().lower()
        if answer not in {"true", "false"}:
            answer = "true"
        question.acceptable_answers = [answer]
        question.choices = list(self.CHOICES)
        question.correct_choices = [0 if answer == "true" else 1]
        return True

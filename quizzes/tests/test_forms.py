"""Validation-only tests for the quiz creation form.

No database is touched here: `QuizUploadForm` decides what a well-formed
submission looks like and writes nothing, so `SimpleTestCase` keeps these
milliseconds rather than seconds of password hashing.
"""

from django.test import SimpleTestCase

from quizzes.forms.upload import QuizUploadForm
from quizzes.question_types import all_types

from .factories import make_text_file


def upload_data(**overrides):
    data = {
        "title": "",
        "question_count": 10,
        "difficulty": "medium",
        "question_types": list(all_types()),
        "partial_credit": "",
    }
    data.update(overrides)
    return data


class QuizUploadFormTests(SimpleTestCase):
    def _form(self, **overrides):
        return QuizUploadForm(upload_data(**overrides), files={"document": make_text_file()})

    # --- partial credit ---------------------------------------------------

    def test_partial_credit_is_kept_with_a_multiple_answer_type(self):
        form = self._form(partial_credit="on", question_types=["mcq_single", "mcq_multi"])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["partial_credit"])

    def test_partial_credit_is_dropped_without_a_multiple_answer_type(self):
        """A no-op setting is dropped rather than stored on the quiz.

        Partial credit is only read when grading `mcq_multi` questions; if the
        user checked the box but picked no multi-answer type, keeping the flag
        would persist a setting that can never fire.
        """
        form = self._form(
            partial_credit="on", question_types=["mcq_single", "short_answer"]
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["partial_credit"])

    def test_partial_credit_is_off_when_unchecked(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data["partial_credit"])

    # --- question types ---------------------------------------------------

    def test_question_types_are_stored_in_registry_order(self):
        """The stored list must not depend on the order boxes were posted in."""
        form = self._form(question_types=["short_answer", "mcq_single"])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["question_types"], ["mcq_single", "short_answer"]
        )

    def test_at_least_one_type_is_still_required(self):
        form = self._form(question_types=[])
        self.assertFalse(form.is_valid())
        self.assertIn("question_types", form.errors)

    # --- title ------------------------------------------------------------

    def test_title_whitespace_is_collapsed(self):
        form = self._form(title="  Mitochondria   Mastery \n 201  ")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "Mitochondria Mastery 201")

    def test_control_characters_are_stripped_from_the_title(self):
        """A crafted POST can put anything in a text input, not just text."""
        form = self._form(title="Cell\x07 Biology\x1f")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "Cell Biology")

    def test_whitespace_only_title_is_treated_as_empty(self):
        """Empty means "fall back to the file name", not a title of spaces."""
        form = self._form(title="   ")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "")

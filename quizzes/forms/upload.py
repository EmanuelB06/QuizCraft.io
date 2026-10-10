"""Upload intake: validate the file and the options a user picked."""

import os

from django import forms
from django.conf import settings

from ..models import Difficulty
from ..question_types import labels as type_labels
from ..services.extraction import SUPPORTED_EXTENSIONS

#: Bounds for how many questions one quiz may hold. Used by the form and the UI.
MIN_QUIZ_QUESTIONS = 3
MAX_QUIZ_QUESTIONS = 40

DEFAULT_QUIZ_QUESTIONS = 10


class QuizUploadForm(forms.Form):
    document = forms.FileField(
        label="Source document",
        help_text=(
            "PDF, Word (.docx), PowerPoint (.pptx) or plain text. Up to {size} MB."
        ).format(size=settings.MAX_UPLOAD_MB),
    )
    title = forms.CharField(
        max_length=200, required=False, help_text="Defaults to the file name."
    )
    question_count = forms.IntegerField(
        min_value=MIN_QUIZ_QUESTIONS,
        max_value=MAX_QUIZ_QUESTIONS,
        initial=DEFAULT_QUIZ_QUESTIONS,
        help_text=(
            f"Between {MIN_QUIZ_QUESTIONS} and {MAX_QUIZ_QUESTIONS}."
        ),
    )
    difficulty = forms.ChoiceField(choices=Difficulty.choices, initial=Difficulty.MEDIUM)
    question_types = forms.MultipleChoiceField(
        choices=type_labels(),
        widget=forms.CheckboxSelectMultiple,
        required=False,  # validated in clean_question_types for a better message
        help_text="Pick the formats you want in the quiz.",
    )
    partial_credit = forms.BooleanField(
        required=False,
        initial=False,
        help_text=(
            "For multiple-answer questions, give partial credit for a "
            "partially correct set."
        ),
    )

    def clean_document(self):
        upload = self.cleaned_data["document"]
        # The name decides, not the browser's content_type, which a user can set
        # to anything. The list is the extractor's, so the form cannot advertise
        # a format the pipeline would then refuse.
        extension = os.path.splitext(upload.name)[1].lower()
        if extension not in SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise forms.ValidationError(
                f"Unsupported file type '{extension or upload.name}'. Allowed: {allowed}."
            )
        limit = settings.MAX_UPLOAD_MB * 1024 * 1024
        if upload.size > limit:
            raise forms.ValidationError(
                f"That file is {upload.size / 1024 / 1024:.1f} MB. "
                f"The limit is {settings.MAX_UPLOAD_MB} MB."
            )
        if upload.size == 0:
            raise forms.ValidationError("That file is empty.")
        return upload

    def clean_question_types(self):
        types = self.cleaned_data["question_types"]
        if not types:
            raise forms.ValidationError("Choose at least one question type.")
        return types


__all__ = [
    "DEFAULT_QUIZ_QUESTIONS",
    "MAX_QUIZ_QUESTIONS",
    "MIN_QUIZ_QUESTIONS",
    "QuizUploadForm",
]

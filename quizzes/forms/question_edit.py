"""Question editing: validation only.

This module decides whether a submitted edit is well formed. Deciding what to do
with it -- deleting, re-keying, creating -- is `services.editing`, because a form
that also writes rows is two responsibilities in one class.
"""

from django import forms

from ..question_types import labels as type_labels
from ..question_types.base import resolve_choice_selection

#: Hand-written question slots offered on the edit screen.
NEW_SLOTS = 2
#: Option inputs per hand-written slot.
NEW_CHOICES = 4

CONTROL_CLASS = "form-control"
SELECT_CLASS = "form-select"


def _input(label="", *, required=False, **attrs):
    return forms.CharField(
        required=required,
        label=label,
        widget=forms.TextInput(attrs={"class": CONTROL_CLASS, **attrs}),
    )


def _area(label="", *, required=False, **attrs):
    return forms.CharField(
        required=required,
        label=label,
        widget=forms.Textarea(attrs={"rows": 2, "class": CONTROL_CLASS, **attrs}),
    )


class QuestionEditForm(forms.Form):
    """One form covering every question on the edit screen, in a single POST.

    Field names keep the `q<uuid hex>_<field>` and `new<slot>_<field>` shape the
    templates and tests post, so the HTTP contract is unchanged.
    """

    new_slots = NEW_SLOTS
    new_choices = NEW_CHOICES

    def __init__(self, *args, questions=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = list(questions)
        for question in self.questions:
            self._add_question_fields(question)
        for slot in range(1, self.new_slots + 1):
            self._add_new_slot_fields(slot)

    # --- field construction ----------------------------------------------

    def _add_question_fields(self, question) -> None:
        prefix = question_prefix(question)
        self.fields[f"{prefix}_delete"] = forms.BooleanField(
            required=False, label="Remove this question"
        )
        self.fields[f"{prefix}_prompt"] = forms.CharField(
            required=True,
            label="Question",
            widget=forms.Textarea(attrs={"rows": 2, "class": CONTROL_CLASS}),
        )
        self.fields[f"{prefix}_type"] = forms.ChoiceField(
            choices=type_labels(), widget=forms.Select(attrs={"class": SELECT_CLASS})
        )
        self.fields[f"{prefix}_points"] = forms.IntegerField(
            min_value=1,
            max_value=100,
            widget=forms.NumberInput(attrs={"class": CONTROL_CLASS}),
        )
        self.fields[f"{prefix}_explanation"] = _area("Explanation")

        if question.is_choice_based:
            count = max(2, len(question.choices))
            for position in range(count):
                self.fields[f"{prefix}_choice_{position}"] = _input()
            self.fields[f"{prefix}_correct"] = forms.MultipleChoiceField(
                required=False,
                choices=[(str(i), f"Choice {i + 1}") for i in range(count)],
                widget=forms.CheckboxSelectMultiple,
                label="Correct",
            )
        else:
            self.fields[f"{prefix}_answer"] = _input("Correct answer")
            self.fields[f"{prefix}_alternatives"] = _input("Also accept (comma separated)")

    def _add_new_slot_fields(self, slot: int) -> None:
        prefix = f"new{slot}"
        self.fields[f"{prefix}_prompt"] = _area(placeholder="Type the question")
        for index in range(self.new_choices):
            self.fields[f"{prefix}_choice_{index}"] = _input()
        self.fields[f"{prefix}_correct"] = forms.MultipleChoiceField(
            required=False,
            choices=[(str(i), str(i + 1)) for i in range(self.new_choices)],
        )

    # --- accessors used by the editing service ----------------------------

    def question_prefix(self, question) -> str:
        return question_prefix(question)

    def raw_choices(self, question) -> list:
        """The submitted option strings, in the order the boxes were rendered."""
        prefix = self.question_prefix(question)
        raw, position = [], 0
        while f"{prefix}_choice_{position}" in self.fields:
            raw.append((self.cleaned_data.get(f"{prefix}_choice_{position}") or "").strip())
            position += 1
        return raw

    def ticked_indexes(self, prefix: str) -> set:
        """Which option positions the user marked correct."""
        return {int(i) for i in (self.cleaned_data.get(f"{prefix}_correct") or [])}

    def was_deleted(self, question) -> bool:
        return bool(self.cleaned_data.get(f"{self.question_prefix(question)}_delete"))

    def edited_values(self, question) -> dict:
        """Everything the review screen said about one question.

        The form is responsible for turning field names into values -- including
        re-keying options through `resolve_choice_selection` -- so the code that
        saves an edit only has to copy fields onto the model.
        """
        prefix = self.question_prefix(question)
        data = self.cleaned_data
        values = {
            "prompt": (data.get(f"{prefix}_prompt") or "").strip(),
            "question_type": data.get(f"{prefix}_type"),
            "points": data.get(f"{prefix}_points"),
            "explanation": (data.get(f"{prefix}_explanation") or "").strip(),
        }
        if question.is_choice_based:
            values["choices"], values["correct"] = resolve_choice_selection(
                self.raw_choices(question), self.ticked_indexes(prefix)
            )
        else:
            values["answer"] = (data.get(f"{prefix}_answer") or "").strip()
            values["alternatives"] = data.get(f"{prefix}_alternatives") or ""
        return values

    def new_slot_values(self, slot: int) -> dict:
        """Prompt, resolved options and key for one hand-written slot."""
        prefix = f"new{slot}"
        prompt = (self.cleaned_data.get(f"{prefix}_prompt") or "").strip()
        if not prompt:
            return {}
        raw = [
            (self.cleaned_data.get(f"{prefix}_choice_{index}") or "").strip()
            for index in range(self.new_choices)
        ]
        choices, correct = resolve_choice_selection(raw, self.ticked_indexes(prefix))
        return {"prompt": prompt, "choices": choices, "correct_choices": correct}

    def non_empty_new_slots(self):
        return [
            values
            for slot in range(1, self.new_slots + 1)
            if (values := self.new_slot_values(slot))
        ]


def question_prefix(question) -> str:
    """The POST field-name prefix for one question.

    Centralised because the view, the templates and the service all have to
    agree on it; a UUID hex with dashes would be awkward in an HTML name.
    """
    return f"q{question.pk.hex}"


__all__ = [
    "NEW_CHOICES",
    "NEW_SLOTS",
    "QuestionEditForm",
    "question_prefix",
]

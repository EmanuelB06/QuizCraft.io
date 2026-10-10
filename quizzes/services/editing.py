"""Persist an edit made on the review screen.

Separate from `forms.question_edit` because validating a submission and writing
rows are different jobs.

The emptiness rule -- a quiz must keep at least one question -- is checked
against a plan built before anything is written. Deleting a row sets its primary
key to ``None`` on the in-memory instance and a rollback does not undo that, so
refusing *after* deleting would leave the re-rendered edit page unable to name
its own fields.
"""

from django.db import transaction

from ..models import Question, QuestionType


class NoQuestionsLeft(ValueError):
    """The edit would have removed every question from the quiz."""


def apply_question_edits(quiz, form) -> int:
    """Save every question in `form` and return the resulting count.

    Raises NoQuestionsLeft if nothing would remain; nothing is written in that
    case.
    """
    with transaction.atomic():
        updates, deletions, additions = _plan(form)
        if not updates and not additions:
            raise NoQuestionsLeft("A quiz needs at least one question.")

        for question in updates:
            question.save()
        for question in deletions:
            question.delete()
        _create(quiz, additions)

        total = quiz.question_set.count()
        quiz.question_count = total
        quiz.save(update_fields=["question_count", "updated_at"])
    return total


def _plan(form):
    """Work out what the edit would do, without touching the database.

    Mutating a question in memory is safe here: if the plan turns out to be
    empty nothing has been written and the instance is discarded.
    """
    updates, deletions = [], []
    for question in form.questions:
        if form.was_deleted(question):
            deletions.append(question)
            continue
        values = form.edited_values(question)
        question.prompt = values["prompt"]
        question.question_type = values["question_type"]
        question.points = values["points"]
        question.explanation = values["explanation"]
        if question.handler.apply_edited(question, values):
            updates.append(question)
        else:
            # No options left, so the question could not be answered.
            deletions.append(question)
    return updates, deletions, list(form.non_empty_new_slots())


def _create(quiz, additions) -> None:
    """Turn the hand-written slots into questions, skipping unusable ones."""
    for values in additions:
        if len(values["choices"]) < 2:
            # Not enough options to be a multiple-choice question.
            continue
        Question.objects.create(
            quiz=quiz,
            order=quiz.question_set.count() + 1,
            question_type=QuestionType.MCQ_SINGLE,
            prompt=values["prompt"],
            choices=values["choices"],
            correct_choices=values["correct_choices"] or [0],
            acceptable_answers=[],
            explanation="",
            points=1,
        )


__all__ = ["NoQuestionsLeft", "apply_question_edits"]

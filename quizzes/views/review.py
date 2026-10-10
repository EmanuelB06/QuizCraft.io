"""Reviewing, taking and exporting a finished quiz.

These views are deliberately thin: the edit screen delegates the save to
`services.editing`, the take screen to `services.attempts`, and the export to
`services.export`. What is left here is the HTTP concern -- ownership checks,
messages, redirects.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from ..forms.question_edit import QuestionEditForm
from ..models import Quiz
from ..question_types import labels as type_labels
from ..services import export
from ..services.attempts import score_attempt
from ..services.editing import apply_question_edits
from ..services.repository import get_repository


def _ready_quiz(request, quiz_id) -> Quiz:
    """Fetch a quiz the user owns and is allowed to work with.

    A quiz that is not ready yet is not an error, so the caller sends the user
    back to the progress page.
    """
    quiz = get_repository().get_quiz(request.user, quiz_id)
    if quiz is None:
        raise Http404("No Quiz matches the given query.")
    return quiz


@login_required
def quiz_edit(request, quiz_id):
    quiz = _ready_quiz(request, quiz_id)
    if not quiz.is_ready:
        if quiz.status == Quiz.Status.FAILED and quiz.error_message:
            messages.error(request, quiz.error_message)
        return redirect("quiz_progress", quiz_id=quiz.pk)

    questions = get_repository().list_questions(quiz)

    if request.method == "POST":
        form = QuestionEditForm(request.POST, questions=questions)
        if form.is_valid():
            try:
                apply_question_edits(quiz, form)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Questions updated.")
                return redirect("quiz_edit", quiz_id=quiz.pk)
    else:
        form = QuestionEditForm(questions=questions)

    answers_visible = request.GET.get("answers") in {"1", "true", "yes"}

    return render(
        request,
        "quiz_edit.html",
        {
            "quiz": quiz,
            "questions": questions,
            "form": form,
            "types": type_labels(),
            "answers_visible": answers_visible,
        },
    )


@login_required
def quiz_take(request, quiz_id):
    quiz = _ready_quiz(request, quiz_id)
    if not quiz.is_ready:
        messages.warning(request, "This quiz is not ready yet.")
        return redirect("quiz_progress", quiz_id=quiz.pk)

    questions = get_repository().list_questions(quiz)

    if request.method == "POST":
        attempt = score_attempt(quiz, request.user, request.POST, questions)
        return redirect("quiz_results", quiz_id=quiz.pk, attempt_id=attempt.pk)

    return render(
        request,
        "quiz_take.html",
        {
            "quiz": quiz,
            "questions": questions,
            "max_score": sum(q.points for q in questions),
        },
    )


@login_required
def quiz_results(request, quiz_id, attempt_id):
    repo = get_repository()
    quiz = repo.get_quiz(request.user, quiz_id)
    if quiz is None:
        raise Http404("No Quiz matches the given query.")
    attempt = repo.get_attempt(request.user, quiz, attempt_id)
    if attempt is None:
        raise Http404("No Attempt matches the given query.")
    return render(request, "quiz_results.html", {"quiz": quiz, "attempt": attempt})


@login_required
def quiz_export(request, quiz_id):
    quiz = _ready_quiz(request, quiz_id)
    get_repository().list_questions(quiz)
    reveal = request.GET.get("answers") in {"1", "true", "yes"}

    try:
        pdf_bytes = export.build_quiz_pdf(quiz, reveal_answers=reveal)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("quiz_edit", quiz_id=quiz.pk)

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{export.export_filename(quiz, reveal)}"'
    )
    return response

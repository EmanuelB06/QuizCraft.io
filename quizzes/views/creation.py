"""Getting a document in and watching it turn into a quiz."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ..forms.upload import QuizUploadForm
from ..models import Quiz
from ..question_types import labels as type_labels
from ..services.extraction import ExtractionError, extract_text
from ..services.generation import start_background_generation
from ..services.repository import get_repository


@login_required
def upload(request):
    if not settings.GEMINI_API_KEY:
        messages.warning(
            request,
            "No AI provider is configured. Add GEMINI_API_KEY to your .env file "
            "before generating a quiz.",
        )

    if request.method == "POST":
        form = QuizUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                quiz = _prepare_quiz(request.user, form.cleaned_data)
            except ExtractionError as exc:
                form.add_error("document", str(exc))
            else:
                start_background_generation(quiz)
                return redirect("quiz_progress", quiz_id=quiz.pk)
    else:
        form = QuizUploadForm(initial={"question_types": [v for v, _ in type_labels()]})

    return render(request, "upload.html", {"form": form, "types": type_labels()})


def _prepare_quiz(user, data):
    """Save the upload, extract its text, and create a pending quiz.

    Raises ExtractionError if the file cannot be read.
    """
    repo = get_repository()
    upload = data["document"]
    document = repo.create_document(
        user, upload, upload.name, upload.size
    )
    try:
        text, pages = extract_text(document.file.path, document.original_filename)
    except Exception:
        # Do not keep an orphan file behind when extraction fails.
        document.file.delete(save=False)
        repo.delete_document(document)
        raise

    if settings.DISCARD_SOURCE_UPLOADS:
        # The source is only ever read for extraction; drop the bytes so no
        # upload is persisted. The record stores an empty `file` path, which
        # hydration already handles.
        document.file.delete(save=False)
        document.file = ""

    repo.save_document_extraction(document, text, len(text), pages)

    return repo.create_quiz(
        user,
        document,
        title=_default_title(document, data.get("title")),
        question_count=data["question_count"],
        difficulty=data["difficulty"],
        question_types=data["question_types"],
        partial_credit=data.get("partial_credit", False),
        status=Quiz.Status.PENDING,
    )


def _default_title(document, given) -> str:
    title = (given or "").strip()
    if title:
        return title[:255]
    stem = document.original_filename.rsplit(".", 1)[0]
    return f"{slugify(stem) or 'quiz'} quiz"[:255]


@login_required
def quiz_progress(request, quiz_id):
    """Page that polls the status endpoint while generation runs."""
    quiz = _owned_quiz(request, quiz_id)
    if quiz.status == Quiz.Status.READY:
        return redirect("quiz_edit", quiz_id=quiz.pk)
    return render(
        request,
        "quiz_progress.html",
        {"quiz": quiz, "status_url": reverse("quiz_status", args=[quiz.pk])},
    )


@login_required
def quiz_status(request, quiz_id):
    """JSON endpoint polled by the progress page."""
    quiz = _owned_quiz(request, quiz_id)
    ready = quiz.status == Quiz.Status.READY
    return JsonResponse(
        {
            "status": quiz.status,
            "done": quiz.progress_done,
            "total": quiz.progress_total,
            "percentage": quiz.percentage,
            "error": quiz.error_message,
            "redirect": reverse("quiz_edit", args=[quiz.pk]) if ready else None,
        }
    )


@login_required
@require_POST
def quiz_delete(request, quiz_id):
    quiz = _owned_quiz(request, quiz_id)
    document = quiz.document
    repo = get_repository()
    repo.delete_quiz(quiz)
    repo.delete_document(document)
    if document.file:
        document.file.delete(save=False)
    messages.success(request, "Quiz deleted.")
    return redirect("dashboard")


def _owned_quiz(request, quiz_id):
    """Fetch a quiz the request user owns, or raise 404.

    The user scope lives in the repository lookup, so a quiz belonging to
    someone else is indistinguishable from one that does not exist.
    """
    quiz = get_repository().get_quiz(request.user, quiz_id)
    if quiz is None:
        raise Http404("No Quiz matches the given query.")
    return quiz

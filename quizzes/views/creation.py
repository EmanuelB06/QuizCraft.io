"""Getting a document in and watching it turn into a quiz."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from ..forms.upload import QuizUploadForm
from ..models import Quiz, SourceDocument
from ..question_types import labels as type_labels
from ..services.extraction import ExtractionError, extract_text
from ..services.generation import start_background_generation


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
    document = _store_document(user, data["document"])
    try:
        text, pages = extract_text(document.file.path, document.original_filename)
    except Exception:
        # Do not keep an orphan file behind when extraction fails.
        document.file.delete(save=False)
        document.delete()
        raise

    document.extracted_text = text
    document.char_count = len(text)
    document.page_count = pages
    document.save(update_fields=["extracted_text", "char_count", "page_count"])

    return Quiz.objects.create(
        user=user,
        document=document,
        title=_default_title(document, data.get("title")),
        question_count=data["question_count"],
        difficulty=data["difficulty"],
        question_types=data["question_types"],
        partial_credit=data.get("partial_credit", False),
        status=Quiz.Status.PENDING,
    )


def _store_document(user, upload):
    return SourceDocument.objects.create(
        user=user, file=upload, original_filename=upload.name, file_size=upload.size
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
    quiz = get_object_or_404(
        Quiz.objects.select_related("document"), pk=quiz_id, user=request.user
    )
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
    quiz = get_object_or_404(Quiz, pk=quiz_id, user=request.user)
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
    quiz = get_object_or_404(Quiz, pk=quiz_id, user=request.user)
    document = quiz.document
    quiz.delete()
    document.delete()
    messages.success(request, "Quiz deleted.")
    return redirect("dashboard")

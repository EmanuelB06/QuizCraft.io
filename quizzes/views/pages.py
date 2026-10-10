"""Static and account pages."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render

from ..forms.auth import SignupForm
from ..models import Quiz
from ..question_types import all_handlers
from ..services.repository import get_repository

#: The four steps of the walkthrough, as (title, body).
STEPS = [
    (
        "Upload your material",
        "A PDF, Word document, slide deck or text file. QuizCraft pulls the text out "
        "of it for you.",
    ),
    (
        "Set the shape of the quiz",
        "Choose how many questions, how hard they should be, and which formats to use.",
    ),
    (
        "Review and edit",
        "Reword a question, fix an answer key, add your own, or delete the ones that "
        "missed the point.",
    ),
    (
        "Take it or print it",
        "Run the quiz online in the browser, or export a printable PDF with the key.",
    ),
]

#: Feature cards, as (icon, title, body, chips). The icon is a Bootstrap Icons
#: class name and the chips are the small labels under the body; most cards have
#: none. The question-format card reads its chips from the handler registry
#: rather than spelling the formats out, so the advertised list cannot drift
#: away from what the app can actually grade.
CAPABILITIES = [
    (
        "bi-file-earmark-arrow-up",
        "Reads the file for you",
        "PDF, Word, PowerPoint, plain text or Markdown. Word and slide tables come "
        "through too, and repeated headers, footers and slide numbers are dropped so "
        "they never become questions.",
        (),
    ),
    (
        "bi-list-check",
        "Every question format",
        "Mix as many of these into one quiz. This list comes from the same registry "
        "that grades the answers.",
        tuple(handler.label for handler in all_handlers()),
    ),
    (
        "bi-pencil-square",
        "Edit before you share",
        "Every question, answer key and explanation is editable. Treat the first pass "
        "as a draft and fix what the model got wrong.",
        (),
    ),
    (
        "bi-display",
        "Take it in the browser",
        "Students answer one question at a time, then get their score with an "
        "explanation for anything they missed.",
        (),
    ),
    (
        "bi-file-earmark-pdf",
        "Printable PDF export",
        "A clean paper copy, with or without the answer key, for rooms with no wifi.",
        (),
    ),
    (
        "bi-sliders",
        "Partial credit",
        "On multiple-answer questions, credit each correct option instead of "
        "demanding the whole set. You choose this per quiz.",
        (),
    ),
]

#: The FAQ, as (question, answer). The answers stay inside what the code really
#: does -- see the scan/OCR and file-type limits in services/extraction.py and
#: the question-count bounds in forms/upload.py.
FAQ = [
    (
        "Is it free?",
        "There is no paid tier to upgrade to and no trial clock. Make an account and "
        "generate as many quizzes as you like.",
    ),
    (
        "Which files can I upload?",
        "PDF, Word (.docx), PowerPoint (.pptx), plain text and Markdown, up to the size "
        "limit shown on the upload form. A password-protected PDF gets a clear message "
        "rather than a silent failure.",
    ),
    (
        "What about a scanned PDF?",
        "Scans and image-only PDFs have no text layer to read, so they are turned "
        "away with an explanation instead of a bad quiz. There is no OCR yet, so "
        "export a text-based PDF or upload a .docx or a .txt. A deck of image-only "
        "slides gets the same message.",
    ),
    (
        "How many questions can a quiz have?",
        "Between 3 and 40, ten by default. You pick the count and the difficulty up "
        "front and the model sticks to both.",
    ),
    (
        "How long does it take?",
        "Long documents are split into chunks and sent a few at a time to stay inside "
        "the free tier's rate limit, so a typical document takes a minute or two. The "
        "progress page keeps counting while it works.",
    ),
    (
        "The model got a question wrong. Now what?",
        "Edit it. Nothing is locked in once a quiz exists, which is the whole point of "
        "the review step.",
    ),
]


def landing(request):
    return render(
        request,
        "landing.html",
        {
            "steps": STEPS,
            "capabilities": CAPABILITIES,
            "faq": FAQ,
        },
    )


def health(request):
    """Login-free liveness probe for the hosting platform's health check."""
    return JsonResponse({"status": "ok"})


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f"Welcome, {user.username}.")
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


def _quizzes_for(user, status=None):
    return get_repository().list_quizzes(user, status=status)


def _stats_for(user) -> dict:
    """The numbers on the dashboard tiles."""
    return get_repository().dashboard_stats(user)


@login_required
def dashboard(request):
    selected_status = request.GET.get("status") or ""
    valid_status = (
        selected_status if selected_status in dict(Quiz.Status.choices) else None
    )
    return render(
        request,
        "dashboard.html",
        {
            "quizzes": _quizzes_for(request.user, status=valid_status),
            "stats": _stats_for(request.user),
            "statuses": Quiz.Status.choices,
            "selected_status": selected_status,
        },
    )

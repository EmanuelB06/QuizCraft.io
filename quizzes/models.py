import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from .question_types import Difficulty, QuestionType, get_handler, label_for

__all__ = [
    "Attempt",
    "Difficulty",
    "Question",
    "QuestionType",
    "Response",
    "Quiz",
    "SourceDocument",
    "get_handler",
    "label_for",
]


def upload_path(instance, filename):
    return f"documents/{instance.user_id}/{uuid.uuid4().hex}{filename[filename.rfind("."):]}"


class SourceDocument(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents"
    )
    file = models.FileField(upload_to=upload_path)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    char_count = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(default=0)
    extracted_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_filename

    @property
    def extension(self):
        return "." + self.original_filename.rsplit(".", 1)[-1].lower() if "." in self.original_filename else ""


class Quiz(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Waiting to start"
        PROCESSING = "processing", "Generating questions"
        READY = "ready", "Ready"
        FAILED = "failed", "Generation failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quizzes"
    )
    document = models.ForeignKey(
        SourceDocument, on_delete=models.CASCADE, related_name="quizzes"
    )
    title = models.CharField(max_length=255)
    question_count = models.PositiveSmallIntegerField(default=10)
    difficulty = models.CharField(
        max_length=10, choices=Difficulty.choices, default=Difficulty.MEDIUM
    )
    question_types = models.JSONField(default=list)
    partial_credit = models.BooleanField(default=False)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True)
    progress_done = models.PositiveSmallIntegerField(default=0)
    progress_total = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        # The edit screen is the canonical landing spot for a quiz; it redirects
        # to the progress page itself when the quiz is not ready yet.
        return reverse("quiz_edit", args=[self.pk])

    @property
    def questions(self):
        # Question.Meta.ordering already sorts by `order`.
        return self.question_set.all()

    @property
    def is_ready(self):
        return self.status == self.Status.READY and self.question_set.exists()

    @property
    def percentage(self):
        total = self.progress_total or 0
        return int(round(self.progress_done / total * 100)) if total else 0


class Question(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="question_set")
    order = models.PositiveSmallIntegerField(default=0)
    question_type = models.CharField(
        max_length=20, choices=QuestionType.choices, default=QuestionType.MCQ_SINGLE
    )
    prompt = models.TextField()
    # List of option strings. Used by mcq_single and mcq_multi.
    choices = models.JSONField(default=list, blank=True)
    # List of indexes into `choices`. For true_false/short_answer/fill_blank
    # the single value 0 is used so grading has one code path.
    correct_choices = models.JSONField(default=list, blank=True)
    acceptable_answers = models.JSONField(default=list, blank=True)
    explanation = models.TextField(blank=True)
    points = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Q{self.order}: {self.prompt[:60]}"

    @property
    def handler(self):
        """The strategy that knows how this question is answered."""
        return get_handler(self.question_type)

    @property
    def type_label(self):
        return label_for(self.question_type)

    @property
    def is_choice_based(self):
        return self.handler.is_choice_based

    @property
    def is_multi(self):
        return self.handler.input_kind == "multi"

    @property
    def answer_view(self):
        """How this question is answered, described for a renderer.

        Always the student's view: templates call this with no arguments, and
        the key is not something the take page should be able to reach. The PDF
        writer asks the handler directly when it needs the revealed version.
        """
        cached = getattr(self, "_answer_view", None)
        if cached is None:
            cached = self._answer_view = self.handler.answer_view(self, reveal=False)
        return cached

    def correct_choice_texts(self):
        """Human-readable correct answers, for the answer key and results page."""
        return self.handler.correct_texts(self)


class Attempt(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts"
    )
    score = models.PositiveIntegerField(default=0)
    max_score = models.PositiveIntegerField(default=0)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.user} - {self.quiz} ({self.score}/{self.max_score})"

    @property
    def percentage(self):
        return int(round(self.score / self.max_score * 100)) if self.max_score else 0

    @property
    def correct_count(self):
        return self.response_set.filter(is_correct=True).count()


class Response(models.Model):
    attempt = models.ForeignKey(
        Attempt, on_delete=models.CASCADE, related_name="response_set"
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    # For single-answer types this is a string; for mcq_multi a list of indexes.
    user_answer = models.JSONField(default=list, blank=True)
    is_correct = models.BooleanField(default=False)
    points_earned = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"], name="unique_response_per_question"
            )
        ]

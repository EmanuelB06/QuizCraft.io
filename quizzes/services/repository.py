"""The data-access seam the pipeline talks to.

Views and services never touch the ORM directly: they ask `get_repository()`
for the store chosen by `settings.DATA_STORE` and call methods on it. Two
implementations satisfy the same interface:

* `OrmRepository` -- the original SQLite/ORM path, unchanged.
* `FirestoreRepository` -- reads and writes Cloud Firestore through
  `services.store`, and hands back Django model instances hydrated from the
  stored records so templates, the edit form and the question-type handlers
  keep working without knowing where the data came from.

Keeping the model instances as the common currency is deliberate: the handlers
read `question.choices` / `correct_choices` / `acceptable_answers`, the form
reads `question.pk.hex`, and the templates read `quiz.document` /
`question.handler`. Hydration preserves all of that.
"""

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import uuid

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from ..models import Attempt, Question, QuestionType, Quiz, Response, SourceDocument
from .store import (
    FirestoreUnavailable,
    attempt_record,
    document_record,
    question_record,
    quiz_record,
    response_record,
)


class OrmRepository:
    """The default store: exactly the queries this app always ran."""

    engine = "sqlite"

    # -- documents ---------------------------------------------------------

    def create_document(self, user, upload, original_filename, file_size):
        return SourceDocument.objects.create(
            user=user,
            file=upload,
            original_filename=original_filename,
            file_size=file_size,
        )

    def save_document_extraction(self, document, text, char_count, page_count):
        document.extracted_text = text
        document.char_count = char_count
        document.page_count = page_count
        # "file" is included so a discarded upload (name set to None by
        # document.file.delete) is persisted as empty rather than dangling.
        document.save(update_fields=["extracted_text", "char_count", "page_count", "file"])

    def delete_document(self, document):
        document.delete()

    # -- quizzes -----------------------------------------------------------

    def create_quiz(self, user, document, **fields):
        return Quiz.objects.create(user=user, document=document, **fields)

    def get_quiz(self, user, quiz_id):
        return (
            Quiz.objects.select_related("document")
            .filter(pk=quiz_id, user=user)
            .first()
        )

    def get_quiz_by_id(self, quiz_id, user_id=None):
        return Quiz.objects.select_related("document").filter(pk=quiz_id).first()

    def list_quizzes(self, user, status=None):
        quizzes = (
            Quiz.objects.filter(user=user)
            .select_related("document")
            .annotate(total_questions=Count("question_set", distinct=True))
        )
        if status:
            quizzes = quizzes.filter(status=status)
        return list(quizzes)

    def update_quiz_by_id(self, quiz_id, user_id=None, **fields):
        Quiz.objects.filter(pk=quiz_id).update(**fields)

    def mark_quiz_failed(self, quiz_id, message, user_id=None):
        Quiz.objects.filter(pk=quiz_id).update(
            status=Quiz.Status.FAILED, error_message=message
        )

    def save_quiz(self, quiz):
        quiz.save()

    def delete_quiz(self, quiz):
        quiz.delete()

    # -- questions ---------------------------------------------------------

    def list_questions(self, quiz):
        questions = list(quiz.question_set.all())
        quiz._questions_cache = questions
        return questions

    def count_questions(self, quiz):
        return quiz.question_set.count()

    def replace_questions(self, quiz, values):
        quiz.question_set.all().delete()
        Question.objects.bulk_create(
            [
                Question(quiz=quiz, order=position, **item)
                for position, item in enumerate(values, start=1)
            ]
        )

    def save_question(self, question):
        question.save()

    def create_question(self, quiz, **fields):
        return Question.objects.create(quiz=quiz, **fields)

    def delete_question(self, question):
        question.delete()

    # -- attempts ----------------------------------------------------------

    def create_attempt(self, quiz, user, max_score):
        return Attempt.objects.create(quiz=quiz, user=user, max_score=max_score)

    def save_responses(self, attempt, responses):
        for response in responses:
            response.attempt = attempt
        Response.objects.bulk_create(responses)

    def update_attempt(self, attempt, **fields):
        for name, value in fields.items():
            setattr(attempt, name, value)
        attempt.save(update_fields=list(fields))

    def get_attempt(self, user, quiz, attempt_id):
        return (
            Attempt.objects.prefetch_related("response_set__question")
            .filter(pk=attempt_id, quiz=quiz, user=user)
            .first()
        )

    def list_attempts(self, user):
        return list(Attempt.objects.filter(user=user))

    # -- stats -------------------------------------------------------------

    def dashboard_stats(self, user):
        attempts = Attempt.objects.filter(user=user)
        ratios = [
            score / max_score * 100
            for score, max_score in attempts.filter(max_score__gt=0).values_list(
                "score", "max_score"
            )
        ]
        return {
            "total": Quiz.objects.filter(user=user).count(),
            "ready": Quiz.objects.filter(user=user, status=Quiz.Status.READY).count(),
            "questions": Question.objects.filter(quiz__user=user).count(),
            "attempts": attempts.count(),
            "average": int(round(sum(ratios) / len(ratios))) if ratios else None,
        }

    # -- transactions ------------------------------------------------------

    @contextmanager
    def transaction(self):
        with transaction.atomic():
            yield


# --- Firestore hydration --------------------------------------------------
# A Firestore record is a plain dict; the rest of the app expects model
# instances (templates read `quiz.document`, the handlers read
# `question.choices`, forms read `question.pk.hex`). These builders reconstruct
# an *unsaved* instance from a record -- nothing is written to SQLite -- and
# relations are attached explicitly so no lazy ORM query is ever triggered.


def _as_uuid(value):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _as_datetime(value):
    return value if isinstance(value, datetime) else timezone.now()


def _as_decimal(value, default="0"):
    if value in (None, ""):
        return Decimal(default)
    return Decimal(str(value))


def hydrate_document(record):
    document = SourceDocument(
        id=_as_uuid(record["id"]),
        user_id=record.get("user_id"),
        original_filename=record.get("original_filename", ""),
        file_size=record.get("file_size") or 0,
        char_count=record.get("char_count") or 0,
        page_count=record.get("page_count") or 0,
        extracted_text=record.get("extracted_text") or "",
        created_at=_as_datetime(record.get("created_at")),
    )
    name = record.get("file") or ""
    if name:
        document.file = name
    return document


def hydrate_question(record, quiz=None):
    if record is None:
        return None
    question = Question(
        id=_as_uuid(record["id"]),
        quiz_id=record.get("quiz_id"),
        order=record.get("order") or 0,
        question_type=record.get("question_type") or QuestionType.MCQ_SINGLE,
        prompt=record.get("prompt") or "",
        choices=list(record.get("choices") or []),
        correct_choices=list(record.get("correct_choices") or []),
        acceptable_answers=list(record.get("acceptable_answers") or []),
        explanation=record.get("explanation") or "",
        points=record.get("points") or 1,
    )
    if quiz is not None:
        question.quiz = quiz
    return question


def hydrate_quiz(record, document=None, questions=None, total_questions=None):
    quiz = Quiz(
        id=_as_uuid(record["id"]),
        user_id=record.get("user_id"),
        document_id=record.get("document_id"),
        title=record.get("title") or "",
        question_count=record.get("question_count") or 0,
        difficulty=record.get("difficulty") or "",
        question_types=list(record.get("question_types") or []),
        partial_credit=bool(record.get("partial_credit")),
        status=record.get("status") or Quiz.Status.PENDING,
        error_message=record.get("error_message") or "",
        progress_done=record.get("progress_done") or 0,
        progress_total=record.get("progress_total") or 0,
        created_at=_as_datetime(record.get("created_at")),
        updated_at=_as_datetime(record.get("updated_at")),
    )
    if document is not None:
        quiz.document = document
    if total_questions is not None:
        quiz.total_questions = total_questions
    if questions is not None:
        quiz._questions_cache = questions
        for question in questions:
            question.quiz = quiz
    return quiz


def hydrate_attempt(record, responses=None):
    attempt = Attempt(
        id=record.get("id"),
        quiz_id=record.get("quiz_id"),
        user_id=record.get("user_id"),
        score=record.get("score") or 0,
        max_score=record.get("max_score") or 0,
        submitted_at=_as_datetime(record.get("submitted_at")),
    )
    if responses is not None:
        attempt._responses_cache = responses
    return attempt


def hydrate_response(record, attempt=None, question=None):
    response = Response(
        id=record.get("id"),
        attempt_id=record.get("attempt_id"),
        question_id=record.get("question_id"),
        user_answer=record.get("user_answer", []),
        is_correct=bool(record.get("is_correct")),
        points_earned=_as_decimal(record.get("points_earned")),
    )
    if attempt is not None:
        response.attempt = attempt
    if question is not None:
        response.question = question
    return response


class FirestoreRepository:
    """The Firestore-backed store.

    Reads records through `FirestoreStore` and hydrates them into model
    instances; writes fan out to the matching collection. Attempt/response ids
    are UUIDs because Firestore has no autoincrement, and the raw upload is
    kept on local disk (the record only carries its path).
    """

    engine = "firestore"

    def __init__(self, client=None):
        from .store import FirestoreStore

        self.store = FirestoreStore(client=client)

    # -- documents ---------------------------------------------------------

    def create_document(self, user, upload, original_filename, file_size):
        document = SourceDocument(
            user_id=user.pk,
            original_filename=original_filename,
            file_size=file_size,
            created_at=timezone.now(),
        )
        # SourceDocument's pk is an AutoField and Firestore has no
        # autoincrement, so mint a UUID and carry it in the record.
        document.id = uuid.uuid4()
        document.file.save(upload.name, upload, save=False)
        self.store.save_document(document_record(document))
        return document

    def save_document_extraction(self, document, text, char_count, page_count):
        document.extracted_text = text
        document.char_count = char_count
        document.page_count = page_count
        self.store.save_document(document_record(document))

    def delete_document(self, document):
        self.store.delete_document(str(document.user_id), str(document.pk))

    # -- quizzes -----------------------------------------------------------

    def create_quiz(self, user, document, **fields):
        now = timezone.now()
        quiz = Quiz(
            user_id=user.pk,
            document_id=document.pk,
            created_at=now,
            updated_at=now,
            **fields,
        )
        quiz.document = document
        self.store.create_quiz(quiz_record(quiz))
        return quiz

    def get_quiz(self, user, quiz_id):
        record = self.store.get_quiz(str(user.pk), str(quiz_id))
        if record is None:
            return None
        return self._build_quiz(record)

    def get_quiz_by_id(self, quiz_id, user_id=None):
        if user_id is None:
            return None
        record = self.store.get_quiz(str(user_id), str(quiz_id))
        if record is None:
            return None
        return self._build_quiz(record)

    def list_quizzes(self, user, status=None):
        user_id = str(user.pk)
        quizzes = []
        for record in self.store.list_quizzes(user_id):
            if status and record.get("status") != status:
                continue
            quizzes.append(self._build_quiz(record))
        return quizzes

    def _build_quiz(self, record):
        user_id = str(record["user_id"])
        document = None
        document_record_ = self.store.get_document(
            user_id, str(record.get("document_id"))
        )
        if document_record_ is not None:
            document = hydrate_document(document_record_)
        quiz = hydrate_quiz(record, document=document)
        # Loading the questions here means `quiz.is_ready` and `quiz.questions`
        # answer from the cache instead of falling through to an ORM query
        # against an empty SQLite table.
        self.list_questions(quiz)
        quiz.total_questions = len(quiz._questions_cache)
        return quiz

    def update_quiz_by_id(self, quiz_id, user_id=None, **fields):
        if user_id is None:
            return
        fields.setdefault("updated_at", timezone.now())
        self.store.update_quiz(str(user_id), str(quiz_id), **fields)

    def mark_quiz_failed(self, quiz_id, message, user_id=None):
        self.update_quiz_by_id(
            quiz_id,
            user_id=user_id,
            status=Quiz.Status.FAILED,
            error_message=message,
        )

    def save_quiz(self, quiz):
        self.store.create_quiz(quiz_record(quiz))

    def delete_quiz(self, quiz):
        user_id, quiz_id = str(quiz.user_id), str(quiz.pk)
        self.store.delete_questions(user_id, quiz_id)
        for attempt in self.store.list_attempts(user_id):
            if attempt.get("quiz_id") == quiz_id:
                self.store.delete_attempt(user_id, str(attempt["id"]))
        self.store.delete_quiz(user_id, quiz_id)

    # -- questions ---------------------------------------------------------

    def list_questions(self, quiz):
        user_id, quiz_id = str(quiz.user_id), str(quiz.pk)
        records = self.store.get_questions(user_id, quiz_id)
        questions = [hydrate_question(record, quiz) for record in records]
        quiz._questions_cache = questions
        return questions

    def count_questions(self, quiz):
        return len(self.store.get_questions(str(quiz.user_id), str(quiz.pk)))

    def replace_questions(self, quiz, values):
        user_id, quiz_id = str(quiz.user_id), str(quiz.pk)
        self.store.delete_questions(user_id, quiz_id)
        records = [
            question_record(Question(quiz_id=quiz_id, order=position, **item))
            for position, item in enumerate(values, start=1)
        ]
        self.store.save_questions(user_id, quiz_id, records)

    def save_question(self, question):
        quiz_id = str(question.quiz_id)
        user_id = str(question.quiz.user_id)
        self.store.save_questions(user_id, quiz_id, [question_record(question)])

    def create_question(self, quiz, **fields):
        question = Question(quiz_id=str(quiz.pk), **fields)
        question.quiz = quiz
        self.store.save_questions(
            str(quiz.user_id), str(quiz.pk), [question_record(question)]
        )
        return question

    def delete_question(self, question):
        self.store.delete_question(
            str(question.quiz.user_id), str(question.quiz_id), str(question.pk)
        )

    # -- attempts ----------------------------------------------------------

    def create_attempt(self, quiz, user, max_score):
        attempt = Attempt(
            id=uuid.uuid4().hex,
            quiz_id=str(quiz.pk),
            user_id=str(user.pk),
            score=0,
            max_score=max_score,
            submitted_at=timezone.now(),
        )
        self.store.create_attempt(attempt_record(attempt))
        return attempt

    def save_responses(self, attempt, responses):
        records = [response_record(response) for response in responses]
        self.store.save_responses(
            str(attempt.user_id), str(attempt.pk), records
        )

    def update_attempt(self, attempt, **fields):
        for name, value in fields.items():
            setattr(attempt, name, value)
        self.store.update_attempt(str(attempt.user_id), str(attempt.pk), **fields)

    def get_attempt(self, user, quiz, attempt_id):
        user_id = str(user.pk)
        record = self.store.get_attempt(user_id, str(attempt_id))
        if record is None or record.get("quiz_id") != str(quiz.pk):
            return None
        attempt = hydrate_attempt(record)

        question_records = {
            item["id"]: item
            for item in self.store.get_questions(user_id, str(quiz.pk))
        }
        responses = []
        for response_record_ in self.store.get_responses(user_id, str(attempt_id)):
            question = hydrate_question(
                question_records.get(response_record_.get("question_id")), quiz
            )
            responses.append(
                hydrate_response(response_record_, attempt=attempt, question=question)
            )
        attempt._responses_cache = responses
        return attempt

    def list_attempts(self, user):
        return [
            hydrate_attempt(record)
            for record in self.store.list_attempts(str(user.pk))
        ]

    # -- stats -------------------------------------------------------------

    def dashboard_stats(self, user):
        quizzes = self.list_quizzes(user)
        attempts = self.list_attempts(user)
        ratios = [
            attempt.score / attempt.max_score * 100
            for attempt in attempts
            if attempt.max_score
        ]
        return {
            "total": len(quizzes),
            "ready": sum(1 for quiz in quizzes if quiz.status == Quiz.Status.READY),
            "questions": sum(
                getattr(quiz, "total_questions", 0) or 0 for quiz in quizzes
            ),
            "attempts": len(attempts),
            "average": int(round(sum(ratios) / len(ratios))) if ratios else None,
        }

    # -- transactions ------------------------------------------------------

    @contextmanager
    def transaction(self):
        # The fake and admin clients expose writes immediately, so this is a
        # best-effort grouping rather than a true multi-document transaction.
        # The "plan before write" guard in services.editing still protects the
        # one case that must not half-apply.
        yield


def get_repository():
    """Pick the repository the pipeline should use, per settings.DATA_STORE.

    ``""``/``"sqlite"`` returns the ORM repository (the default, fully
    offline); ``"firestore"`` returns the Firestore-backed one and fails loudly
    if Firebase is not configured, so a misconfigured swap never silently keeps
    writing to SQLite.
    """
    from django.conf import settings

    choice = (settings.DATA_STORE or "sqlite").lower()
    if choice == "firestore":
        return FirestoreRepository()
    return OrmRepository()


__all__ = ["FirestoreRepository", "FirestoreUnavailable", "OrmRepository", "get_repository"]

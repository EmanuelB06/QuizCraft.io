import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .question_types import Difficulty, QuestionType, get_handler, label_for
from .services.store import FirestoreStore, user_record

__all__ = [
    "Attempt",
    "Difficulty",
    "FirestoreUserManager",
    "Question",
    "QuestionType",
    "Response",
    "Quiz",
    "SourceDocument",
    "User",
    "get_handler",
    "label_for",
]


def upload_path(instance, filename):
    return f"documents/{instance.user_id}/{uuid.uuid4().hex}{filename[filename.rfind("."):]}"


# --- Firestore-backed accounts ------------------------------------------
# When DATA_STORE=firestore the User manager and User.save() stop using the ORM
# and read/write the `users/{uid}` profile documents (and Django, in turn,
# keeps sessions in the `sessions` collection). This is what lets a Render
# deployment hold accounts durably on Firestore instead of an ephemeral
# SQLite file. Tests force DATA_STORE="" (see settings), so this branch only
# runs in production and in the offline tests that inject client_override.


def _firestore_mode() -> bool:
    return (settings.DATA_STORE or "").lower() == "firestore"


#: Tests inject a fake Firestore here; production keeps it None so FirestoreStore
#: builds its client from the configured credentials.
_firestore_client_override = None


def _make_store():
    return FirestoreStore(client=_firestore_client_override)


def _hydrate_user(record: dict) -> "User":
    """Rebuild a User from a ``users/{uid}`` profile document.

    The id comes back as the string it was stored as, but ``User.pk`` is a
    UUIDField, so it is parsed back -- otherwise a hydrated account compares
    unequal to the one that was just created.
    """
    raw_id = record.get("id") or ""
    try:
        pk = uuid.UUID(str(raw_id))
    except (TypeError, ValueError):
        pk = raw_id
    return User(
        pk=pk,
        username=record.get("username", ""),
        email=record.get("email", ""),
        password=record.get("password", ""),
        is_active=bool(record.get("is_active", True)),
        is_staff=bool(record.get("is_staff", False)),
        is_superuser=bool(record.get("is_superuser", False)),
        last_login=record.get("last_login"),
        date_joined=record.get("date_joined") or timezone.now(),
    )


class _FirestoreUserResults:
    """A tiny stand-in for a queryset over the Firestore user profiles.

    Production code only needs the handful of lookups Django's auth machinery
    makes (``exists`` for ``createsuperuser``, iteration in the admin), and
    supporting the full ORM queryset API here would be scope for a real
    Firestore ORM. Anything else fails loudly instead of silently querying
    the empty SQLite `users` table.
    """

    def __init__(self, manager, lookup: dict):
        self._manager = manager
        self._lookup = dict(lookup or {})

    def _records(self) -> list[dict]:
        records = self._manager._store().list_users()
        for key, expected in self._lookup.items():
            records = [r for r in records if r.get(key) == expected]
        return records

    def exists(self) -> bool:
        return bool(self._records())

    def count(self) -> int:
        return len(self._records())

    def first(self):
        records = self._records()
        return _hydrate_user(records[0]) if records else None

    def get(self, **kwargs):
        lookup = dict(self._lookup)
        lookup.update(kwargs)
        return self._manager.get(**lookup)

    def __iter__(self):
        return (_hydrate_user(record) for record in self._records())

    def __len__(self) -> int:
        return len(self._records())

    def __bool__(self) -> bool:
        return bool(self._records())


class FirestoreUserManager(BaseUserManager):
    """Manager that reads/writes accounts from Firestore when configured.

    In ORM mode (the tests, and any non-Firestore deployment) every method
    falls straight through to ``BaseUserManager``, so behaviour is unchanged.
    """

    use_in_migrations = True

    def _is_firestore(self) -> bool:
        return _firestore_mode()

    def _store(self):
        return _make_store()

    # -- lookup ------------------------------------------------------------

    def get(self, *args, **kwargs):
        if self._is_firestore():
            store = self._store()
            record = None
            if "pk" in kwargs:
                record = store.get_user(str(kwargs.pop("pk")))
            elif "id" in kwargs:
                record = store.get_user(str(kwargs.pop("id")))
            elif "username" in kwargs:
                record = store.get_user_by_username(kwargs.pop("username"))
            else:
                raise TypeError(
                    "Firestore user lookup supports only pk, id or username."
                )
            if kwargs:
                raise TypeError("Unsupported lookups: %r" % (kwargs,))
            if record is None:
                raise self.model.DoesNotExist(
                    "%s matching query does not exist."
                    % self.model._meta.object_name
                )
            return _hydrate_user(record)
        return super().get(*args, **kwargs)

    def get_or_create(self, **kwargs):
        if self._is_firestore():
            defaults = kwargs.pop("defaults", {})
            try:
                return self.get(**kwargs), False
            except self.model.DoesNotExist:
                lookup = dict(kwargs)
                lookup.update(defaults)
                obj = self.model(**lookup)
                if obj.pk is None:
                    obj.pk = uuid.uuid4()
                obj.save(using=self._db)
                return obj, True
        return super().get_or_create(**kwargs)

    def filter(self, *args, **kwargs):
        if self._is_firestore():
            return _FirestoreUserResults(self, kwargs)
        return super().filter(*args, **kwargs)

    def all(self, *args, **kwargs):
        if self._is_firestore():
            return _FirestoreUserResults(self, {})
        return super().all(*args, **kwargs)

    # -- creation ----------------------------------------------------------

    def _create_user(self, username, email, password, **extra_fields):
        """Build, hash and save a user, on whichever store is active.

        ``BaseUserManager`` in this Django version does not provide the create
        helpers (they live on ``UserManager`` in ``django.contrib.auth``), so
        they are defined here. ``User.save`` picks ORM-insert or Firestore
        write from ``settings.DATA_STORE``, which is what makes one manager
        work for both.
        """
        if not username:
            raise ValueError("The given username must be set")
        email = self.normalize_email(email)
        username = self.model.normalize_username(username)
        user = self.model(username=username, email=email, **extra_fields)
        if user.pk is None:
            user.pk = uuid.uuid4()
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(
            username, email=email, password=password, **extra_fields
        )

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(
            username, email=email, password=password, **extra_fields
        )


class User(AbstractBaseUser, PermissionsMixin):
    """The account model.

    In ORM mode it is an ordinary Django user row. In Firestore mode the
    ``save`` below writes the ``users/{uid}`` profile document instead, and the
    manager reads from the same place, so ``AUTH_USER_MODEL`` can point here
    while the ORM table stays unused.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(max_length=254, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = FirestoreUserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return self.username

    def save(self, *args, **kwargs):
        if _firestore_mode():
            if self.pk is None:
                self.pk = uuid.uuid4()
            if self.date_joined is None:
                self.date_joined = timezone.now()
            _make_store().save_user(user_record(self))
            return
        return super().save(*args, **kwargs)


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
        # A hydrated instance (Firestore) carries its questions in a cache;
        # otherwise fall through to the relation, which Meta.ordering sorts.
        cached = getattr(self, "_questions_cache", None)
        if cached is not None:
            return cached
        return self.question_set.all()

    @property
    def is_ready(self):
        if self.status != self.Status.READY:
            return False
        cached = getattr(self, "_questions_cache", None)
        if cached is not None:
            return bool(cached)
        return self.question_set.exists()

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
    def responses(self):
        cached = getattr(self, "_responses_cache", None)
        if cached is not None:
            return cached
        return self.response_set.all()

    @property
    def response_count(self):
        cached = getattr(self, "_responses_cache", None)
        if cached is not None:
            return len(cached)
        return self.response_set.count()

    @property
    def correct_count(self):
        cached = getattr(self, "_responses_cache", None)
        if cached is not None:
            return sum(1 for response in cached if response.is_correct)
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

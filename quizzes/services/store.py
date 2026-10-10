"""Optional Cloud Firestore backend for the business entities.

This module is the store seam: it defines the Firestore document shapes
for the business tables and a `FirestoreStore` that reads and writes them
through the Firebase Admin SDK. Nothing else in the app should import
`google.cloud` or `firebase_admin`; services talk to a store, and the store
they get is chosen by `settings.DATA_STORE` (see `get_store()`).

Record shapes
-------------
Records are plain dicts with fixed keys, so both backends round-trip the same
shape and a backend swap is a settings change. Timestamps are native `datetime`
objects so real Firestore queries can order by them.

Layout
------
Each record lives beneath the user's document, which also scopes data access:

  users/{uid}/documents/{doc_id}
  users/{uid}/quizzes/{quiz_id}
  users/{uid}/quizzes/{quiz_id}/questions/{question_id}
  users/{uid}/attempts/{attempt_id}
  users/{uid}/attempts/{attempt_id}/responses/{question_id}

Document ids reuse the SQLite UUIDs so the backends can coexist during a
migration. Django's users, sessions, admin and CSRF stay on SQLite either way.
"""

from __future__ import annotations

from quizcraft import firebase


# --- Record builders ------------------------------------------------------
# Each *_record() maps a Django model instance to a Firestore-shaped dict.
# The FirestoreStore writes and reads these; the SQLite path never sees them.


def document_record(document) -> dict:
    return {
        "id": str(document.pk),
        "user_id": str(document.user_id),
        "original_filename": document.original_filename,
        "file_size": document.file_size,
        "char_count": document.char_count,
        "page_count": document.page_count,
        "extracted_text": document.extracted_text,
        "file": document.file.name if document.file else "",
        "created_at": document.created_at,
    }


def quiz_record(quiz) -> dict:
    return {
        "id": str(quiz.pk),
        "user_id": str(quiz.user_id),
        "document_id": str(quiz.document_id),
        "title": quiz.title,
        "question_count": quiz.question_count,
        "difficulty": quiz.difficulty,
        "question_types": list(quiz.question_types),
        "partial_credit": bool(quiz.partial_credit),
        "status": quiz.status,
        "error_message": quiz.error_message,
        "progress_done": quiz.progress_done,
        "progress_total": quiz.progress_total,
        "created_at": quiz.created_at,
        "updated_at": quiz.updated_at,
    }


def question_record(question) -> dict:
    return {
        "id": str(question.pk),
        "quiz_id": str(question.quiz_id),
        "order": question.order,
        "question_type": question.question_type,
        "prompt": question.prompt,
        "choices": list(question.choices),
        "correct_choices": list(question.correct_choices),
        "acceptable_answers": list(question.acceptable_answers),
        "explanation": question.explanation,
        "points": question.points,
    }


def attempt_record(attempt, responses=None) -> dict:
    record = {
        "id": str(attempt.pk),
        "quiz_id": str(attempt.quiz_id),
        "user_id": str(attempt.user_id),
        "score": attempt.score,
        "max_score": attempt.max_score,
        "submitted_at": attempt.submitted_at,
    }
    if responses is not None:
        record["responses"] = [response_record(response) for response in responses]
    return record


def response_record(response) -> dict:
    return {
        "id": str(response.question_id),
        "attempt_id": str(response.attempt_id),
        "question_id": str(response.question_id),
        "user_answer": response.user_answer,
        "is_correct": bool(response.is_correct),
        "points_earned": str(response.points_earned),
    }


# --- Firestore store ------------------------------------------------------


class FirestoreUnavailable(RuntimeError):
    """Raised when a Firestore store is requested but Firebase is not set up."""


class FirestoreStore:
    """Persist the business records to Cloud Firestore.

    The client is injected (dependency injection) so tests can hand in a fake
    and run offline; production code lets it default to the configured client.
    """

    engine = "firestore"

    def __init__(self, client=None):
        self._client = client if client is not None else firebase.firestore_client()
        if self._client is None:
            raise FirestoreUnavailable(
                "Firestore is not configured. Set FIREBASE_CREDENTIAL_PATH and "
                "FIREBASE_PROJECT_ID (see .env.example), then run "
                "`manage.py firebase_status` and give DATA_STORE=firestore."
            )

    # -- documents ---------------------------------------------------------

    def save_document(self, record: dict) -> str:
        self._put(f"users/{record['user_id']}/documents", record)
        return record["id"]

    def get_document(self, user_id: str, document_id: str) -> dict | None:
        return self._get(f"users/{user_id}/documents/{document_id}")

    def delete_document(self, user_id: str, document_id: str) -> None:
        self._client.document(f"users/{user_id}/documents/{document_id}").delete()

    # -- quizzes -----------------------------------------------------------

    def create_quiz(self, record: dict) -> str:
        self._put(f"users/{record['user_id']}/quizzes", record)
        return record["id"]

    def update_quiz(self, user_id: str, quiz_id: str, **fields) -> None:
        ref = self._client.document(f"users/{user_id}/quizzes/{quiz_id}")
        ref.update(fields)

    def get_quiz(self, user_id: str, quiz_id: str) -> dict | None:
        return self._get(f"users/{user_id}/quizzes/{quiz_id}")

    def list_quizzes(self, user_id: str) -> list[dict]:
        collection = self._client.collection(f"users/{user_id}/quizzes")
        return [snap.to_dict() for snap in collection.get()]

    def delete_quiz(self, user_id: str, quiz_id: str) -> None:
        self._client.document(f"users/{user_id}/quizzes/{quiz_id}").delete()

    # -- questions ---------------------------------------------------------

    def save_questions(self, user_id: str, quiz_id: str, records: list[dict]) -> None:
        collection = self._client.collection(f"users/{user_id}/quizzes/{quiz_id}/questions")
        for record in records:
            collection.document(record["id"]).set(record)

    def get_questions(self, user_id: str, quiz_id: str) -> list[dict]:
        collection = self._client.collection(f"users/{user_id}/quizzes/{quiz_id}/questions")
        docs = sorted(collection.get(), key=lambda snap: snap.to_dict().get("order", 0))
        return [snap.to_dict() for snap in docs]

    def delete_question(self, user_id: str, quiz_id: str, question_id: str) -> None:
        self._client.document(
            f"users/{user_id}/quizzes/{quiz_id}/questions/{question_id}"
        ).delete()

    def delete_questions(self, user_id: str, quiz_id: str) -> None:
        collection = self._client.collection(
            f"users/{user_id}/quizzes/{quiz_id}/questions"
        )
        for snap in collection.get():
            snap.reference.delete()

    # -- attempts ----------------------------------------------------------

    def create_attempt(self, record: dict) -> str:
        self._put(f"users/{record['user_id']}/attempts", record)
        return record["id"]

    def get_attempt(self, user_id: str, attempt_id: str) -> dict | None:
        return self._get(f"users/{user_id}/attempts/{attempt_id}")

    def list_attempts(self, user_id: str) -> list[dict]:
        collection = self._client.collection(f"users/{user_id}/attempts")
        return [snap.to_dict() for snap in collection.get()]

    def update_attempt(self, user_id: str, attempt_id: str, **fields) -> None:
        ref = self._client.document(f"users/{user_id}/attempts/{attempt_id}")
        ref.update(fields)

    def delete_attempt(self, user_id: str, attempt_id: str) -> None:
        responses = self._client.collection(
            f"users/{user_id}/attempts/{attempt_id}/responses"
        )
        for snap in responses.get():
            snap.reference.delete()
        self._client.document(f"users/{user_id}/attempts/{attempt_id}").delete()

    # -- responses ---------------------------------------------------------

    def save_responses(self, user_id: str, attempt_id: str, records: list[dict]) -> None:
        collection = self._client.collection(
            f"users/{user_id}/attempts/{attempt_id}/responses"
        )
        for record in records:
            collection.document(record["question_id"]).set(record)

    def get_responses(self, user_id: str, attempt_id: str) -> list[dict]:
        collection = self._client.collection(
            f"users/{user_id}/attempts/{attempt_id}/responses"
        )
        return [snap.to_dict() for snap in collection.get()]

    # -- helpers -----------------------------------------------------------

    def _put(self, parent_path: str, record: dict) -> None:
        collection = self._client.collection(parent_path)
        collection.document(record["id"]).set(record)

    def _get(self, path: str) -> dict | None:
        snapshot = self._client.document(path).get()
        return snapshot.to_dict() if snapshot.exists else None


def get_store():
    """Pick the store the pipeline should use, per settings.DATA_STORE.

    ``""``/``"sqlite"`` resolves to None: the caller keeps using its existing
    ORM path. ``"firestore"`` returns a configured FirestoreStore and fails
    loudly if Firebase is unavailable, so a misconfigured swap never silently
    keeps using SQLite.
    """
    from django.conf import settings

    choice = (settings.DATA_STORE or "sqlite").lower()
    if choice == "firestore":
        return FirestoreStore()
    return None
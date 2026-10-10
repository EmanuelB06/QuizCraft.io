"""Contract tests for the Firestore store seam.

These run without Firebase: the store's client is injected, and a fake
Firestore stands in for the admin SDK, so the serialization and read/write
shapes are pinned offline. `FirestoreStore` must never touch the network in
the test suite.
"""

from datetime import datetime, timezone

from django.test import SimpleTestCase, TestCase, override_settings

from quizzes.services import store as store_module
from quizzes.services.store import FirestoreStore

from .factories import make_document, make_question, make_quiz, make_user


class FakeSnapshot:
    def __init__(self, doc):
        self._doc = doc

    @property
    def exists(self):
        return self._doc.path in self._doc.store.data

    @property
    def id(self):
        return self._doc.path.rsplit("/", 1)[-1]

    @property
    def reference(self):
        return self._doc

    def to_dict(self):
        return self._doc.store.data.get(self._doc.path)


class FakeDocument:
    def __init__(self, store, path):
        self.store = store
        self.path = path

    def set(self, record):
        self.store.data[self.path] = record
        return self

    def update(self, fields):
        current = self.store.data.setdefault(self.path, {})
        current.update(fields)
        return self

    def delete(self):
        self.store.data.pop(self.path, None)
        return self

    def get(self):
        return FakeSnapshot(self)


class FakeCollection:
    def __init__(self, store, path):
        self.store = store
        self.path = path

    def document(self, doc_id):
        return FakeDocument(self.store, f"{self.path}/{doc_id}")

    def get(self):
        prefix = self.path + "/"
        return [
            FakeSnapshot(FakeDocument(self.store, key))
            for key in self.store.data
            if key.startswith(prefix) and "/" not in key[len(prefix):]
        ]


class FakeFirestore:
    def __init__(self):
        self.data = {}

    def document(self, path):
        return FakeDocument(self, path)

    def collection(self, path):
        return FakeCollection(self, path)


class FirestoreStoreTests(SimpleTestCase):
    def setUp(self):
        self.client = FakeFirestore()
        self.store = FirestoreStore(client=self.client)

    def test_round_trips_a_document(self):
        record = self._document()
        self.store.save_document(record)
        stored = self.client.data["users/u1/documents/doc1"]
        self.assertEqual(stored["original_filename"], "notes.pdf")

    def test_quiz_writes_and_reads_back_under_the_user(self):
        record = self._quiz()
        self.store.create_quiz(record)
        self.assertEqual(self.store.get_quiz("u1", "quiz1")["title"], "Bio quiz")
        self.assertIsNone(self.store.get_quiz("u1", "nope"))
        self.assertIsNone(self.store.get_quiz("u2", "quiz1"))

    def test_quiz_update_merges_fields(self):
        self.store.create_quiz(self._quiz())
        self.store.update_quiz("u1", "quiz1", status="ready", progress_done=3)
        quiz = self.client.data["users/u1/quizzes/quiz1"]
        self.assertEqual(quiz["status"], "ready")
        self.assertEqual(quiz["progress_done"], 3)
        self.assertEqual(quiz["title"], "Bio quiz")

    def test_quiz_delete(self):
        self.store.create_quiz(self._quiz())
        self.store.delete_quiz("u1", "quiz1")
        self.assertNotIn("users/u1/quizzes/quiz1", self.client.data)

    def test_questions_are_returned_in_order(self):
        self.store.create_quiz(self._quiz())
        self.store.save_questions(
            "u1",
            "quiz1",
            [
                {"id": "a", "quiz_id": "quiz1", "order": 3, "prompt": "three", "points": 1},
                {"id": "c", "quiz_id": "quiz1", "order": 1, "prompt": "one", "points": 1},
                {"id": "b", "quiz_id": "quiz1", "order": 2, "prompt": "two", "points": 1},
            ],
        )
        prompts = [q["prompt"] for q in self.store.get_questions("u1", "quiz1")]
        self.assertEqual(prompts, ["one", "two", "three"])

    def test_attempt_round_trips(self):
        record = self._attempt()
        self.store.create_attempt(record)
        stored = self.store.get_attempt("u1", "att1")
        self.assertEqual(stored["score"], 3)
        self.assertEqual(stored["max_score"], 4)

    def test_records_are_isolated_per_user(self):
        self.store.create_quiz(self._quiz(user_id="u1"))
        self.store.create_quiz(self._quiz(user_id="u2", quiz_id="quiz2"))
        self.assertIsNone(self.store.get_quiz("u1", "quiz2"))
        self.assertIsNone(self.store.get_quiz("u2", "quiz1"))

    # -- helpers -----------------------------------------------------------

    def _document(self, user_id="u1", doc_id="doc1"):
        return {
            "id": doc_id,
            "user_id": user_id,
            "original_filename": "notes.pdf",
            "file_size": 1234,
            "char_count": 500,
            "page_count": 2,
            "extracted_text": "some text",
            "created_at": datetime.now(timezone.utc),
        }

    def _quiz(self, user_id="u1", quiz_id="quiz1"):
        return {
            "id": quiz_id,
            "user_id": user_id,
            "document_id": "doc1",
            "title": "Bio quiz",
            "question_count": 2,
            "difficulty": "medium",
            "question_types": ["mcq_single", "short_answer"],
            "partial_credit": False,
            "status": "pending",
            "error_message": "",
            "progress_done": 0,
            "progress_total": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

    def _attempt(self, user_id="u1", attempt_id="att1"):
        return {
            "id": attempt_id,
            "quiz_id": "quiz1",
            "user_id": user_id,
            "score": 3,
            "max_score": 4,
            "submitted_at": datetime.now(timezone.utc),
        }


class StoreSelectionTests(SimpleTestCase):
    def test_sqlite_store_choice_yields_none(self):
        with override_settings(DATA_STORE="sqlite"):
            self.assertIsNone(store_module.get_store())

    def test_empty_store_choice_yields_none(self):
        with override_settings(DATA_STORE=""):
            self.assertIsNone(store_module.get_store())

    def test_firestore_choice_fails_loudly_when_unconfigured(self):
        with override_settings(
            DATA_STORE="firestore", FIREBASE_CREDENTIAL_PATH="", FIREBASE_PROJECT_ID=""
        ):
            with self.assertRaises(store_module.FirestoreUnavailable):
                store_module.get_store()


class RecordBuilderTests(TestCase):
    def test_records_build_from_models(self):
        user = make_user()
        document = make_document(user)
        quiz = make_quiz(user, document=document)
        question = make_question(quiz)

        doc_rec = store_module.document_record(document)
        quiz_rec = store_module.quiz_record(quiz)
        question_rec = store_module.question_record(question)

        self.assertEqual(doc_rec["user_id"], str(user.pk))
        self.assertEqual(quiz_rec["id"], str(quiz.pk))
        self.assertEqual(quiz_rec["document_id"], str(document.pk))
        self.assertEqual(question_rec["quiz_id"], str(quiz.pk))
        self.assertEqual(quiz_rec["status"], quiz.status)
        self.assertEqual(
            question_rec["prompt"], question.prompt,
        )
"""Contract tests for the repository seam, exercised through Firestore.

The client is injected (the same offline fake `test_store` uses), so the whole
lifecycle runs with no network and no credentials. These tests pin the parts a
database swap is most likely to break: hydration back into model instances,
relation attachment, numeric types, and the cascade on delete.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from quizzes.question_types import get_handler
from quizzes.services import repository as repository_module
from quizzes.services.generation import run_generation
from quizzes.services.repository import FirestoreRepository, get_repository

from .factories import make_text_file, make_user
from .test_store import FakeFirestore


class FirestoreRepositoryTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = FakeFirestore()
        self.repo = FirestoreRepository(client=self.client)
        self.documents = []

    def tearDown(self):
        for document in self.documents:
            if document.file:
                document.file.delete(save=False)

    # -- helpers -----------------------------------------------------------

    def _document(self, text="Some source text about cells. " * 20):
        upload = make_text_file(text)
        document = self.repo.create_document(
            self.user, upload, upload.name, upload.size
        )
        self.documents.append(document)
        self.repo.save_document_extraction(document, text, len(text), 1)
        return document

    def _quiz(self, document=None, **overrides):
        document = document or self._document()
        defaults = {
            "title": "Cell Biology Quiz",
            "question_count": 2,
            "difficulty": "medium",
            "question_types": ["mcq_single", "short_answer"],
            "partial_credit": False,
            "status": "ready",
        }
        defaults.update(overrides)
        return self.repo.create_quiz(self.user, document, **defaults)

    def _question_values(self, prompt="Which organelle produces ATP?"):
        return {
            "question_type": "mcq_single",
            "prompt": prompt,
            "choices": ["Nucleus", "Mitochondrion", "Ribosome"],
            "correct_choices": [1],
            "acceptable_answers": [],
            "explanation": "Mitochondria produce most ATP.",
        }

    # -- documents ---------------------------------------------------------

    def test_document_round_trips_with_its_file_path(self):
        document = self._document()
        stored = self.client.data[f"users/{self.user.pk}/documents/{document.pk}"]
        self.assertEqual(stored["original_filename"], "notes.txt")
        self.assertTrue(stored["file"])
        self.assertEqual(stored["char_count"], len(document.extracted_text))

    # -- quizzes -----------------------------------------------------------

    def test_quiz_hydrates_with_its_document_attached(self):
        document = self._document()
        quiz = self._quiz(document)

        loaded = self.repo.get_quiz(self.user, quiz.pk)
        self.assertEqual(loaded.pk, quiz.pk)
        self.assertEqual(loaded.title, "Cell Biology Quiz")
        self.assertEqual(loaded.document.pk, document.pk)
        self.assertEqual(loaded.document.original_filename, "notes.txt")

    def test_quiz_is_scoped_to_its_owner(self):
        quiz = self._quiz()
        other = make_user("someone-else")
        self.assertIsNone(self.repo.get_quiz(other, quiz.pk))

    def test_list_quizzes_reports_the_question_count(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])

        quizzes = self.repo.list_quizzes(self.user)
        self.assertEqual(len(quizzes), 1)
        self.assertEqual(quizzes[0].total_questions, 1)
        self.assertTrue(quizzes[0].document.original_filename)

    def test_list_quizzes_filters_by_status(self):
        self._quiz(status="ready")
        self._quiz(status="failed")
        self.assertEqual(len(self.repo.list_quizzes(self.user, status="ready")), 1)

    def test_get_quiz_by_id_needs_a_user_to_locate_it(self):
        quiz = self._quiz()
        self.assertIsNone(self.repo.get_quiz_by_id(quiz.pk))
        found = self.repo.get_quiz_by_id(quiz.pk, user_id=self.user.pk)
        self.assertEqual(found.pk, quiz.pk)

    # -- questions ---------------------------------------------------------

    def test_questions_hydrate_and_answer_like_models(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])

        questions = self.repo.list_questions(quiz)
        self.assertEqual(len(questions), 1)
        self.assertEqual(quiz.questions, questions)
        self.assertTrue(quiz.is_ready)

        question = questions[0]
        self.assertEqual(question.handler, get_handler("mcq_single"))
        self.assertTrue(question.is_choice_based)
        self.assertEqual(question.type_label, get_handler("mcq_single").label)
        self.assertEqual(question.correct_choice_texts(), ["Mitochondrion"])
        view = question.answer_view
        self.assertEqual(view.kind, "single")
        self.assertEqual(view.options[1].text, "Mitochondrion")
        # `question.pk.hex` is what the edit form and take page key on.
        self.assertEqual(len(question.pk.hex), 32)

    def test_replace_questions_drops_the_previous_set(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])
        self.repo.replace_questions(
            quiz, [self._question_values(prompt="A brand new question?")]
        )
        questions = self.repo.list_questions(quiz)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].prompt, "A brand new question?")

    def test_editing_a_question_writes_back(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])
        question = self.repo.list_questions(quiz)[0]
        question.prompt = "Edited prompt about ATP?"
        question.points = 3
        self.repo.save_question(question)

        reloaded = self.repo.list_questions(quiz)[0]
        self.assertEqual(reloaded.prompt, "Edited prompt about ATP?")
        self.assertEqual(reloaded.points, 3)

    def test_create_and_delete_a_question(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])
        added = self.repo.create_question(
            quiz, order=2, question_type="true_false", prompt="Cells have DNA.",
            choices=["True", "False"], correct_choices=[0],
            acceptable_answers=["true"], explanation="", points=1,
        )
        self.assertEqual(self.repo.count_questions(quiz), 2)
        self.repo.delete_question(added)
        self.assertEqual(self.repo.count_questions(quiz), 1)

    # -- attempts ----------------------------------------------------------

    def test_attempt_hydrates_with_responses_and_questions(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])
        question = self.repo.list_questions(quiz)[0]

        attempt = self.repo.create_attempt(quiz, self.user, max_score=question.points)
        question.user_answer = 1
        from quizzes.models import Response

        self.repo.save_responses(
            attempt,
            [
                Response(
                    question=question,
                    user_answer=1,
                    is_correct=True,
                    points_earned=Decimal("1"),
                )
            ],
        )
        self.repo.update_attempt(attempt, score=1)

        loaded = self.repo.get_attempt(self.user, quiz, attempt.pk)
        self.assertEqual(loaded.pk, attempt.pk)
        self.assertEqual(loaded.response_count, 1)
        self.assertEqual(loaded.correct_count, 1)
        self.assertEqual(loaded.percentage, 100)
        response = loaded.responses[0]
        self.assertEqual(response.question.prompt, question.prompt)
        self.assertIsInstance(response.points_earned, Decimal)
        self.assertTrue(response.points_earned > 0)

    def test_attempt_is_scoped_to_its_quiz_and_owner(self):
        quiz = self._quiz()
        other_quiz = self._quiz()
        attempt = self.repo.create_attempt(quiz, self.user, max_score=1)
        self.assertIsNone(self.repo.get_attempt(self.user, other_quiz, attempt.pk))
        other = make_user("other")
        self.assertIsNone(self.repo.get_attempt(other, quiz, attempt.pk))

    # -- stats -------------------------------------------------------------

    def test_dashboard_stats_aggregate_over_records(self):
        ready = self._quiz(status="ready")
        self._quiz(status="failed")
        self.repo.replace_questions(ready, [self._question_values()])

        attempt = self.repo.create_attempt(ready, self.user, max_score=4)
        self.repo.update_attempt(attempt, score=2)

        stats = self.repo.dashboard_stats(self.user)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["ready"], 1)
        self.assertEqual(stats["questions"], 1)
        self.assertEqual(stats["attempts"], 1)
        self.assertEqual(stats["average"], 50)

    # -- delete ------------------------------------------------------------

    def test_delete_quiz_removes_questions_and_attempts(self):
        quiz = self._quiz()
        self.repo.replace_questions(quiz, [self._question_values()])
        attempt = self.repo.create_attempt(quiz, self.user, max_score=1)

        self.repo.delete_quiz(quiz)

        self.assertIsNone(self.repo.get_quiz(self.user, quiz.pk))
        self.assertIsNone(self.repo.get_attempt(self.user, quiz, attempt.pk))
        self.assertEqual(self.repo.count_questions(quiz), 0)


class RepositorySelectionTests(SimpleTestCase):
    def test_default_store_is_the_orm(self):
        with override_settings(DATA_STORE=""):
            self.assertEqual(get_repository().engine, "sqlite")

    def test_sqlite_choice_is_the_orm(self):
        with override_settings(DATA_STORE="sqlite"):
            self.assertEqual(get_repository().engine, "sqlite")

    def test_firestore_choice_needs_credentials(self):
        with override_settings(
            DATA_STORE="firestore",
            FIREBASE_CREDENTIAL_PATH="",
            FIREBASE_PROJECT_ID="",
        ):
            with self.assertRaises(repository_module.FirestoreUnavailable):
                get_repository()


class _StubGenerator:
    """Stands in for the Gemini client; returns one canned raw question."""

    def __init__(self):
        self.calls = []

    def generate_questions(self, text, quota, difficulty, types, note):
        self.calls.append(note)
        return [
            {
                "question_type": "mcq_single",
                "prompt": "Which organelle produces ATP?",
                "choices": ["Nucleus", "Mitochondrion", "Ribosome"],
                "correct_indexes": [1],
                "explanation": "Mitochondria produce most ATP.",
            }
        ]


class FirestoreGenerationTests(TestCase):
    """The generation pipeline writing end to end through the fake store."""

    def setUp(self):
        self.user = make_user()
        self.repo = FirestoreRepository(client=FakeFirestore())
        self.document = self._document()

    def tearDown(self):
        if self.document.file:
            self.document.file.delete(save=False)

    def _document(self):
        text = "Some source text about cells. " * 40
        upload = make_text_file(text)
        document = self.repo.create_document(self.user, upload, upload.name, upload.size)
        self.repo.save_document_extraction(document, text, len(text), 1)
        return document

    def test_run_generation_persists_questions_and_status(self):
        quiz = self.repo.create_quiz(
            self.user,
            self.document,
            title="Cell Biology Quiz",
            question_count=1,
            difficulty="medium",
            question_types=["mcq_single"],
            partial_credit=False,
            status="pending",
        )
        generator = _StubGenerator()

        with patch(
            "quizzes.services.generation.get_repository", return_value=self.repo
        ):
            run_generation(quiz.pk, generator, str(self.user.pk))

        loaded = self.repo.get_quiz(self.user, quiz.pk)
        self.assertEqual(loaded.status, "ready")
        self.assertEqual(loaded.error_message, "")
        questions = self.repo.list_questions(loaded)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].prompt, "Which organelle produces ATP?")
        self.assertEqual(questions[0].correct_choice_texts(), ["Mitochondrion"])
        self.assertEqual(generator.calls, [""])

    def test_run_generation_marks_a_failed_quiz(self):
        quiz = self.repo.create_quiz(
            self.user,
            self.document,
            title="Cell Biology Quiz",
            question_count=1,
            difficulty="medium",
            question_types=["mcq_single"],
            partial_credit=False,
            status="pending",
        )

        class Boom:
            def generate_questions(self, *args, **kwargs):
                raise RuntimeError("provider exploded")

        with patch(
            "quizzes.services.generation.get_repository", return_value=self.repo
        ):
            run_generation(quiz.pk, Boom(), str(self.user.pk))

        loaded = self.repo.get_quiz(self.user, quiz.pk)
        self.assertEqual(loaded.status, "failed")
        self.assertIn("provider exploded", loaded.error_message)


@override_settings(DATA_STORE="firestore")
class FirestoreViewFlowTests(TestCase):
    """The real views over the fake store: hydration must survive templates.

    Every `get_repository()` call inside the views builds a fresh
    `FirestoreRepository`, so patching the client factory is what keeps them
    all pointed at the same in-memory fake.
    """

    def setUp(self):
        self.fake = FakeFirestore()
        # Start the patch before make_user/force_login: under this class's
        # override the account manager and the session engine also build a
        # store, and they resolve the client through the same factory.
        self._patch = patch(
            "quizcraft.firebase.firestore_client", return_value=self.fake
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)

        self.user = make_user()
        self.client.force_login(self.user)
        self.repo = FirestoreRepository(client=self.fake)
        self.document = self._document()
        self.quiz = self.repo.create_quiz(
            self.user,
            self.document,
            title="Cell Biology Quiz",
            question_count=1,
            difficulty="medium",
            question_types=["mcq_single"],
            partial_credit=False,
            status="ready",
        )
        self.repo.replace_questions(self.quiz, [self._question_values()])
        self.question = self.repo.list_questions(self.quiz)[0]

    def _document(self):
        text = "Some source text about cells. " * 40
        upload = make_text_file(text)
        document = self.repo.create_document(self.user, upload, upload.name, upload.size)
        self.repo.save_document_extraction(document, text, len(text), 1)
        self.addCleanup(lambda: document.file and document.file.delete(save=False))
        return document

    def _question_values(self):
        return {
            "question_type": "mcq_single",
            "prompt": "Which organelle produces ATP?",
            "choices": ["Nucleus", "Mitochondrion", "Ribosome"],
            "correct_choices": [1],
            "acceptable_answers": [],
            "explanation": "Mitochondria produce most ATP.",
        }

    def test_dashboard_renders_a_firestore_quiz(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cell Biology Quiz")
        self.assertContains(response, "notes.txt")

    def test_edit_and_take_pages_render(self):
        edit = self.client.get(reverse("quiz_edit", args=[self.quiz.pk]))
        self.assertContains(edit, "Which organelle produces ATP?")
        take = self.client.get(reverse("quiz_take", args=[self.quiz.pk]))
        self.assertContains(take, "Which organelle produces ATP?")

    def test_taking_the_quiz_records_and_shows_the_attempt(self):
        response = self.client.post(
            reverse("quiz_take", args=[self.quiz.pk]),
            {f"q{self.question.pk.hex}": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/results/", response["Location"])

        results = self.client.get(response["Location"])
        self.assertEqual(results.status_code, 200)
        self.assertContains(results, "Which organelle produces ATP?")
        self.assertContains(results, "Correct")
        self.assertContains(results, "Mitochondrion")

    def test_deleting_a_quiz_removes_its_records(self):
        response = self.client.post(reverse("quiz_delete", args=[self.quiz.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.repo.get_quiz(self.user, self.quiz.pk))
        self.assertEqual(self.repo.count_questions(self.quiz), 0)



"""Tests for the one-off SQLite -> Firestore migration command.

The command is pointed at an injected fake Firestore by patching the client
factory, so the copy runs fully offline and the result is read back through
`FirestoreRepository` to prove the migrated records hydrate into the same
model instances the app expects.
"""

from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from quizzes.models import Attempt, Response
from quizzes.services.repository import FirestoreRepository

from .factories import make_document, make_question, make_quiz, make_user
from .test_store import FakeFirestore


class MigrateToFirestoreTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.document = make_document(self.user)
        self.quiz = make_quiz(self.user, document=self.document)
        self.question = make_question(self.quiz)
        self.attempt = Attempt.objects.create(
            quiz=self.quiz, user=self.user, score=1, max_score=1
        )
        Response.objects.create(
            attempt=self.attempt,
            question=self.question,
            user_answer="1",
            is_correct=True,
            points_earned=1,
        )
        self.fake = FakeFirestore()
        self._patch = patch(
            "quizcraft.firebase.firestore_client", return_value=self.fake
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.repo = FirestoreRepository(client=self.fake)

    def _run(self, *args):
        out = StringIO()
        call_command("migrate_to_firestore", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        output = self._run("--dry-run")
        self.assertIn("Dry run", output)
        self.assertEqual(self.fake.data, {})

    def test_migrates_the_whole_graph(self):
        self._run()

        quiz = self.repo.get_quiz(self.user, self.quiz.pk)
        self.assertIsNotNone(quiz)
        self.assertEqual(quiz.title, self.quiz.title)
        self.assertEqual(quiz.document.original_filename, "notes.txt")

        questions = self.repo.list_questions(quiz)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].prompt, self.question.prompt)
        self.assertEqual(questions[0].correct_choice_texts(), ["Mitochondrion"])

        attempts = self.repo.list_attempts(self.user)
        self.assertEqual(len(attempts), 1)
        loaded = self.repo.get_attempt(self.user, quiz, attempts[0].pk)
        self.assertEqual(loaded.score, 1)
        self.assertEqual(loaded.response_count, 1)
        response = loaded.responses[0]
        self.assertTrue(response.is_correct)
        self.assertEqual(response.points_earned, Decimal("1.00"))

    def test_document_id_is_remapped_to_a_uuid(self):
        self._run()
        record = self.fake.data[f"users/{self.user.pk}/quizzes/{self.quiz.pk}"]
        document_id = record["document_id"]
        self.assertNotEqual(document_id, str(self.document.pk))
        self.assertIn(f"users/{self.user.pk}/documents/{document_id}", self.fake.data)

    def test_rerunning_overwrites_without_duplicating(self):
        self._run()
        self._run()
        quizzes = self.repo.list_quizzes(self.user)
        self.assertEqual(len(quizzes), 1)
        self.assertEqual(len(self.repo.list_questions(quizzes[0])), 1)

    def test_user_filter_skips_other_users(self):
        other = make_user(username="other")
        other_document = make_document(other)
        other_quiz = make_quiz(other, document=other_document)
        make_question(other_quiz)

        self._run("--user", str(self.user.pk))

        self.assertEqual(len(self.repo.list_quizzes(self.user)), 1)
        self.assertEqual(self.repo.list_quizzes(other), [])

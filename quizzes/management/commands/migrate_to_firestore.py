"""One-off: copy the SQLite business rows up to Cloud Firestore.

Django's own tables (users, sessions, admin) are never copied -- they stay on
SQLite. Only documents, quizzes, questions, attempts and responses move.

`SourceDocument` and `Attempt` are autoincrement integers in SQLite but need
string ids in Firestore, so this command derives a **deterministic** uuid5 id
from the old integer. Running it twice therefore overwrites instead of
duplicating, and a quiz keeps pointing at the same document.

Usage:
    manage.py migrate_to_firestore --dry-run
    manage.py migrate_to_firestore
    manage.py migrate_to_firestore --user 3
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from quizzes.models import Attempt, Quiz, SourceDocument
from quizzes.services.store import (
    FirestoreStore,
    FirestoreUnavailable,
    attempt_record,
    document_record,
    question_record,
    quiz_record,
    response_record,
)


def _stable_id(kind, old_pk):
    """A repeatable id for a row whose SQLite pk is an integer."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"quizcraft:{kind}:{old_pk}"))


class Command(BaseCommand):
    help = (
        "Copy the SQLite business data (documents, quizzes, questions, "
        "attempts, responses) into Cloud Firestore."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be copied without writing anything.",
        )
        parser.add_argument(
            "--user",
            type=int,
            default=None,
            help="Only migrate the data owned by this Django user id.",
        )

    def handle(self, *args, **options):
        user_id = options["user"]

        documents = SourceDocument.objects.all()
        quizzes = Quiz.objects.all()
        attempts = Attempt.objects.all()
        if user_id is not None:
            documents = documents.filter(user_id=user_id)
            quizzes = quizzes.filter(user_id=user_id)
            attempts = attempts.filter(user_id=user_id)

        documents = list(documents)
        quizzes = list(quizzes)
        attempts = list(attempts)
        questions_by_quiz = {quiz.pk: list(quiz.question_set.all()) for quiz in quizzes}
        responses_by_attempt = {
            attempt.pk: list(attempt.response_set.all()) for attempt in attempts
        }
        question_total = sum(len(items) for items in questions_by_quiz.values())
        response_total = sum(len(items) for items in responses_by_attempt.values())

        self.stdout.write(
            f"SQLite holds {len(documents)} documents, {len(quizzes)} quizzes, "
            f"{question_total} questions, {len(attempts)} attempts, "
            f"{response_total} responses."
        )

        if options["dry_run"]:
            self.stdout.write(self.style.NOTICE("Dry run: nothing written."))
            return

        try:
            store = FirestoreStore()
        except FirestoreUnavailable as exc:
            raise CommandError(str(exc))

        document_ids = {}
        for document in documents:
            record = document_record(document)
            record["id"] = _stable_id("document", document.pk)
            document_ids[document.pk] = record["id"]
            store.save_document(record)

        for quiz in quizzes:
            record = quiz_record(quiz)
            record["document_id"] = document_ids.get(
                quiz.document_id, str(quiz.document_id)
            )
            store.create_quiz(record)
            store.save_questions(
                str(quiz.user_id),
                str(quiz.pk),
                [question_record(q) for q in questions_by_quiz[quiz.pk]],
            )

        for attempt in attempts:
            record = attempt_record(attempt)
            record["id"] = _stable_id("attempt", attempt.pk)
            store.create_attempt(record)
            responses = []
            for response in responses_by_attempt[attempt.pk]:
                item = response_record(response)
                item["attempt_id"] = record["id"]
                responses.append(item)
            store.save_responses(str(attempt.user_id), record["id"], responses)

        self.stdout.write(
            self.style.SUCCESS(
                f"Copied {len(documents)} documents, {len(quizzes)} quizzes, "
                f"{question_total} questions, {len(attempts)} attempts, "
                f"{response_total} responses to Firestore."
            )
        )

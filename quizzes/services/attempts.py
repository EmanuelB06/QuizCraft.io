"""Scoring a submitted quiz.

Lives outside `views` because grading a set of answers is domain logic, not an
HTTP concern. The view supplies a callable that knows how to read the submitted
form, and everything else happens here.
"""

from ..models import Attempt, Response
from .grading import grade


def extract_submission(post, question, key):
    """Read one question's answer out of a submitted form.

    Which shape to read is the question type's business: a checkbox group posts
    several values, everything else posts one. The handler declares that, so
    this does not have to know what any particular type is.
    """
    handler = question.handler
    if handler.expects_many:
        value = post.getlist(key) if hasattr(post, "getlist") else post.get(key)
    else:
        value = post.get(key)
    return handler.extract_submission(value if value is not None else "")


def score_attempt(quiz, user, post, questions):
    """Persist an Attempt and its Responses for a submitted quiz."""
    partial_credit = bool(getattr(quiz, "partial_credit", False))
    attempt = Attempt.objects.create(
        quiz=quiz, user=user, max_score=sum(q.points for q in questions)
    )

    responses = []
    for question in questions:
        submission = extract_submission(post, question, f"q{question.pk.hex}")
        is_correct, points = grade(question, submission, partial_credit)
        responses.append(
            Response(
                attempt=attempt,
                question=question,
                user_answer=submission,
                is_correct=is_correct,
                points_earned=points,
            )
        )

    Response.objects.bulk_create(responses)
    attempt.score = int(sum(r.points_earned for r in responses))
    attempt.save(update_fields=["score"])
    return attempt

"""Orchestrates quiz generation: chunking, calling the model, saving results.

The public entry point is `run_generation(quiz_id)`, which is safe to call
from a background thread.

Validation of the model's output is delegated to the per-type handlers, so this
module has no idea what a multiple-choice question looks like.
"""

import logging
import random
import re
import threading
import time
from typing import Protocol

from django.conf import settings
from django.db import close_old_connections, transaction

from ..models import Question, QuestionType, Quiz
from ..question_types import get_handler
from ..question_types.normalize import normalize
from . import gemini

logger = logging.getLogger(__name__)


class QuestionGenerator(Protocol):
    """What this module needs from a question source.

    Declared as a protocol so the provider stays swappable: `gemini` is the
    default, but a test or an alternative vendor can supply its own object
    without this module importing or branching on any of them.
    """

    def generate_questions(self, text, quota, difficulty, types, note) -> list: ...


class NoValidQuestions(Exception):
    """The model responded, but nothing it produced was usable."""


def default_generator() -> QuestionGenerator:
    return gemini



def split_text(text, chunk_size=None, overlap=None):
    """Split text into overlapping chunks on paragraph boundaries.

    A hard character split can cut a sentence in half, which gives the model a
    context-free fragment to write a question about.
    """
    chunk_size = chunk_size or settings.CHUNK_CHARS
    overlap = settings.CHUNK_OVERLAP_CHARS if overlap is None else overlap

    if len(text) <= chunk_size:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current, length = [], [], 0

    for paragraph in paragraphs:
        if length + len(paragraph) + 2 > chunk_size and current:
            chunks.append("\n\n".join(current))
            # Carry the tail forward so facts spanning a boundary stay visible.
            tail, carried = [], 0
            for prev in reversed(current):
                if carried + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                carried += len(prev) + 2
            current, length = tail, carried

        current.append(paragraph)
        length += len(paragraph) + 2

    if current:
        chunks.append("\n\n".join(current))

    return [c for c in chunks if c.strip()]


def _allocate(count, chunk_count):
    """Distribute the requested question count across chunks as evenly as possible."""
    base, extra = divmod(count, chunk_count)
    return [base + (1 if i < extra else 0) for i in range(chunk_count)]


def validate(raw, allowed_types):
    """Turn one model response item into Question field values, or return None.

    Anything that would be unanswerable or ungradable is rejected rather than
    saved as a broken question. The rules live with the question type; this
    only routes to the right handler and honours the caller's type filter.
    """
    if not isinstance(raw, dict):
        return None
    qtype = raw.get("question_type")
    if qtype not in allowed_types:
        return None
    return get_handler(qtype).validate(raw)



def _dedupe_key(item):
    words = normalize(item["prompt"]).split()
    return " ".join(words[:12])


def _merge(candidates, target_count):
    """Deduplicate and trim to the requested number of questions.

    Items are interleaved by type so a quiz is not all multiple choice
    followed by all true/false.
    """
    seen, unique = set(), []
    for item in candidates:
        key = _dedupe_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    buckets = {}
    for item in unique:
        buckets.setdefault(item["question_type"], []).append(item)

    # Round-robin across types so the quiz is not all multiple choice followed
    # by all true/false.
    merged = []
    while len(merged) < target_count and any(buckets.values()):
        for qtype, items in buckets.items():
            if items and len(merged) < target_count:
                merged.append(items.pop(0))

    return merged[:target_count]


def run_generation(quiz_id, generator=None):
    """Generate and persist the questions for a quiz. Safe to call in a thread.

    `generator` defaults to the Gemini client; passing an alternative satisfies
    the QuestionGenerator protocol instead of reaching for the provider here.
    """
    close_old_connections()
    try:
        _run_generation(quiz_id, generator or default_generator())
    except Exception as exc:
        logger.exception("Quiz generation failed for %s", quiz_id)
        Quiz.objects.filter(pk=quiz_id).update(
            status=Quiz.Status.FAILED, error_message=str(exc)[:2000]
        )
    finally:
        close_old_connections()


def _run_generation(quiz_id, generator):
    quiz = Quiz.objects.select_related("document").get(pk=quiz_id)
    document = quiz.document
    types = [t for t in (quiz.question_types or []) if t in QuestionType.values]
    if not types:
        types = list(QuestionType.values)

    chunks = split_text(document.extracted_text)
    # Ask for a little extra per chunk so de-duplication still leaves enough.
    per_chunk = _allocate(max(quiz.question_count + 4, len(chunks) * 2), len(chunks))

    quiz.status = Quiz.Status.PROCESSING
    quiz.progress_done = 0
    quiz.progress_total = len(chunks)
    quiz.save(update_fields=["status", "progress_done", "progress_total"])

    candidates = []
    for index, (chunk, quota) in enumerate(zip(chunks, per_chunk), start=1):
        note = f", part {index} of {len(chunks)}" if len(chunks) > 1 else ""
        raw_questions = generator.generate_questions(
            chunk, quota, quiz.difficulty, types, note
        )
        for raw in raw_questions:
            item = validate(raw, set(types))
            if item:
                candidates.append(item)

        quiz.progress_done = index
        quiz.save(update_fields=["progress_done"])

        if index < len(chunks):
            # The free tier allows roughly 15 requests a minute. Pacing here is
            # what keeps a multi-chunk document from tripping the limit.
            time.sleep(random.uniform(2.5, 4.5))

    final = _merge(candidates, quiz.question_count)
    if not final:
        raise NoValidQuestions(
            "The AI did not return any usable questions for this document. "
            "Try a longer file, or a different question count."
        )

    with transaction.atomic():
        quiz.question_set.all().delete()
        Question.objects.bulk_create(
            [
                Question(quiz=quiz, order=position, **item)
                for position, item in enumerate(final, start=1)
            ]
        )
        Quiz.objects.filter(pk=quiz_id).update(
            status=Quiz.Status.READY,
            progress_done=len(chunks),
            question_count=len(final),
            error_message="",
        )


def start_background_generation(quiz, generator=None):
    """Kick off generation without blocking the request/response cycle."""
    thread = threading.Thread(
        target=run_generation,
        args=(str(quiz.pk), generator),
        daemon=True,
        name=f"quizgen-{quiz.pk}",
    )
    thread.start()
    return thread


__all__ = [
    "run_generation",
    "start_background_generation",
    "split_text",
    "validate",
    "NoValidQuestions",
]

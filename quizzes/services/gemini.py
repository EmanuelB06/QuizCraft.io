"""The only module that talks to the AI provider.

Everything provider-specific lives here so switching to another model vendor
means rewriting this file and nothing else.
"""

import json
import logging
import random
import time

from django.conf import settings

logger = logging.getLogger(__name__)

class GenerationError(Exception):
    """Raised when the provider cannot produce a usable result."""


class MissingAPIKeyError(GenerationError):
    pass


def api_key():
    key = settings.GEMINI_API_KEY
    if not key:
        raise MissingAPIKeyError(
            "No Gemini API key is configured. Get a free key at "
            "https://aistudio.google.com/apikey and add it to your .env file as "
            "GEMINI_API_KEY."
        )
    return key


def build_schema(question_type_choices):
    """Response schema for a batch of generated questions.

    Using a schema (rather than parsing prose) means the model is constrained
    to the exact shape the app expects.
    """
    from google.genai import types

    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "questions": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "question_type": types.Schema(
                            type=types.Type.STRING,
                            enum=list(question_type_choices),
                        ),
                        "prompt": types.Schema(type=types.Type.STRING),
                        "choices": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description=(
                                "Exactly 4 options for multiple choice questions, "
                                "in display order. Empty array for other types."
                            ),
                        ),
                        "correct_indexes": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.INTEGER),
                            description=(
                                "Indexes into `choices` that are correct. Exactly one "
                                "for single-answer questions, two or three for "
                                "multiple-answer. [0] for true/false, short answer "
                                "and fill-in-the-blank."
                            ),
                        ),
                        "correct_answer": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "The answer text for short answer, fill in the blank "
                                "and true/false questions. Empty string otherwise."
                            ),
                        ),
                        "acceptable_answers": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                            description=(
                                "Alternative phrasings of correct_answer that should "
                                "also be accepted. Empty array otherwise."
                            ),
                        ),
                        "explanation": types.Schema(
                            type=types.Type.STRING,
                            description="One sentence on why the answer is correct.",
                        ),
                    },
                    required=[
                        "question_type",
                        "prompt",
                        "choices",
                        "correct_indexes",
                        "correct_answer",
                        "explanation",
                    ],
                ),
            )
        },
        required=["questions"],
    )


SYSTEM_INSTRUCTION = """\
You are an expert exam writer. You create quiz questions that are answered
strictly from the source material the user provides.

Rules you must always follow:
1. Every question must be answerable using ONLY the source text. Never introduce
   outside knowledge, and never speculate.
2. Each question must have exactly one unambiguously correct answer. Avoid
   wording that allows more than one defensible reading.
3. For multiple choice, all four options must be plausible and of similar
   length. Exactly one is correct for single-answer, two or three for
   multiple-answer. Never use "All of the above" or "None of the above".
4. Never number a question or reveal the answer inside the prompt text.
5. Vary what you test: definitions, mechanisms, comparisons, causes and
   effects, application, and recall.
6. Do not repeat a question you have already written, and do not write two
   questions that test the same fact.
7. Keep prompts under 40 words and options under 15 words each.
8. Match the requested difficulty. Easy is recall, medium is comprehension,
   hard requires analysis or synthesis of two parts of the text.
9. For true/false, the question must be a plain statement with no question mark,
   and set correct_answer to "true" or "false".
10. For fill in the blank, write the prompt with the blank marked as "_____" and
    put only the missing word or phrase in correct_answer.
11. The explanation must state why the answer is right, grounded in the source.

Return only questions supported by the given source text.\
"""


def _build_prompt(chunk_text, count, difficulty, question_types, chunk_note):
    type_list = ", ".join(question_types)
    return (
        f"Source material{chunk_note}:\n\n"
        f"---\n{chunk_text}\n---\n\n"
        f"Write {count} quiz questions based only on the source material above.\n"
        f"Difficulty: {difficulty}.\n"
        f"Use only these question types: {type_list}.\n"
        f"Spread the types as evenly as the count allows."
    )


def _is_rate_limited(exc):
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "resource_exhausted" in text


def _is_daily_quota_exceeded(exc):
    """A daily cap will not clear by waiting minutes, so it needs its own message."""
    text = str(exc).lower()
    return "quota" in text and ("per day" in text or "daily" in text)


def generate_questions(text, count, difficulty, question_types, chunk_note=""):
    """Ask the model for `count` questions about `text`.

    Returns a list of dicts matching the response schema.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key())

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=0.7,
        response_mime_type="application/json",
        response_schema=build_schema(question_types),
    )
    prompt = _build_prompt(text, count, difficulty, question_types, chunk_note)

    last_error = None
    for attempt in range(1, settings.GEMINI_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL, contents=prompt, config=config
            )
            return _parse_response(response)
        except MissingAPIKeyError:
            raise
        except Exception as exc:
            last_error = exc
            if _is_daily_quota_exceeded(exc):
                raise GenerationError(
                    "You have used up today's free Gemini quota. It resets daily, "
                    "so try again tomorrow."
                ) from exc
            if not _is_rate_limited(exc) or attempt == settings.GEMINI_MAX_RETRIES:
                raise GenerationError(f"The AI request failed: {exc}") from exc

            # Jitter keeps concurrent workers from retrying in lockstep.
            delay = settings.GEMINI_BACKOFF_BASE * (2 ** (attempt - 1))
            delay += random.uniform(0, 1.5)
            logger.warning(
                "Rate limited by provider (attempt %s/%s), retrying in %.1fs",
                attempt,
                settings.GEMINI_MAX_RETRIES,
                delay,
            )
            time.sleep(delay)

    raise GenerationError(f"The AI request failed: {last_error}")


def _parse_response(response):
    if getattr(response, "prompt_feedback", None) and getattr(
        response.prompt_feedback, "block_reason", None
    ):
        raise GenerationError(
            f"The document was rejected by the provider's safety filter "
            f"({response.prompt_feedback.block_reason})."
        )

    text = getattr(response, "text", None)
    if not text:
        raise GenerationError("The AI returned an empty response.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        # Rare, but the model can wrap JSON in prose despite the schema.
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise GenerationError("The AI returned a response that was not valid JSON.") from exc
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise GenerationError("The AI returned a response that was not valid JSON.") from exc

    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        return []
    return questions

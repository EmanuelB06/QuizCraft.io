from unittest.mock import patch

from django.test import TestCase

from quizzes.models import QuestionType, Quiz
from quizzes.services import gemini
from quizzes.services.generation import (
    _allocate,
    _merge,
    run_generation,
    split_text,
    validate,
)

from .factories import LONG_TEXT, make_document, make_quiz, make_user

ALLOWED = set(QuestionType.values)


def sample(question_type=QuestionType.MCQ_SINGLE, prompt="Which organelle makes ATP?", **overrides):
    data = {
        "question_type": question_type,
        "prompt": prompt,
        "choices": ["Nucleus", "Mitochondrion", "Ribosome", "Golgi body"],
        "correct_indexes": [1],
        "correct_answer": "",
        "acceptable_answers": [],
        "explanation": "Mitochondria produce most cellular ATP.",
    }
    data.update(overrides)
    return data


class SplitTextTests(TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(split_text("Short enough."), ["Short enough."])

    def test_long_text_splits(self):
        text = "\n\n".join(f"Paragraph number {n} about cells." for n in range(200))
        chunks = split_text(text, chunk_size=500, overlap=50)
        self.assertGreater(len(chunks), 1)

    def test_chunks_respect_the_size_limit(self):
        text = "\n\n".join(f"Paragraph number {n} about cells." for n in range(200))
        for chunk in split_text(text, chunk_size=500, overlap=50):
            # A single paragraph longer than the limit can still overflow,
            # but nothing should be wildly over.
            self.assertLessEqual(len(chunk), 900)

    def test_no_content_is_lost(self):
        text = "\n\n".join(f"Unique marker {n} about cells." for n in range(120))
        joined = " ".join(split_text(text, chunk_size=400, overlap=40))
        for n in (0, 60, 119):
            self.assertIn(f"Unique marker {n}", joined)

    def test_boundary_context_is_carried_over(self):
        text = "\n\n".join(f"Fact about topic {n} in biology." for n in range(60))
        chunks = split_text(text, chunk_size=200, overlap=80)
        # Some paragraph should appear in more than one chunk, otherwise a
        # fact spanning a boundary is invisible to the model.
        appearances = sum(text.count(p) for c in chunks for p in c.split("\n\n") if p)
        self.assertGreater(appearances, 60)

    def test_realistic_document(self):
        self.assertGreaterEqual(len(split_text(LONG_TEXT)), 1)


class AllocateTests(TestCase):
    def test_even_split(self):
        self.assertEqual(_allocate(10, 2), [5, 5])

    def test_remainder_goes_to_the_front(self):
        self.assertEqual(_allocate(10, 3), [4, 3, 3])

    def test_exact_multiple(self):
        self.assertEqual(_allocate(9, 3), [3, 3, 3])


class ValidateTests(TestCase):
    def test_valid_single_choice(self):
        result = validate(sample(), ALLOWED)
        self.assertIsNotNone(result)
        self.assertEqual(result["correct_choices"], [1])
        self.assertEqual(len(result["choices"]), 4)

    def test_duplicate_choices_are_removed(self):
        # The model keyed original position 2, which is "B". After de-duplication
        # "B" sits at position 1.
        result = validate(
            sample(choices=["A", "A", "B", "C"], correct_indexes=[2]), ALLOWED
        )
        self.assertEqual(result["choices"], ["A", "B", "C"])
        self.assertEqual(result["correct_choices"], [1])
        self.assertEqual(result["choices"][1], "B")

    def test_dedup_remaps_the_answer_key(self):
        # The model keyed option 1 (the first "A"). After de-duplication "A"
        # sits at position 0, so the key must follow it there.
        result = validate(
            sample(choices=["A", "A", "B", "C"], correct_indexes=[1]), ALLOWED
        )
        self.assertEqual(result["choices"], ["A", "B", "C"])
        self.assertEqual(result["correct_choices"], [0])
        self.assertEqual(result["choices"][result["correct_choices"][0]], "A")

    def test_dedup_keeps_later_key_intact(self):
        result = validate(
            sample(choices=["A", "B", "B", "C"], correct_indexes=[3]), ALLOWED
        )
        self.assertEqual(result["correct_choices"], [2])
        self.assertEqual(result["choices"][2], "C")

    def test_wrong_type_is_rejected(self):
        self.assertIsNone(validate(sample(question_type="essay"), ALLOWED))

    def test_short_prompt_is_rejected(self):
        self.assertIsNone(validate(sample(prompt="Why?"), ALLOWED))

    def test_too_few_choices_is_rejected(self):
        self.assertIsNone(validate(sample(choices=["Only one"]), ALLOWED))

    def test_out_of_range_index_is_dropped(self):
        result = validate(sample(correct_indexes=[9]), ALLOWED)
        self.assertIsNone(result)

    def test_single_answer_needs_exactly_one_key(self):
        self.assertIsNone(validate(sample(correct_indexes=[0, 1]), ALLOWED))

    def test_multi_answer_needs_at_least_two_keys(self):
        result = validate(
            sample(question_type=QuestionType.MCQ_MULTI, correct_indexes=[1]), ALLOWED
        )
        self.assertIsNone(result)

    def test_valid_multi_answer(self):
        result = validate(
            sample(question_type=QuestionType.MCQ_MULTI, correct_indexes=[0, 2]), ALLOWED
        )
        self.assertEqual(result["correct_choices"], [0, 2])

    def test_true_false(self):
        result = validate(
            sample(question_type=QuestionType.TRUE_FALSE, correct_answer="true"), ALLOWED
        )
        self.assertEqual(result["choices"], ["True", "False"])
        self.assertEqual(result["correct_choices"], [0])
        self.assertEqual(result["acceptable_answers"], ["true"])

    def test_true_false_needs_a_boolean_answer(self):
        result = validate(
            sample(question_type=QuestionType.TRUE_FALSE, correct_answer="maybe"), ALLOWED
        )
        self.assertIsNone(result)

    def test_fill_blank_needs_a_blank_marker(self):
        bad = validate(
            sample(
                question_type=QuestionType.FILL_BLANK,
                prompt="Mitochondria are the _____ of the cell.",
                correct_answer="powerhouse",
            ),
            ALLOWED,
        )
        self.assertIsNotNone(bad)

        good = validate(
            sample(
                question_type=QuestionType.FILL_BLANK,
                prompt="Mitochondria are the _____ of the cell.",
                correct_answer="powerhouse",
            ),
            ALLOWED,
        )
        self.assertIsNotNone(good)

    def test_fill_blank_without_underscores_is_rejected(self):
        result = validate(
            sample(
                question_type=QuestionType.FILL_BLANK,
                prompt="Mitochondria are the powerhouse of the cell.",
                correct_answer="mitochondria",
            ),
            ALLOWED,
        )
        self.assertIsNone(result)

    def test_short_answer_needs_an_answer(self):
        result = validate(
            sample(question_type=QuestionType.SHORT_ANSWER, correct_answer=""), ALLOWED
        )
        self.assertIsNone(result)

    def test_alternatives_are_deduplicated(self):
        result = validate(
            sample(
                question_type=QuestionType.SHORT_ANSWER,
                correct_answer="Mitochondria",
                acceptable_answers=["mitochondria", "mitochondrion"],
            ),
            ALLOWED,
        )
        self.assertEqual(len(result["acceptable_answers"]), 2)

    def test_non_dict_is_rejected(self):
        self.assertIsNone(validate("just a string", ALLOWED))
        self.assertIsNone(validate(None, ALLOWED))


class MergeTests(TestCase):
    def test_duplicates_are_dropped(self):
        items = [validate(sample(prompt="Which organelle makes ATP?"), ALLOWED)] * 3
        self.assertEqual(len(_merge(items, 5)), 1)

    def test_trimmed_to_target(self):
        items = [validate(sample(prompt=f"Question number {n}?"), ALLOWED) for n in range(20)]
        self.assertEqual(len(_merge(items, 6)), 6)

    def test_types_are_interleaved(self):
        items = []
        for n in range(3):
            items.append(validate(sample(prompt=f"Choice question {n}?"), ALLOWED))
            items.append(
                validate(
                    sample(
                        question_type=QuestionType.TRUE_FALSE,
                        prompt=f"Statement {n} is true.",
                        correct_answer="true",
                    ),
                    ALLOWED,
                )
            )
        types = [item["question_type"] for item in _merge(items, 4)]
        # Alternating, not all of one type followed by all of the other.
        self.assertEqual(types[0], QuestionType.MCQ_SINGLE)
        self.assertEqual(types[1], QuestionType.TRUE_FALSE)
        self.assertEqual(types[2], QuestionType.MCQ_SINGLE)

    def test_empty_input(self):
        self.assertEqual(_merge([], 5), [])


class RunGenerationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.document = make_document(self.user, text=LONG_TEXT)
        self.quiz = make_quiz(self.user, self.document, question_count=3)

    @patch("quizzes.services.generation.time.sleep", return_value=None)
    @patch("quizzes.services.generation.gemini.generate_questions")
    def test_successful_generation_persists_questions(self, mock_generate, mock_sleep):
        mock_generate.return_value = [
            sample(prompt="Which organelle produces ATP?"),
            sample(prompt="Where does glycolysis occur?", correct_indexes=[0]),
            sample(
                question_type=QuestionType.TRUE_FALSE,
                prompt="Chloroplasts contain chlorophyll.",
                correct_answer="true",
            ),
        ]

        run_generation(self.quiz.pk)

        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.status, Quiz.Status.READY)
        self.assertEqual(self.quiz.question_set.count(), 3)
        self.assertEqual(list(self.quiz.questions.values_list("order", flat=True)), [1, 2, 3])
        self.assertEqual(self.quiz.error_message, "")

    @patch("quizzes.services.generation.time.sleep", return_value=None)
    @patch("quizzes.services.generation.gemini.generate_questions")
    def test_invalid_questions_are_dropped_not_saved(self, mock_generate, mock_sleep):
        mock_generate.return_value = [
            sample(prompt="A perfectly good question?"),
            sample(prompt="Bad"),  # too short
            {"nonsense": True},
        ]

        run_generation(self.quiz.pk)

        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.question_set.count(), 1)
        self.assertEqual(self.quiz.status, Quiz.Status.READY)

    @patch("quizzes.services.generation.time.sleep", return_value=None)
    @patch("quizzes.services.generation.gemini.generate_questions")
    def test_all_invalid_questions_fails_cleanly(self, mock_generate, mock_sleep):
        mock_generate.return_value = [sample(prompt="Bad"), {"nonsense": True}]

        run_generation(self.quiz.pk)

        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.status, Quiz.Status.FAILED)
        self.assertIn("usable questions", self.quiz.error_message)
        self.assertEqual(self.quiz.question_set.count(), 0)

    @patch("quizzes.services.generation.gemini.generate_questions")
    def test_provider_error_is_captured_on_the_quiz(self, mock_generate):
        mock_generate.side_effect = gemini.GenerationError("Daily quota reached.")

        run_generation(self.quiz.pk)

        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.status, Quiz.Status.FAILED)
        self.assertIn("quota", self.quiz.error_message.lower())

    @patch("quizzes.services.generation.time.sleep", return_value=None)
    @patch("quizzes.services.generation.gemini.generate_questions")
    def test_long_document_calls_the_provider_per_chunk(self, mock_generate, mock_sleep):
        self.document.extracted_text = "\n\n".join(
            f"Section {n} covers a distinct biological process in detail." for n in range(600)
        )
        self.document.save(update_fields=["extracted_text"])

        mock_generate.return_value = [sample(prompt=f"Question about section {mock_generate.call_count}?")]

        with self.settings(CHUNK_CHARS=1000, CHUNK_OVERLAP_CHARS=100):
            run_generation(self.quiz.pk)

        self.assertGreater(mock_generate.call_count, 1)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.status, Quiz.Status.READY)

    @patch("quizzes.services.generation.time.sleep", return_value=None)
    @patch("quizzes.services.generation.gemini.generate_questions")
    def test_regeneration_replaces_old_questions(self, mock_generate, mock_sleep):
        from .factories import make_question

        make_question(self.quiz, prompt="Old question from a previous run")
        self.assertEqual(self.quiz.question_set.count(), 1)

        mock_generate.return_value = [sample(prompt="A brand new question?")]
        run_generation(self.quiz.pk)

        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.question_set.count(), 1)
        self.assertEqual(
            self.quiz.questions.first().prompt, "A brand new question?"
        )

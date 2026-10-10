from decimal import Decimal

from django.test import SimpleTestCase

from quizzes.services.grading import format_answer, grade, normalize


class FakeQuestion:
    def __init__(self, question_type, points=1, choices=None, correct=None, acceptable=None):
        self.question_type = question_type
        self.points = points
        self.choices = choices or []
        self.correct_choices = correct or []
        self.acceptable_answers = acceptable or []


class NormalizeTests(SimpleTestCase):
    def test_lowercases_and_strips_punctuation(self):
        self.assertEqual(normalize("The Cell."), "cell")
        self.assertEqual(normalize("  CELL  "), "cell")

    def test_drops_articles(self):
        self.assertEqual(normalize("a mitochondrion"), "mitochondrion")
        self.assertEqual(normalize("the nucleus"), "nucleus")

    def test_keeps_numbers(self):
        self.assertEqual(normalize("Chapter 12"), "chapter 12")

    def test_handles_none(self):
        self.assertEqual(normalize(None), "")


class GradeMultipleChoiceTests(SimpleTestCase):
    def setUp(self):
        self.question = FakeQuestion(
            "mcq_single", choices=["A", "B", "C", "D"], correct=[2]
        )

    def test_correct_index_scores_full_points(self):
        correct, points = grade(self.question, 2)
        self.assertTrue(correct)
        self.assertEqual(points, Decimal(1))

    def test_wrong_index_scores_zero(self):
        correct, points = grade(self.question, 0)
        self.assertFalse(correct)
        self.assertEqual(points, Decimal(0))

    def test_single_element_list_is_accepted(self):
        correct, _ = grade(self.question, [2])
        self.assertTrue(correct)

    def test_multiple_selection_is_rejected_for_single_answer(self):
        correct, points = grade(self.question, [1, 2])
        self.assertFalse(correct)
        self.assertEqual(points, Decimal(0))

    def test_garbage_input_is_rejected(self):
        correct, _ = grade(self.question, "not-a-number")
        self.assertFalse(correct)


class GradeMultiSelectTests(SimpleTestCase):
    def setUp(self):
        self.question = FakeQuestion(
            "mcq_multi", points=3, choices=["A", "B", "C", "D"], correct=[0, 2]
        )

    def test_exact_set_is_correct(self):
        correct, points = grade(self.question, [2, 0])
        self.assertTrue(correct)
        self.assertEqual(points, Decimal(3))

    def test_missing_answer_is_incorrect_with_no_credit(self):
        correct, points = grade(self.question, [0])
        self.assertFalse(correct)
        self.assertEqual(points, Decimal(0))

    def test_extra_answer_is_incorrect_with_no_credit(self):
        correct, points = grade(self.question, [0, 1, 2])
        self.assertFalse(correct)
        self.assertEqual(points, Decimal(0))

    def test_empty_answer_is_incorrect(self):
        correct, points = grade(self.question, [])
        self.assertFalse(correct)
        self.assertEqual(points, Decimal(0))


class GradeMultiSelectPartialCreditTests(SimpleTestCase):
    """Partial credit is opt-in via the quiz's `partial_credit` setting."""

    def setUp(self):
        self.question = FakeQuestion(
            "mcq_multi", points=4, choices=["A", "B", "C", "D"], correct=[0, 1]
        )

    def test_one_of_two_correct_earns_half(self):
        _, points = grade(self.question, [0], partial_credit=True)
        self.assertEqual(points, Decimal(2))

    def test_wrong_picks_cancel_correct_ones(self):
        _, points = grade(self.question, [0, 2, 3], partial_credit=True)
        self.assertEqual(points, Decimal(0))

    def test_selecting_everything_earns_nothing(self):
        correct, points = grade(self.question, [0, 1, 2, 3], partial_credit=True)
        self.assertFalse(correct)
        self.assertEqual(points, Decimal(0))

    def test_partial_credit_disabled_gives_zero(self):
        _, points = grade(self.question, [0], partial_credit=False)
        self.assertEqual(points, Decimal(0))

    def test_exact_set_still_scores_full_marks(self):
        correct, points = grade(self.question, [0, 1], partial_credit=True)
        self.assertTrue(correct)
        self.assertEqual(points, Decimal(4))


class GradeTrueFalseTests(SimpleTestCase):
    def setUp(self):
        self.question = FakeQuestion("true_false", acceptable=["true"])

    def test_true_answers_true(self):
        correct, points = grade(self.question, "true")
        self.assertTrue(correct)
        self.assertEqual(points, Decimal(1))

    def test_case_and_whitespace_ignored(self):
        self.assertTrue(grade(self.question, "  True ")[0])

    def test_false_is_wrong(self):
        self.assertFalse(grade(self.question, "false")[0])

    def test_unrecognised_answer_is_wrong(self):
        self.assertFalse(grade(self.question, "maybe")[0])

    def test_empty_answer_is_wrong(self):
        self.assertFalse(grade(self.question, "")[0])


class GradeTextTests(SimpleTestCase):
    def setUp(self):
        self.question = FakeQuestion(
            "short_answer", acceptable=["mitochondria", "the mitochondria"]
        )

    def test_exact_match(self):
        self.assertTrue(grade(self.question, "Mitochondria")[0])

    def test_article_insensitive(self):
        self.assertTrue(grade(self.question, "the mitochondria")[0])

    def test_punctuation_insensitive(self):
        self.assertTrue(grade(self.question, "mitochondria.")[0])

    def test_answer_contained_in_longer_response_is_accepted(self):
        correct, _ = grade(self.question, "i think it is the mitochondria")
        self.assertTrue(correct)

    def test_wrong_answer(self):
        self.assertFalse(grade(self.question, "nucleus")[0])

    def test_empty_answer(self):
        self.assertFalse(grade(self.question, "   ")[0])

    def test_question_with_no_accepted_answers_never_correct(self):
        self.assertFalse(grade(FakeQuestion("fill_blank", acceptable=[]), "anything")[0])


class FormatAnswerTests(SimpleTestCase):
    def test_multiple_choice_index_becomes_text(self):
        question = FakeQuestion("mcq_single", choices=["A", "B", "C"], correct=[1])
        self.assertEqual(format_answer(question, 2), "C")

    def test_multi_select_joins_texts(self):
        question = FakeQuestion("mcq_multi", choices=["A", "B", "C"], correct=[0, 2])
        self.assertEqual(format_answer(question, [0, 2]), "A, C")

    def test_true_false_is_readable(self):
        question = FakeQuestion("true_false", acceptable=["true"])
        self.assertEqual(format_answer(question, "true"), "True")

    def test_out_of_range_index_falls_back(self):
        question = FakeQuestion("mcq_single", choices=["A"], correct=[0])
        self.assertEqual(format_answer(question, 9), "No answer")

    def test_missing_answer(self):
        question = FakeQuestion("mcq_single", choices=["A"], correct=[0])
        self.assertEqual(format_answer(question, ""), "No answer")

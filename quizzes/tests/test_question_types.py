"""Tests for the question-type registry and the handler contract.

These lock in the property the whole refactor rests on: the registry is the only
place that knows what a question type means, and it stays in step with the
`QuestionType` enum and with the model's own field.
"""

from django.test import SimpleTestCase

from quizzes.models import Question, QuestionType
from quizzes.question_types import (
    DEFAULT,
    REGISTRY,
    choice_based_types,
    get_handler,
    label_for,
    labels,
)
from quizzes.question_types.base import (
    dedupe_choices,
    remap_indexes,
    resolve_choice_selection,
)


class RegistryParityTests(SimpleTestCase):
    """The registry and the enum must describe the same set of types."""

    def test_every_enum_value_has_a_handler(self):
        self.assertEqual(set(REGISTRY), set(QuestionType.values))

    def test_no_handler_declares_an_unregistered_value(self):
        for value, handler in REGISTRY.items():
            with self.subTest(value=value):
                self.assertEqual(value, handler.type_value)

    def test_no_value_is_claimed_twice(self):
        values = [handler.type_value for handler in REGISTRY.values()]
        self.assertEqual(len(values), len(set(values)))

    def test_enum_choices_and_registry_labels_agree(self):
        for value, label in QuestionType.choices:
            with self.subTest(value=value):
                self.assertEqual(label, label_for(value))

    def test_labels_returns_one_pair_per_type_in_order(self):
        self.assertEqual(labels(), [(h.type_value, h.label) for h in REGISTRY.values()])

    def test_every_handler_has_a_label_and_a_template(self):
        for value, handler in REGISTRY.items():
            with self.subTest(value=value):
                self.assertTrue(handler.label, f"{value} has no label")
                self.assertTrue(handler.input_template, f"{value} has no template")

    def test_unknown_type_falls_back_rather_than_raising(self):
        # A row written by a future version should still display.
        self.assertEqual(get_handler("no-such-type").type_value, DEFAULT)

    def test_choice_based_types_are_exactly_the_two_mcqs(self):
        self.assertEqual(choice_based_types(), {"mcq_single", "mcq_multi"})


class QuestionDelegationTests(SimpleTestCase):
    """The model should be a thin forwarder, not a second source of truth."""

    def test_handler_comes_from_the_registry(self):
        question = Question(question_type=QuestionType.MCQ_MULTI)
        self.assertIs(question.handler, REGISTRY[QuestionType.MCQ_MULTI])

    def test_type_label_comes_from_the_handler(self):
        question = Question(question_type=QuestionType.FILL_BLANK)
        self.assertEqual(question.type_label, REGISTRY[QuestionType.FILL_BLANK].label)

    def test_type_label_is_never_blank(self):
        for value in QuestionType.values:
            with self.subTest(value=value):
                self.assertNotEqual(label_for(value).strip(), "")

    def test_is_multi_matches_the_input_kind(self):
        self.assertTrue(Question(question_type=QuestionType.MCQ_MULTI).is_multi)
        self.assertFalse(Question(question_type=QuestionType.MCQ_SINGLE).is_multi)

    def test_true_false_is_not_choice_based_despite_owning_options(self):
        # The flag drives the edit screen, which authors true/false as text.
        question = Question(question_type=QuestionType.TRUE_FALSE)
        self.assertFalse(question.is_choice_based)
        self.assertEqual(question.handler.CHOICES, ("True", "False"))


class ExpectsManyTests(SimpleTestCase):
    """Only a checkbox group posts several values."""

    def test_only_multi_select_expects_many(self):
        many = {v for v, h in REGISTRY.items() if h.expects_many}
        self.assertEqual(many, {"mcq_multi"})

    def test_single_choice_ignores_a_list_and_falls_back(self):
        handler = REGISTRY["mcq_single"]
        self.assertEqual(handler.extract_submission("2"), 2)
        self.assertEqual(handler.extract_submission(""), "")
        self.assertEqual(handler.extract_submission(None), "")

    def test_multi_keeps_several_indexes(self):
        handler = REGISTRY["mcq_multi"]
        self.assertEqual(handler.extract_submission(["0", "2", "9"]), [0, 2, 9])
        self.assertEqual(handler.extract_submission("1"), [1])
        self.assertEqual(handler.extract_submission(""), [])

    def test_typed_types_return_a_stripped_string(self):
        for value in ("short_answer", "fill_blank"):
            with self.subTest(value=value):
                self.assertEqual(REGISTRY[value].extract_submission("  hi  "), "hi")

    def test_true_false_passes_the_word_through_untouched(self):
        # It is compared via to_bool(), which normalises; no need to re-strip.
        self.assertEqual(REGISTRY["true_false"].extract_submission("true"), "true")


class AnswerViewTests(SimpleTestCase):
    """The student view must never carry the key."""

    def test_choice_view_hides_correct_answers_until_revealed(self):
        question = Question(
            question_type=QuestionType.MCQ_SINGLE,
            choices=["A", "B"],
            correct_choices=[1],
        )
        hidden = question.answer_view
        self.assertEqual(hidden.kind, "single")
        self.assertFalse(any(o.is_correct for o in hidden.options))
        self.assertTrue(question.handler.answer_view(question, reveal=True).options[1].is_correct)

    def test_text_view_hides_the_accepted_answer_until_revealed(self):
        question = Question(
            question_type=QuestionType.SHORT_ANSWER, acceptable_answers=["mitochondrion"]
        )
        self.assertNotIn("mitochondrion", question.answer_view.placeholder)
        revealed = question.handler.answer_view(question, reveal=True)
        self.assertIn("mitochondrion", revealed.placeholder)

    def test_booleans_expose_exactly_two_options(self):
        question = Question(question_type=QuestionType.TRUE_FALSE, correct_choices=[0])
        view = question.answer_view
        self.assertEqual(view.kind, "boolean")
        self.assertEqual([o.text for o in view.options], ["True", "False"])

    def test_view_is_cached_per_instance(self):
        question = Question(question_type=QuestionType.MCQ_SINGLE, choices=["A", "B"])
        self.assertIs(question.answer_view, question.answer_view)


class ApplyEditedTests(SimpleTestCase):
    """Writing an edit back must preserve the position of the key."""

    def _mcq(self, **kwargs):
        values = {
            "question_type": QuestionType.MCQ_SINGLE,
            "choices": ["A", "B", "C"],
            "correct_choices": [1],
            "acceptable_answers": [],
        }
        values.update(kwargs)
        return Question(**values)

    def test_key_follows_the_option_when_an_earlier_one_is_blanked(self):
        # This is the path the edit form takes: the form resolves the posted
        # options, then the handler stores the already-rekeyed result.
        question = self._mcq()
        choices, correct = resolve_choice_selection(["", "B", "C"], [1])
        kept = question.handler.apply_edited(question, {"choices": choices, "correct": correct})
        self.assertTrue(kept)
        self.assertEqual(question.choices, ["B", "C"])
        self.assertEqual(question.choices[question.correct_choices[0]], "B")

    def test_blanking_every_option_drops_the_question(self):
        question = self._mcq()
        self.assertFalse(question.handler.apply_edited(question, {"choices": ["", ""], "correct": []}))

    def test_dropping_a_later_option_leaves_the_key_alone(self):
        question = self._mcq()
        choices, correct = resolve_choice_selection(["A", "B", ""], [1])
        question.handler.apply_edited(question, {"choices": choices, "correct": correct})
        self.assertEqual(question.choices, ["A", "B"])
        self.assertEqual(question.choices[question.correct_choices[0]], "B")

    def test_unticking_everything_falls_back_to_the_first_option(self):
        question = self._mcq()
        question.handler.apply_edited(question, {"choices": ["A", "B"], "correct": []})
        self.assertEqual(question.correct_choices, [0])

    def test_true_false_normalises_onto_its_fixed_options(self):
        question = Question(question_type=QuestionType.TRUE_FALSE)
        question.handler.apply_edited(question, {"answer": "FALSE"})
        self.assertEqual(question.acceptable_answers, ["false"])
        self.assertEqual(question.choices, ["True", "False"])
        self.assertEqual(question.correct_choices, [1])

    def test_unrecognisable_true_false_falls_back_to_true(self):
        question = Question(question_type=QuestionType.TRUE_FALSE)
        question.handler.apply_edited(question, {"answer": "maybe"})
        self.assertEqual(question.acceptable_answers, ["true"])

    def test_typed_answer_keys_position_zero(self):
        question = Question(question_type=QuestionType.SHORT_ANSWER)
        question.handler.apply_edited(question, {"answer": "ribosome", "alternatives": ""})
        self.assertEqual(question.correct_choices, [0])
        self.assertEqual(question.acceptable_answers, ["ribosome"])
        self.assertEqual(question.choices, [])

    def test_alternatives_are_split_on_commas(self):
        question = Question(question_type=QuestionType.SHORT_ANSWER)
        question.handler.apply_edited(
            question, {"answer": "mitochondrion", "alternatives": " chondrion , mito "}
        )
        self.assertEqual(question.acceptable_answers, ["mitochondrion", "chondrion", "mito"])


class OptionIndexRemapTests(SimpleTestCase):
    """One helper, so a dropped option can never silently move the key."""

    def test_dedupe_maps_the_duplicate_onto_its_twin(self):
        options, old_to_new = dedupe_choices(["A", "B", "A"])
        self.assertEqual(list(options), ["A", "B"])
        self.assertEqual(remap_indexes([2], old_to_new), [0])

    def test_blank_options_do_not_shift_later_keys(self):
        options, correct = resolve_choice_selection(["", "B", "C"], [1, 2])
        self.assertEqual(options, ["B", "C"])
        self.assertEqual(correct, [0, 1])

    def test_a_key_on_a_dropped_option_is_discarded(self):
        options, correct = resolve_choice_selection(["A", "", "C"], [0, 1])
        self.assertEqual(options, ["A", "C"])
        self.assertEqual(correct, [0])

    def test_key_is_sorted_and_deduplicated(self):
        _, correct = resolve_choice_selection(["A", "B", "C"], [2, 0, 0])
        self.assertEqual(correct, [0, 2])

import io
import re
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from quizzes.models import Attempt, QuestionType, Quiz, Response, SourceDocument
from quizzes.question_types import all_handlers
from quizzes.services.extraction import SUPPORTED_EXTENSIONS

from .factories import (
    make_document,
    make_pptx_file,
    make_question,
    make_quiz,
    make_text_file,
    make_user,
)


class UploadFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def post_upload(self, upload=None, **overrides):
        data = {
            "document": upload or make_text_file(),
            "title": "",
            "question_count": 5,
            "difficulty": "medium",
            "question_types": [t.value for t in QuestionType],
            "partial_credit": "",
        }
        data.update(overrides)
        return self.client.post(reverse("upload"), data)

    @patch("quizzes.views.creation.start_background_generation")
    def test_upload_creates_document_and_quiz(self, mock_start):
        with patch("quizzes.views.creation.extract_text", return_value=("x" * 500, 2)):
            response = self.post_upload()

        self.assertEqual(response.status_code, 302)
        quiz = Quiz.objects.get()
        self.assertEqual(quiz.user, self.user)
        self.assertEqual(quiz.status, Quiz.Status.PENDING)
        self.assertEqual(quiz.document.char_count, 500)
        self.assertEqual(quiz.document.page_count, 2)
        self.assertEqual(quiz.question_count, 5)
        mock_start.assert_called_once()

    def test_the_upload_page_offers_every_supported_extension(self):
        """A stale `accept` attribute hides the format in the file picker.

        The set is compared exactly, so a format added to the extractor without
        updating the template fails here rather than being silently unreachable.
        """
        response = self.client.get(reverse("upload"))
        self.assertEqual(response.status_code, 200)
        accept = re.search(r'accept="([^"]+)"', response.content.decode()).group(1)
        offered = {part.strip().lower() for part in accept.split(",")}
        self.assertEqual(offered, SUPPORTED_EXTENSIONS)

    @patch("quizzes.views.creation.start_background_generation")
    def test_a_pptx_upload_is_carried_through_end_to_end(self, mock_start):
        """The real extractor runs, so this covers form, storage and extraction.

        Every other upload test stubs extract_text, which would hide a format
        the form accepts but the pipeline cannot read.
        """
        response = self.post_upload(upload=make_pptx_file(name="lecture.pptx"))

        document = SourceDocument.objects.get()
        # Uploads leave a real file in MEDIA_ROOT and the suite never rolls those
        # back, so take this one with us.
        self.addCleanup(document.file.delete, save=False)
        self.addCleanup(document.delete)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(document.original_filename, "lecture.pptx")
        self.assertIn("Calvin cycle", document.extracted_text)
        self.assertEqual(document.page_count, 5)
        self.assertGreater(document.char_count, 200)
        self.assertEqual(Quiz.objects.get().status, Quiz.Status.PENDING)
        mock_start.assert_called_once()

    @patch("quizzes.views.creation.start_background_generation")
    def test_title_defaults_from_filename(self, mock_start):
        with patch("quizzes.views.creation.extract_text", return_value=("x" * 500, 0)):
            self.post_upload(upload=make_text_file(name="Biology 101 Notes.txt"))
        self.assertEqual(Quiz.objects.get().title, "biology-101-notes quiz")

    @patch("quizzes.views.creation.start_background_generation")
    def test_explicit_title_wins(self, mock_start):
        with patch("quizzes.views.creation.extract_text", return_value=("x" * 500, 0)):
            self.post_upload(title="Mitochondria Mastery")
        self.assertEqual(Quiz.objects.get().title, "Mitochondria Mastery")

    def test_extraction_failure_shows_a_form_error(self):
        from quizzes.services.extraction import ExtractionError

        with patch("quizzes.views.creation.extract_text", side_effect=ExtractionError("scan detected")):
            response = self.post_upload()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Quiz.objects.count(), 0)
        self.assertContains(response, "scan detected")

    @patch("quizzes.views.creation.start_background_generation")
    def test_extraction_failure_removes_the_uploaded_file(self, mock_start):
        from quizzes.models import SourceDocument
        from quizzes.services.extraction import ExtractionError

        with patch("quizzes.views.creation.extract_text", side_effect=ExtractionError("nope")):
            self.post_upload()

        self.assertEqual(Quiz.objects.count(), 0)
        self.assertEqual(SourceDocument.objects.count(), 0)

    def test_disallowed_file_type_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.post_upload(
            upload=SimpleUploadedFile("bad.exe", b"MZ\x90\x00", content_type="application/octet-stream")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unsupported file type")

    def test_question_count_bounds_are_enforced(self):
        self.assertEqual(self.post_upload(question_count=1).status_code, 200)
        self.assertEqual(self.post_upload(question_count=500).status_code, 200)

    def test_at_least_one_type_is_required(self):
        response = self.post_upload(question_types=[])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one question type")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("upload"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)


class StatusPollingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.quiz = make_quiz(self.user, status=Quiz.Status.PROCESSING)
        self.quiz.progress_done, self.quiz.progress_total = 1, 4
        self.quiz.save()
        self.client.force_login(self.user)

    def test_returns_progress_json(self):
        response = self.client.get(reverse("quiz_status", args=[self.quiz.pk]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "processing")
        self.assertEqual(payload["done"], 1)
        self.assertEqual(payload["total"], 4)
        self.assertEqual(payload["percentage"], 25)
        self.assertIsNone(payload["redirect"])

    def test_ready_status_includes_redirect(self):
        make_question(self.quiz)
        self.quiz.status = Quiz.Status.READY
        self.quiz.save()
        payload = self.client.get(reverse("quiz_status", args=[self.quiz.pk])).json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["redirect"], reverse("quiz_edit", args=[self.quiz.pk]))

    def test_cannot_read_someone_elses_quiz(self):
        other = make_user("intruder")
        other_quiz = make_quiz(other)
        response = self.client.get(reverse("quiz_status", args=[other_quiz.pk]))
        self.assertEqual(response.status_code, 404)


class EditFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.quiz = make_quiz(self.user, question_count=2)
        self.q1 = make_question(self.quiz, prompt="First question about cells?")
        self.q2 = make_question(
            self.quiz, question_type=QuestionType.SHORT_ANSWER, prompt="Name the powerhouse?",
            choices=[], correct_choices=[0], acceptable_answers=["mitochondria"],
        )
        self.client.force_login(self.user)

    def _post(self, **overrides):
        p1, p2 = f"q{self.q1.pk.hex}", f"q{self.q2.pk.hex}"
        data = {
            f"{p1}_prompt": "Reworded first question?",
            f"{p1}_type": QuestionType.MCQ_SINGLE,
            f"{p1}_points": 2,
            f"{p1}_explanation": "Because of biology.",
            f"{p1}_choice_0": "Nucleus",
            f"{p1}_choice_1": "Mitochondrion",
            f"{p1}_choice_2": "Ribosome",
            f"{p1}_choice_3": "Golgi body",
            f"{p1}_correct": ["2"],
            f"{p2}_prompt": "Name the powerhouse of the cell?",
            f"{p2}_type": QuestionType.SHORT_ANSWER,
            f"{p2}_points": 1,
            f"{p2}_explanation": "",
            f"{p2}_answer": "mitochondrion",
            f"{p2}_alternatives": "powerhouse, mito",
            "new1_prompt": "",
            "new2_prompt": "",
        }
        data.update(overrides)
        return self.client.post(reverse("quiz_edit", args=[self.quiz.pk]), data)

    def test_page_renders(self):
        response = self.client.get(reverse("quiz_edit", args=[self.quiz.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First question about cells?")

    def test_answer_key_is_masked_by_default(self):
        response = self.client.get(reverse("quiz_edit", args=[self.quiz.pk]))
        self.assertContains(response, 'id="question-form"')
        self.assertContains(response, "qc-key-hidden")
        self.assertContains(response, "Show answers")

    def test_answer_key_can_be_revealed(self):
        url = reverse("quiz_edit", args=[self.quiz.pk]) + "?answers=1"
        response = self.client.get(url)
        self.assertNotContains(response, "qc-key-hidden")
        self.assertContains(response, "Hide answers")
        self.assertContains(response, 'aria-expanded="true"')

    def test_edits_are_saved(self):
        self._post()
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.prompt, "Reworded first question?")
        self.assertEqual(self.q1.points, 2)
        self.assertEqual(self.q1.correct_choices, [2])
        self.assertEqual(self.q1.explanation, "Because of biology.")

    def test_alternatives_become_acceptable_answers(self):
        self._post()
        self.q2.refresh_from_db()
        self.assertEqual(self.q2.acceptable_answers, ["mitochondrion", "powerhouse", "mito"])

    def test_question_can_be_deleted(self):
        p2 = f"q{self.q2.pk.hex}"
        self._post(**{f"{p2}_delete": "on"})
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.question_set.count(), 1)
        self.assertEqual(self.quiz.question_count, 1)

    def test_blank_prompt_is_rejected(self):
        p1 = f"q{self.q1.pk.hex}"
        response = self._post(**{f"{p1}_prompt": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.quiz.question_set.count(), 2)

    def test_emptying_all_choices_deletes_the_question(self):
        p1 = f"q{self.q1.pk.hex}"
        self._post(**{f"{p1}_choice_0": "", f"{p1}_choice_1": "", f"{p1}_choice_2": "", f"{p1}_choice_3": ""})
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.question_set.count(), 1)

    def test_new_question_is_appended(self):
        self._post(
            **{
                "new1_prompt": "Which organelle makes ATP?",
                "new1_choice_0": "Nucleus",
                "new1_choice_1": "Mitochondrion",
                "new1_choice_2": "Ribosome",
                "new1_choice_3": "Golgi",
                "new1_correct": ["1"],
            }
        )
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.question_set.count(), 3)
        added = self.quiz.questions.last()
        self.assertEqual(added.prompt, "Which organelle makes ATP?")
        self.assertEqual(added.correct_choices, [1])
        self.assertEqual(added.order, 3)

    def test_new_question_key_survives_a_blank_option(self):
        # Option 1 is left empty, so positions shift and the key must follow.
        self._post(
            **{
                "new1_prompt": "Which organelle makes ATP?",
                "new1_choice_0": "Nucleus",
                "new1_choice_1": "",
                "new1_choice_2": "Mitochondrion",
                "new1_choice_3": "Golgi",
                "new1_correct": ["2"],
            }
        )
        added = self.quiz.questions.last()
        self.assertEqual(added.choices, ["Nucleus", "Mitochondrion", "Golgi"])
        self.assertEqual(added.correct_choices, [1])
        self.assertEqual(added.choices[1], "Mitochondrion")

    def test_key_follows_the_option_when_one_is_blanked(self):
        # Blanking option 0 shifts everything, so ticking position 2 must still
        # key "Golgi body" and not whatever slides into position 2.
        p1 = f"q{self.q1.pk.hex}"
        self._post(
            **{
                f"{p1}_choice_0": "",
                f"{p1}_choice_1": "Nucleus",
                f"{p1}_choice_2": "Mitochondrion",
                f"{p1}_choice_3": "Golgi body",
                f"{p1}_correct": ["3"],
            }
        )
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.choices, ["Nucleus", "Mitochondrion", "Golgi body"])
        self.assertEqual(self.q1.correct_choices, [2])
        self.assertEqual(self.q1.choices[2], "Golgi body")

    def test_cannot_edit_someone_elses_quiz(self):
        other = make_user("stranger")
        response = self.client.get(reverse("quiz_edit", args=[make_quiz(other).pk]))
        self.assertEqual(response.status_code, 404)

    def test_deleting_everything_is_refused(self):
        p1, p2 = f"q{self.q1.pk.hex}", f"q{self.q2.pk.hex}"
        response = self._post(**{f"{p1}_delete": "on", f"{p2}_delete": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one question")
        self.assertEqual(self.quiz.question_set.count(), 2)


class TakeAndGradeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.quiz = make_quiz(self.user, question_count=4, partial_credit=True)
        self.mcq = make_question(self.quiz, prompt="Which organelle makes ATP?")
        self.multi = make_question(
            self.quiz, question_type=QuestionType.MCQ_MULTI, prompt="Select both correct.",
            choices=["A", "B", "C", "D"], correct_choices=[0, 2],
        )
        self.tf = make_question(
            self.quiz, question_type=QuestionType.TRUE_FALSE, prompt="Cells have mitochondria?",
            choices=["True", "False"], correct_choices=[0], acceptable_answers=["true"],
        )
        self.blank = make_question(
            self.quiz, question_type=QuestionType.FILL_BLANK, prompt="Powerhouse of the cell is _____.",
            choices=[], correct_choices=[0], acceptable_answers=["mitochondria"],
        )
        self.client.force_login(self.user)

    def _submit(self, **answers):
        data = {}
        data[f"q{self.mcq.pk.hex}"] = 1
        data[f"q{self.multi.pk.hex}"] = ["0", "2"]
        data[f"q{self.tf.pk.hex}"] = "true"
        data[f"q{self.blank.pk.hex}"] = "mitochondria"
        data.update(answers)
        return self.client.post(reverse("quiz_take", args=[self.quiz.pk]), data)

    def test_all_correct_scores_full_marks(self):
        response = self._submit()
        self.assertEqual(response.status_code, 302)

        attempt = Attempt.objects.get()
        self.assertEqual(attempt.score, 4)
        self.assertEqual(attempt.max_score, 4)
        self.assertEqual(attempt.percentage, 100)
        self.assertEqual(attempt.response_set.count(), 4)
        self.assertTrue(all(r.is_correct for r in attempt.response_set.all()))

    def test_results_page_shows_the_score(self):
        self._submit()
        attempt = Attempt.objects.get()
        response = self.client.get(
            reverse("quiz_results", args=[self.quiz.pk, attempt.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100%")
        # Answer text, not the raw stored index.
        self.assertContains(response, "Mitochondrion")

    def test_wrong_answers_score_zero(self):
        self._submit(**{f"q{self.mcq.pk.hex}": 0})
        attempt = Attempt.objects.get()
        self.assertEqual(attempt.score, 3)

    def test_multi_select_partial_credit(self):
        self._submit(**{f"q{self.multi.pk.hex}": ["0"]})
        response = Response.objects.get(question=self.multi)
        self.assertFalse(response.is_correct)
        self.assertEqual(float(response.points_earned), 0.5)

    def test_multi_select_exact_set_is_correct(self):
        self._submit()
        response = Response.objects.get(question=self.multi)
        self.assertTrue(response.is_correct)
        self.assertEqual(float(response.points_earned), 1.0)

    def test_fill_blank_is_case_insensitive(self):
        self._submit(**{f"q{self.blank.pk.hex}": "  MITOCHONDRIA "})
        attempt_response = Response.objects.get(question=self.blank)
        self.assertTrue(attempt_response.is_correct)

    def test_blank_answers_are_graded_as_wrong_not_skipped(self):
        data = {f"q{self.mcq.pk.hex}": 1}
        self.client.post(reverse("quiz_take", args=[self.quiz.pk]), data)
        attempt = Attempt.objects.get()
        self.assertEqual(attempt.response_set.count(), 4)
        self.assertEqual(attempt.score, 1)

    def test_repeated_attempts_are_separate(self):
        self._submit()
        self._submit()
        self.assertEqual(Attempt.objects.count(), 2)

    def test_take_page_renders_all_questions(self):
        response = self.client.get(reverse("quiz_take", args=[self.quiz.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Which organelle makes ATP?")
        # The type label for multi-select must render, not come out blank.
        self.assertContains(response, "Multiple answer")
        self.assertContains(response, "Tick every option that applies.")
        self.assertContains(response, "Multiple choice")
    def test_cannot_take_someone_elses_quiz(self):
        other = make_user("nosy")
        response = self.client.get(reverse("quiz_take", args=[make_quiz(other).pk]))
        self.assertEqual(response.status_code, 404)

    def test_cannot_read_someone_elses_results(self):
        attempt = Attempt.objects.create(quiz=self.quiz, user=self.user, max_score=4)
        self.client.logout()
        self.client.force_login(make_user("peek"))
        response = self.client.get(
            reverse("quiz_results", args=[self.quiz.pk, attempt.pk])
        )
        self.assertEqual(response.status_code, 404)


class ExportTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.quiz = make_quiz(self.user)
        make_question(self.quiz, prompt="Which organelle makes ATP?")
        make_question(
            self.quiz, question_type=QuestionType.TRUE_FALSE, prompt="Cells have DNA.",
            choices=["True", "False"], correct_choices=[0], acceptable_answers=["true"],
        )
        make_question(
            self.quiz, question_type=QuestionType.SHORT_ANSWER, prompt="Powerhouse?",
            choices=[], correct_choices=[0], acceptable_answers=["mitochondria"],
        )
        self.client.force_login(self.user)

    def test_student_pdf(self):
        response = self.client.get(reverse("quiz_export", args=[self.quiz.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".pdf", response["Content-Disposition"])

    def test_answer_key_pdf(self):
        response = self.client.get(
            reverse("quiz_export", args=[self.quiz.pk]), {"answers": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertIn("answer-key", response["Content-Disposition"])

    def _pdf_text(self, **params):
        response = self.client.get(
            reverse("quiz_export", args=[self.quiz.pk]), params
        )
        self.assertEqual(response.status_code, 200)
        reader = PdfReader(io.BytesIO(response.content))
        return "".join(page.extract_text() for page in reader.pages)

    def test_pdfs_only_use_glyphs_helvetica_can_render(self):
        # Helvetica is WinAnsi-encoded, so a literal U+2713 tick would either
        # raise or render as an empty box on a printed answer key.
        for params in ({}, {"answers": "1"}):
            with self.subTest(params=params):
                text = self._pdf_text(**params)
                text.encode("ascii", "strict")
                self.assertIn("Which organelle makes ATP?", text)

    def test_student_pdf_hides_the_answers(self):
        text = self._pdf_text()
        self.assertNotIn("mitochondria", text.lower())
        self.assertNotIn("(correct)", text.lower())
        self.assertIn("Mark one option per question.", text)

    def test_answer_key_marks_the_correct_choice(self):
        text = self._pdf_text(answers="1")
        self.assertIn("ANSWER KEY", text)
        self.assertIn("(correct)", text.lower())
        # The key states the accepted answers and explanations.
        self.assertIn("mitochondria", text.lower())
        self.assertIn("Mitochondria produce most cellular ATP.", text)
        # ...but must not ask the student to mark anything.
        self.assertNotIn("Select all that apply", text)
        self.assertNotIn("Mark one option per question.", text)

    def test_filename_is_filesystem_safe(self):
        self.quiz.title = 'Weird / title: "with" <chars>'
        self.quiz.save()
        response = self.client.get(reverse("quiz_export", args=[self.quiz.pk]))
        disposition = response["Content-Disposition"]
        self.assertNotIn("/", disposition.split("filename=")[1].replace('"', ""))
        self.assertNotIn(":", disposition.split("filename=")[1].replace('"', ""))

    def test_empty_quiz_redirects_with_an_error(self):
        empty = make_quiz(self.user, title="Empty")
        response = self.client.get(reverse("quiz_export", args=[empty.pk]))
        self.assertEqual(response.status_code, 302)

    def test_cannot_export_someone_elses_quiz(self):
        other = make_user("thief")
        self.assertEqual(
            self.client.get(reverse("quiz_export", args=[make_quiz(other).pk])).status_code,
            404,
        )


class DashboardTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_empty_dashboard(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No quizzes yet")

    def test_lists_user_quizzes_only(self):
        make_quiz(self.user, title="Mine")
        make_quiz(make_user("other"), title="Theirs")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    def test_average_score_is_shown(self):
        quiz = make_quiz(self.user)
        make_question(quiz)
        Attempt.objects.create(quiz=quiz, user=self.user, score=3, max_score=4)
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "75%")


class DeleteTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.quiz = make_quiz(self.user)
        self.client.force_login(self.user)

    def test_delete_removes_the_quiz_and_document(self):
        from quizzes.models import SourceDocument

        self.client.post(reverse("quiz_delete", args=[self.quiz.pk]))
        self.assertEqual(Quiz.objects.count(), 0)
        self.assertEqual(SourceDocument.objects.count(), 0)

    def test_delete_requires_post(self):
        response = self.client.get(reverse("quiz_delete", args=[self.quiz.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Quiz.objects.count(), 1)

    def test_cannot_delete_someone_elses_quiz(self):
        other = make_quiz(make_user("victim"))
        self.client.post(reverse("quiz_delete", args=[other.pk]))
        self.assertEqual(Quiz.objects.filter(pk=other.pk).count(), 1)


class AuthTests(TestCase):
    def test_signup_creates_a_user_and_logs_in(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "newcomer",
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

    def test_duplicate_username_is_rejected(self):
        make_user("taken")
        response = self.client.post(
            reverse("signup"),
            {"username": "taken", "password1": "a-strong-passphrase-42", "password2": "a-strong-passphrase-42"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")

    def test_landing_is_public(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)

    def test_landing_renders_every_section(self):
        response = self.client.get(reverse("landing"))
        self.assertContains(response, "How it works")
        self.assertContains(response, "Every question format")
        self.assertContains(response, 'id="faq-body-0"')
        self.assertContains(response, 'id="faq-body-5"')

    def test_landing_advertises_every_registered_question_type(self):
        response = self.client.get(reverse("landing"))
        for handler in all_handlers():
            self.assertContains(response, handler.label)

    def test_landing_prompts_anonymous_visitors_to_sign_up(self):
        response = self.client.get(reverse("landing"))
        self.assertContains(response, reverse("signup"))
        self.assertNotContains(response, reverse("dashboard"))

    def test_landing_points_logged_in_users_at_their_quizzes(self):
        self.client.force_login(make_user())
        response = self.client.get(reverse("landing"))
        self.assertContains(response, reverse("upload"))
        self.assertContains(response, reverse("dashboard"))

    def test_every_visible_auth_input_is_styled(self):
        """Django's widget rendering emits no class, so this is not automatic.

        `{{ field }}` used to produce unstyled native inputs on the signup form.
        The counts are the login form's two fields and the signup form's three,
        so a new field that skips the styling fails here.
        """
        for url, expected in ((reverse("login"), 2), (reverse("signup"), 3)):
            html = self.client.get(url).content.decode()
            inputs = re.findall(r"<input\b[^>]*>", html)
            styled = [tag for tag in inputs if "form-control" in tag]
            self.assertEqual(len(styled), expected, f"{url} inputs: {inputs}")

    def test_auth_inputs_keep_their_autocomplete_hints(self):
        """`login.html` used to hand-write its inputs and drop these."""
        login = self.client.get(reverse("login")).content.decode()
        self.assertIn('autocomplete="username"', login)
        self.assertIn('autocomplete="current-password"', login)

        signup = self.client.get(reverse("signup")).content.decode()
        self.assertIn('autocomplete="username"', signup)
        self.assertIn('autocomplete="new-password"', signup)

    def test_signup_marks_only_the_field_that_failed(self):
        make_user("taken")
        response = self.client.post(
            reverse("signup"),
            {
                "username": "taken",
                "password1": "a-strong-passphrase-42",
                "password2": "a-strong-passphrase-42",
            },
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("already exists", html)
        username = re.search(r"<input\b[^>]*name=\"username\"[^>]*>", html).group(0)
        self.assertIn("is-invalid", username)
        for name in ("password1", "password2"):
            tag = re.search(r"<input\b[^>]*name=\"%s\"[^>]*>" % name, html).group(0)
            self.assertNotIn("is-invalid", tag)

    def test_bad_credentials_are_reported_without_marking_a_field(self):
        """Neither field is wrong on its own, so nothing goes red."""
        response = self.client.post(
            reverse("login"), {"username": "nobody", "password": "wrongwrongwrong"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alert-danger")
        self.assertNotContains(response, "is-invalid")

    def test_password_fields_offer_a_reveal_toggle(self):
        for url, names in (
            (reverse("login"), ["password"]),
            (reverse("signup"), ["password1", "password2"]),
        ):
            html = self.client.get(url).content.decode()
            for name in names:
                self.assertIn(f'data-reveal="{name}"', html)
            self.assertIn('aria-pressed="false"', html)
            self.assertIn("js/auth.js", html)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

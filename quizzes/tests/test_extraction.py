import io
from tempfile import NamedTemporaryFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from quizzes.forms.upload import QuizUploadForm
from quizzes.services.extraction import (
    SUPPORTED_EXTENSIONS,
    ExtractionError,
    extract_text,
)

from .factories import (
    LONG_TEXT,
    make_docx_file,
    make_image_only_pptx_file,
    make_pdf_file,
    make_pptx_file,
    make_scanned_pdf,
    make_text_file,
)


class TextExtractionTests(SimpleTestCase):
    def _write(self, upload, suffix):
        with NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(upload.read())
            return handle.name

    def test_plain_text(self):
        path = self._write(make_text_file(), ".txt")
        text, pages = extract_text(path, "notes.txt")
        self.assertIn("cell membrane", text)
        self.assertEqual(pages, 0)

    def test_markdown_uses_text_handler(self):
        path = self._write(make_text_file(name="notes.md"), ".md")
        text, _ = extract_text(path, "notes.md")
        self.assertIn("cell membrane", text)

    def test_docx(self):
        path = self._write(make_docx_file(), ".docx")
        text, _ = extract_text(path, "notes.docx")
        self.assertIn("Chlorophyll", text)
        self.assertIn("Calvin cycle", text)

    def test_docx_tables_are_included(self):
        path = self._write(make_docx_file(), ".docx")
        text, _ = extract_text(path, "notes.docx")
        self.assertIn("Thylakoid membrane", text)

    def test_pdf(self):
        path = self._write(make_pdf_file(pages=4), ".pdf")
        text, pages = extract_text(path, "paper.pdf")
        self.assertIn("oxidative phosphorylation", text)
        self.assertEqual(pages, 4)

    def test_repeated_page_headers_are_dropped(self):
        # The running header appears on every page, so it is boilerplate.
        path = self._write(make_pdf_file(pages=6), ".pdf")
        text, _ = extract_text(path, "paper.pdf")
        self.assertNotIn("Cell Biology Notes", text)
        self.assertIn("cristae", text)

    def test_unsupported_extension_is_rejected(self):
        with self.assertRaises(ExtractionError) as ctx:
            extract_text("whatever.exe", "malware.exe")
        self.assertIn("Unsupported", str(ctx.exception))

    def test_no_extension_is_rejected(self):
        with self.assertRaises(ExtractionError):
            extract_text("noext", "noext")

    @override_settings(MIN_SOURCE_CHARS=100_000)
    def test_short_document_is_rejected(self):
        path = self._write(make_text_file("Too short."), ".txt")
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(path, "tiny.txt")
        self.assertIn("not enough", str(ctx.exception))

    def test_scanned_pdf_gives_an_actionable_message(self):
        path = self._write(make_scanned_pdf(), ".pdf")
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(path, "scan.pdf")
        message = str(ctx.exception)
        self.assertIn("scan", message.lower())
        self.assertIn("OCR", message)

    def test_corrupt_pdf_is_reported_not_crashed(self):
        with NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(b"this is definitely not a pdf")
            path = handle.name
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(path, "broken.pdf")
        self.assertIn("Could not read", str(ctx.exception))

    def test_empty_txt_is_rejected(self):
        path = self._write(make_text_file(""), ".txt")
        with self.assertRaises(ExtractionError):
            extract_text(path, "empty.txt")

    def test_long_text_survives(self):
        path = self._write(make_text_file(LONG_TEXT), ".txt")
        text, _ = extract_text(path, "long.txt")
        self.assertGreater(len(text), 1000)


class PptxExtractionTests(SimpleTestCase):
    def _write(self, upload, suffix=".pptx"):
        with NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(upload.read())
            return handle.name

    def _extract(self, upload, name="lecture.pptx"):
        return extract_text(self._write(upload), name)

    def test_slide_titles_and_body_text(self):
        text, pages = self._extract(make_pptx_file())
        self.assertIn("Photosynthesis Overview", text)
        self.assertIn("The Calvin Cycle", text)
        self.assertIn("glyceraldehyde-3-phosphate", text)
        # Slides are the PowerPoint page, and the count is stored and shown.
        self.assertEqual(pages, 5)

    def test_grouped_text_is_found(self):
        """Group shapes nest, so the walk has to recurse into them."""
        text, _ = self._extract(make_pptx_file())
        self.assertIn("the thylakoid stack", text)

    def test_tables_are_included(self):
        text, _ = self._extract(make_pptx_file())
        self.assertIn("Thylakoid membrane", text)
        # Row cells keep the Word handler's pipe separator.
        self.assertIn("Light reactions | Thylakoid membrane", text)

    def test_chart_title_is_included(self):
        text, _ = self._extract(make_pptx_file())
        self.assertIn("ATP yield by stage", text)

    def test_speaker_notes_are_included(self):
        text, _ = self._extract(make_pptx_file())
        self.assertIn("Speaker notes:", text)
        self.assertIn("most students get wrong on the exam", text)

    def test_repeated_slide_footers_are_dropped(self):
        """A slide number on every slide is boilerplate, same as a PDF page header."""
        text, _ = self._extract(make_pptx_file())
        self.assertNotIn("Cell Biology 101", text)
        self.assertIn("Limiting Factors", text)

    def test_repeated_body_content_is_kept(self):
        """Only short lines at a slide's edges are boilerplate, as for a PDF."""
        path = self._write(make_text_file(LONG_TEXT), ".txt")
        text, _ = extract_text(path, "notes.txt")
        self.assertEqual(text.count("regulates transport"), 40)

    def test_image_only_deck_gives_an_actionable_message(self):
        with self.assertRaises(ExtractionError) as ctx:
            self._extract(make_image_only_pptx_file(), "scanned-deck.pptx")
        message = str(ctx.exception)
        self.assertIn("images", message)
        self.assertIn("OCR", message)

    def test_corrupt_pptx_is_reported_not_crashed(self):
        path = self._write(SimpleUploadedFile("broken.pptx", b"definitely not a pptx"))
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(path, "broken.pptx")
        self.assertIn("Could not read", str(ctx.exception))

    def test_a_deck_with_no_slides_is_rejected(self):
        from pptx import Presentation

        buffer = io.BytesIO()
        Presentation().save(buffer)
        path = self._write(SimpleUploadedFile("empty.pptx", buffer.getvalue()))
        with self.assertRaises(ExtractionError) as ctx:
            extract_text(path, "empty.pptx")
        self.assertIn("no slides", str(ctx.exception))


class UploadFormatContractTests(SimpleTestCase):
    """The form and the extractor have to agree on what is uploadable.

    They used to keep separate lists, so a format could be added to the
    extractor and quietly refused by the form that runs first.
    """

    def _form_with(self, upload):
        form = QuizUploadForm(files={"document": upload})
        form.is_valid()
        return form

    def test_every_supported_extension_passes_the_form(self):
        for extension in sorted(SUPPORTED_EXTENSIONS):
            form = self._form_with(SimpleUploadedFile(f"notes{extension}", b"x" * 10))
            self.assertNotIn("document", form.errors, extension)

    def test_the_rejection_message_lists_every_supported_extension(self):
        form = self._form_with(SimpleUploadedFile("bad.exe", b"MZ"))
        message = str(form.errors["document"])
        for extension in SUPPORTED_EXTENSIONS:
            self.assertIn(extension, message)

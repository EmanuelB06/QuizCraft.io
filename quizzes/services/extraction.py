"""Turn an uploaded PDF / DOCX / PPTX / TXT file into plain text."""

import re
from collections import Counter

from django.conf import settings

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".text"}


class ExtractionError(Exception):
    """Raised when a file cannot be turned into usable text."""


def _clean(text):
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


MAX_BOILERPLATE_CHARS = 90
EDGE_LINES = 3


def _drop_repeated_lines(pages):
    """Remove running headers and footers that repeat across most pages.

    Restricted to short lines near the top and bottom of the page, because that
    is where headers and page numbers live. Testing every line would delete
    repeated body content such as a table or a recurring section, which is
    real material. Digits are blanked before counting so "Page 4" matches
    "Page 11".
    """
    if len(pages) < 4:
        return "\n\n".join(pages)

    threshold = max(3, int(len(pages) * 0.6))
    counts = Counter()

    for page in pages:
        lines = page.splitlines()
        edge = [ln.strip() for ln in lines[:EDGE_LINES] + lines[-EDGE_LINES:]]
        for line in set(edge):
            if 2 < len(line) <= MAX_BOILERPLATE_CHARS:
                counts[re.sub(r"\d", "#", line)] += 1

    boilerplate = {key for key, hits in counts.items() if hits >= threshold}
    if not boilerplate:
        return "\n\n".join(pages)

    cleaned = []
    for page in pages:
        lines = page.splitlines()
        keep = []
        for position, line in enumerate(lines):
            at_edge = position < EDGE_LINES or position >= len(lines) - EDGE_LINES
            stripped = line.strip()
            is_boiler = (
                at_edge
                and 2 < len(stripped) <= MAX_BOILERPLATE_CHARS
                and re.sub(r"\d", "#", stripped) in boilerplate
            )
            if not is_boiler:
                keep.append(line)
        cleaned.append("\n".join(keep).strip())
    return "\n\n".join(cleaned)


def _extract_pdf(path):
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise ExtractionError("This PDF is password protected.") from exc
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Could not read this PDF: {exc}") from exc

    if not pages:
        raise ExtractionError("This PDF has no pages.")

    text = _clean(_drop_repeated_lines(pages))

    # A scanned PDF is just images with no text layer, so extraction yields
    # almost nothing. A real page yields hundreds of characters, even a sparse
    # one, so a very low yield means we are not looking at a text PDF.
    per_page = len(text) / len(pages)
    if len(text) < 60 or per_page < 20:
        raise ExtractionError(
            "Almost no text could be read from this PDF, which usually means it "
            "is a scan or an image. OCR is not supported yet, so please upload a "
            "text-based PDF, a .docx, or a .txt file."
        )
    return text, len(pages)


def _extract_docx(path):
    import docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ExtractionError(f"Could not read this Word file: {exc}") from exc

    blocks = [p.text for p in document.paragraphs if p.text and p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))

    text = _clean("\n\n".join(blocks))
    if not text:
        raise ExtractionError("This Word file appears to be empty.")
    return text, 0


def _slide_blocks(shapes):
    """Flatten one slide's shape tree into text blocks.

    A slide is a flat, z-ordered list of shapes, and the text hides in several
    kinds of them: text frames (which includes the title placeholder), tables,
    and chart titles. Groups nest, so this recurses. Images and embedded objects
    carry no text at all.
    """
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    blocks = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            blocks.extend(_slide_blocks(shape.shapes))
        elif shape.has_table:
            for row in shape.table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
        elif shape.has_chart and shape.chart.has_title:
            # Only the title. The plotted values live in an embedded workbook and
            # come out as opaque cell references, which read as noise here.
            title = shape.chart.chart_title.text_frame.text.strip()
            if title:
                blocks.append(title)
        elif shape.has_text_frame:
            text = shape.text_frame.text.strip()
            if text:
                blocks.append(text)
    return blocks


def _slide_notes(slide):
    """The speaker's notes, which are often the only prose in a bullet deck.

    Touching `notes_slide` makes python-pptx build one if the deck has none, so
    a deck without a notes master must not be allowed to raise here.
    """
    try:
        frame = slide.notes_slide.notes_text_frame
    except Exception:
        return ""
    return frame.text.strip() if frame is not None else ""


#: A deck of real slides still yields far more than this. Below it the slides
#: are almost certainly pictures, which is the PowerPoint answer to a scanned
#: PDF and deserves the same explanation.
PPTX_MIN_CHARS = 60


def _extract_pptx(path):
    from pptx import Presentation

    try:
        presentation = Presentation(str(path))
    except Exception as exc:
        raise ExtractionError(f"Could not read this PowerPoint file: {exc}") from exc

    slides = list(presentation.slides)
    if not slides:
        raise ExtractionError("This PowerPoint file has no slides.")

    pages = []
    for slide in slides:
        blocks = _slide_blocks(slide.shapes)
        notes = _slide_notes(slide)
        if notes:
            # Prefixed inline rather than given a line of its own: a one-word
            # marker repeated at the end of most slides reads as boilerplate and
            # would be stripped by the pass below.
            blocks.append(f"Speaker notes: {notes}")
        pages.append("\n\n".join(blocks))

    # Slides are the PowerPoint page, so the PDF's page-wise clean-up drops a
    # repeated footer or slide number here for the same reason it does there.
    text = _clean(_drop_repeated_lines(pages))

    if len(text) < PPTX_MIN_CHARS:
        raise ExtractionError(
            "Almost no text could be read from this PowerPoint file, which usually "
            "means the slides are images. OCR is not supported yet, so please upload "
            "a deck whose slides contain real text, a .docx or a .txt file."
        )
    return text, len(slides)


def _extract_txt(path):
    for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as handle:
                text = _clean(handle.read())
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        raise ExtractionError("Could not decode this text file.")

    if not text:
        raise ExtractionError("This text file is empty.")
    return text, 0


_HANDLERS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".pptx": _extract_pptx,
    ".txt": _extract_txt,
    ".md": _extract_txt,
    ".text": _extract_txt,
}


def extract_text(path, filename):
    """Return (text, page_count) for an uploaded file.

    Raises ExtractionError with a message that is safe to show to the user.
    """
    name = filename or str(path)
    extension = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if extension not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(_HANDLERS))
        raise ExtractionError(f"Unsupported file type '{extension or name}'. Allowed: {allowed}.")

    text, pages = _HANDLERS[extension](path)

    if len(text) < settings.MIN_SOURCE_CHARS:
        raise ExtractionError(
            f"Only {len(text)} characters could be extracted, which is not enough to "
            f"build a quiz. You need at least {settings.MIN_SOURCE_CHARS}."
        )
    return text, pages

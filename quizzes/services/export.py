"""Render a quiz to PDF with reportlab (pure Python, no system dependencies).

The question types decide what an answer area looks like, via
`handler.answer_view()`. This module only knows how to draw the handful of
answer-area shapes, so adding a question type never requires editing it.
"""

import io
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

ACCENT = colors.HexColor("#4f46e5")
MUTED = colors.HexColor("#6b7280")

_LETTERS = "ABCDEFGH"

# Base-14 Helvetica is WinAnsi-encoded, so anything drawn into the PDF has to
# stay inside that repertoire or it renders as an empty box on paper. Keep new
# literals ASCII-only, or register a TTF font.
_CORRECT_MARK = " (correct)"
_CORRECT_COLOR = "#059669"
_TEXT_COLOR = "#111827"


def _escape(text):
    return re.sub(r"[&<>]", lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[m.group()], str(text))


def _option_label(index: int) -> str:
    """`0 -> 'A'`, `1 -> 'B'`, falling back to a number past the eighth."""
    return _LETTERS[index] if index < len(_LETTERS) else str(index + 1)


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "QCTitle", parent=base["Title"], fontSize=20, leading=24,
            textColor=ACCENT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "QCSubtitle", parent=base["Normal"], fontSize=10, leading=14,
            textColor=MUTED, spaceAfter=14,
        ),
        "prompt": ParagraphStyle(
            "QCPrompt", parent=base["Normal"], fontSize=11, leading=15,
            alignment=TA_LEFT, spaceAfter=6,
        ),
        "choice": ParagraphStyle(
            "QCChoice", parent=base["Normal"], fontSize=10, leading=14,
            leftIndent=16, spaceAfter=2,
        ),
        "answer": ParagraphStyle(
            "QCAnswer", parent=base["Normal"], fontSize=10, leading=14, spaceAfter=3,
        ),
        "hint": ParagraphStyle(
            "QCHint", parent=base["Normal"], fontSize=9, leading=12,
            textColor=MUTED, spaceAfter=4,
        ),
    }


def _footer(canvas, doc, subtitle):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.75 * inch, 0.5 * inch, subtitle)
    canvas.drawRightString(LETTER[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _answer_flowables(view, styles, reveal_answers):
    """Draw one answer area from the shape its handler described."""
    if view.kind in {"single", "multi", "boolean"}:
        out = []
        for option in view.options:
            marker = "[ ]" if view.kind == "boolean" else f"{_option_label(option.position)}."
            if reveal_answers and option.is_correct:
                out.append(
                    Paragraph(
                        f'<font color="{_CORRECT_COLOR}"><b>{marker}</b> '
                        f"{_escape(option.text)}{_escape(_CORRECT_MARK)}</font>",
                        styles["choice"],
                    )
                )
            else:
                out.append(
                    Paragraph(f"<b>{marker}</b> {_escape(option.text)}", styles["choice"])
                )
        return out

    # Typed answer: leave a ruled gap, or reveal what is accepted.
    if reveal_answers:
        accepted = ", ".join(view.accepted) or "-"
        return [Paragraph(f"<b>Answer:</b> {_escape(accepted)}", styles["choice"])]
    return [Paragraph("<b>Answer:</b> ______________________", styles["choice"])]


def _question_flowables(question, styles, numbering, reveal_answers):
    """Build the flowables for one question."""
    view = question.handler.answer_view(question, reveal=reveal_answers)
    out = [
        Paragraph(
            f"<b>{numbering}.</b> {_escape(question.prompt)}", styles["prompt"]
        )
    ]
    out.extend(_answer_flowables(view, styles, reveal_answers))

    if reveal_answers and question.explanation:
        out.append(Paragraph(f"<i>{_escape(question.explanation)}</i>", styles["hint"]))

    out.append(Spacer(1, 10))
    return out


def _instructions(questions):
    """The directions a student needs for the formats actually in this quiz."""
    kinds = {q.handler.answer_view(q).kind for q in questions}
    notes = []
    if "multi" in kinds:
        notes.append("Select all that apply: mark every correct option.")
    if "single" in kinds:
        notes.append("Mark one option per question.")
    if "boolean" in kinds:
        notes.append("True/false: tick the correct box.")
    return notes


def build_quiz_pdf(quiz, reveal_answers=False):
    """Return PDF bytes for a quiz.

    reveal_answers=False produces the student handout, True the answer key.
    """
    questions = list(quiz.questions)
    if not questions:
        raise ValueError("This quiz has no questions to export.")

    styles = _styles()
    title = quiz.title if reveal_answers else f"{quiz.title} - Quiz"
    points = sum(q.points for q in questions)
    if reveal_answers:
        subtitle = (
            f"ANSWER KEY  |  {len(questions)} questions  |  "
            f"Difficulty: {quiz.get_difficulty_display()}  |  Total: {points} points"
        )
    else:
        subtitle = (
            f"Name: ______________________    Date: ____________  |  "
            f"{len(questions)} questions  |  Difficulty: "
            f"{quiz.get_difficulty_display()}  |  Total: {points} points"
        )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.85 * inch,
        title=title,
        author="QuizCraft",
    )

    story = [
        Paragraph(_escape(title), styles["title"]),
        Paragraph(_escape(subtitle), styles["subtitle"]),
    ]

    if not reveal_answers:
        notes = _instructions(questions)
        if notes:
            story.append(
                Paragraph(
                    "<b>Instructions:</b> " + " ".join(notes), styles["hint"]
                )
            )
            story.append(Spacer(1, 8))

    for number, question in enumerate(questions, start=1):
        # Keep a question and its options on the same page.
        story.append(KeepTogether(_question_flowables(question, styles, number, reveal_answers)))

    def on_page(canvas, document):
        _footer(canvas, document, quiz.title)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()


def safe_filename(name, extension="pdf"):
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip().replace(" ", "-")
    cleaned = re.sub(r"-{2,}", "-", cleaned) or "quiz"
    return f"{cleaned[:80]}.{extension}"


def export_filename(quiz, reveal_answers=False):
    """The download name for one quiz, e.g. `Cell-Biology-Quiz-answer-key.pdf`."""
    suffix = "answer-key" if reveal_answers else "quiz"
    return safe_filename(f"{quiz.title}-{suffix}")

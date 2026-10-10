"""Shared factories for the test suite."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from quizzes.models import Difficulty, Question, QuestionType, Quiz, SourceDocument

LONG_TEXT = " ".join(
    f"Paragraph {n}: the cell membrane regulates transport across the boundary of the cell."
    for n in range(40)
)


def make_user(username="tester", password="quizcraft-pass-123"):
    return get_user_model().objects.create_user(username=username, password=password)


def make_text_file(content=LONG_TEXT, name="notes.txt"):
    return SimpleUploadedFile(
        name, content.encode("utf-8"), content_type="text/plain"
    )


def make_docx_file(name="notes.docx"):
    import docx

    document = docx.Document()
    document.add_heading("Photosynthesis", level=1)
    document.add_paragraph(
        "Photosynthesis converts light energy into chemical energy stored as "
        "glucose. It happens in two linked stages: the light reactions, which "
        "capture photons, and the Calvin cycle, which fixes carbon dioxide."
    )
    document.add_paragraph(
        "Chlorophyll absorbs mainly blue and red wavelengths and reflects green "
        "light, which is why most leaves appear green to the eye."
    )
    document.add_paragraph(
        "In the light reactions, water is split at photosystem two, releasing "
        "oxygen as a by-product and supplying electrons to photosystem one."
    )
    document.add_paragraph(
        "The Calvin cycle uses ATP and NADPH from the light reactions to reduce "
        "carbon dioxide into glyceraldehyde-3-phosphate, which is used to build "
        "glucose and other carbohydrates."
    )
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Stage"
    table.cell(0, 1).text = "Location"
    table.cell(1, 0).text = "Light reactions"
    table.cell(1, 1).text = "Thylakoid membrane"

    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return SimpleUploadedFile(
        name,
        buffer.read(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


PPTX_TITLES = [
    "Photosynthesis Overview",
    "The Light Reactions",
    "The Calvin Cycle",
    "Limiting Factors",
    "Chlorophyll and Leaf Colour",
]

PPTX_BODY = [
    "Photosynthesis converts light energy into chemical energy stored as glucose. "
    "It happens in two linked stages that hand carriers to each other.",
    "In the light reactions water is split at photosystem two, releasing oxygen as "
    "a by-product and supplying electrons to photosystem one.",
    "The Calvin cycle uses ATP and NADPH from the light reactions to reduce carbon "
    "dioxide into glyceraldehyde-3-phosphate, which builds glucose.",
    "Light intensity, carbon dioxide concentration and temperature each cap the "
    "rate, and whichever runs out first is the limiting factor.",
    "Chlorophyll absorbs mainly blue and red wavelengths and reflects green light, "
    "which is why most leaves appear green to the eye.",
]

#: Repeated on every slide as a footer. Digit-blanked it is the same line on all
#: of them, so the extractor should treat it as boilerplate.
PPTX_FOOTER = "Cell Biology 101, lecture {}"


def make_pptx_file(name="lecture.pptx", slides=5, with_table=True, with_chart=True):
    """A deck that exercises every shape kind the extractor has to read.

    Real decks carry text in placeholders, groups, tables and charts, plus
    speaker notes, and a footer on every slide. Uses the blank-ish layout so
    the title and body arrive as genuine placeholders rather than text boxes.
    """
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches

    presentation = Presentation()
    for index in range(slides):
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = PPTX_TITLES[index % len(PPTX_TITLES)]
        slide.placeholders[1].text_frame.text = PPTX_BODY[index % len(PPTX_BODY)]

        # A group, because decks are assembled from grouped labels and icons.
        group = slide.shapes.add_group_shape()
        caption = group.shapes.add_textbox(Inches(0.6), Inches(5.6), Inches(5), Inches(0.4))
        caption.text_frame.text = f"Figure {index + 1}: the thylakoid stack"

        if with_chart and index == 0:
            data = CategoryChartData()
            data.categories = ["Light reactions", "Calvin cycle"]
            data.add_series("ATP yield", (34, 18))
            chart = slide.shapes.add_chart(
                XL_CHART_TYPE.BAR_CLUSTERED,
                Inches(6.0),
                Inches(1.2),
                Inches(3.5),
                Inches(2.5),
                data,
            ).chart
            # add_chart leaves the title off, and the extractor only reads a
            # chart's title, so the fixture has to set one.
            chart.has_title = True
            chart.chart_title.text_frame.text = "ATP yield by stage"

        if with_table and index == slides - 1:
            frame = slide.shapes.add_table(2, 2, Inches(0.6), Inches(4.4), Inches(5), Inches(1.0))
            frame.table.cell(0, 0).text = "Stage"
            frame.table.cell(0, 1).text = "Location"
            frame.table.cell(1, 0).text = "Light reactions"
            frame.table.cell(1, 1).text = "Thylakoid membrane"

        footer = slide.shapes.add_textbox(Inches(0.6), Inches(6.9), Inches(6), Inches(0.3))
        footer.text_frame.text = PPTX_FOOTER.format(index + 1)

        slide.notes_slide.notes_text_frame.text = (
            f"Stress that {PPTX_TITLES[index % len(PPTX_TITLES)].lower()} is the part "
            "most students get wrong on the exam."
        )

    buffer = io.BytesIO()
    presentation.save(buffer)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )


def make_image_only_pptx_file(name="scanned-deck.pptx", slides=3):
    """A deck of pictures, the PowerPoint equivalent of a scanned PDF.

    Real images rather than empty text boxes, so this also proves the Picture
    branch of the extractor contributes no text.
    """
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    for _ in range(slides):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])  # blank
        # A fresh stream per slide: add_picture reads it to the end.
        picture = io.BytesIO()
        Image.new("RGB", (320, 240), "white").save(picture, format="PNG")
        slide.shapes.add_picture(picture, Inches(1), Inches(1), Inches(6), Inches(4.5))

    buffer = io.BytesIO()
    presentation.save(buffer)
    return SimpleUploadedFile(
        name,
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )


PDF_BODY = [
    "Mitochondria generate most of the cell's ATP through oxidative phosphorylation.",
    "The inner membrane folds into structures called cristae, which expand the",
    "surface area available for the electron transport chain to operate on.",
    "Protons accumulate in the intermembrane space and flow back through ATP",
    "synthase, and that flow is what actually drives the production of ATP.",
]

PDF_PAGE_TOPICS = [
    "Mitochondria and energy production in the eukaryotic cell.",
    "Chloroplast structure and the light reactions of photosynthesis.",
    "Ribosomes, the endoplasmic reticulum, and the secretory pathway.",
]


def make_pdf_file(name="paper.pdf", pages=3):
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    document = canvas.Canvas(buffer, pagesize=LETTER)
    for page in range(pages):
        document.setFont("Helvetica", 11)
        # A running header, which the extractor should treat as boilerplate.
        document.drawString(72, 740, "Cell Biology Notes, page {}".format(page + 1))
        document.drawString(72, 710, PDF_PAGE_TOPICS[page % len(PDF_PAGE_TOPICS)])
        y = 670
        for offset, line in enumerate(PDF_BODY):
            document.drawString(72, y, "{} {}".format(page + 1, line))
            y -= 16
        document.drawString(72, y, "Section {} continues overleaf.".format(page + 1))
        document.showPage()
    document.save()
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="application/pdf")


def make_scanned_pdf(name="scan.pdf"):
    """A PDF with a single page and effectively no text layer, like a scan."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 700, "x")
    document.save()
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="application/pdf")


def make_document(user, upload=None, text=LONG_TEXT, name="notes.txt"):
    return SourceDocument.objects.create(
        user=user,
        file=upload or make_text_file(text, name),
        original_filename=(upload or make_text_file(text, name)).name,
        file_size=len(text),
        char_count=len(text),
        extracted_text=text,
    )


def make_quiz(user, document=None, **kwargs):
    defaults = {
        "document": document or make_document(user),
        "title": "Cell Biology Quiz",
        "question_count": 3,
        "difficulty": Difficulty.MEDIUM,
        "question_types": list(QuestionType.values),
        "status": Quiz.Status.READY,
    }
    defaults.update(kwargs)
    return Quiz.objects.create(user=user, **defaults)


def make_question(quiz, **kwargs):
    defaults = {
        "question_type": QuestionType.MCQ_SINGLE,
        "prompt": "Which organelle produces ATP?",
        "choices": ["Nucleus", "Mitochondrion", "Ribosome", "Golgi body"],
        "correct_choices": [1],
        "acceptable_answers": [],
        "explanation": "Mitochondria produce most cellular ATP.",
        "points": 1,
    }
    defaults.update(kwargs)
    question = Question.objects.create(quiz=quiz, **defaults)
    Question.objects.filter(pk=question.pk).update(order=quiz.question_set.count())
    return question

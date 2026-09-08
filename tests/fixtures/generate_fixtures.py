"""Regenerates the fixture corpus under tests/fixtures/<format>/.

Run with: python tests/fixtures/generate_fixtures.py
Requires the `dev` extra (`pip install -e ".[dev]"`) for `reportlab` and
`python-pptx`.

Per spec #31, the corpus deliberately includes broken files, not just
valid ones — the point is to test the *detection* of real problems.
"""

from __future__ import annotations

from pathlib import Path

import pypdf
from docx import Document
from pptx import Presentation
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

PDF_OUT_DIR = Path(__file__).parent / "pdf"
PPTX_OUT_DIR = Path(__file__).parent / "pptx"
DOCX_OUT_DIR = Path(__file__).parent / "docx"


# ---------------------------------------------------------------- PDF ----

def make_good_2page() -> None:
    path = PDF_OUT_DIR / "good_2page.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Sample Doc")
    c.drawString(100, 700, "Page one content")
    c.showPage()
    c.drawString(100, 700, "Page two content")
    c.showPage()
    c.save()


def make_empty_0page() -> None:
    path = PDF_OUT_DIR / "empty_0page.pdf"
    writer = pypdf.PdfWriter()
    with open(path, "wb") as f:
        writer.write(f)


def make_encrypted() -> None:
    src = PDF_OUT_DIR / "good_2page.pdf"
    path = PDF_OUT_DIR / "encrypted.pdf"
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.encrypt("secret123")
    with open(path, "wb") as f:
        writer.write(f)


def make_corrupt_pdf() -> None:
    path = PDF_OUT_DIR / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\n%% this is not a real pdf body, just garbage\nendobj trailer garbage")


def make_mislabeled_html() -> None:
    path = PDF_OUT_DIR / "mislabeled_html.pdf"
    path.write_text("<html><body>not a pdf</body></html>\n")


# --------------------------------------------------------------- PPTX ----

def make_good_2slide_pptx() -> None:
    path = PPTX_OUT_DIR / "good_2slide.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    slide.shapes.title.text = "Sample Deck"
    slide.placeholders[1].text = "Some body content"
    prs.slides.add_slide(prs.slide_layouts[6])  # blank layout, no placeholders
    prs.save(str(path))


def make_empty_placeholder_pptx() -> None:
    """One slide with a title placeholder left empty — the 'leftover
    placeholder' case verify_structural()'s empty_placeholders check
    should flag as WARN."""
    path = PPTX_OUT_DIR / "empty_placeholder.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.placeholders[1].text = "Body has content but the title was never filled in"
    prs.save(str(path))


def make_zero_slide_pptx() -> None:
    path = PPTX_OUT_DIR / "zero_slide.pptx"
    prs = Presentation()
    prs.save(str(path))


def make_corrupt_pptx() -> None:
    path = PPTX_OUT_DIR / "corrupt.pptx"
    path.write_bytes(b"PK\x03\x04this is not a real zip/ooxml body, just garbage bytes")


def make_mislabeled_pdf_as_pptx() -> None:
    """A real PDF saved with a .pptx extension, mirroring the PDF corpus's
    mislabeled_html.pdf — proves type detection never trusts the extension."""
    path = PPTX_OUT_DIR / "mislabeled_pdf.pptx"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


# --------------------------------------------------------------- DOCX ----

def make_good_docx() -> None:
    path = DOCX_OUT_DIR / "good.docx"
    doc = Document()
    doc.add_heading("Sample Document", level=1)
    doc.add_paragraph("Some body content.")
    doc.add_table(rows=1, cols=2)
    doc.save(str(path))


def make_empty_docx() -> None:
    """Zero paragraphs — python-docx always creates at least the body
    element, but a document with no add_paragraph()/add_heading() calls
    has zero *paragraph* entries, which is the case verify_structural()'s
    paragraph_count check should flag as FAIL."""
    path = DOCX_OUT_DIR / "empty.docx"
    doc = Document()
    # Remove the single implicit empty paragraph python-docx starts with.
    for p in list(doc.paragraphs):
        p._element.getparent().remove(p._element)
    doc.save(str(path))


def make_corrupt_docx() -> None:
    path = DOCX_OUT_DIR / "corrupt.docx"
    path.write_bytes(b"PK\x03\x04this is not a real zip/ooxml body, just garbage bytes")


def make_mislabeled_pdf_as_docx() -> None:
    path = DOCX_OUT_DIR / "mislabeled_pdf.docx"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


if __name__ == "__main__":
    PDF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    PPTX_OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCX_OUT_DIR.mkdir(parents=True, exist_ok=True)

    make_good_2page()
    make_empty_0page()
    make_encrypted()
    make_corrupt_pdf()
    make_mislabeled_html()

    make_good_2slide_pptx()
    make_empty_placeholder_pptx()
    make_zero_slide_pptx()
    make_corrupt_pptx()
    make_mislabeled_pdf_as_pptx()

    make_good_docx()
    make_empty_docx()
    make_corrupt_docx()
    make_mislabeled_pdf_as_docx()

    print(f"Wrote PDF fixtures to {PDF_OUT_DIR}")
    print(f"Wrote PPTX fixtures to {PPTX_OUT_DIR}")
    print(f"Wrote DOCX fixtures to {DOCX_OUT_DIR}")

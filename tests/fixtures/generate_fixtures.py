"""Regenerates the PDF fixture corpus under tests/fixtures/pdf/.

Run with: python tests/fixtures/generate_fixtures.py
Requires the `dev` extra (`pip install -e ".[dev]"`) for `reportlab`.

Per spec #31, the corpus deliberately includes broken files, not just
valid ones — the point is to test the *detection* of real problems.
"""

from __future__ import annotations

from pathlib import Path

import pypdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).parent / "pdf"


def make_good_2page() -> None:
    path = OUT_DIR / "good_2page.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Sample Doc")
    c.drawString(100, 700, "Page one content")
    c.showPage()
    c.drawString(100, 700, "Page two content")
    c.showPage()
    c.save()


def make_empty_0page() -> None:
    path = OUT_DIR / "empty_0page.pdf"
    writer = pypdf.PdfWriter()
    with open(path, "wb") as f:
        writer.write(f)


def make_encrypted() -> None:
    src = OUT_DIR / "good_2page.pdf"
    path = OUT_DIR / "encrypted.pdf"
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.encrypt("secret123")
    with open(path, "wb") as f:
        writer.write(f)


def make_corrupt() -> None:
    path = OUT_DIR / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\n%% this is not a real pdf body, just garbage\nendobj trailer garbage")


def make_mislabeled_html() -> None:
    path = OUT_DIR / "mislabeled_html.pdf"
    path.write_text("<html><body>not a pdf</body></html>\n")


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_good_2page()
    make_empty_0page()
    make_encrypted()
    make_corrupt()
    make_mislabeled_html()
    print(f"Wrote fixtures to {OUT_DIR}")

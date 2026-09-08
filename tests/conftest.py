from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pdf"
PPTX_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pptx"
DOCX_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "docx"
XLSX_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "xlsx"
IMAGE_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "image"
HTML_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "html"


@pytest.fixture()
def good_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "good_2page.pdf"
    shutil.copy(FIXTURES_DIR / "good_2page.pdf", dst)
    return dst


@pytest.fixture()
def corrupt_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "corrupt.pdf"
    shutil.copy(FIXTURES_DIR / "corrupt.pdf", dst)
    return dst


@pytest.fixture()
def empty_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "empty_0page.pdf"
    shutil.copy(FIXTURES_DIR / "empty_0page.pdf", dst)
    return dst


@pytest.fixture()
def encrypted_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "encrypted.pdf"
    shutil.copy(FIXTURES_DIR / "encrypted.pdf", dst)
    return dst


@pytest.fixture()
def mislabeled_html_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_html.pdf"
    shutil.copy(FIXTURES_DIR / "mislabeled_html.pdf", dst)
    return dst


@pytest.fixture()
def embedded_font_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "embedded_font.pdf"
    shutil.copy(FIXTURES_DIR / "embedded_font.pdf", dst)
    return dst


@pytest.fixture()
def nonembedded_custom_font_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "nonembedded_custom_font.pdf"
    shutil.copy(FIXTURES_DIR / "nonembedded_custom_font.pdf", dst)
    return dst


@pytest.fixture()
def good_pptx(tmp_path: Path) -> Path:
    dst = tmp_path / "good_2slide.pptx"
    shutil.copy(PPTX_FIXTURES_DIR / "good_2slide.pptx", dst)
    return dst


@pytest.fixture()
def empty_placeholder_pptx(tmp_path: Path) -> Path:
    dst = tmp_path / "empty_placeholder.pptx"
    shutil.copy(PPTX_FIXTURES_DIR / "empty_placeholder.pptx", dst)
    return dst


@pytest.fixture()
def zero_slide_pptx(tmp_path: Path) -> Path:
    dst = tmp_path / "zero_slide.pptx"
    shutil.copy(PPTX_FIXTURES_DIR / "zero_slide.pptx", dst)
    return dst


@pytest.fixture()
def corrupt_pptx(tmp_path: Path) -> Path:
    dst = tmp_path / "corrupt.pptx"
    shutil.copy(PPTX_FIXTURES_DIR / "corrupt.pptx", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_pptx(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.pptx"
    shutil.copy(PPTX_FIXTURES_DIR / "mislabeled_pdf.pptx", dst)
    return dst


@pytest.fixture()
def good_docx(tmp_path: Path) -> Path:
    dst = tmp_path / "good.docx"
    shutil.copy(DOCX_FIXTURES_DIR / "good.docx", dst)
    return dst


@pytest.fixture()
def empty_docx(tmp_path: Path) -> Path:
    dst = tmp_path / "empty.docx"
    shutil.copy(DOCX_FIXTURES_DIR / "empty.docx", dst)
    return dst


@pytest.fixture()
def corrupt_docx(tmp_path: Path) -> Path:
    dst = tmp_path / "corrupt.docx"
    shutil.copy(DOCX_FIXTURES_DIR / "corrupt.docx", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_docx(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.docx"
    shutil.copy(DOCX_FIXTURES_DIR / "mislabeled_pdf.docx", dst)
    return dst


@pytest.fixture()
def good_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "good.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "good.xlsx", dst)
    return dst


@pytest.fixture()
def formula_error_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "formula_error.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "formula_error.xlsx", dst)
    return dst


@pytest.fixture()
def no_formula_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "no_formula.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "no_formula.xlsx", dst)
    return dst


@pytest.fixture()
def corrupt_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "corrupt.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "corrupt.xlsx", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "mislabeled_pdf.xlsx", dst)
    return dst


@pytest.fixture()
def good_png(tmp_path: Path) -> Path:
    dst = tmp_path / "good.png"
    shutil.copy(IMAGE_FIXTURES_DIR / "good.png", dst)
    return dst


@pytest.fixture()
def alpha_png(tmp_path: Path) -> Path:
    dst = tmp_path / "alpha.png"
    shutil.copy(IMAGE_FIXTURES_DIR / "alpha.png", dst)
    return dst


@pytest.fixture()
def exif_rotated_jpeg(tmp_path: Path) -> Path:
    dst = tmp_path / "exif_rotated.jpg"
    shutil.copy(IMAGE_FIXTURES_DIR / "exif_rotated.jpg", dst)
    return dst


@pytest.fixture()
def corrupt_png(tmp_path: Path) -> Path:
    dst = tmp_path / "corrupt.png"
    shutil.copy(IMAGE_FIXTURES_DIR / "corrupt.png", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_png(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.png"
    shutil.copy(IMAGE_FIXTURES_DIR / "mislabeled_pdf.png", dst)
    return dst


@pytest.fixture()
def good_html(tmp_path: Path) -> Path:
    dst = tmp_path / "good.html"
    shutil.copy(HTML_FIXTURES_DIR / "good.html", dst)
    return dst


@pytest.fixture()
def missing_local_resource_html(tmp_path: Path) -> Path:
    dst = tmp_path / "missing_local_resource.html"
    shutil.copy(HTML_FIXTURES_DIR / "missing_local_resource.html", dst)
    return dst


@pytest.fixture()
def external_resource_html(tmp_path: Path) -> Path:
    dst = tmp_path / "external_resource.html"
    shutil.copy(HTML_FIXTURES_DIR / "external_resource.html", dst)
    return dst


@pytest.fixture()
def no_title_html(tmp_path: Path) -> Path:
    dst = tmp_path / "no_title.html"
    shutil.copy(HTML_FIXTURES_DIR / "no_title.html", dst)
    return dst


@pytest.fixture()
def binary_garbage_html(tmp_path: Path) -> Path:
    dst = tmp_path / "binary_garbage.html"
    shutil.copy(HTML_FIXTURES_DIR / "binary_garbage.html", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_html(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.html"
    shutil.copy(HTML_FIXTURES_DIR / "mislabeled_pdf.html", dst)
    return dst

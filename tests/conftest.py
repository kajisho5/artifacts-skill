from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pdf"
PPTX_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pptx"


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

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "pdf"


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

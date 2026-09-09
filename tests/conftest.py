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
SVG_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "svg"
CSV_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "csv"
MARKDOWN_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "markdown"
EPUB_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "epub"
MEDIA_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "media"


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
def blank_page_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "blank_page.pdf"
    shutil.copy(FIXTURES_DIR / "blank_page.pdf", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_pdf(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.pdf"
    shutil.copy(FIXTURES_DIR / "leftover_placeholder.pdf", dst)
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
def leftover_placeholder_text_pptx(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder_text.pptx"
    shutil.copy(PPTX_FIXTURES_DIR / "leftover_placeholder_text.pptx", dst)
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
def leftover_placeholder_docx(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.docx"
    shutil.copy(DOCX_FIXTURES_DIR / "leftover_placeholder.docx", dst)
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
def entity_bomb_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "entity_bomb.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "entity_bomb.xlsx", dst)
    return dst


@pytest.fixture()
def external_link_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "external_link.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "external_link.xlsx", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "mislabeled_pdf.xlsx", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_xlsx(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.xlsx"
    shutil.copy(XLSX_FIXTURES_DIR / "leftover_placeholder.xlsx", dst)
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
def leftover_placeholder_html(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.html"
    shutil.copy(HTML_FIXTURES_DIR / "leftover_placeholder.html", dst)
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


@pytest.fixture()
def good_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "good.svg"
    shutil.copy(SVG_FIXTURES_DIR / "good.svg", dst)
    return dst


@pytest.fixture()
def missing_local_resource_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "missing_local_resource.svg"
    shutil.copy(SVG_FIXTURES_DIR / "missing_local_resource.svg", dst)
    return dst


@pytest.fixture()
def external_resource_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "external_resource.svg"
    shutil.copy(SVG_FIXTURES_DIR / "external_resource.svg", dst)
    return dst


@pytest.fixture()
def no_size_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "no_size.svg"
    shutil.copy(SVG_FIXTURES_DIR / "no_size.svg", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.svg"
    shutil.copy(SVG_FIXTURES_DIR / "leftover_placeholder.svg", dst)
    return dst


@pytest.fixture()
def malformed_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "malformed.svg"
    shutil.copy(SVG_FIXTURES_DIR / "malformed.svg", dst)
    return dst


@pytest.fixture()
def entity_bomb_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "entity_bomb.svg"
    shutil.copy(SVG_FIXTURES_DIR / "entity_bomb.svg", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_svg(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.svg"
    shutil.copy(SVG_FIXTURES_DIR / "mislabeled_pdf.svg", dst)
    return dst


# ---------------------------------------------------------------- CSV ----


@pytest.fixture()
def good_csv(tmp_path: Path) -> Path:
    dst = tmp_path / "good.csv"
    shutil.copy(CSV_FIXTURES_DIR / "good.csv", dst)
    return dst


@pytest.fixture()
def ragged_csv(tmp_path: Path) -> Path:
    dst = tmp_path / "ragged.csv"
    shutil.copy(CSV_FIXTURES_DIR / "ragged.csv", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_csv(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.csv"
    shutil.copy(CSV_FIXTURES_DIR / "leftover_placeholder.csv", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_csv(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.csv"
    shutil.copy(CSV_FIXTURES_DIR / "mislabeled_pdf.csv", dst)
    return dst


# ----------------------------------------------------------- MARKDOWN ----


@pytest.fixture()
def good_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "good.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "good.md", dst)
    return dst


@pytest.fixture()
def unclosed_fence_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "unclosed_fence.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "unclosed_fence.md", dst)
    return dst


@pytest.fixture()
def missing_local_resource_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "missing_local_resource.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "missing_local_resource.md", dst)
    return dst


@pytest.fixture()
def external_resource_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "external_resource.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "external_resource.md", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "leftover_placeholder.md", dst)
    return dst


@pytest.fixture()
def leftover_in_code_fence_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_in_code_fence.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "leftover_in_code_fence.md", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_markdown(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.md"
    shutil.copy(MARKDOWN_FIXTURES_DIR / "mislabeled_pdf.md", dst)
    return dst


# --------------------------------------------------------------- EPUB ----


@pytest.fixture()
def good_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "good.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "good.epub", dst)
    return dst


@pytest.fixture()
def broken_manifest_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "broken_manifest.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "broken_manifest.epub", dst)
    return dst


@pytest.fixture()
def broken_spine_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "broken_spine.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "broken_spine.epub", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "leftover_placeholder.epub", dst)
    return dst


@pytest.fixture()
def mimetype_not_first_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "mimetype_not_first.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "mimetype_not_first.epub", dst)
    return dst


@pytest.fixture()
def entity_bomb_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "entity_bomb.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "entity_bomb.epub", dst)
    return dst


@pytest.fixture()
def missing_container_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "missing_container.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "missing_container.epub", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_epub(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.epub"
    shutil.copy(EPUB_FIXTURES_DIR / "mislabeled_pdf.epub", dst)
    return dst


@pytest.fixture()
def good_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "good.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "good.mp4", dst)
    return dst


@pytest.fixture()
def good_webm(tmp_path: Path) -> Path:
    dst = tmp_path / "good.webm"
    shutil.copy(MEDIA_FIXTURES_DIR / "good.webm", dst)
    return dst


@pytest.fixture()
def good_wav(tmp_path: Path) -> Path:
    dst = tmp_path / "good.wav"
    shutil.copy(MEDIA_FIXTURES_DIR / "good.wav", dst)
    return dst


@pytest.fixture()
def audio_only_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "audio_only.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "audio_only.mp4", dst)
    return dst


@pytest.fixture()
def leftover_placeholder_wav(tmp_path: Path) -> Path:
    dst = tmp_path / "leftover_placeholder.wav"
    shutil.copy(MEDIA_FIXTURES_DIR / "leftover_placeholder.wav", dst)
    return dst


@pytest.fixture()
def corrupt_truncated_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "corrupt_truncated.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "corrupt_truncated.mp4", dst)
    return dst


@pytest.fixture()
def garbage_too_short_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "garbage_too_short.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "garbage_too_short.mp4", dst)
    return dst


@pytest.fixture()
def mislabeled_pdf_as_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "mislabeled_pdf.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "mislabeled_pdf.mp4", dst)
    return dst


@pytest.fixture()
def audio_with_cover_m4a(tmp_path: Path) -> Path:
    dst = tmp_path / "audio_with_cover.m4a"
    shutil.copy(MEDIA_FIXTURES_DIR / "audio_with_cover.m4a", dst)
    return dst


@pytest.fixture()
def single_frame_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "single_frame.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "single_frame.mp4", dst)
    return dst


@pytest.fixture()
def oversized_resolution_mp4(tmp_path: Path) -> Path:
    dst = tmp_path / "oversized_resolution.mp4"
    shutil.copy(MEDIA_FIXTURES_DIR / "oversized_resolution.mp4", dst)
    return dst

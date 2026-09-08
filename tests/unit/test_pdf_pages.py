"""Unit tests for rendering/pdf_pages.py.

Adapter-level render() tests (test_pdf_adapter.py etc.) cover the real
end-to-end path per format. This file isolates render_pdf_pages()'s own
page-count limit (Grok review P0-3): `Limits.max_pages` was declared in
security/limits.py but read nowhere - rendering was completely
unconditional, so a document with an enormous page count would burn
unbounded disk and time with no cap at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artifact_skill.core.errors import ArtifactSecurityError
from artifact_skill.rendering.pdf_pages import render_pdf_pages
from artifact_skill.security.limits import Limits


def _make_pdf(path: Path, page_count: int) -> Path:
    import pypdf

    writer = pypdf.PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_renders_normally_when_under_the_default_page_limit(tmp_path):
    pdf_path = _make_pdf(tmp_path / "small.pdf", 3)
    result = render_pdf_pages(pdf_path, tmp_path / "out")
    assert len(result.files) == 3
    assert all(f.exists() for f in result.files)


def test_rejects_a_document_over_a_custom_page_limit(tmp_path):
    pdf_path = _make_pdf(tmp_path / "five.pdf", 5)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        render_pdf_pages(pdf_path, tmp_path / "out", limits=Limits(max_pages=3))
    assert exc_info.value.code == "ARTIFACT_TOO_MANY_PAGES"
    assert exc_info.value.evidence["page_count"] == 5
    assert exc_info.value.evidence["max_pages"] == 3


def test_rejecting_for_page_limit_writes_no_output_files(tmp_path):
    pdf_path = _make_pdf(tmp_path / "five.pdf", 5)
    out_dir = tmp_path / "out"
    with pytest.raises(ArtifactSecurityError):
        render_pdf_pages(pdf_path, out_dir, limits=Limits(max_pages=3))
    assert not any(out_dir.glob("page-*.png"))


def test_a_document_exactly_at_the_limit_is_allowed(tmp_path):
    pdf_path = _make_pdf(tmp_path / "three.pdf", 3)
    result = render_pdf_pages(pdf_path, tmp_path / "out", limits=Limits(max_pages=3))
    assert len(result.files) == 3


def test_default_limits_max_pages_is_2000_and_actually_used(tmp_path):
    """A regression guard against silently reverting to the old
    unconditional-render behavior: the default must be a real, finite
    cap, not e.g. accidentally left at float('inf')."""
    from artifact_skill.security.limits import DEFAULT_LIMITS

    assert DEFAULT_LIMITS.max_pages == 2000

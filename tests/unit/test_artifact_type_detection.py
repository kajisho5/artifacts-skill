from __future__ import annotations

from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from tests.conftest import FIXTURES_DIR


def test_pdf_detected_by_content_not_extension():
    ref = ArtifactRef.from_path(FIXTURES_DIR / "good_2page.pdf")
    assert ref.type == ArtifactType.PDF


def test_html_content_with_pdf_extension_is_detected_as_html(mislabeled_html_pdf):
    """The whole point of spec #7: never trust '.pdf' alone."""
    ref = ArtifactRef.from_path(mislabeled_html_pdf)
    assert ref.type == ArtifactType.HTML


def test_corrupt_pdf_is_still_type_pdf_by_header(corrupt_pdf):
    """A %PDF- header is enough to type it PDF even if the body is broken;
    *readability* is a separate, later concern (adapter.inspect / verify)."""
    ref = ArtifactRef.from_path(corrupt_pdf)
    assert ref.type == ArtifactType.PDF


def test_missing_file_raises_input_error(tmp_path):
    import pytest

    from artifact_skill.core.errors import ArtifactInputError

    with pytest.raises(ArtifactInputError):
        ArtifactRef.from_path(tmp_path / "does_not_exist.pdf")


def test_hash_is_stable_for_same_content(good_pdf):
    ref1 = ArtifactRef.from_path(good_pdf)
    ref2 = ArtifactRef.from_path(good_pdf)
    assert ref1.sha256 == ref2.sha256
    assert len(ref1.sha256) == 64

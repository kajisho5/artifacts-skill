from __future__ import annotations

import pytest

from artifact_skill.core.artifact import sha256_of
from artifact_skill.core.engine import run_lifecycle
from artifact_skill.core.errors import ArtifactSecurityError
from artifact_skill.core.operation import default_output_path


def test_default_output_path_never_equals_input(tmp_path):
    input_path = tmp_path / "report.pdf"
    input_path.write_bytes(b"%PDF-1.4\n")
    out = default_output_path(input_path, "metadata_set")
    assert out != input_path
    assert out.name == "report_artifact_metadata_set.pdf"


def test_input_hash_unchanged_after_execute(good_pdf, tmp_path):
    before = sha256_of(good_pdf)
    output_path = tmp_path / "out.pdf"
    run_lifecycle(
        good_pdf, "metadata_set", {"title": "changed"}, output_path,
        evidence_dir=tmp_path / "reports",
    )
    after = sha256_of(good_pdf)
    assert before == after


def test_input_hash_unchanged_across_multiple_operations(good_pdf, tmp_path):
    before = sha256_of(good_pdf)
    for i, args in enumerate([{"title": "a"}, {"author": "b"}, {"subject": "c"}]):
        run_lifecycle(
            good_pdf, "metadata_set", args, tmp_path / f"out{i}.pdf",
            evidence_dir=tmp_path / f"reports{i}",
        )
    assert sha256_of(good_pdf) == before


# --- output_path aliasing the input must be rejected, not silently  --------
# --- allowed to overwrite it (Grok review P0-1)                     --------
#
# Every adapter's execute() writes straight to whatever output_path it's
# given - before core/engine.py::build_plan() gained this guard, "Original
# Protection" only held by accident whenever a caller's --output happened
# not to collide with the input. It was previously untested and, when
# reproduced directly against the CLI, a real bug: `execute --output`
# equal to (or resolving to, or a hard/symlink alias of) the input path
# silently overwrote it.


def test_execute_rejects_output_path_identical_to_input(good_pdf, tmp_path):
    before = sha256_of(good_pdf)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        run_lifecycle(
            good_pdf, "metadata_set", {"title": "x"}, good_pdf,
            evidence_dir=tmp_path / "reports",
        )
    assert exc_info.value.code == "ARTIFACT_OUTPUT_OVERWRITES_INPUT"
    assert sha256_of(good_pdf) == before
    assert not (tmp_path / "reports").exists()


def test_execute_rejects_output_path_that_resolves_to_the_same_input(good_pdf, tmp_path):
    """A relative path and an absolute path can name the same file
    without being string-equal - the guard must resolve() both sides,
    not just compare the literal path objects."""
    before = sha256_of(good_pdf)
    relative_equivalent = good_pdf.parent / ".." / good_pdf.parent.name / good_pdf.name
    with pytest.raises(ArtifactSecurityError) as exc_info:
        run_lifecycle(
            good_pdf, "metadata_set", {"title": "x"}, relative_equivalent,
            evidence_dir=tmp_path / "reports",
        )
    assert exc_info.value.code == "ARTIFACT_OUTPUT_OVERWRITES_INPUT"
    assert sha256_of(good_pdf) == before


def test_execute_rejects_a_symlink_output_pointing_at_the_input(good_pdf, tmp_path):
    before = sha256_of(good_pdf)
    symlink_path = tmp_path / "alias.pdf"
    symlink_path.symlink_to(good_pdf)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        run_lifecycle(
            good_pdf, "metadata_set", {"title": "x"}, symlink_path,
            evidence_dir=tmp_path / "reports",
        )
    assert exc_info.value.code == "ARTIFACT_OUTPUT_OVERWRITES_INPUT"
    assert sha256_of(good_pdf) == before


def test_execute_rejects_a_hard_link_output_pointing_at_the_input(good_pdf, tmp_path):
    """A hard link is a second name for the same inode - resolve()
    doesn't detect it (the paths stay textually distinct), only an
    (st_dev, st_ino) comparison does."""
    import os

    before = sha256_of(good_pdf)
    hardlink_path = tmp_path / "hardlink.pdf"
    os.link(good_pdf, hardlink_path)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        run_lifecycle(
            good_pdf, "metadata_set", {"title": "x"}, hardlink_path,
            evidence_dir=tmp_path / "reports",
        )
    assert exc_info.value.code == "ARTIFACT_OUTPUT_OVERWRITES_INPUT"
    assert sha256_of(good_pdf) == before


def test_execute_rejects_output_equal_to_input_for_every_format(
    good_pdf, good_pptx, good_docx, good_xlsx, good_png, tmp_path
):
    cases = [
        (good_pdf, "metadata_set", {"title": "x"}),
        (good_pptx, "metadata_set", {"title": "x"}),
        (good_docx, "metadata_set", {"title": "x"}),
        (good_xlsx, "metadata_set", {"title": "x"}),
        (good_png, "resize", {"width": 10, "height": 10}),
    ]
    for input_path, operation, args in cases:
        before = sha256_of(input_path)
        with pytest.raises(ArtifactSecurityError) as exc_info:
            run_lifecycle(input_path, operation, args, input_path, evidence_dir=tmp_path / "reports")
        assert exc_info.value.code == "ARTIFACT_OUTPUT_OVERWRITES_INPUT", input_path
        assert sha256_of(input_path) == before, input_path


def test_plan_also_rejects_output_equal_to_input_not_just_execute(good_pdf, tmp_path):
    """The guard lives in build_plan() (shared by plan/execute/receipt),
    not bolted onto execute() alone - a bare `plan` call must refuse too,
    rather than silently describing a plan that would corrupt Original
    Protection if it were ever run."""
    from artifact_skill.core.engine import build_plan

    with pytest.raises(ArtifactSecurityError) as exc_info:
        build_plan(good_pdf, "metadata_set", {"title": "x"}, good_pdf)
    assert exc_info.value.code == "ARTIFACT_OUTPUT_OVERWRITES_INPUT"

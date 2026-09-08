from __future__ import annotations

import hashlib

from artifact_skill.core.artifact import ArtifactRef, sha256_of
from artifact_skill.core.engine import run_lifecycle
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

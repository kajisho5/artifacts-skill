"""Sanity guard for run_scaling_benchmark.py (docs/performance.md).

Only checks what's cheap and environment-independent: every synthetic
"large" fixture builder must actually produce a file that `detect_type()`
recognizes as the format it claims to build - not a smoke test of the
full benchmark run (soffice/Chromium launches, large fixtures), which is
slow and belongs to a developer-run report, not the routine test suite
(same reasoning as test_perf_benchmark.py's narrower scope).

This is exactly the regression class the Markdown builder hit once
already: enough ATX headings alone (one Markdown signal) isn't enough
for core/artifact.py's deliberately conservative `_looks_like_markdown()`
- confirmed by direct reproduction before adding the list-item line that
fixed it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_scaling_benchmark import _LARGE_BUILDERS

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from artifact_skill.core.artifact import ArtifactRef, ArtifactType  # noqa: E402

_EXPECTED_TYPE = {
    "pdf": ArtifactType.PDF,
    "pptx": ArtifactType.PPTX,
    "docx": ArtifactType.DOCX,
    "xlsx": ArtifactType.XLSX,
    "image": ArtifactType.IMAGE_PNG,
    "html": ArtifactType.HTML,
    "svg": ArtifactType.SVG,
    "csv": ArtifactType.CSV,
    "markdown": ArtifactType.MARKDOWN,
    "epub": ArtifactType.EPUB,
}


def test_every_large_fixture_builder_is_detected_as_its_own_format():
    assert set(_LARGE_BUILDERS) == set(_EXPECTED_TYPE)
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        for fmt, builder in _LARGE_BUILDERS.items():
            path, _size_label = builder(tmp_dir)
            ref = ArtifactRef.from_path(path)
            assert ref.type == _EXPECTED_TYPE[fmt], (
                f"{fmt}'s large fixture builder produced a file detected as "
                f"{ref.type}, not {_EXPECTED_TYPE[fmt]} - detect_type() didn't "
                "recognize its own synthetic fixture."
            )

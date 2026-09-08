"""Full-process integration tests: invokes the real `artifact-skill`
console script (installed by `pip install -e .`) via subprocess, exactly
as an agent or a human would."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CLI = shutil.which("artifact-skill")


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    if CLI:
        cmd = [CLI, *args]
    else:
        cmd = [sys.executable, "-m", "artifact_skill.cli.main", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def test_doctor_json_is_valid_and_has_pdf_capabilities(tmp_path):
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert "pdf.structural" in data["capabilities"]


def test_contract_json_matches_nine_tools(tmp_path):
    proc = run_cli(["contract", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert len(data["tools"]) == 9


def test_execute_dry_run_writes_nothing(good_pdf, tmp_path):
    proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}', "--dry-run", "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["dry_run"] is True
    produced = list(tmp_path.iterdir())
    assert produced == [good_pdf] or set(p.name for p in produced) == {good_pdf.name}


def test_execute_then_verify_pass(good_pdf, tmp_path):
    exec_proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"CLI Test"}',
         "--output", "out.pdf", "--json"],
        cwd=tmp_path,
    )
    assert exec_proc.returncode in (0, 1, 3)  # depends on font_embedding UNKNOWN rollup; must not crash
    assert (tmp_path / "out.pdf").exists()

    verify_proc = run_cli(
        ["verify", "out.pdf", "--policy", '{"require_page_count": 2, "require_metadata": {"Title": "CLI Test"}}',
         "--json"],
        cwd=tmp_path,
    )
    data = json.loads(verify_proc.stdout)
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["page_count_requirement"]["status"] == "pass"
    assert check_by_id["metadata_title"]["status"] == "pass"


def test_verify_fail_gives_nonzero_exit(empty_pdf, tmp_path):
    proc = run_cli(["verify", str(empty_pdf), "--json"], cwd=tmp_path)
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["status"] == "fail"


def test_input_not_found_gives_input_exit_code(tmp_path):
    proc = run_cli(["inspect", str(tmp_path / "nope.pdf"), "--json"], cwd=tmp_path)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_INPUT_NOT_FOUND"


def test_unimplemented_format_gives_clear_capability_error(tmp_path):
    """XLSX is still on the `_PLANNED` list (registry.py) as of this test —
    PDF/PPTX/DOCX moved off it as their adapters landed, so this
    specifically needs a format that's genuinely not implemented yet."""
    xlsx_like = tmp_path / "sheet.xlsx"
    xlsx_like.write_bytes(b"not a real xlsx, just bytes")
    proc = run_cli(["inspect", str(xlsx_like), "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert data["error"]["code"] in ("ARTIFACT_ADAPTER_NOT_IMPLEMENTED", "ARTIFACT_TYPE_UNSUPPORTED")
    assert proc.returncode == 3


def test_receipt_end_to_end_human_readable_report(good_pdf, tmp_path):
    proc = run_cli(
        ["receipt", str(good_pdf), "--operation", "metadata_set", "--args", '{"author":"CLI"}'],
        cwd=tmp_path,
    )
    assert "ARTIFACT REPORT" in proc.stdout
    assert "Status:" in proc.stdout
    assert (tmp_path / "reports" / "receipt.json").exists()


def test_pptx_doctor_reports_structural_capability(tmp_path):
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert "pptx.structural" in data["capabilities"]
    assert "pptx.render" in data["capabilities"]


def test_pptx_execute_then_verify(good_pptx, tmp_path):
    exec_proc = run_cli(
        ["execute", str(good_pptx), "--operation", "metadata_set", "--args", '{"title":"CLI PPTX Test"}',
         "--output", "out.pptx", "--json"],
        cwd=tmp_path,
    )
    assert (tmp_path / "out.pptx").exists()

    verify_proc = run_cli(
        ["verify", "out.pptx", "--policy", '{"require_slide_count": 2, "require_metadata": {"title": "CLI PPTX Test"}}',
         "--json"],
        cwd=tmp_path,
    )
    data = json.loads(verify_proc.stdout)
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["slide_count_requirement"]["status"] == "pass"
    assert check_by_id["metadata_title"]["status"] == "pass"


def test_pptx_input_unchanged_after_execute(good_pptx, tmp_path):
    from artifact_skill.core.artifact import sha256_of

    before = sha256_of(good_pptx)
    run_cli(
        ["execute", str(good_pptx), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", "out.pptx", "--json"],
        cwd=tmp_path,
    )
    assert sha256_of(good_pptx) == before


def test_docx_doctor_reports_structural_capability(tmp_path):
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert "docx.structural" in data["capabilities"]
    assert "docx.render" in data["capabilities"]


def test_docx_execute_then_verify(good_docx, tmp_path):
    exec_proc = run_cli(
        ["execute", str(good_docx), "--operation", "metadata_set", "--args", '{"title":"CLI DOCX Test"}',
         "--output", "out.docx", "--json"],
        cwd=tmp_path,
    )
    assert (tmp_path / "out.docx").exists()

    verify_proc = run_cli(
        ["verify", "out.docx", "--policy", '{"require_metadata": {"title": "CLI DOCX Test"}}', "--json"],
        cwd=tmp_path,
    )
    data = json.loads(verify_proc.stdout)
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["metadata_title"]["status"] == "pass"
    assert check_by_id["page_count"]["status"] == "unknown"


def test_docx_input_unchanged_after_execute(good_docx, tmp_path):
    from artifact_skill.core.artifact import sha256_of

    before = sha256_of(good_docx)
    run_cli(
        ["execute", str(good_docx), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", "out.docx", "--json"],
        cwd=tmp_path,
    )
    assert sha256_of(good_docx) == before

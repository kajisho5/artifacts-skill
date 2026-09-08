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
    html_like = tmp_path / "deck.pptx"
    html_like.write_bytes(b"not a real pptx, just bytes")
    proc = run_cli(["inspect", str(html_like), "--json"], cwd=tmp_path)
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

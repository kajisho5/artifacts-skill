"""Issue #47: concurrent CLI runs that omit --evidence-dir/--out-dir must
not share (and overwrite) one evidence directory.

Before the fix every such run wrote to the same ./reports/receipt.json and
./reports/rendered/page-NNN.png. With 50 concurrent `receipt` processes on
two different inputs, every receipt ended up pointing at page images some
other process had written, and only one receipt.json survived on disk.
These tests run real, separate OS processes (not threads), exactly as
concurrent agents would.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tests.conftest import FIXTURES_DIR

CLI = shutil.which("artifacts-skill")
CONCURRENT_RUNS = 50


def _cmd(args: list[str]) -> list[str]:
    if CLI:
        return [CLI, *args]
    return [sys.executable, "-m", "artifact_skill.cli.main", *args]


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(_cmd(args), cwd=cwd, capture_output=True, text=True, timeout=120)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_hashes(receipt: dict, cwd: Path) -> list[str]:
    return [_sha256(cwd / a["path"]) for a in receipt["artifacts"]]


def test_50_concurrent_receipt_processes_never_share_or_overwrite_evidence(tmp_path):
    # Two inputs whose renders differ (2 pages vs. 3 pages), so evidence
    # written by a run for the other input is detectable by content.
    inputs = {
        "a.pdf": FIXTURES_DIR / "good_2page.pdf",
        "b.pdf": FIXTURES_DIR / "blank_page.pdf",
    }
    for name, src in inputs.items():
        shutil.copy(src, tmp_path / name)

    # What each input's evidence looks like when rendered alone.
    expected: dict[str, list[str]] = {}
    for name in inputs:
        proc = run_cli(["receipt", name, "--json", "--evidence-dir", f"ref-{name}"], cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        expected[name] = _evidence_hashes(json.loads(proc.stdout), tmp_path)
        assert expected[name], "the reference run produced no visual evidence to compare"
    assert expected["a.pdf"] != expected["b.pdf"]

    # 50 real processes, all started before any is waited on.
    names = [name for name in inputs for _ in range(CONCURRENT_RUNS // 2)]
    procs = [
        (name, subprocess.Popen(
            _cmd(["receipt", name, "--json"]), cwd=tmp_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
        for name in names
    ]
    receipts: list[tuple[str, dict]] = []
    for name, proc in procs:
        stdout, stderr = proc.communicate(timeout=300)
        assert proc.returncode == 0, stderr
        receipts.append((name, json.loads(stdout)))

    # One receipt.json per run, each in its own directory, none lost.
    on_disk = sorted((tmp_path / "reports").glob("*/receipt.json"))
    assert len(on_disk) == CONCURRENT_RUNS
    assert not (tmp_path / "reports" / "receipt.json").exists()

    run_dirs = set()
    all_paths: list[str] = []
    for name, receipt in receipts:
        paths = [a["path"] for a in receipt["artifacts"]]
        run_dir = {Path(p).parents[1] for p in paths}  # reports/<run-id>/rendered/page-NNN.png
        assert len(run_dir) == 1
        run_dirs |= run_dir
        all_paths.extend(paths)
        # The evidence each receipt points at is still that run's own render
        # of its own input - nothing another process wrote.
        assert _evidence_hashes(receipt, tmp_path) == expected[name], f"evidence for {name} was overwritten"
        # And the receipt.json next to it is this run's receipt.
        saved = json.loads((tmp_path / next(iter(run_dir)) / "receipt.json").read_text())
        assert saved == receipt

    assert len(run_dirs) == CONCURRENT_RUNS
    assert len(all_paths) == len(set(all_paths))


def test_render_and_look_default_to_a_fresh_directory_per_run(tmp_path):
    shutil.copy(FIXTURES_DIR / "good_2page.pdf", tmp_path / "a.pdf")

    first = json.loads(run_cli(["render", "a.pdf", "--json"], cwd=tmp_path).stdout)
    second = json.loads(run_cli(["render", "a.pdf", "--json"], cwd=tmp_path).stdout)
    first_dir = {Path(f).parent for f in first["files"]}
    second_dir = {Path(f).parent for f in second["files"]}
    assert len(first_dir) == len(second_dir) == 1
    assert first_dir != second_dir
    for d in (*first_dir, *second_dir):
        assert d.name == "rendered"
        assert d.parent.parent == Path("reports")

    sheets = {
        json.loads(run_cli(["look", "a.pdf", "--json"], cwd=tmp_path).stdout)["contact_sheet"]
        for _ in range(2)
    }
    assert len(sheets) == 2
    for sheet in sheets:
        assert (tmp_path / sheet).exists()
        assert Path(sheet).name == "contact-sheet.png"


def test_explicit_out_dir_is_still_used_exactly_as_given(tmp_path):
    shutil.copy(FIXTURES_DIR / "good_2page.pdf", tmp_path / "a.pdf")
    data = json.loads(run_cli(["render", "a.pdf", "--out-dir", "mine", "--json"], cwd=tmp_path).stdout)
    assert {Path(f).parent for f in data["files"]} == {Path("mine")}
    assert not (tmp_path / "reports").exists()


def test_a_failed_run_leaves_no_empty_run_directory_behind(tmp_path):
    proc = run_cli(["receipt", "missing.pdf", "--json"], cwd=tmp_path)
    assert proc.returncode != 0
    assert list((tmp_path / "reports").iterdir()) == []


def test_dry_run_creates_no_run_directory(tmp_path):
    shutil.copy(FIXTURES_DIR / "good_2page.pdf", tmp_path / "a.pdf")
    proc = run_cli(["render", "a.pdf", "--dry-run", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["would_render_to"].startswith("reports")
    assert not (tmp_path / "reports").exists()

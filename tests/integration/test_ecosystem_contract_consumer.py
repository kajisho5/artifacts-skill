"""Issue #10's "ecosystem integration" dogfood proof, kept honest on every
CI run rather than being a one-off manual check: runs
examples/standalone_contract_consumer.py — a script that imports nothing
from `artifact_skill` and discovers what to call purely from
`artifacts-skill contract --json` — against a real fixture, as a real
subprocess, exactly as an external, unfamiliar orchestrator would.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / "examples" / "standalone_contract_consumer.py"


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)


def test_script_imports_nothing_from_artifact_skill():
    """The whole point is that this script needs no special knowledge of
    this package — enforce that literally, not just by convention."""
    source = SCRIPT.read_text()
    assert "import artifact_skill" not in source
    assert "from artifact_skill" not in source


def test_discovers_and_calls_a_tool_purely_from_the_contract(good_pdf):
    proc = _run_script(str(good_pdf))
    assert proc.returncode == 0, proc.stderr
    assert "Discovered read-only, single-input tool(s) from the contract alone" in proc.stdout
    assert "Success." in proc.stdout
    assert "type:    pdf" in proc.stdout


def test_reports_structured_error_for_a_broken_input(corrupt_pdf):
    proc = _run_script(str(corrupt_pdf))
    assert proc.returncode == 1
    assert "reported a structured error: ARTIFACT_PDF_UNREADABLE" in proc.stdout


def test_usage_error_on_missing_argument():
    proc = _run_script()
    assert proc.returncode == 2
    assert "usage:" in proc.stderr

"""Credits and licence lines checked against the assets' licence records
(ecosystem roadmap step 8, docs/architecture.md "Credits and licence
lines").

Every attribution an asset's licence asks for must be in the end roll,
description or credits document; each one that is not is reported with
the asset, and nothing is changed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from artifact_skill.adapters.registry import adapter_for
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.engine import run_lifecycle
from artifact_skill.core.errors import ArtifactInputError
from artifact_skill.core.verification import CheckStatus
from artifact_skill.credits_check import CHECK_ID, parse_cc
from artifact_skill.policies import known_policy_keys, resolve_policy
from artifact_skill.text_checks import verify_structural_with_text_checks

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "credits"
RECORDS = FIXTURES / "licences.json"


def _check(doc: Path, policy: dict):
    ref = ArtifactRef.from_path(doc)
    result = verify_structural_with_text_checks(adapter_for(ref), ref, policy)
    checks = [c for c in result.checks if c.id == CHECK_ID]
    assert len(checks) == 1
    return checks[0]


def _doc(tmp_path: Path, *lines: str) -> Path:
    # A heading and list items make the file Markdown.
    path = tmp_path / "credits.md"
    path.write_text("# Credits\n\n" + "".join(f"- {line}\n" for line in lines), encoding="utf-8")
    return path


def _findings(tmp_path: Path, records: list[dict], *lines: str) -> list[dict]:
    return _check(_doc(tmp_path, *lines), {"licence_records": records}).evidence["findings"]


# ---- the roadmap's "Done when" ---------------------------------------------


def test_missing_cc_by_attribution_is_reported_with_the_asset(tmp_path):
    doc = tmp_path / "end_roll.md"
    shutil.copy(FIXTURES / "end_roll_missing_one.md", doc)
    before = doc.read_bytes()

    check = _check(doc, {"licence_records": str(RECORDS)})

    assert check.status == CheckStatus.FAIL
    assert [(f["asset"], f["licence"], f["kind"]) for f in check.evidence["findings"]] == [
        ("sfx/rain-loop.wav", "CC-BY-4.0", "missing")
    ]
    assert "sfx/rain-loop.wav" in check.message
    assert doc.read_bytes() == before  # reported, never fixed


def test_complete_end_roll_passes():
    check = _check(FIXTURES / "end_roll_complete.md", {"licence_records": str(RECORDS)})
    assert check.status == CheckStatus.PASS
    assert check.evidence["assets"] == 6


def test_without_licence_records_there_is_no_check():
    ref = ArtifactRef.from_path(FIXTURES / "end_roll_missing_one.md")
    result = verify_structural_with_text_checks(adapter_for(ref), ref, {})
    assert all(c.id != CHECK_ID for c in result.checks)


# ---- what each licence asks for --------------------------------------------


def test_declared_attribution_line_must_be_there_as_written(tmp_path):
    record = {"asset": "bgm.wav", "licence": "CC-BY-4.0", "title": "Tide", "author": "Ana Ruiz",
              "attribution": f"{chr(0x201C)}Tide{chr(0x201D)} by Ana Ruiz, licensed under CC BY 4.0"}
    assert _findings(tmp_path, [record], '"Tide" by  ana ruiz, licensed under CC BY 4.0') == []
    worded = _findings(tmp_path, [record], "Music: Tide (Ana Ruiz)")
    assert [(f["kind"], f["expected"]) for f in worded] == [("worded_differently", record["attribution"])]
    assert [f["kind"] for f in _findings(tmp_path, [record], "Music: someone else")] == ["missing"]


def test_credit_line_is_used_when_there_is_no_attribution_line(tmp_path):
    record = {"asset": "voice", "licence": "Provider terms", "credit": "Voice: Lumen Voice Studio"}
    assert _findings(tmp_path, [record], "Voice: Lumen Voice Studio") == []
    assert [f["kind"] for f in _findings(tmp_path, [record], "Voice by Lumen")] == ["missing"]


@pytest.mark.parametrize(
    "line",
    [
        "Rain Loop by Kenji Mori, CC BY-SA 4.0",
        "Rain Loop by Kenji Mori, CC-BY-SA-4.0",
        "Rain Loop by Kenji Mori, Creative Commons Attribution-ShareAlike 4.0 International",
        "Rain Loop by Kenji Mori, https://creativecommons.org/licenses/by-sa/4.0/",
    ],
)
def test_cc_by_without_a_line_needs_author_title_and_licence(tmp_path, line):
    record = {"asset": "rain.wav", "licence": "CC-BY-SA-4.0", "title": "Rain Loop", "author": "Kenji Mori",
              "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/"}
    assert _findings(tmp_path, [record], line) == []


def test_cc_by_with_the_wrong_licence_or_version_is_incomplete(tmp_path):
    record = {"asset": "rain.wav", "licence": "CC-BY-SA-4.0", "title": "Rain Loop", "author": "Kenji Mori"}
    for line in ("Rain Loop by Kenji Mori, CC BY 4.0", "Rain Loop by Kenji Mori, CC BY-SA 3.0"):
        findings = _findings(tmp_path, [record], line)
        assert [(f["kind"], f["missing_parts"]) for f in findings] == [("incomplete", ["licence"])]


@pytest.mark.parametrize("licence", ["CC0-1.0", "Public domain", "OFL-1.1", "Royalty-free stock licence"])
def test_licences_that_ask_for_no_credit_need_nothing(tmp_path, licence):
    assert _findings(tmp_path, [{"asset": "x", "licence": licence, "author": "Someone"}], "Nothing here") == []


def test_unknown_licence_is_unknown_not_pass(tmp_path):
    check = _check(_doc(tmp_path, "Music: Tide"), {"licence_records": [{"asset": "bgm.wav", "licence": "unknown"}]})
    assert check.status == CheckStatus.UNKNOWN
    assert check.evidence["findings"][0]["kind"] == "licence_unknown"


def test_cc_by_record_with_nothing_to_look_for_is_unknown(tmp_path):
    check = _check(_doc(tmp_path, "Music"), {"licence_records": [{"asset": "bgm.wav", "licence": "CC-BY-4.0"}]})
    assert check.status == CheckStatus.UNKNOWN
    assert check.evidence["findings"][0]["kind"] == "record_incomplete"


def test_a_failure_outranks_an_unknown_record(tmp_path):
    records = [{"asset": "a", "licence": "unknown"}, {"asset": "b", "licence": "CC-BY-4.0", "author": "Ana Ruiz"}]
    check = _check(_doc(tmp_path, "Nothing"), {"licence_records": records})
    assert check.status == CheckStatus.FAIL


def test_sound_design_library_index_rows_are_read(tmp_path):
    rows = {"files": [{"file": "bgm/tide.wav", "licence": {
        "licence": "CC-BY-4.0", "status": "declared", "title": "Tide", "author": "Ana Ruiz",
        "attribution": "\"Tide\" by Ana Ruiz, licensed under CC-BY-4.0", "attribution_source": "composed"}}]}
    path = tmp_path / "index.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    check = _check(_doc(tmp_path, "Nothing"), {"licence_records": str(path)})
    assert [(f["asset"], f["kind"]) for f in check.evidence["findings"]] == [("bgm/tide.wav", "missing")]


def test_parse_cc():
    assert parse_cc("CC BY-NC-SA 4.0") == [(frozenset({"by", "nc", "sa"}), "4.0")]
    assert parse_cc("cc0 1.0") == [(frozenset({"zero"}), "1.0")]
    assert parse_cc("Creative Commons Attribution-NonCommercial 3.0") == [(frozenset({"by", "nc"}), "3.0")]
    assert parse_cc("accurate by design") == []


# ---- reading the records ---------------------------------------------------


def test_missing_records_file_is_unknown(tmp_path):
    check = _check(_doc(tmp_path, "x"), {"licence_records": str(tmp_path / "nope.json")})
    assert check.status == CheckStatus.UNKNOWN


def test_records_file_that_is_not_json_is_unknown(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _check(_doc(tmp_path, "x"), {"licence_records": str(bad)}).status == CheckStatus.UNKNOWN


def test_format_without_text_is_unknown(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    img = tmp_path / "x.png"
    pil.new("RGB", (4, 4)).save(img)
    assert _check(img, {"licence_records": str(RECORDS)}).status == CheckStatus.UNKNOWN


def test_docx_credits_document(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph('"Morning Light" by Ana Ruiz, licensed under CC BY 4.0')
    path = tmp_path / "credits.docx"
    document.save(str(path))
    record = {"asset": "bgm", "licence": "CC-BY-4.0",
              "attribution": '"Morning Light" by Ana Ruiz, licensed under CC BY 4.0'}
    assert _check(path, {"licence_records": [record]}).status == CheckStatus.PASS


# ---- policy ----------------------------------------------------------------


def test_policy_key_is_known():
    assert "licence_records" in known_policy_keys()
    assert resolve_policy(None, {"licence_records": "x.json"})["licence_records"] == "x.json"


@pytest.mark.parametrize(
    "value",
    ["", [], {"asset": "x"}, ["x"], [{"licence": "CC-BY-4.0"}], 3],
)
def test_malformed_policy_is_rejected(value):
    with pytest.raises(ArtifactInputError) as exc:
        resolve_policy(None, {"licence_records": value})
    assert exc.value.code == "ARTIFACT_INVALID_ARGS"


def test_receipt_carries_the_check(tmp_path):
    doc = tmp_path / "end_roll.md"
    shutil.copy(FIXTURES / "end_roll_missing_one.md", doc)
    result = run_lifecycle(doc, None, {}, None, policy={"licence_records": str(RECORDS)}, evidence_dir=tmp_path / "ev")
    assert result.receipt is not None
    receipt = result.receipt.to_dict()
    assert receipt["status"] == "fail"
    checks = {c["id"]: c for c in receipt["verification"]["structural"]["checks"]}
    assert checks[CHECK_ID]["evidence"]["findings"][0]["asset"] == "sfx/rain-loop.wav"


def test_mcp_verify_runs_the_check():
    from artifact_skill.mcp.server import _h_verify

    out = _h_verify({"input": str(FIXTURES / "end_roll_missing_one.md"), "policy": {"licence_records": str(RECORDS)}})
    assert out["status"] == "fail"
    assert any(c["id"] == CHECK_ID for c in out["checks"])


def test_cli_verify_exits_fail_and_names_the_asset(tmp_path):
    cli = shutil.which("artifacts-skill")
    cmd = [cli] if cli else [sys.executable, "-m", "artifact_skill.cli.main"]
    policy = json.dumps({"licence_records": str(RECORDS)})
    proc = subprocess.run(
        [*cmd, "verify", str(FIXTURES / "end_roll_missing_one.md"), "--policy", policy, "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1, proc.stderr
    data = json.loads(proc.stdout)
    check = next(c for c in data["checks"] if c["id"] == CHECK_ID)
    assert [f["asset"] for f in check["evidence"]["findings"]] == ["sfx/rain-loop.wav"]

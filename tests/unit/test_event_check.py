"""Event documents checked against the organiser's source list (ecosystem
roadmap step 6, docs/architecture.md "Event documents against their
source").

Every name, title and affiliation in a cue sheet, run-of-show sheet,
speaker list or lower-third CSV must be spelled exactly as the source
list does, and every session time must agree with it; each difference is
reported with its source row, and nothing is changed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import artifact_skill
from artifact_skill.adapters.registry import adapter_for
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.engine import run_lifecycle
from artifact_skill.core.errors import ArtifactInputError
from artifact_skill.core.verification import CheckStatus
from artifact_skill.event_check import CHECK_ID
from artifact_skill.policies import known_policy_keys, resolve_policy
from artifact_skill.text_checks import verify_structural_with_text_checks

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "event"
SOURCE = FIXTURES / "source.csv"
HEADER = "start,end,session,speaker,title,affiliation\n"


def _check(doc: Path, policy: dict):
    ref = ArtifactRef.from_path(doc)
    result = verify_structural_with_text_checks(adapter_for(ref), ref, policy)
    checks = [c for c in result.checks if c.id == CHECK_ID]
    assert len(checks) == 1
    return checks[0]


def _csv(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "lower_thirds.csv"
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


def _kinds(check) -> list[tuple[str, str, int]]:
    return [(f["kind"], f["field"], f["source_row"]["row"]) for f in check.evidence["findings"]]


ROW_1 = {"name": "髙橋 一郎", "honorific": "先生", "title": "教授", "affiliation": "東京医科大学 循環器内科",
         "start": "09:30", "end": "10:20"}


# ---- the roadmap's "Done when" ---------------------------------------------


def test_one_variant_kanji_and_one_wrong_time_give_two_findings_with_their_source_rows(tmp_path):
    doc = tmp_path / "run_of_show.csv"
    shutil.copy(FIXTURES / "run_of_show_two_errors.csv", doc)
    before = doc.read_bytes()

    check = _check(doc, {"event_source": str(SOURCE)})

    assert check.status == CheckStatus.FAIL
    findings = check.evidence["findings"]
    assert len(findings) == 2
    name, time = findings
    assert name["kind"] == "variant_spelling"
    assert name["field"] == "name"
    assert name["expected"] == "髙橋 一郎"
    assert name["found"] == "高橋 一郎"
    assert name["differs"] == [{"expected": "髙", "found": "高"}]
    assert name["source_row"]["row"] == 1
    assert name["source_row"]["name"] == "髙橋 一郎"
    assert name["line"] == 2
    assert time["kind"] == "time_mismatch"
    assert time["field"] == "start"
    assert time["expected"] == {"start": "10:30", "end": "11:00"}
    assert time["found"] == ["10:40", "11:00"]
    assert time["source_row"]["row"] == 2
    assert time["source_row"]["name"] == "Ana Ruiz"
    assert time["line"] == 3
    assert "row 1" in check.message and "row 2" in check.message
    assert doc.read_bytes() == before


@pytest.mark.parametrize("fixture", ["run_of_show_ok.csv", "speaker_list_ok.md"])
def test_documents_that_match_the_source_pass(fixture):
    check = _check(FIXTURES / fixture, {"event_source": str(SOURCE)})
    assert check.status == CheckStatus.PASS, check.evidence
    assert check.evidence["rows"] == 4


# ---- names -----------------------------------------------------------------


def test_compatibility_ideograph_is_not_folded_away(tmp_path):
    # NFKC turns 﨑 (U+FA11) into 崎; the check must not.
    doc = _csv(tmp_path, "11:10,11:50,症例検討,山崎 花子 先生,准教授,大阪総合病院")
    row = {"name": "山﨑 花子", "start": "11:10", "end": "11:50"}
    check = _check(doc, {"event_source": [row]})
    assert _kinds(check) == [("variant_spelling", "name", 1)]
    assert check.evidence["findings"][0]["differs"] == [{"expected": "﨑", "found": "崎"}]


def test_the_reverse_swap_is_found_too(tmp_path):
    doc = _csv(tmp_path, "09:30,10:20,基調講演,髙橋 一郎,教授,東京医科大学")
    check = _check(doc, {"event_source": [{"name": "高橋 一郎", "start": "09:30"}]})
    assert _kinds(check) == [("variant_spelling", "name", 1)]


def test_spacing_inside_a_cjk_name_and_full_width_ascii_are_not_differences(tmp_path):
    doc = _csv(tmp_path, "０９：３０,10:20,基調講演,髙橋　一郎,教授,東京医科大学", "10:30,11:00,x,ＡＮＡ ＲＵＩＺ,,")  # noqa: RUF001 - full-width on purpose
    rows = [{"name": "髙橋一郎", "start": "09:30", "end": "10:20"}, {"name": "ANA RUIZ"}]
    assert _check(doc, {"event_source": rows}).status == CheckStatus.PASS


def test_a_name_not_in_the_document_is_missing(tmp_path):
    doc = _csv(tmp_path, "09:30,10:20,基調講演,髙橋 一郎,教授,東京医科大学")
    check = _check(doc, {"event_source": [ROW_1, {"name": "Kwame Mensah"}]})
    assert ("missing", "name", 2) in _kinds(check)


def test_a_latin_name_inside_a_longer_name_does_not_count(tmp_path):
    doc = _csv(tmp_path, "10:30,11:00,x,Joanna Lee,,")
    assert _kinds(_check(doc, {"event_source": [{"name": "Anna Lee"}]})) == [("missing", "name", 1)]


def test_a_dropped_honorific_is_reported(tmp_path):
    doc = _csv(tmp_path, "09:30,10:20,基調講演,髙橋 一郎,教授,東京医科大学 循環器内科")
    assert _kinds(_check(doc, {"event_source": [ROW_1]})) == [("honorific_dropped", "honorific", 1)]


def test_a_prefixed_honorific_counts(tmp_path):
    doc = _csv(tmp_path, "11:50,12:00,Closing,Dr. Kwame Mensah,,")
    row = {"name": "Kwame Mensah", "honorific": "Dr."}
    assert _check(doc, {"event_source": [row]}).status == CheckStatus.PASS
    bare = _csv(tmp_path, "11:50,12:00,Closing,Kwame Mensah,,")
    assert _kinds(_check(bare, {"event_source": [row]})) == [("honorific_dropped", "honorific", 1)]


# ---- titles and affiliations ----------------------------------------------


def test_a_variant_character_in_the_affiliation_is_reported(tmp_path):
    doc = _csv(tmp_path, "09:30,10:20,基調講演,髙橋 一郎 先生,教授,東京医科大學 循環器内科")
    check = _check(doc, {"event_source": [ROW_1]})
    assert _kinds(check) == [("variant_spelling", "affiliation", 1)]
    assert check.evidence["findings"][0]["differs"] == [{"expected": "学", "found": "學"}]


def test_a_different_title_is_a_mismatch(tmp_path):
    # "教授" is inside "准教授": a whole value is compared, not a substring.
    doc = _csv(tmp_path, "09:30,10:20,基調講演,髙橋 一郎 先生,准教授,東京医科大学 循環器内科")
    assert _kinds(_check(doc, {"event_source": [ROW_1]})) == [("mismatch", "title", 1)]


def test_titles_exchanged_between_adjacent_csv_rows_are_found(tmp_path):
    doc = _csv(tmp_path, "09:00,09:30,Talk A,Ana Ruiz,Lecturer,Uni A", "09:30,10:00,Talk B,Bo Chen,Professor,Uni B")
    rows = [{"name": "Ana Ruiz", "title": "Professor"}, {"name": "Bo Chen", "title": "Lecturer"}]
    assert _kinds(_check(doc, {"event_source": rows})) == [("mismatch", "title", 1), ("mismatch", "title", 2)]


def test_an_honorific_in_the_next_csv_cell_counts(tmp_path):
    doc = tmp_path / "lt.csv"
    doc.write_text("name,honorific,title\nAna Ruiz,Dr.,Lecturer\nBo Chen,,Lecturer\n", encoding="utf-8")
    rows = [{"name": "Ana Ruiz", "honorific": "Dr."}, {"name": "Bo Chen", "honorific": "Dr."}]
    assert _kinds(_check(doc, {"event_source": rows})) == [("honorific_dropped", "honorific", 2)]


def test_latin_name_parts_need_their_space(tmp_path):
    doc = _csv(tmp_path, "10:30,11:00,x,VanDyke,,")
    assert _kinds(_check(doc, {"event_source": [{"name": "Van Dyke"}]})) == [("missing", "name", 1)]


def test_event_fields_limits_what_the_document_must_carry(tmp_path):
    # A lower-third CSV with names and roles only.
    doc = tmp_path / "lt.csv"
    doc.write_text("name,title\n髙橋 一郎 先生,教授\n", encoding="utf-8")
    assert _check(doc, {"event_source": [ROW_1]}).status == CheckStatus.FAIL
    check = _check(doc, {"event_source": [ROW_1], "event_fields": ["name", "honorific", "title"]})
    assert check.status == CheckStatus.PASS


# ---- times -----------------------------------------------------------------


def test_a_wrong_end_time_is_reported(tmp_path):
    doc = _csv(tmp_path, "09:30,10:25,基調講演,髙橋 一郎 先生,教授,東京医科大学 循環器内科")
    check = _check(doc, {"event_source": [ROW_1]})
    assert _kinds(check) == [("time_mismatch", "end", 1)]


def test_exchanged_start_and_end_are_found(tmp_path):
    doc = _csv(tmp_path, "10:20,09:30,基調講演,髙橋 一郎 先生,教授,東京医科大学 循環器内科")
    assert _kinds(_check(doc, {"event_source": [ROW_1]})) == [("time_mismatch", "start", 1)]


def test_an_entry_with_no_time_near_it_is_reported(tmp_path):
    doc = tmp_path / "list.md"
    doc.write_text("# Speakers\n\n- Ana Ruiz, Senior Lecturer\n", encoding="utf-8")
    row = {"name": "Ana Ruiz", "title": "Senior Lecturer", "start": "10:30"}
    assert _kinds(_check(doc, {"event_source": [row]})) == [("time_missing", "start", 1)]
    assert _check(doc, {"event_source": [row], "event_fields": ["name", "title"]}).status == CheckStatus.PASS


def test_without_the_name_the_entry_is_found_by_its_session(tmp_path):
    doc = _csv(tmp_path, "10:00,10:30,Panel,,,")
    row = {"name": "Ana Ruiz", "session": "Panel", "start": "09:30"}
    check = _check(doc, {"event_source": [row], "event_fields": ["session", "start"]})
    assert _kinds(check) == [("time_mismatch", "start", 1)]
    nowhere = _csv(tmp_path, "10:00,10:30,Workshop,,,")
    assert _kinds(_check(nowhere, {"event_source": [row], "event_fields": ["start"]})) == [
        ("entry_not_found", "session", 1)]


def test_a_speaker_with_two_sessions_is_checked_per_session(tmp_path):
    doc = _csv(tmp_path, "09:30,10:20,Keynote,Ana Ruiz,,", "14:00,14:30,Panel,Ana Ruiz,,")
    rows = [{"name": "Ana Ruiz", "session": "Keynote", "start": "09:30"},
            {"name": "Ana Ruiz", "session": "Panel", "start": "14:00"}]
    assert _check(doc, {"event_source": rows}).status == CheckStatus.PASS
    wrong = _csv(tmp_path, "09:30,10:20,Keynote,Ana Ruiz,,", "15:00,15:30,Panel,Ana Ruiz,,")
    assert _kinds(_check(wrong, {"event_source": rows})) == [("time_mismatch", "start", 2)]


def test_times_written_with_ji_and_fun(tmp_path):
    doc = tmp_path / "cue.md"
    doc.write_text("# キューシート\n\n- 9時30分 基調講演 髙橋 一郎 先生\n", encoding="utf-8")
    row = {"name": "髙橋 一郎", "honorific": "先生", "start": "09:30"}
    assert _check(doc, {"event_source": [row]}).status == CheckStatus.PASS


def test_a_row_without_a_name_is_anchored_on_its_session(tmp_path):
    doc = _csv(tmp_path, "12:00,13:00,Lunch,,,")
    assert _kinds(_check(doc, {"event_source": [{"session": "Lunch", "start": "12:10"}]})) == [
        ("time_mismatch", "start", 1)]


# ---- the source list -------------------------------------------------------


def test_json_source_with_japanese_headers(tmp_path):
    src = tmp_path / "speakers.json"
    src.write_text(json.dumps({"speakers": [
        {"氏名": "髙橋 一郎", "敬称": "先生", "役職": "教授", "所属": "東京医科大学 循環器内科", "開始": "9:30", "終了": "10:20"},
    ]}, ensure_ascii=False), encoding="utf-8")
    ok = _check(FIXTURES / "run_of_show_ok.csv", {"event_source": str(src)})
    assert ok.status == CheckStatus.PASS
    bad = _check(FIXTURES / "run_of_show_two_errors.csv", {"event_source": str(src)})
    assert _kinds(bad) == [("variant_spelling", "name", 1)]


def test_missing_source_file_is_unknown(tmp_path):
    check = _check(FIXTURES / "run_of_show_ok.csv", {"event_source": str(tmp_path / "nope.csv")})
    assert check.status == CheckStatus.UNKNOWN


def test_source_rows_with_a_bad_time_are_unknown(tmp_path):
    src = tmp_path / "src.csv"
    src.write_text("name,start\nAna Ruiz,half past ten\n", encoding="utf-8")
    assert _check(FIXTURES / "run_of_show_ok.csv", {"event_source": str(src)}).status == CheckStatus.UNKNOWN


def test_format_without_text_is_unknown(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    img = tmp_path / "x.png"
    pil.new("RGB", (4, 4)).save(img)
    assert _check(img, {"event_source": [ROW_1]}).status == CheckStatus.UNKNOWN


def test_docx_run_of_show(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("09:30–10:20 基調講演：心不全診療の現在")  # noqa: RUF001 - as organisers write it
    document.add_paragraph("高橋 一郎 先生 — 東京医科大学 循環器内科 教授")
    path = tmp_path / "run.docx"
    document.save(path)
    assert _kinds(_check(path, {"event_source": [ROW_1]})) == [("variant_spelling", "name", 1)]


# ---- policy and call sites -------------------------------------------------


def test_policy_keys_are_known():
    assert {"event_source", "event_fields"} <= known_policy_keys()
    assert resolve_policy(None, {"event_source": "x.csv"})["event_source"] == "x.csv"


@pytest.mark.parametrize("policy", [
    {"event_source": ""},
    {"event_source": []},
    {"event_source": [{"title": "教授"}]},
    {"event_source": [{"name": "A", "start": "soon"}]},
    {"event_fields": ["name"]},
    {"event_source": "x.csv", "event_fields": ["nickname"]},
    {"event_source": "x.csv", "event_fields": []},
])
def test_malformed_policy_is_rejected(policy):
    with pytest.raises(ArtifactInputError) as exc:
        resolve_policy(None, policy)
    assert exc.value.code == "ARTIFACT_INVALID_ARGS"


def test_receipt_carries_the_check(tmp_path):
    doc = tmp_path / "run_of_show.csv"
    shutil.copy(FIXTURES / "run_of_show_two_errors.csv", doc)
    result = run_lifecycle(doc, None, {}, None, policy={"event_source": str(SOURCE)}, evidence_dir=tmp_path / "ev")
    assert result.receipt is not None
    receipt = result.receipt.to_dict()
    assert receipt["status"] == "fail"
    assert CHECK_ID in json.dumps(receipt)


def test_mcp_verify_runs_the_check():
    from artifact_skill.mcp.server import _h_verify

    out = _h_verify({"input": str(FIXTURES / "run_of_show_two_errors.csv"), "policy": {"event_source": str(SOURCE)}})
    check = next(c for c in out["checks"] if c["id"] == CHECK_ID)
    assert check["status"] == "fail"


def test_cli_verify_exits_fail_and_names_the_rows(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path(artifact_skill.__file__).resolve().parent.parent)}
    policy = json.dumps({"event_source": str(SOURCE)})
    proc = subprocess.run(
        [sys.executable, "-m", "artifact_skill.cli.main", "verify", str(FIXTURES / "run_of_show_two_errors.csv"),
         "--policy", policy, "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    check = next(c for c in payload["checks"] if c["id"] == CHECK_ID)
    assert [f["source_row"]["row"] for f in check["evidence"]["findings"]] == [1, 2]

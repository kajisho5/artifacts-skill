"""Generated text checked against its source (ecosystem roadmap step 7,
docs/architecture.md "Numbers and terms in generated text").

Every number with a unit, date, drug name, titled person's name and listed
term in a generated document must appear in the source files the policy
names; each one that does not is listed, and nothing is changed.
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
from artifact_skill.policies import known_policy_keys, resolve_policy
from artifact_skill.source_grounding import CHECK_ID, extract_claims, verify_structural_with_grounding

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "grounding"
TRANSCRIPT = FIXTURES / "transcript.txt"


def _check(doc: Path, policy: dict):
    ref = ArtifactRef.from_path(doc)
    result = verify_structural_with_grounding(adapter_for(ref), ref, policy)
    checks = [c for c in result.checks if c.id == CHECK_ID]
    assert len(checks) == 1
    return checks[0]


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ---- the roadmap's "Done when" ---------------------------------------------


def test_invented_dosage_is_reported_and_nothing_else(tmp_path):
    doc = tmp_path / "summary.md"
    shutil.copy(FIXTURES / "summary_invented_dosage.md", doc)
    before = doc.read_bytes()

    check = _check(doc, {"grounding_sources": [str(TRANSCRIPT)]})

    assert check.status == CheckStatus.FAIL
    assert check.evidence["unsupported"] == [{"kind": "number", "text": "10 mg", "lines": [6]}]
    assert check.evidence["claims_checked"] > 10
    assert "'10 mg' (line 6)" in check.message
    assert doc.read_bytes() == before  # reported, never fixed


def test_faithful_summary_passes():
    check = _check(FIXTURES / "summary_faithful.md", {"grounding_sources": [str(TRANSCRIPT)]})
    assert check.status == CheckStatus.PASS
    assert check.evidence["unsupported"] == []


def test_without_grounding_sources_there_is_no_check():
    ref = ArtifactRef.from_path(FIXTURES / "summary_invented_dosage.md")
    result = verify_structural_with_grounding(adapter_for(ref), ref, {})
    assert all(c.id != CHECK_ID for c in result.checks)


# ---- what counts as a claim, and when the source supports it ---------------


def _unsupported(tmp_path: Path, generated: str, source: str, terms: list[str] | None = None) -> list[str]:
    # A heading and a list item make the file Markdown (plain text is not a format this
    # tool verifies); it holds no claims.
    doc = _write(tmp_path, "doc.md", f"# Notes\n\n- {generated}\n")
    src = _write(tmp_path, "source.txt", source)
    policy: dict = {"grounding_sources": [str(src)]}
    if terms:
        policy["grounding_terms"] = terms
    return [u["text"] for u in _check(doc, policy).evidence["unsupported"]]


@pytest.mark.parametrize(
    ("generated", "source"),
    [
        ("Give 5 mg.", "give 5mg"),
        ("Give 5.0 mg.", "give 5 mg"),
        ("Give ５ｍｇ.", "give 5 mg"),  # noqa: RUF001 - full-width on purpose
        ("Give 50 mcg.", "give 50 μg"),
        ("Give 50 µg.", "give 50 ug"),
        ("Over 3 hours.", "over 3 h"),
        ("In 1,200 patients.", "in 1200 patients"),
        ("Give 40 milligrams.", "give 40 mg"),
        ("A 12 % fall.", "fell 12%"),
        ("1日5mg、3日間。", "1日 5 mg を 3 日間"),
    ],
)
def test_numbers_compare_by_value_and_unit(tmp_path, generated, source):
    assert _unsupported(tmp_path, generated, source) == []


def test_a_range_is_two_numbers(tmp_path):
    assert _unsupported(tmp_path, "Give 5-10 mg/kg.", "give 5 mg/kg, at most 12 mg/kg") == ["10 mg/kg"]


def test_a_number_without_a_unit_is_not_a_claim(tmp_path):
    assert _unsupported(tmp_path, "Step 3 of the plan, group 7.", "nothing here") == []


def test_a_different_unit_is_unsupported(tmp_path):
    assert _unsupported(tmp_path, "Give 5 g.", "give 5 mg") == ["5 g"]


@pytest.mark.parametrize(
    ("generated", "source", "expected"),
    [
        ("On 14 October 2026.", "Recorded 2026-10-14.", []),
        ("In October 2026.", "Recorded on October 14, 2026.", []),
        ("2026年10月14日に収録。", "Recorded 14 Oct 2026.", []),
        ("On 15 October 2026.", "Recorded 14 October 2026.", ["15 October 2026"]),
        ("In November 2026.", "Recorded 14 October 2026.", ["November 2026"]),
    ],
)
def test_dates(tmp_path, generated, source, expected):
    assert _unsupported(tmp_path, generated, source) == expected


def test_drug_names(tmp_path):
    generated = "Continue apixaban; add atorvastatin and aspirin. Cholesterol was checked."
    assert _unsupported(tmp_path, generated, "apixaban and Aspirin were used; cholesterol") == ["atorvastatin"]


def test_people_with_a_title(tmp_path):
    generated = "Dr. Maria Okafor and Prof. J. Reyes spoke, then Dr. Lena Park. 山田教授が総括した。"
    source = "Speakers: Maria Okafor, Daniel Reyes. 東京大学の山田教授"
    assert _unsupported(tmp_path, generated, source) == ["Dr. Lena Park"]


def test_listed_terms(tmp_path):
    generated = "Velisa and Northwind were compared with Halcyon."
    source = "We tested Velisa and Halcyon."
    assert _unsupported(tmp_path, generated, source, ["Velisa", "Northwind", "Halcyon", "Unused"]) == ["Northwind"]


def test_claims_list_every_line_once():
    claims = extract_claims("40 mg\nthen 40 mg again\n40mg")
    assert [(c.text, c.lines) for c in claims] == [("40 mg", [1, 2, 3])]


# ---- sources and documents in other formats --------------------------------


def test_slides_as_the_source(tmp_path):
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Apixaban dosing"
    slide.placeholders[1].text = "5 mg twice a day; 2.5 mg if over 80 years"
    deck = tmp_path / "slides.pptx"
    prs.save(str(deck))

    doc = _write(tmp_path, "script.md", "# Script\n\n- Apixaban 5 mg twice a day, or 2.5 mg over 80 years, never 7.5 mg.")
    check = _check(doc, {"grounding_sources": [str(deck)]})
    assert [u["text"] for u in check.evidence["unsupported"]] == ["7.5 mg"]


def test_docx_as_the_generated_document(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Metoprolol 25 mg, up to 150 mg.")
    path = tmp_path / "summary.docx"
    document.save(str(path))

    check = _check(path, {"grounding_sources": [str(TRANSCRIPT)]})
    assert [u["text"] for u in check.evidence["unsupported"]] == ["150 mg"]


def test_several_sources_are_read_together(tmp_path):
    a = _write(tmp_path, "a.txt", "give 5 mg")
    b = _write(tmp_path, "b.srt", "1\n00:00:01,000 --> 00:00:02,000\nthen 10 mg\n")
    doc = _write(tmp_path, "doc.md", "# Notes\n\n- 5 mg, then 10 mg.\n")
    assert _check(doc, {"grounding_sources": [str(a), str(b)]}).status == CheckStatus.PASS


def test_missing_source_is_unknown_not_pass(tmp_path):
    doc = _write(tmp_path, "doc.md", "# Notes\n\n- Give 5 mg.\n")
    check = _check(doc, {"grounding_sources": [str(tmp_path / "nope.txt")]})
    assert check.status == CheckStatus.UNKNOWN
    assert check.evidence["unreadable_sources"][0]["source"].endswith("nope.txt")


def test_binary_source_of_an_unknown_type_is_unknown(tmp_path):
    src = tmp_path / "blob.bin"
    src.write_bytes(b"\xff\xfe\x00\x81" * 10)
    doc = _write(tmp_path, "doc.md", "# Notes\n\n- Give 5 mg.\n")
    assert _check(doc, {"grounding_sources": [str(src)]}).status == CheckStatus.UNKNOWN


def test_format_without_text_is_unknown(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    img = tmp_path / "x.png"
    pil.new("RGB", (4, 4)).save(img)
    check = _check(img, {"grounding_sources": [str(TRANSCRIPT)]})
    assert check.status == CheckStatus.UNKNOWN


def test_inspect_output_does_not_carry_the_text():
    ref = ArtifactRef.from_path(FIXTURES / "summary_faithful.md")
    report = adapter_for(ref).inspect(ref)
    assert report.text and "apixaban" in report.text
    assert "apixaban" not in json.dumps(report.to_dict())


# ---- policy ----------------------------------------------------------------


def test_policy_keys_are_known():
    assert {"grounding_sources", "grounding_terms"} <= known_policy_keys()
    policy = resolve_policy("web-no-external", {"grounding_sources": ["t.txt"], "grounding_terms": ["X"]})
    assert policy["grounding_sources"] == ["t.txt"]


@pytest.mark.parametrize(
    "policy",
    [
        {"grounding_sources": "transcript.txt"},
        {"grounding_sources": []},
        {"grounding_sources": ["ok.txt", 3]},
        {"grounding_terms": ["X"]},
        {"grounding_sources": ["ok.txt"], "grounding_terms": "X"},
    ],
)
def test_malformed_policy_is_rejected(policy):
    with pytest.raises(ArtifactInputError) as exc:
        resolve_policy(None, policy)
    assert exc.value.code == "ARTIFACT_INVALID_ARGS"


def test_receipt_carries_the_check(tmp_path):
    doc = tmp_path / "summary.md"
    shutil.copy(FIXTURES / "summary_invented_dosage.md", doc)
    result = run_lifecycle(
        doc, None, {}, None, policy={"grounding_sources": [str(TRANSCRIPT)]}, evidence_dir=tmp_path / "ev"
    )
    assert result.receipt is not None
    receipt = result.receipt.to_dict()
    assert receipt["status"] == "fail"
    checks = {c["id"]: c for c in receipt["verification"]["structural"]["checks"]}
    assert checks[CHECK_ID]["evidence"]["unsupported"] == [{"kind": "number", "text": "10 mg", "lines": [6]}]


def test_mcp_verify_runs_the_check():
    from artifact_skill.mcp.server import _h_verify

    out = _h_verify({
        "input": str(FIXTURES / "summary_invented_dosage.md"),
        "policy": {"grounding_sources": [str(TRANSCRIPT)]},
    })
    assert out["status"] == "fail"
    assert any(c["id"] == CHECK_ID for c in out["checks"])


def test_cli_verify_exits_fail_and_lists_the_dosage(tmp_path):
    cli = shutil.which("artifacts-skill")
    cmd = [cli] if cli else [sys.executable, "-m", "artifact_skill.cli.main"]
    policy = json.dumps({"grounding_sources": [str(TRANSCRIPT)]})
    proc = subprocess.run(
        [*cmd, "verify", str(FIXTURES / "summary_invented_dosage.md"), "--policy", policy, "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 1, proc.stderr
    data = json.loads(proc.stdout)
    check = next(c for c in data["checks"] if c["id"] == CHECK_ID)
    assert [u["text"] for u in check["evidence"]["unsupported"]] == ["10 mg"]


# ---- review follow-ups -----------------------------------------------------


def test_the_sign_of_a_number_counts(tmp_path):
    assert _unsupported(tmp_path, "Store at -5 °C.", "store at 5 °C") == ["-5 °C"]
    assert _unsupported(tmp_path, f"Store at {chr(0x2212)}5 °C.", "store at -5 °C") == []


def test_a_known_unit_prefix_is_not_the_claim(tmp_path):
    assert _unsupported(tmp_path, "Give 5 mg/kg/week.", "give 5 mg/kg") == ["5 mg/kg/week"]
    assert _unsupported(tmp_path, "Give 5 mg/kg/week.", "give 5 mg/kg/week") == []


def test_a_name_must_appear_as_one_name(tmp_path):
    source = "Speakers: Dr. Jane Doe and Dr. Alice Smith; also Jane K. Smith."
    assert _unsupported(tmp_path, "Dr. Jane Smith spoke.", "Dr. Jane Doe and Dr. Alice Smith") == ["Dr. Jane Smith"]
    assert _unsupported(tmp_path, "Dr. Jane Smith spoke.", source) == []


def test_an_empty_source_is_unknown(tmp_path):
    src = _write(tmp_path, "empty.txt", "  \n")
    doc = _write(tmp_path, "doc.md", "# Notes\n\n- Give 5 mg.\n")
    assert _check(doc, {"grounding_sources": [str(src)]}).status == CheckStatus.UNKNOWN


def test_text_inside_grouped_pptx_shapes_is_read(tmp_path):
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    outer = slide.shapes.add_group_shape()
    inner = outer.shapes.add_group_shape()
    inner.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text_frame.text = "Give 7.5 mg"
    deck = tmp_path / "deck.pptx"
    prs.save(str(deck))
    check = _check(deck, {"grounding_sources": [str(TRANSCRIPT)]})
    assert [u["text"] for u in check.evidence["unsupported"]] == ["7.5 mg"]


def test_svg_tspan_tails_are_read(tmp_path):
    svg = _write(tmp_path, "x.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                 "<text><tspan>Note</tspan> 7.5 mg</text></svg>")
    check = _check(svg, {"grounding_sources": [str(TRANSCRIPT)]})
    assert [u["text"] for u in check.evidence["unsupported"]] == ["7.5 mg"]


def test_xlsx_numeric_cells_are_read(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["dose", 7.5, "mg"])
    path = tmp_path / "doses.xlsx"
    wb.save(str(path))
    check = _check(path, {"grounding_sources": [str(TRANSCRIPT)]})
    assert [u["text"] for u in check.evidence["unsupported"]] == ["7.5 mg"]


def test_xlsx_formula_without_a_cached_value_is_unknown(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append([1, 2, "=A1+B1"])
    path = tmp_path / "f.xlsx"
    wb.save(str(path))
    assert _check(path, {"grounding_sources": [str(TRANSCRIPT)]}).status == CheckStatus.UNKNOWN


def test_pdf_with_a_page_whose_text_fails_is_unknown(tmp_path, monkeypatch):
    pypdf = pytest.importorskip("pypdf")
    pdf = tmp_path / "good.pdf"
    shutil.copy(FIXTURES.parent / "pdf" / "good_2page.pdf", pdf)

    def boom(self, *args, **kwargs):
        raise ValueError("broken page")

    monkeypatch.setattr(pypdf.PageObject, "extract_text", boom)
    assert _check(pdf, {"grounding_sources": [str(TRANSCRIPT)]}).status == CheckStatus.UNKNOWN

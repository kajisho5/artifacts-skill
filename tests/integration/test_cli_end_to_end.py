"""Full-process integration tests: invokes the real `artifacts-skill`
console script (installed by `pip install -e .`) via subprocess, exactly
as an agent or a human would."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

CLI = shutil.which("artifacts-skill")


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    if CLI:
        cmd = [CLI, *args]
    else:
        cmd = [sys.executable, "-m", "artifact_skill.cli.main", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def test_verbose_and_progress_flags_were_removed_not_left_as_silent_no_ops(tmp_path):
    """Issue #31: --verbose/--progress used to be declared on every
    subcommand with help text implying real behavior, but nothing in the
    codebase ever read args.verbose/args.progress - passing either flag
    silently did nothing, with no indication to the caller. Removed
    rather than implemented, per the project's minimalism stance; this
    guards against either flag quietly coming back as another no-op."""
    proc = run_cli(["doctor", "--verbose"], cwd=tmp_path)
    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr

    proc = run_cli(["doctor", "--progress"], cwd=tmp_path)
    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr


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
    assert produced == [good_pdf] or {p.name for p in produced} == {good_pdf.name}


def test_execute_then_verify_pass(good_pdf, tmp_path):
    exec_proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"CLI Test"}',
         "--output", "out.pdf", "--json"],
        cwd=tmp_path,
    )
    assert exec_proc.returncode == 0  # standard-font PDF: genuinely clean PASS, not UNKNOWN (Issue #7)
    assert (tmp_path / "out.pdf").exists()

    verify_proc = run_cli(
        ["verify", "out.pdf", "--policy", '{"require_page_count": 2, "require_metadata": {"Title": "CLI Test"}}',
         "--json"],
        cwd=tmp_path,
    )
    assert verify_proc.returncode == 0
    data = json.loads(verify_proc.stdout)
    assert data["status"] == "pass"
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["page_count_requirement"]["status"] == "pass"
    assert check_by_id["metadata_title"]["status"] == "pass"
    assert check_by_id["font_embedding"]["status"] == "pass"


# --- plan subcommand (Issue #29: previously zero CLI e2e coverage) --------


def test_plan_is_pure_and_writes_nothing(good_pdf, tmp_path):
    proc = run_cli(
        ["plan", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}', "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["operation"] == "pdf.metadata_set"
    assert data["adapter"] == "pdf"
    assert "output_path" in data
    # Purely descriptive: no output file, no reports/ dir, from a plan call alone.
    assert list(tmp_path.iterdir()) == [good_pdf]


def test_plan_rejects_unknown_operation_with_input_exit_code(good_pdf, tmp_path):
    proc = run_cli(
        ["plan", str(good_pdf), "--operation", "not_a_real_operation", "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_OPERATION_UNKNOWN"


def test_plan_rejects_args_that_violate_the_operations_schema(good_pdf, tmp_path):
    proc = run_cli(
        ["plan", str(good_pdf), "--operation", "fit_page_size", "--args", '{"width_pt": "not a number"}', "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_INVALID_ARGS"


# --- render subcommand (Issue #29: previously zero CLI e2e coverage) ------


def test_render_produces_one_page_image_per_page(good_pdf, tmp_path):
    proc = run_cli(["render", str(good_pdf), "--out-dir", "rendered", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["kind"] == "page_images"
    assert data["backend"] == "pypdfium2"
    assert len(data["files"]) == 2  # good_2page.pdf
    for f in data["files"]:
        assert (tmp_path / f).exists()


def test_render_dry_run_writes_nothing_and_reports_estimated_files(good_pdf, tmp_path):
    proc = run_cli(["render", str(good_pdf), "--out-dir", "rendered", "--dry-run", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["dry_run"] is True
    assert data["estimated_files"] == 2
    assert not (tmp_path / "rendered").exists()


# --- look subcommand (Issue #29: previously zero CLI e2e coverage) --------


def test_look_builds_a_real_contact_sheet_png(good_pdf, tmp_path):
    proc = run_cli(["look", str(good_pdf), "--out-dir", "look_out", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["source_pages"] == 2
    sheet_path = tmp_path / data["contact_sheet"]
    assert sheet_path.exists()
    assert sheet_path.suffix == ".png"


def test_look_compare_to_builds_a_before_after_png(good_pdf, tmp_path):
    proc = run_cli(
        ["look", str(good_pdf), "--compare-to", str(good_pdf), "--out-dir", "look_out", "--json"], cwd=tmp_path
    )
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    sheet_path = tmp_path / data["contact_sheet"]
    assert sheet_path.exists()
    assert sheet_path.name == "before-after.png"


def test_look_dry_run_writes_nothing(good_pdf, tmp_path):
    proc = run_cli(["look", str(good_pdf), "--out-dir", "look_out", "--dry-run", "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["dry_run"] is True
    assert not (tmp_path / "look_out").exists()


def test_execute_gates_its_own_receipt_with_an_explicit_policy(good_pdf, tmp_path):
    """Regression guard: execute() used to always verify against an empty
    policy regardless of what the caller wanted, silently ignoring the
    fact that a bad execute could be reported as a clean receipt.json.
    A policy this input can't satisfy must now show up as a FAIL in
    execute's own reports/receipt.json, not just a subsequent `verify`
    call the caller has to remember to make."""
    proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", "out.pdf", "--policy", '{"require_page_count": 999}', "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    check_by_id = {c["id"]: c for c in data["receipt"]["verification"]["structural"]["checks"]}
    assert check_by_id["page_count_requirement"]["status"] == "fail"
    assert data["receipt"]["status"] == "fail"


def test_execute_default_evidence_dir_matches_receipts_default(good_pdf, tmp_path):
    """FIX_PROMPT P2-5: execute used to always write its receipt to
    output_path.parent/'reports', which diverged from `receipt`'s own
    cwd-relative './reports' default whenever --output pointed outside
    cwd - confirmed by direct reproduction before this fix that an
    absolute --output path put the receipt somewhere an agent checking
    the conventional ./reports location would never find it."""
    out_dir = tmp_path / "elsewhere"
    out_dir.mkdir()
    proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", str(out_dir / "out.pdf"), "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    assert (tmp_path / "reports" / "receipt.json").exists()
    assert not (out_dir / "reports" / "receipt.json").exists()


def test_execute_evidence_dir_flag_overrides_the_default(good_pdf, tmp_path):
    proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", "out.pdf", "--evidence-dir", "custom_evidence", "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    assert (tmp_path / "custom_evidence" / "receipt.json").exists()
    assert not (tmp_path / "reports" / "receipt.json").exists()


def test_execute_accepts_policy_preset(good_pdf, tmp_path):
    proc = run_cli(
        ["execute", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", "out.pdf", "--policy-preset", "print-a4", "--json"],
        cwd=tmp_path,
    )
    data = json.loads(proc.stdout)
    check_by_id = {c["id"]: c for c in data["receipt"]["verification"]["structural"]["checks"]}
    # good_2page.pdf is US Letter, not A4 - print-a4's page-size requirement must fail.
    assert check_by_id["page_size_requirement"]["status"] == "fail"


def test_print_a4_preset_fails_leftover_placeholder_text_not_just_warns(leftover_placeholder_pdf, tmp_path):
    """Regression guard for an external review's specific claim: none of
    the original policy presets set forbid_placeholder_text, so a Lorem-
    ipsum-filled PDF verified against `print-a4` would only ever WARN
    (exit 0) on its leftover text, never actually block on it. The preset
    must FAIL the leftover_placeholder_text check specifically, not just
    fail overall for an unrelated reason like page size."""
    proc = run_cli(["verify", str(leftover_placeholder_pdf), "--policy-preset", "print-a4", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["leftover_placeholder_text"]["status"] == "fail"
    assert proc.returncode == 1


def test_spreadsheet_preset_shows_unknown_not_pass_on_a_clean_workbook_with_a_formula(good_xlsx, tmp_path):
    """Issue #19: the honest, documented behavior this preset was renamed
    over (spreadsheet-no-errors -> spreadsheet-no-cached-errors) - a
    workbook with zero cached errors, zero external links, zero leftover
    text still reports overall UNKNOWN (not PASS) the moment it contains
    any formula, since formula_recalculation is UNKNOWN-by-design and
    UNKNOWN outranks PASS in aggregation. Confirms this is really what
    the CLI returns end to end, not just at the adapter level."""
    proc = run_cli(
        ["verify", str(good_xlsx), "--policy-preset", "spreadsheet-no-cached-errors", "--json"], cwd=tmp_path
    )
    data = json.loads(proc.stdout)
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["formula_cached_errors"]["status"] == "pass"
    assert check_by_id["formula_recalculation"]["status"] == "unknown"
    assert data["status"] == "unknown"


def test_verify_fail_gives_nonzero_exit(empty_pdf, tmp_path):
    proc = run_cli(["verify", str(empty_pdf), "--json"], cwd=tmp_path)
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["status"] == "fail"


def test_verify_with_policy_preset(good_pdf, tmp_path):
    """good_2page.pdf is US Letter (612x792pt); print-a4 requires A4
    (595x842pt) - the preset alone should be enough to fail it."""
    proc = run_cli(["verify", str(good_pdf), "--policy-preset", "print-a4", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    check = next(c for c in data["checks"] if c["id"] == "page_size_requirement")
    assert check["status"] == "fail"


def test_verify_policy_preset_and_explicit_policy_merge(good_pdf, tmp_path):
    """--policy overrides individual preset fields rather than replacing
    the whole preset."""
    proc = run_cli(
        [
            "verify", str(good_pdf), "--policy-preset", "print-a4",
            "--policy", '{"require_page_size_pt": [612, 792]}', "--json",
        ],
        cwd=tmp_path,
    )
    data = json.loads(proc.stdout)
    check = next(c for c in data["checks"] if c["id"] == "page_size_requirement")
    assert check["status"] == "pass"  # now matches the overridden (Letter) size
    font_check = next(c for c in data["checks"] if c["id"] == "font_embedding")
    assert font_check["status"] == "pass"  # preset's forbid_unembedded_fonts still applies


def test_verify_unknown_policy_preset_gives_clear_error(good_pdf, tmp_path):
    proc = run_cli(["verify", str(good_pdf), "--policy-preset", "not-a-real-preset"], cwd=tmp_path)
    assert proc.returncode != 0
    assert "not-a-real-preset" in proc.stderr


def test_verify_misspelled_policy_key_gives_clear_error_not_a_silent_pass(good_pdf, tmp_path):
    """Issue #24: --policy '{"min_pagess": 1}' (typo'd from min_pages) used
    to be silently treated by every adapter as "not specified" and produce
    a false PASS. It must now be rejected with a clear error naming the
    unrecognized key, the same way an unknown --policy-preset name is."""
    proc = run_cli(["verify", str(good_pdf), "--policy", '{"min_pagess": 1}'], cwd=tmp_path)
    assert proc.returncode != 0
    assert "min_pagess" in proc.stderr


def test_input_not_found_gives_input_exit_code(tmp_path):
    proc = run_cli(["inspect", str(tmp_path / "nope.pdf"), "--json"], cwd=tmp_path)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_INPUT_NOT_FOUND"


def test_unrecognized_content_gives_clear_type_unsupported_error(tmp_path):
    """Every currently-known ArtifactType has a real adapter now (`_PLANNED`
    is empty — see tests/unit/test_registry.py for that code path's own
    coverage), so this needs content that doesn't match *any* format's
    magic-byte signature at all, to exercise ARTIFACT_TYPE_UNSUPPORTED."""
    unknown_like = tmp_path / "mystery.bin"
    unknown_like.write_bytes(b"\x00\x01\x02\x03 this matches no known format signature \xff\xfe")
    proc = run_cli(["inspect", str(unknown_like), "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_TYPE_UNSUPPORTED"


def test_password_protected_looking_ooxml_gives_a_specific_actionable_error(tmp_path):
    """Issue #27: a .pptx/.docx/.xlsx saved with a password isn't a zip at
    all - Office wraps it in a CFB/OLE2 container instead. That must not
    collapse into the same generic "unrecognized file" error as content
    matching no known format signature at all (the case above) - the CLI
    should say specifically what's going on and what to do about it."""
    cfb_magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    looks_encrypted = tmp_path / "protected.pptx"
    looks_encrypted.write_bytes(cfb_magic + b"\x00" * 512)
    proc = run_cli(["inspect", str(looks_encrypted), "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_OLE_COMPOUND_FILE_UNSUPPORTED"
    assert "password" in data["error"]["remediation"].lower()
    assert proc.returncode == 3


def test_malformed_policy_json_respects_the_json_flag(good_pdf, tmp_path):
    """Self-audit finding (CLI/MCP parity audit): _load_json_arg() used to
    raise a bare SystemExit(str) - a plain-text message to stderr that
    completely ignores --json, unlike every other error path in this CLI.
    Confirmed by direct reproduction before this fix: `verify doc.pdf
    --policy 'not json' --json` printed a plain "error: ..." line, not
    JSON, even though --json was explicitly requested."""
    proc = run_cli(["verify", str(good_pdf), "--policy", "not valid json", "--json"], cwd=tmp_path)
    assert proc.returncode != 0
    data = json.loads(proc.stdout)  # must be parseable JSON, not a plain-text message
    assert data["error"]["code"] == "ARTIFACT_INVALID_ARGS"


def test_non_object_policy_json_respects_the_json_flag(good_pdf, tmp_path):
    proc = run_cli(["verify", str(good_pdf), "--policy", '"just a string"', "--json"], cwd=tmp_path)
    assert proc.returncode != 0
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_INVALID_ARGS"


def test_receipt_max_iterations_above_the_limit_is_rejected_over_the_cli(good_pdf, tmp_path):
    """Self-audit finding (CLI/MCP parity): the CLI used to silently accept
    --max-iterations outside [1, Limits.max_fix_iterations] and just run
    with a silently-clamped value - confirmed by direct reproduction that
    `--max-iterations 15` on a real run exited 0 with status "pass" and no
    indication the requested retry cap was overridden. Must now be a
    structured rejection, same as MCP's schema already enforced."""
    proc = run_cli(
        ["receipt", str(good_pdf), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--max-iterations", "15", "--json"],
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_INVALID_ARGS"


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
    # No returncode==0 assertion: overall status (and thus exit code) can be
    # UNKNOWN here if the environment's LibreOffice can't actually render
    # (present-but-non-functional soffice - see PptxAdapter's docstring),
    # same reasoning as the unit tests' _probe_pptx_render_works pattern.
    run_cli(
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
    # No returncode==0 assertion: see the equivalent PPTX test's comment above.
    run_cli(
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


def test_xlsx_doctor_reports_structural_capability(tmp_path):
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert "xlsx.structural" in data["capabilities"]
    assert "xlsx.render" in data["capabilities"]


def test_xlsx_execute_then_verify(good_xlsx, tmp_path):
    # No returncode==0 assertion: see the equivalent PPTX test's comment above.
    run_cli(
        ["execute", str(good_xlsx), "--operation", "metadata_set", "--args", '{"title":"CLI XLSX Test"}',
         "--output", "out.xlsx", "--json"],
        cwd=tmp_path,
    )
    assert (tmp_path / "out.xlsx").exists()

    verify_proc = run_cli(
        ["verify", "out.xlsx", "--policy", '{"require_sheet_count": 2, "require_metadata": {"title": "CLI XLSX Test"}}',
         "--json"],
        cwd=tmp_path,
    )
    data = json.loads(verify_proc.stdout)
    check_by_id = {c["id"]: c for c in data["checks"]}
    assert check_by_id["sheet_count_requirement"]["status"] == "pass"
    assert check_by_id["metadata_title"]["status"] == "pass"
    assert check_by_id["formula_recalculation"]["status"] == "unknown"


def test_xlsx_formula_error_gives_fail_exit(formula_error_xlsx, tmp_path):
    proc = run_cli(["verify", str(formula_error_xlsx), "--json"], cwd=tmp_path)
    assert proc.returncode == 1
    data = json.loads(proc.stdout)
    assert data["status"] == "fail"


def test_xlsx_input_unchanged_after_execute(good_xlsx, tmp_path):
    from artifact_skill.core.artifact import sha256_of

    before = sha256_of(good_xlsx)
    run_cli(
        ["execute", str(good_xlsx), "--operation", "metadata_set", "--args", '{"title":"x"}',
         "--output", "out.xlsx", "--json"],
        cwd=tmp_path,
    )
    assert sha256_of(good_xlsx) == before


def test_image_doctor_reports_deduplicated_capability(tmp_path):
    """PNG/JPEG/WebP share one adapter — doctor should show `image.*`,
    never per-subtype ids like `image/png.*`."""
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert "image.structural" in data["capabilities"]
    assert "image.render" in data["capabilities"]
    assert not any(k.startswith("image/") for k in data["capabilities"])


def test_image_execute_resize_then_verify(good_png, tmp_path):
    exec_proc = run_cli(
        ["execute", str(good_png), "--operation", "resize", "--args", '{"width":50,"height":50}',
         "--output", "small.png", "--json"],
        cwd=tmp_path,
    )
    assert exec_proc.returncode == 0
    assert (tmp_path / "small.png").exists()

    verify_proc = run_cli(["verify", "small.png", "--policy", '{"require_width": 50}', "--json"], cwd=tmp_path)
    assert verify_proc.returncode == 0
    data = json.loads(verify_proc.stdout)
    assert data["status"] == "pass"


def test_image_input_unchanged_after_execute(good_png, tmp_path):
    from artifact_skill.core.artifact import sha256_of

    before = sha256_of(good_png)
    run_cli(
        ["execute", str(good_png), "--operation", "resize", "--args", '{"width":10,"height":10}',
         "--output", "out.png", "--json"],
        cwd=tmp_path,
    )
    assert sha256_of(good_png) == before


def test_html_doctor_reports_structural_always_available(tmp_path):
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert data["capabilities"]["html.structural"]["status"] == "available"
    assert "html.render" in data["capabilities"]


def test_html_inspect_and_verify(good_html, tmp_path):
    inspect_proc = run_cli(["inspect", str(good_html), "--json"], cwd=tmp_path)
    assert inspect_proc.returncode == 0
    data = json.loads(inspect_proc.stdout)
    assert data["details"]["title"] == "Sample Page"

    verify_proc = run_cli(["verify", str(good_html), "--policy", '{"require_title": true}', "--json"], cwd=tmp_path)
    assert verify_proc.returncode == 0
    assert json.loads(verify_proc.stdout)["status"] == "pass"


def test_html_has_no_mutating_operations(good_html, tmp_path):
    proc = run_cli(["execute", str(good_html), "--operation", "metadata_set", "--args", "{}", "--json"], cwd=tmp_path)
    assert proc.returncode == 2  # ArtifactInputError -> input category
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_OPERATION_UNKNOWN"


def test_html_receipt_without_operation_gives_a_real_verify_only_receipt(good_html, tmp_path):
    """Issue #18: HTML has zero mutating operations, so before this feature
    `artifacts-skill receipt page.html` had no way to succeed at all - an
    agent had to hand-assemble inspect/render/verify calls instead of using
    this project's own flagship 'get a Production Receipt' command."""
    proc = run_cli(["receipt", str(good_html), "--json"], cwd=tmp_path)
    assert proc.returncode == 0
    data = json.loads(proc.stdout)
    assert data["status"] == "pass"
    assert data["operations"] == []
    assert (tmp_path / "reports" / "receipt.json").exists()


def test_receipt_without_operation_rejects_output(good_html, tmp_path):
    proc = run_cli(["receipt", str(good_html), "--output", "out.html", "--json"], cwd=tmp_path)
    assert proc.returncode != 0
    assert "requires --operation" in proc.stderr


def test_pdf_receipt_without_operation_still_gates_on_policy(leftover_placeholder_pdf, tmp_path):
    """A verify-only receipt goes through the same structural verify -
    including policy - as the mutating path, not a weaker check."""
    proc = run_cli(
        ["receipt", str(leftover_placeholder_pdf), "--policy-preset", "print-a4", "--json"], cwd=tmp_path
    )
    data = json.loads(proc.stdout)
    check_by_id = {c["id"]: c for c in data["verification"]["structural"]["checks"]}
    assert check_by_id["leftover_placeholder_text"]["status"] == "fail"
    assert proc.returncode == 1


def test_svg_doctor_reports_structural_always_available(tmp_path):
    proc = run_cli(["doctor", "--json"], cwd=tmp_path)
    data = json.loads(proc.stdout)
    assert data["capabilities"]["svg.structural"]["status"] == "available"
    assert "svg.render" in data["capabilities"]


def test_svg_inspect_and_verify(good_svg, tmp_path):
    inspect_proc = run_cli(["inspect", str(good_svg), "--json"], cwd=tmp_path)
    assert inspect_proc.returncode == 0
    data = json.loads(inspect_proc.stdout)
    assert data["details"]["has_explicit_size"] is True

    verify_proc = run_cli(["verify", str(good_svg), "--json"], cwd=tmp_path)
    assert verify_proc.returncode == 0
    assert json.loads(verify_proc.stdout)["status"] == "pass"


def test_svg_entity_bomb_rejected_via_cli(entity_bomb_svg, tmp_path):
    proc = run_cli(["inspect", str(entity_bomb_svg), "--json"], cwd=tmp_path)
    assert proc.returncode == 4  # ArtifactSecurityError -> security category
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_svg_has_no_mutating_operations(good_svg, tmp_path):
    proc = run_cli(["execute", str(good_svg), "--operation", "metadata_set", "--args", "{}", "--json"], cwd=tmp_path)
    assert proc.returncode == 2
    data = json.loads(proc.stdout)
    assert data["error"]["code"] == "ARTIFACT_OPERATION_UNKNOWN"

from __future__ import annotations

import pytest

from artifact_skill.adapters.pdf.adapter import PdfAdapter
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.errors import ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult


@pytest.fixture()
def adapter() -> PdfAdapter:
    return PdfAdapter()


def test_inspect_good_pdf_reports_two_pages(good_pdf, adapter):
    ref = ArtifactRef.from_path(good_pdf)
    report = adapter.inspect(ref)
    assert report.details["page_count"] == 2
    assert report.details["is_encrypted"] is False
    assert report.details["text_extractable_pages"] == 2


def test_inspect_corrupt_pdf_raises_structured_error(corrupt_pdf, adapter):
    ref = ArtifactRef.from_path(corrupt_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_PDF_UNREADABLE"


def test_inspect_encrypted_pdf_does_not_crash(encrypted_pdf, adapter):
    """Regression test: inspect() used to crash on `reader.metadata and not
    is_encrypted` evaluating `reader.metadata` (which raises on an
    undecrypted file) before short-circuiting on `is_encrypted`."""
    ref = ArtifactRef.from_path(encrypted_pdf)
    report = adapter.inspect(ref)
    assert report.details["is_encrypted"] is True
    assert report.details["page_count"] == 0
    assert "encrypted" in report.warnings[0].lower()


def test_verify_empty_pdf_fails_page_count(empty_pdf, adapter):
    ref = ArtifactRef.from_path(empty_pdf)
    result = adapter.verify_structural(ref, {})
    page_count_check = next(c for c in result.checks if c.id == "page_count")
    assert page_count_check.status == CheckStatus.FAIL
    assert result.status == CheckStatus.FAIL


def test_inspect_blank_page_pdf_identifies_the_blank_page(blank_page_pdf, adapter):
    ref = ArtifactRef.from_path(blank_page_pdf)
    report = adapter.inspect(ref)
    assert report.details["blank_pages"] == [2]


def test_verify_good_pdf_has_no_blank_pages(good_pdf, adapter):
    ref = ArtifactRef.from_path(good_pdf)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "blank_pages")
    assert check.status == CheckStatus.PASS


def test_verify_blank_page_pdf_warns_by_default(blank_page_pdf, adapter):
    ref = ArtifactRef.from_path(blank_page_pdf)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "blank_pages")
    assert check.status == CheckStatus.WARN
    assert check.evidence["blank_pages"] == [2]


def test_verify_blank_page_pdf_fails_under_strict_policy(blank_page_pdf, adapter):
    ref = ArtifactRef.from_path(blank_page_pdf)
    result = adapter.verify_structural(ref, {"forbid_blank_pages": True})
    check = next(c for c in result.checks if c.id == "blank_pages")
    assert check.status == CheckStatus.FAIL


def test_verify_good_pdf_has_no_leftover_placeholder_text(good_pdf, adapter):
    ref = ArtifactRef.from_path(good_pdf)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_verify_leftover_placeholder_pdf_warns_by_default(leftover_placeholder_pdf, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_pdf)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "click to add" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]
    assert "todo" in check.evidence["markers"]


def test_verify_leftover_placeholder_pdf_fails_under_strict_policy(leftover_placeholder_pdf, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_pdf)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_verify_encrypted_pdf_warns_by_default(encrypted_pdf, adapter):
    ref = ArtifactRef.from_path(encrypted_pdf)
    result = adapter.verify_structural(ref, {})
    enc_check = next(c for c in result.checks if c.id == "encryption")
    assert enc_check.status == CheckStatus.WARN


def test_verify_encrypted_pdf_fails_when_policy_forbids_it(encrypted_pdf, adapter):
    ref = ArtifactRef.from_path(encrypted_pdf)
    result = adapter.verify_structural(ref, {"require_no_encryption": True})
    enc_check = next(c for c in result.checks if c.id == "encryption")
    assert enc_check.status == CheckStatus.FAIL


def test_verify_good_pdf_page_count_requirement(good_pdf, adapter):
    ref = ArtifactRef.from_path(good_pdf)
    ok = adapter.verify_structural(ref, {"require_page_count": 2})
    assert next(c for c in ok.checks if c.id == "page_count_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_page_count": 5})
    assert next(c for c in mismatch.checks if c.id == "page_count_requirement").status == CheckStatus.FAIL


def test_font_embedding_passes_for_standard14_font(good_pdf, adapter):
    """good_2page.pdf uses plain Helvetica (reportlab's default) — a
    standard-14 font is fine unembedded; every conformant viewer must
    render it correctly without one."""
    ref = ArtifactRef.from_path(good_pdf)
    result = adapter.verify_structural(ref, {})
    font_check = next(c for c in result.checks if c.id == "font_embedding")
    assert font_check.status == CheckStatus.PASS


def test_font_embedding_passes_when_custom_font_is_embedded(embedded_font_pdf, adapter):
    ref = ArtifactRef.from_path(embedded_font_pdf)
    result = adapter.verify_structural(ref, {})
    font_check = next(c for c in result.checks if c.id == "font_embedding")
    assert font_check.status == CheckStatus.PASS


def test_font_embedding_warns_by_default_when_custom_font_not_embedded(nonembedded_custom_font_pdf, adapter):
    ref = ArtifactRef.from_path(nonembedded_custom_font_pdf)
    result = adapter.verify_structural(ref, {})
    font_check = next(c for c in result.checks if c.id == "font_embedding")
    assert font_check.status == CheckStatus.WARN
    assert "CustomNonEmbedded" in font_check.evidence["unembedded_fonts"]


def test_font_embedding_fails_under_strict_policy(nonembedded_custom_font_pdf, adapter):
    ref = ArtifactRef.from_path(nonembedded_custom_font_pdf)
    result = adapter.verify_structural(ref, {"forbid_unembedded_fonts": True})
    font_check = next(c for c in result.checks if c.id == "font_embedding")
    assert font_check.status == CheckStatus.FAIL


def test_verify_good_pdf_now_passes_cleanly_overall(good_pdf, adapter):
    """Regression guard: before the font-embedding check was implemented
    (Issue #7), this always rolled up to UNKNOWN because font_embedding
    was unconditionally UNKNOWN — see docs/verification.md's history of
    this. A plain, standard-font PDF should now genuinely PASS overall."""
    ref = ArtifactRef.from_path(good_pdf)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_execute_metadata_set_writes_new_file_and_preserves_input(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    before_hash = ref.sha256
    output_path = tmp_path / "out.pdf"

    result_ref = adapter.execute(ref, "metadata_set", {"title": "New Title", "author": "QA"}, output_path)

    assert output_path.exists()
    assert result_ref.sha256 != before_hash
    # Original Protection: re-reading the *input* path must show it untouched.
    assert ArtifactRef.from_path(good_pdf).sha256 == before_hash

    out_report = adapter.inspect(result_ref)
    assert out_report.details["metadata"]["Title"] == "New Title"
    assert out_report.details["metadata"]["Author"] == "QA"
    assert out_report.details["page_count"] == 2  # postcondition: page count preserved


def test_verify_require_metadata_matches_lowercase_policy_keys(good_pdf, adapter, tmp_path):
    """Independent-review finding: details["metadata"] is keyed by the PDF
    Info dict's own native casing ("Title", not "title" - see the test
    above), but require_metadata's own field names use metadata_set's
    lowercase schema convention ("title"), matching every other adapter
    (DOCX/PPTX/XLSX). A bare `.get(key)` against the Capitalized dict
    always FAILed for this, the natural way to write this policy
    (confirmed by direct reproduction before this fix)."""
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "metadata_set", {"title": "My Title", "author": "QA"}, tmp_path / "out.pdf")

    result = adapter.verify_structural(result_ref, {"require_metadata": {"title": "My Title", "author": "QA"}})
    title_check = next(c for c in result.checks if c.id == "metadata_title")
    author_check = next(c for c in result.checks if c.id == "metadata_author")
    assert title_check.status == CheckStatus.PASS
    assert author_check.status == CheckStatus.PASS


def test_verify_require_metadata_still_fails_on_a_real_mismatch(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "metadata_set", {"title": "Actual Title"}, tmp_path / "out.pdf")

    result = adapter.verify_structural(result_ref, {"require_metadata": {"title": "Wrong Title"}})
    check = next(c for c in result.checks if c.id == "metadata_title")
    assert check.status == CheckStatus.FAIL


def test_execute_metadata_set_on_encrypted_pdf_raises(encrypted_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(encrypted_pdf)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.execute(ref, "metadata_set", {"title": "x"}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_PDF_ENCRYPTED"


def test_execute_merge_doubles_page_count(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    output_path = tmp_path / "merged.pdf"
    result_ref = adapter.execute(ref, "merge", {"additional_inputs": [str(good_pdf)]}, output_path)
    report = adapter.inspect(result_ref)
    assert report.details["page_count"] == 4


def test_execute_merge_rejects_an_oversized_additional_input(adapter, tmp_path, monkeypatch):
    """Independent-review finding: check_input_size() is called for the
    primary input via inspect(), but merge's additional_inputs never
    passed through it on either plan() or execute() - pypdf.PdfReader()
    loads a file entirely into memory, so the size cap this exists to
    enforce was silently bypassed for every secondary merge input
    (confirmed by direct reproduction before this fix: a file exceeding
    the configured limit was correctly rejected as the primary input but
    merged in without error as an additional_inputs entry).

    Uses a small primary and a deliberately larger additional_inputs file,
    with a limit between the two sizes - a blanket tiny limit on both
    would pass even without this fix (the primary's own long-standing
    check would fire first via inspect() and mask a missing additional-
    inputs check entirely), so this specifically isolates the fix."""
    import pypdf

    from artifact_skill.core.errors import ArtifactSecurityError
    from artifact_skill.security.limits import Limits

    def _make_pdf(path, pages: int) -> None:
        writer = pypdf.PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        with open(path, "wb") as f:
            writer.write(f)

    small = tmp_path / "small.pdf"
    big = tmp_path / "big.pdf"
    _make_pdf(small, pages=1)
    _make_pdf(big, pages=20)
    assert small.stat().st_size < big.stat().st_size

    import artifact_skill.adapters.pdf.adapter as pdf_adapter_module

    real_check = pdf_adapter_module.check_input_size
    between = Limits(max_input_bytes=(small.stat().st_size + big.stat().st_size) // 2)

    def _discriminating_check(path, limits=between):
        return real_check(path, limits=limits)

    monkeypatch.setattr(pdf_adapter_module, "check_input_size", _discriminating_check)

    ref = ArtifactRef.from_path(small)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.execute(ref, "merge", {"additional_inputs": [str(big)]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INPUT_TOO_LARGE"
    assert exc_info.value.evidence["path"] == str(big)

    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.plan(ref, "merge", {"additional_inputs": [str(big)]}, tmp_path / "out2.pdf")
    assert exc_info.value.code == "ARTIFACT_INPUT_TOO_LARGE"
    assert exc_info.value.evidence["path"] == str(big)


def test_execute_unknown_operation_raises(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError):
        adapter.execute(ref, "not_a_real_operation", {}, tmp_path / "out.pdf")


def test_render_produces_one_png_per_page(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 2
    assert all(f.exists() for f in result.files)
    assert result.backend == "pypdfium2"


def test_render_empty_pdf_produces_zero_files_with_warning(empty_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(empty_pdf)
    result = adapter.render(ref, tmp_path / "rendered")
    assert result.files == []
    assert result.warnings


def test_execute_fit_page_size_scales_to_exact_target(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    output_path = tmp_path / "fitted.pdf"
    result_ref = adapter.execute(ref, "fit_page_size", {"width_pt": 400, "height_pt": 500}, output_path)
    report = adapter.inspect(result_ref)
    assert report.details["page_count"] == 2  # postcondition: page count preserved
    for size in report.details["page_sizes"]:
        assert size["width_pt"] == pytest.approx(400, abs=0.5)
        assert size["height_pt"] == pytest.approx(500, abs=0.5)


def test_execute_fit_page_size_rejects_non_positive_dimensions(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "fit_page_size", {"width_pt": 0, "height_pt": 500}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"


def test_execute_fit_page_size_on_encrypted_pdf_raises(encrypted_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(encrypted_pdf)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.execute(ref, "fit_page_size", {"width_pt": 400, "height_pt": 500}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_PDF_ENCRYPTED"


def test_execute_extract_pages_keeps_only_the_requested_page(good_pdf, adapter, tmp_path):
    """good_2page.pdf's real content: page 1 = "Page one content",
    page 2 = "Page two content" (tests/fixtures/generate_fixtures.py) -
    checking extracted text, not just page count, proves the *right*
    page was kept, not just *a* page."""
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "extract_pages", {"pages": [2]}, tmp_path / "out.pdf")
    report = adapter.inspect(result_ref)
    assert report.details["page_count"] == 1

    import pypdf

    reader = pypdf.PdfReader(str(result_ref.path))
    assert "Page two content" in reader.pages[0].extract_text()


def test_execute_extract_pages_can_reorder(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "extract_pages", {"pages": [2, 1]}, tmp_path / "out.pdf")
    import pypdf

    reader = pypdf.PdfReader(str(result_ref.path))
    assert len(reader.pages) == 2
    assert "Page two content" in reader.pages[0].extract_text()
    assert "Page one content" in reader.pages[1].extract_text()


def test_execute_extract_pages_rejects_out_of_range_page(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "extract_pages", {"pages": [5]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"
    assert exc_info.value.evidence["page_count"] == 2


def test_execute_extract_pages_on_encrypted_pdf_raises(encrypted_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(encrypted_pdf)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.execute(ref, "extract_pages", {"pages": [1]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_PDF_ENCRYPTED"


def test_execute_delete_pages_removes_the_requested_page(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "delete_pages", {"pages": [1]}, tmp_path / "out.pdf")
    report = adapter.inspect(result_ref)
    assert report.details["page_count"] == 1

    import pypdf

    reader = pypdf.PdfReader(str(result_ref.path))
    assert "Page two content" in reader.pages[0].extract_text()


def test_execute_delete_pages_rejects_removing_every_page(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "delete_pages", {"pages": [1, 2]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"
    assert "every page" in exc_info.value.message


def test_execute_delete_pages_rejects_out_of_range_page(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "delete_pages", {"pages": [99]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"


def test_execute_delete_pages_on_encrypted_pdf_raises(encrypted_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(encrypted_pdf)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.execute(ref, "delete_pages", {"pages": [1]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_PDF_ENCRYPTED"


def test_execute_rotate_pages_rotates_all_pages_by_default(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "rotate_pages", {"degrees": 90}, tmp_path / "out.pdf")
    import pypdf

    reader = pypdf.PdfReader(str(result_ref.path))
    assert [p.rotation for p in reader.pages] == [90, 90]


def test_execute_rotate_pages_rotates_only_specified_pages(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    result_ref = adapter.execute(ref, "rotate_pages", {"pages": [1], "degrees": 180}, tmp_path / "out.pdf")
    import pypdf

    reader = pypdf.PdfReader(str(result_ref.path))
    assert reader.pages[0].rotation == 180
    assert reader.pages[1].rotation == 0


def test_execute_rotate_pages_rejects_invalid_degrees(good_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "rotate_pages", {"degrees": 45}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"


def test_execute_rotate_pages_on_encrypted_pdf_raises(encrypted_pdf, adapter, tmp_path):
    ref = ArtifactRef.from_path(encrypted_pdf)
    with pytest.raises(ArtifactExecutionError) as exc_info:
        adapter.execute(ref, "rotate_pages", {"degrees": 90}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_PDF_ENCRYPTED"


def test_plan_extract_pages_validates_page_range_before_executing(good_pdf, adapter, tmp_path):
    """plan() must catch a bad page number itself (spec: plan is pure but
    still validates), not just execute()."""
    ref = ArtifactRef.from_path(good_pdf)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.plan(ref, "extract_pages", {"pages": [7]}, tmp_path / "out.pdf")
    assert exc_info.value.code == "ARTIFACT_INVALID_ARGS"


def test_verify_page_size_requirement_carries_fixer_evidence(good_pdf, adapter, tmp_path):
    """The fit_page_size fixer (see test_fix_* below) reads
    expected_width_pt/expected_height_pt straight off this check's
    evidence — this locks in that shape as a contract."""
    ref = ArtifactRef.from_path(good_pdf)
    result = adapter.verify_structural(ref, {"require_page_size_pt": (300, 300), "page_size_tolerance_pt": 1.0})
    check = next(c for c in result.checks if c.id == "page_size_requirement")
    assert check.status == CheckStatus.FAIL
    assert check.evidence["expected_width_pt"] == 300
    assert check.evidence["expected_height_pt"] == 300
    assert check.evidence["actual_width_pt"] == pytest.approx(612, abs=0.5)


def test_fix_returns_corrected_args_for_page_size_mismatch(adapter, good_pdf):
    """Given the exact failed_result shape verify_structural() produces,
    fix() must read the expected size and hand back args that would fix it —
    this is the unit-level guarantee behind the full retry-loop test in
    test_engine_lifecycle.py."""
    ref = ArtifactRef.from_path(good_pdf)
    failed = VerificationResult(
        kind="structural",
        checks=[
            Check(
                id="page_size_requirement", name="Page size matches requirement", status=CheckStatus.FAIL,
                evidence={"expected_width_pt": 612, "expected_height_pt": 792, "actual_width_pt": 100, "actual_height_pt": 100},
            )
        ],
    )
    fixed_args = adapter.fix(ref, "fit_page_size", {"width_pt": 100, "height_pt": 100}, failed)
    assert fixed_args == {"width_pt": 612, "height_pt": 792}


def test_fix_returns_none_for_operations_other_than_fit_page_size(adapter, good_pdf):
    ref = ArtifactRef.from_path(good_pdf)
    failed = VerificationResult(kind="structural", checks=[Check(id="page_count", name="Page count", status=CheckStatus.FAIL)])
    assert adapter.fix(ref, "metadata_set", {"title": "x"}, failed) is None


def test_fix_returns_none_when_already_at_expected_size(adapter, good_pdf):
    """Retrying with identical args would loop pointlessly instead of
    honestly giving up — this is the one case this fixer intentionally
    doesn't handle, per spec #16."""
    ref = ArtifactRef.from_path(good_pdf)
    failed = VerificationResult(
        kind="structural",
        checks=[
            Check(
                id="page_size_requirement", name="Page size matches requirement", status=CheckStatus.FAIL,
                evidence={"expected_width_pt": 612, "expected_height_pt": 792, "actual_width_pt": 611, "actual_height_pt": 792},
            )
        ],
    )
    assert adapter.fix(ref, "fit_page_size", {"width_pt": 612, "height_pt": 792}, failed) is None


def test_fix_returns_none_when_no_page_size_check_present(adapter, good_pdf):
    ref = ArtifactRef.from_path(good_pdf)
    failed = VerificationResult(kind="structural", checks=[Check(id="page_count", name="Page count", status=CheckStatus.FAIL)])
    assert adapter.fix(ref, "fit_page_size", {"width_pt": 100, "height_pt": 100}, failed) is None


def test_capabilities_report_available_when_deps_installed(adapter):
    caps = {c.id: c for c in adapter.capabilities()}
    assert "pdf.structural" in caps
    assert "pdf.render" in caps
    # In this dev environment both deps are installed; if they weren't,
    # this would legitimately need to assert MISSING instead — the point
    # of this test is that capabilities() never lies about the probe result.
    from artifact_skill.core.capability import CapabilityStatus

    assert caps["pdf.structural"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)
    assert caps["pdf.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_limitations_names_the_real_known_caveats(adapter):
    """Issue #29: limitations() content had zero test coverage anywhere -
    a regression that silently dropped a real caveat (or emptied the list
    entirely) would have passed every other test. Checks specific,
    known-true strings, not just "is a non-empty list"."""
    text = " ".join(adapter.limitations())
    assert "not decrypted automatically" in text
    assert "never executed or analyzed further" in text
    assert "blank_pages" in text


def _pdf_with_openaction_javascript(tmp_path):
    """Build a minimal PDF whose /OpenAction (not /Names /JavaScript) runs
    JavaScript - the exact real-world payload location FIX_PROMPT P2-2
    found this adapter previously missed entirely."""
    import pypdf
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    action = DictionaryObject()
    action[NameObject("/S")] = NameObject("/JavaScript")
    action[NameObject("/JS")] = TextStringObject("app.alert('hi');")
    writer._root_object[NameObject("/OpenAction")] = action
    path = tmp_path / "openaction_js.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _pdf_with_page_level_aa_javascript(tmp_path):
    """Same idea, but the JavaScript sits in a page's own /AA (additional
    actions) dict rather than the document catalog."""
    import pypdf
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    action = DictionaryObject()
    action[NameObject("/S")] = NameObject("/JavaScript")
    action[NameObject("/JS")] = TextStringObject("app.alert('page open');")
    aa = DictionaryObject()
    aa[NameObject("/O")] = action
    writer.pages[0][NameObject("/AA")] = aa
    path = tmp_path / "page_aa_js.pdf"
    with open(path, "wb") as f:
        writer.write(f)
    return path


def test_inspect_detects_javascript_in_openaction(adapter, tmp_path):
    """FIX_PROMPT P2-2: /Names /JavaScript is only one of several places a
    PDF can carry JavaScript - confirmed by direct reproduction before this
    fix that a crafted /OpenAction JavaScript action passed has_javascript
    == False."""
    path = _pdf_with_openaction_javascript(tmp_path)
    report = adapter.inspect(ArtifactRef.from_path(path))
    assert report.details["has_javascript"] is True


def test_inspect_detects_javascript_in_page_level_additional_actions(adapter, tmp_path):
    path = _pdf_with_page_level_aa_javascript(tmp_path)
    report = adapter.inspect(ArtifactRef.from_path(path))
    assert report.details["has_javascript"] is True


def test_verify_openaction_javascript_fails_under_default_policy(adapter, tmp_path):
    path = _pdf_with_openaction_javascript(tmp_path)
    result = adapter.verify_structural(ArtifactRef.from_path(path), {})
    check = next(c for c in result.checks if c.id == "javascript")
    assert check.status == CheckStatus.FAIL


def test_render_honors_a_custom_limits_max_pages(good_pdf, adapter, tmp_path):
    """Grok review P0-3: Limits.max_pages must actually reach the render
    backend through PdfAdapter.render(), not just render_pdf_pages()
    directly (test_pdf_pages.py covers that in isolation)."""
    from artifact_skill.core.errors import ArtifactSecurityError
    from artifact_skill.security.limits import Limits

    ref = ArtifactRef.from_path(good_pdf)  # good_2page.pdf
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.render(ref, tmp_path / "rendered", limits=Limits(max_pages=1))
    assert exc_info.value.code == "ARTIFACT_TOO_MANY_PAGES"

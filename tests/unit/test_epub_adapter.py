from __future__ import annotations

import pytest

from artifact_skill.adapters.epub.adapter import EpubAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> EpubAdapter:
    return EpubAdapter()


def test_type_detection_by_content_not_extension(good_epub, mislabeled_pdf_as_epub):
    assert ArtifactRef.from_path(good_epub).type == ArtifactType.EPUB
    assert ArtifactRef.from_path(mislabeled_pdf_as_epub).type == ArtifactType.PDF


def test_inspect_good_epub_reports_metadata_and_spine(good_epub, adapter):
    ref = ArtifactRef.from_path(good_epub)
    report = adapter.inspect(ref)
    assert report.details["title"] == ["My Book"]
    assert report.details["creator"] == ["Author Name"]
    assert report.details["spine_count"] == 1
    assert report.details["manifest_item_count"] == 1
    assert report.details["manifest_references_missing"] == []
    assert report.details["spine_references_missing"] == []
    assert report.details["mimetype_first_and_stored"] is True


def test_inspect_missing_container_raises_structured_error(missing_container_epub, adapter):
    ref = ArtifactRef.from_path(missing_container_epub)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_EPUB_UNREADABLE"


def test_inspect_entity_bomb_is_rejected_before_parsing(entity_bomb_epub, adapter):
    """Issue #21's class of risk, generalized to EPUB's own OPF/XHTML XML:
    xml.etree.ElementTree is not hardened against entity-expansion DoS -
    a DOCTYPE/ENTITY declaration in the package document must be refused
    outright, never handed to ET.fromstring()."""
    ref = ArtifactRef.from_path(entity_bomb_epub)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_inspect_rejects_an_entity_declaration_in_a_member_over_10mb(adapter, tmp_path):
    """Self-audit finding (P1-2, the same bypass class fixed in
    security/xml_safety.py): this adapter's own OPF parser used to skip
    the entity-declaration check entirely for any member over 10MB -
    ET.fromstring() would still run against it completely unguarded.
    Confirmed directly (DID NOT RAISE) before this fix."""
    import zipfile

    path = tmp_path / "big_entity_bomb.epub"
    entity_decl = (
        '<?xml version="1.0"?>\n<!DOCTYPE package [<!ENTITY xxe "pwned">]>\n'
        '<package xmlns="http://www.idpf.org/2007/opf">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>&xxe;</dc:title></metadata>\n"
        "  <manifest/><spine/>\n"
        "<!-- "
    )
    padding = "A" * (11 * 1024 * 1024)
    opf = entity_decl + padding + " --></package>\n"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
            'version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)

    ref = ArtifactRef.from_path(path)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.inspect(ref)
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_verify_good_epub_passes_cleanly(good_epub, adapter):
    ref = ArtifactRef.from_path(good_epub)
    result = adapter.verify_structural(ref, {})
    assert result.status == CheckStatus.PASS


def test_verify_broken_manifest_reference_fails(broken_manifest_epub, adapter):
    ref = ArtifactRef.from_path(broken_manifest_epub)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "manifest_references_resolve")
    assert check.status == CheckStatus.FAIL
    assert result.status == CheckStatus.FAIL


def test_verify_broken_spine_reference_fails(broken_spine_epub, adapter):
    ref = ArtifactRef.from_path(broken_spine_epub)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "spine_references_resolve")
    assert check.status == CheckStatus.FAIL


def test_verify_mimetype_not_first_warns_not_fails(mimetype_not_first_epub, adapter):
    """Most real-world reading systems tolerate this; it's a real spec
    violation but not severe enough to FAIL a receipt over by default."""
    ref = ArtifactRef.from_path(mimetype_not_first_epub)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "mimetype_first_and_stored")
    assert check.status == CheckStatus.WARN


def test_verify_good_epub_spine_count_requirement(good_epub, adapter):
    ref = ArtifactRef.from_path(good_epub)
    ok = adapter.verify_structural(ref, {"require_spine_count": 1})
    assert next(c for c in ok.checks if c.id == "spine_count_requirement").status == CheckStatus.PASS

    mismatch = adapter.verify_structural(ref, {"require_spine_count": 5})
    assert next(c for c in mismatch.checks if c.id == "spine_count_requirement").status == CheckStatus.FAIL


def test_verify_good_epub_has_no_leftover_placeholder_text(good_epub, adapter):
    ref = ArtifactRef.from_path(good_epub)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.PASS


def test_verify_leftover_placeholder_epub_warns_by_default(leftover_placeholder_epub, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_epub)
    result = adapter.verify_structural(ref, {})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.WARN
    assert "todo" in check.evidence["markers"]
    assert "lorem ipsum" in check.evidence["markers"]


def test_verify_leftover_placeholder_epub_fails_under_strict_policy(leftover_placeholder_epub, adapter):
    ref = ArtifactRef.from_path(leftover_placeholder_epub)
    result = adapter.verify_structural(ref, {"forbid_placeholder_text": True})
    check = next(c for c in result.checks if c.id == "leftover_placeholder_text")
    assert check.status == CheckStatus.FAIL


def test_execute_metadata_set_preserves_input_and_spine(good_epub, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_epub)
    before_hash = ref.sha256
    output_path = tmp_path / "out.epub"

    result_ref = adapter.execute(ref, "metadata_set", {"title": "New Title", "author": "New Author"}, output_path)

    assert output_path.exists()
    assert result_ref.sha256 != before_hash
    assert ArtifactRef.from_path(good_epub).sha256 == before_hash  # Original Protection

    out_report = adapter.inspect(result_ref)
    assert out_report.details["title"] == ["New Title"]
    assert out_report.details["creator"] == ["New Author"]
    assert out_report.details["spine_count"] == 1
    assert out_report.details["mimetype_first_and_stored"] is True


def test_execute_metadata_set_output_is_still_a_readable_epub_by_type(good_epub, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_epub)
    output_path = tmp_path / "out.epub"
    result_ref = adapter.execute(ref, "metadata_set", {"title": "New Title"}, output_path)
    assert result_ref.type == ArtifactType.EPUB


def test_execute_metadata_set_preserves_the_opf_default_namespace_prefix(good_epub, adapter, tmp_path):
    """Self-audit finding: ET.tostring() auto-generates an 'ns0:' prefix
    for any namespace it has no registered mapping for. good_epub's OPF
    (like virtually every real-world EPUB) declares
    xmlns="http://www.idpf.org/2007/opf" as the *default* (unprefixed)
    namespace - without registering it as such before serializing, the
    rewritten OPF's elements were all getting rewritten to
    <ns0:package>/<ns0:metadata>/etc. Namespace-URI-aware XML parsers
    (this project's own EpubAdapter included) don't care about the prefix
    name, but a strict or naive validator/reading system doing
    string-based tag comparison could - and it's needless churn in output
    a human might diff against the original."""
    import zipfile

    ref = ArtifactRef.from_path(good_epub)
    output_path = tmp_path / "out.epub"
    adapter.execute(ref, "metadata_set", {"title": "New Title"}, output_path)
    with zipfile.ZipFile(output_path) as zf:
        opf_bytes = zf.read("OEBPS/content.opf")
    assert b"ns0:" not in opf_bytes
    assert b"<package" in opf_bytes


def test_execute_unknown_operation_raises(good_epub, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_epub)
    with pytest.raises(ArtifactInputError) as exc_info:
        adapter.execute(ref, "not_a_real_operation", {}, tmp_path / "out.epub")
    assert exc_info.value.code == "ARTIFACT_OPERATION_UNKNOWN"
    with pytest.raises(ArtifactInputError):
        adapter.plan(ref, "not_a_real_operation", {}, tmp_path / "out.epub")


def test_plan_metadata_set_is_pure_and_writes_nothing(good_epub, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_epub)
    output_path = tmp_path / "out.epub"
    plan = adapter.plan(ref, "metadata_set", {"title": "New Title"}, output_path)
    assert plan.operation == "metadata_set"
    assert not output_path.exists()


def test_capabilities_report_never_lies_about_probe_result(adapter):
    from artifact_skill.core.capability import CapabilityStatus

    caps = {c.id: c for c in adapter.capabilities()}
    assert "epub.structural" in caps
    assert caps["epub.structural"].status == CapabilityStatus.AVAILABLE  # stdlib-only, always available
    assert "epub.render" in caps
    assert caps["epub.render"].status == CapabilityStatus.NOT_IMPLEMENTED


def test_render_is_honestly_not_implemented(good_epub, adapter, tmp_path):
    """capabilities() must never claim more than render() actually does -
    see docs/adapters.md's 'never treat a stub as done' rule."""
    from artifact_skill.core.errors import ArtifactCapabilityError

    ref = ArtifactRef.from_path(good_epub)
    with pytest.raises(ArtifactCapabilityError) as exc_info:
        adapter.render(ref, tmp_path / "rendered")
    assert exc_info.value.code == "ARTIFACT_RENDER_NOT_IMPLEMENTED"


def test_limitations_names_the_real_known_caveats(adapter):
    text = " ".join(adapter.limitations())
    assert "Rendering is not implemented" in text
    assert "title/author only" in text

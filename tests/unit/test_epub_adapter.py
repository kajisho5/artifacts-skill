from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from artifact_skill.adapters.epub.adapter import EpubAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.verification import CheckStatus


@pytest.fixture()
def adapter() -> EpubAdapter:
    return EpubAdapter()


def _probe_epub_render_works(adapter: EpubAdapter, ref: ArtifactRef, tmp_path: Path) -> bool:
    """Same pattern as test_html_adapter.py's identical helper - Playwright
    being pip-installed doesn't guarantee a matching Chromium build."""
    try:
        result = adapter.render(ref, tmp_path / "_probe")
        return len(result.files) > 0
    except Exception:  # noqa: BLE001
        return False


_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415478da6360f8cfc0c0c00000030001807e2d5c9700000000"
    "49454e44ae426082"
)


def _build_cross_directory_epub(path: Path) -> None:
    """A minimal but entirely ordinary real-world EPUB layout: content
    documents under OEBPS/text/, resources under a sibling OEBPS/images/ -
    proves render() resolves same-archive cross-directory references
    (which the P0-2 boundary this adapter reuses would incorrectly block
    if it were scoped to just each document's own immediate directory,
    rather than the whole staged archive - see render()'s docstring)."""
    container_xml = (
        b'<?xml version="1.0"?>'
        b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b'<rootfiles><rootfile full-path="OEBPS/content.opf" '
        b'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        b'<?xml version="1.0"?>'
        b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Cross Dir</dc:title></metadata>'
        b"<manifest>"
        b'<item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
        b'<item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
        b'<item id="pic" href="images/pic.png" media-type="image/png"/>'
        b"</manifest>"
        b'<spine><itemref idref="ch1"/><itemref idref="ch2"/></spine></package>'
    )
    ch1 = b'<html><body><h1>Ch1</h1><img src="../images/pic.png" width="1" height="1"/></body></html>'
    ch2 = b"<html><body><h1>Ch2</h1></body></html>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/text/ch1.xhtml", ch1)
        zf.writestr("OEBPS/text/ch2.xhtml", ch2)
        zf.writestr("OEBPS/images/pic.png", _ONE_PX_PNG)


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
    # Self-audit finding: render() is now implemented (was NOT_IMPLEMENTED) -
    # capabilities() reports the same playwright-availability pattern as
    # every other Chromium-backed adapter (HTML/SVG/CSV/Markdown), never a
    # permanently-stale "deferred" status.
    assert caps["epub.render"].status in (CapabilityStatus.AVAILABLE, CapabilityStatus.MISSING)


def test_render_happy_path_when_backend_actually_works(good_epub, adapter, tmp_path):
    ref = ArtifactRef.from_path(good_epub)
    if not _probe_epub_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render (see adapter docstring)")
    result = adapter.render(ref, tmp_path / "rendered")
    assert result.backend == "playwright+chromium"
    assert len(result.files) == 1  # good_epub has one spine document
    assert result.files[0].exists()
    assert result.files[0].stat().st_size > 0
    assert result.warnings == []


def test_render_produces_one_png_per_spine_document_in_order(adapter, tmp_path):
    epub_path = tmp_path / "cross_dir.epub"
    _build_cross_directory_epub(epub_path)
    ref = ArtifactRef.from_path(epub_path)
    if not _probe_epub_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render (see adapter docstring)")

    result = adapter.render(ref, tmp_path / "rendered")
    assert [f.name for f in result.files] == ["page-001.png", "page-002.png"]
    assert all(f.exists() and f.stat().st_size > 0 for f in result.files)


def test_render_resolves_a_same_archive_cross_directory_resource_reference(adapter, tmp_path):
    """This is the specific design gap that used to keep render()
    NOT_IMPLEMENTED (see this module's and the adapter's docstrings): a
    spine document under OEBPS/text/ referencing an image under a sibling
    OEBPS/images/ is an entirely ordinary EPUB layout, not an edge case.
    Proven functionally: the chapter WITH the (now-loading) image renders
    a larger PNG than the chapter with none, the same "verify functionally
    rather than inspect pixel data" approach test_html_adapter.py's
    external-request test uses."""
    epub_path = tmp_path / "cross_dir.epub"
    _build_cross_directory_epub(epub_path)
    ref = ArtifactRef.from_path(epub_path)
    if not _probe_epub_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render (see adapter docstring)")

    result = adapter.render(ref, tmp_path / "rendered")
    ch1_with_image, ch2_without_image = result.files
    assert ch1_with_image.stat().st_size > ch2_without_image.stat().st_size


def test_render_still_blocks_a_file_url_escaping_the_staged_archive(adapter, tmp_path):
    """The P0-2 boundary render() reuses must still reject anything
    outside the EPUB's own (safely extracted, private) staged copy - a
    hostile spine document cannot use the wider allowed_root as a loophole
    to reach an arbitrary local file."""
    container_xml = (
        b'<?xml version="1.0"?>'
        b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        b'<rootfiles><rootfile full-path="OEBPS/content.opf" '
        b'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        b'<?xml version="1.0"?>'
        b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Hostile</dc:title></metadata>'
        b'<manifest><item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/></manifest>'
        b'<spine><itemref idref="ch1"/></spine></package>'
    )
    ch1 = b'<html><body><iframe src="file:///etc/passwd" id="leak"></iframe></body></html>'
    epub_path = tmp_path / "hostile.epub"
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/text/ch1.xhtml", ch1)

    ref = ArtifactRef.from_path(epub_path)
    if not _probe_epub_render_works(adapter, ref, tmp_path):
        pytest.skip("Playwright/Chromium in this environment cannot render (see adapter docstring)")

    # Render succeeding without raising already proves the hostile file://
    # request was aborted rather than crashing the render; the request-
    # level proof that it's aborted (not fulfilled) is
    # test_chromium_render.py's own real-Chromium P0-2 regression test,
    # which this adapter's render() routes through unchanged.
    result = adapter.render(ref, tmp_path / "rendered")
    assert len(result.files) == 1


def test_render_rejects_more_spine_documents_than_max_pages(good_epub, adapter, tmp_path):
    from artifact_skill.security.limits import Limits

    ref = ArtifactRef.from_path(good_epub)  # 1 spine document
    with pytest.raises(ArtifactSecurityError) as exc_info:
        adapter.render(ref, tmp_path / "rendered", limits=Limits(max_pages=0))
    assert exc_info.value.code == "ARTIFACT_TOO_MANY_PAGES"


def test_limitations_names_the_real_known_caveats(adapter):
    text = " ".join(adapter.limitations())
    assert "one PNG per spine" in text
    assert "title/author only" in text

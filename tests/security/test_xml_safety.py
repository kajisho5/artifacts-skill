from __future__ import annotations

import zipfile

import pytest

from artifact_skill.core.errors import ArtifactSecurityError
from artifact_skill.security.xml_safety import (
    reject_xml_entities_in_file,
    reject_xml_entities_in_zip,
    reject_xml_entity_declaration,
)


def test_clean_xml_passes():
    reject_xml_entity_declaration(b'<?xml version="1.0"?><root>hello</root>', "test.xml")


def test_rejects_entity_declaration():
    payload = b'<?xml version="1.0"?><!DOCTYPE root [<!ENTITY lol "lol">]><root>&lol;</root>'
    with pytest.raises(ArtifactSecurityError) as exc_info:
        reject_xml_entity_declaration(payload, "test.xml")
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_rejects_bare_entity_tag_without_a_bracketed_doctype():
    """<!ENTITY on its own (no `<!DOCTYPE ... [` wrapper) is still rejected
    - the check doesn't assume a well-formed DOCTYPE, only that <!ENTITY
    appears at all, since that's the actual dangerous construct."""
    payload = b'<?xml version="1.0"?><!ENTITY lol "lol"><root>&lol;</root>'
    with pytest.raises(ArtifactSecurityError):
        reject_xml_entity_declaration(payload, "test.xml")


def test_doctype_without_a_bracketed_entity_block_is_not_rejected():
    """A plain external DTD reference (no `[...]` internal subset) isn't
    the entity-expansion shape this guards against - don't over-reject."""
    payload = b'<?xml version="1.0"?><!DOCTYPE html><root>hello</root>'
    reject_xml_entity_declaration(payload, "test.xml")


def test_reject_xml_entities_in_file(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_bytes(b'<?xml version="1.0"?><!DOCTYPE root [<!ENTITY lol "lol">]><root>&lol;</root>')
    with pytest.raises(ArtifactSecurityError):
        reject_xml_entities_in_file(path)


def test_reject_xml_entities_in_zip_scans_every_xml_member(tmp_path):
    """Issue #21: the whole point of this function is to catch a
    malicious XML part anywhere inside an OOXML zip, not just the first
    or most obvious one - use a member name that isn't alphabetically
    first to prove the scan doesn't stop early."""
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a_clean.xml", '<?xml version="1.0"?><root>fine</root>')
        zf.writestr(
            "z_last_member.xml",
            '<?xml version="1.0"?><!DOCTYPE root [<!ENTITY lol "lol">]><root>&lol;</root>',
        )
        zf.writestr("readme.txt", "not xml, should be ignored entirely")

    with pytest.raises(ArtifactSecurityError) as exc_info:
        reject_xml_entities_in_zip(path)
    assert "z_last_member.xml" in exc_info.value.evidence["source"]


def test_reject_xml_entities_in_zip_passes_a_clean_archive(tmp_path):
    path = tmp_path / "clean.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.xml", '<?xml version="1.0"?><root>fine</root>')
        zf.writestr("b.xml", '<?xml version="1.0"?><root>also fine</root>')
    reject_xml_entities_in_zip(path)  # must not raise


def test_reject_xml_entities_in_zip_scans_a_large_but_clean_member(tmp_path):
    """A legitimately huge XML part (a real worksheet/slide/document.xml
    can genuinely be tens of MB) must not be rejected just for being
    large - and, per the P1-2 fix below, is now actually scanned in full
    rather than skipped, so this also proves that's still fast enough not
    to matter (~0.2s for 200MB, measured directly - see the fix commit)."""
    path = tmp_path / "big.zip"
    huge_but_clean = b'<?xml version="1.0"?><root>' + b"x" * (11 * 1024 * 1024) + b"</root>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("huge.xml", huge_but_clean)
    reject_xml_entities_in_zip(path)  # must not raise


# --- P1-2 (Grok review): the scan had two real, confirmed bypasses -------
# (1) only the first 64KB of a document was searched, so an entity
#     declaration pushed past that offset (e.g. by a large leading XML
#     comment - entirely legal XML prolog content) went undetected; (2) a
#     zip member over 10MB was skipped entirely regardless of what it
#     contained, so a >10MB member with an entity declaration anywhere in
#     it - near the very start included - was never scanned at all. Both
#     reproduced directly against the pre-fix code before being fixed
#     (see this module's git history for the exact bypass payloads that
#     confirmed each).


def test_an_entity_declaration_pushed_past_the_old_64kb_window_is_still_caught(tmp_path):
    padding = b"<!-- " + b"A" * 70_000 + b" -->"
    payload = b'<?xml version="1.0"?>' + padding + b'<!DOCTYPE root [<!ENTITY xxe "pwned">]><root>&xxe;</root>'
    with pytest.raises(ArtifactSecurityError) as exc_info:
        reject_xml_entity_declaration(payload, "test.xml")
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_a_member_over_10mb_with_an_entity_declaration_is_still_caught(tmp_path):
    entity_decl = b'<?xml version="1.0"?><!DOCTYPE root [<!ENTITY xxe "pwned">]><root>'
    payload = entity_decl + b"X" * (11 * 1024 * 1024) + b"&xxe;</root>"
    path = tmp_path / "bypass2.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", payload)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        reject_xml_entities_in_zip(path)
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"


def test_a_rels_member_with_an_entity_declaration_is_still_caught(tmp_path):
    """Grok-review finding, verified by direct reproduction before this
    fix: OPC package relationship files (`_rels/.rels`, `xl/_rels/
    workbook.xml.rels`, etc. - every real OOXML package has at least one)
    are named `*.rels`, not `*.xml`. The old `.endswith(".xml")` filter
    silently skipped every one of them - confirmed exploitable end to
    end: an entity declaration injected into a real XLSX's `xl/_rels/
    workbook.xml.rels` was let through unmodified by this function, and
    `openpyxl.load_workbook()` then parsed the file without raising."""
    path = tmp_path / "hostile.xlsx"
    payload = b'<?xml version="1.0"?><!DOCTYPE root [<!ENTITY xxe "pwned">]><Relationships/>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/_rels/workbook.xml.rels", payload)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        reject_xml_entities_in_zip(path)
    assert exc_info.value.code == "ARTIFACT_XML_ENTITY_DECLARATION_REJECTED"
    assert "workbook.xml.rels" in exc_info.value.evidence["source"]


def test_doctype_internal_subset_marker_far_from_the_doctype_keyword_is_still_caught(tmp_path):
    """The internal-subset '[' search previously only looked 2048 bytes
    past '<!DOCTYPE' - exercised here directly (rather than as an
    independent bypass: in practice a '[' pushed this far out usually
    means '<!ENTITY' itself also falls outside the outer window, so this
    mostly collapses into the same 64KB case above once that's fixed)."""
    padding = b" " + b"A" * 3000  # a syntactically-tolerated stretch after the DOCTYPE keyword
    payload = b'<?xml version="1.0"?><!DOCTYPE root' + padding + b'[<!ENTITY xxe "pwned">]><root>&xxe;</root>'
    with pytest.raises(ArtifactSecurityError):
        reject_xml_entity_declaration(payload, "test.xml")

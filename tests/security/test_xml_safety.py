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


def test_reject_xml_entities_in_zip_skips_implausibly_large_members(tmp_path):
    """Not this function's job to flag a legitimately huge XML part as
    suspicious just for being large - only scans members small enough to
    plausibly be a real OOXML part (worksheet/slide/document.xml etc.)."""
    path = tmp_path / "big.zip"
    huge_but_clean = b'<?xml version="1.0"?><root>' + b"x" * (11 * 1024 * 1024) + b"</root>"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("huge.xml", huge_but_clean)
    reject_xml_entities_in_zip(path)  # must not raise (and must not hang scanning it)

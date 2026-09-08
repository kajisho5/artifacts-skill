"""Shared XML entity-expansion ("billion laughs") rejection (Issue #21).

Originated as SVG-only (`adapters/svg/adapter.py`'s `_reject_xml_entities()`,
since `xml.etree.ElementTree` — an `expat`-based parser — is not hardened
against entity-expansion DoS by default: a tiny file can decompress to
gigabytes in memory before parsing ever completes, which
`security/limits.py`'s plain file-size cap alone doesn't prevent).

Generalized here after actually auditing whether the same exposure exists
in PPTX/DOCX/XLSX's internal XML parsing (python-pptx/python-docx/
openpyxl), a question tracker issue #2 had left open since the first
external review pass. The audit was empirical, not just a source read:

- **python-pptx and python-docx: NOT vulnerable.** Both explicitly
  construct their `lxml.etree.XMLParser` with `resolve_entities=False`
  everywhere they parse OOXML XML (`pptx/oxml/__init__.py`,
  `docx/oxml/parser.py`, `docx/opc/oxml.py`) — confirmed by reading their
  installed source and by round-tripping a crafted `.pptx`/`.docx` with a
  DOCTYPE-declared entity through `Presentation()`/`Document()`: the
  entity is neither resolved into text nor left as literal escaped text,
  it is dropped from the extracted content entirely (lxml represents an
  unresolved entity reference as a non-text `Entity` node).
- **openpyxl: exposed, confirmed by direct testing.** `openpyxl.xml.
  functions` only swaps in the hardened `lxml` parser
  (`resolve_entities=False`) — or, failing that, `defusedxml` — when
  those packages happen to be importable; if neither is, it falls back to
  plain `xml.etree.ElementTree.fromstring`/`iterparse` with no guard at
  all. This project's own `xlsx` extra (`pyproject.toml`) pulls in
  neither `lxml` nor `defusedxml` — a `pip install -e ".[xlsx]"`-only
  install (a real, documented, minimal install path) gets zero
  entity-expansion protection from openpyxl by default. Confirmed
  exploitable: a crafted `xl/worksheets/sheet1.xml` with a DOCTYPE-declared
  entity had its value substituted into a real cell
  (`ws["A1"].value == "PWNED_VALUE"`), and a small nested-entity chain
  (10x10) amplified to its full expanded length exactly as the classic
  "billion laughs" pattern predicts — proving this isn't just entity
  substitution but genuine unbounded-amplification DoS exposure. A
  `SYSTEM "file://..."` external entity was *not* resolved (expat's
  default posture doesn't fetch external entities), so this is a DoS/
  data-integrity risk, not full XXE file disclosure.

The fix operates below the format-specific library entirely: scan every
`.xml` member of the OOXML zip for a DOCTYPE/ENTITY declaration and
refuse outright before python-pptx/python-docx/openpyxl ever gets a
chance to parse any of it. This is deliberately *not* implemented by
monkeypatching openpyxl's internal parser choice - that would be a
fragile dependency on its exact import structure, liable to silently stop
working on a version bump. A pre-scan at the zip level protects PPTX/DOCX
too (defense in depth, even though they're not currently exposed) without
assuming anything about how any of the three libraries parse XML
internally.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from artifact_skill.core.errors import ArtifactSecurityError

# Self-audit finding (FIX_PROMPT P1-2): this used to only scan the first
# 64KB of a document, and skip any zip member over 10MB entirely -
# confirmed exploitable both ways (a DOCTYPE/ENTITY declaration pushed
# past 64KB by a large-but-legal leading XML comment, or placed inside a
# >10MB member, went completely unscanned). Measured directly before
# removing both limits: scanning a full in-memory buffer for these two
# short literal substrings is cheap even at real-world scale (~0.2s for
# 200MB), so there is no meaningful cost to scanning every byte this
# function is ever handed instead of a bounded prefix - the caller (via
# `security/paths.py::check_input_size()`) already bounds how much data
# can reach here in the first place.


def reject_xml_entity_declaration(data: bytes, source: str) -> None:
    """Raise `ArtifactSecurityError` if `data` (an XML document's raw
    bytes) declares a DOCTYPE/ENTITY. Legitimate documents from any of
    the formats this project handles essentially never declare a custom
    DTD entity, so refusing outright — rather than attempting to parse
    and hoping the underlying parser's own limits save it — is a simple,
    safe default. Scans the entire input, not a bounded prefix — see this
    module's docstring for why that's fine performance-wise.
    """
    if b"<!ENTITY" in data or (b"<!DOCTYPE" in data and b"[" in data.split(b"<!DOCTYPE", 1)[1]):
        raise ArtifactSecurityError(
            code="ARTIFACT_XML_ENTITY_DECLARATION_REJECTED",
            message=f"'{source}' declares a DOCTYPE/ENTITY, which this adapter refuses to parse "
            "(entity-expansion DoS risk).",
            remediation="Remove the DOCTYPE/ENTITY declaration. Legitimate documents do not need one.",
            evidence={"source": source},
        )


def reject_xml_entities_in_file(path: Path) -> None:
    """Convenience wrapper for a single on-disk XML file (e.g. SVG)."""
    reject_xml_entity_declaration(path.read_bytes(), str(path))


def reject_xml_entities_in_zip(path: Path) -> None:
    """Scan every `.xml` member of an OOXML zip (`.pptx`/`.docx`/`.xlsx`)
    for a DOCTYPE/ENTITY declaration before the format-specific library
    gets a chance to parse any of them. See this module's docstring for
    which of the three libraries this is actually load-bearing for
    (openpyxl) versus defense in depth (python-pptx, python-docx).
    """
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if not info.filename.endswith(".xml"):
                continue
            reject_xml_entity_declaration(zf.read(info), f"{path}!{info.filename}")

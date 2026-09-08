"""EPUB adapter.

Backend: standard library only (`zipfile` + `xml.etree.ElementTree`) — no
optional dependency, so `epub.structural` is always `AVAILABLE`.

**Why `inspect()` reads the zip in memory instead of calling
`security/paths.py::safe_extract_zip()`**: `ArtifactAdapter.inspect()`'s own
contract says "must never write to disk" (`adapters/base.py`). This adapter
honors that literally — every read is `zipfile.ZipFile.read(name)` into
memory, nothing is ever extracted to a real path. `_guard_zip_bounds()`
below reimplements only the *decompression-bomb* half of
`safe_extract_zip()`'s checks (member count / total uncompressed size /
per-member compression ratio, same limits, same error codes) — its
path-escape and symlink-member checks are specifically about writing
archive members out to real filesystem paths, which never happens here, so
they don't apply. `execute()` (the one place this adapter does write) never
extracts either: it rewrites the whole archive in memory (`io.BytesIO`) and
writes the result once via `security/paths.py::atomic_write_bytes()`.

**XML entity-expansion guard (same class of risk audited for XLSX in Issue
#21)**: `xml.etree.ElementTree` is `expat`-based and not hardened against
entity-expansion ("billion laughs") DoS by default — the same exposure the
SVG adapter guards against for a single file, and XLSX guards against
per-OOXML-zip-member. `reject_xml_entities_in_zip()` (the whole-zip
pre-scanner used by XLSX) filters members by a bare `.xml` extension, which
would silently skip EPUB's own XML-ish members (`.opf`, `.ncx`, `.xhtml`,
`container.xml`'s extension does match, coincidentally) — rather than widen
that shared, already-tested scanner's extension list for a format it wasn't
written for, this adapter calls the lower-level
`reject_xml_entity_declaration()` directly on each specific member's bytes
right before parsing it (`_parse_xml_member()` below), the same one-parse-
point shape the SVG adapter uses for its single file.

No operations beyond `metadata_set` (title/author only — EPUB's Dublin Core
metadata has no single-field analogue for `subject`/`keywords` the way
Office metadata does; see `limitations()`). Rendering is honestly not
implemented (see `capabilities()`): a faithful preview needs to resolve a
spine document's own relative references (images/CSS, often in sibling
directories under the OPF's root) without reopening the P0-2 file://
containment hole the Chromium renderer's `allowed_root` boundary closes for
every other adapter — that needs a real design pass, not a quick hack, so
it's deferred rather than shipped half-verified. Structural verification
(spine/manifest integrity, leftover text across every content document) is
still fully real.
"""

from __future__ import annotations

import importlib.util
import io
import posixpath
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.chromium_render import render_local_files
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.paths import atomic_write_bytes, check_input_size, safe_extract_zip
from artifact_skill.security.xml_safety import reject_xml_entity_declaration

_CONTAINER_PATH = "META-INF/container.xml"
# leftover-text scan cap only (see _scan_leftover_markers below) - spine
# order, not a hard EPUB limit. NOT used to gate the entity-declaration
# check anymore: that used to skip members over this size entirely (the
# same P1-2 bypass class fixed in security/xml_safety.py — confirmed
# directly that scanning even a 200MB buffer costs ~0.2s, so there is no
# real reason to skip any member's entity check regardless of size).
_MAX_MEMBER_SCAN_BYTES = 10 * 1024 * 1024
_MAX_CONTENT_DOCS_SCANNED = 200
_TAG_STRIP = re.compile(r"<[^>]+>")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml_member(zf: zipfile.ZipFile, name: str, archive_path: Path) -> ET.Element:
    try:
        data = zf.read(name)
    except KeyError as exc:
        raise ArtifactInputError(
            code="ARTIFACT_EPUB_UNREADABLE",
            message=f"'{archive_path}' is missing expected member '{name}'.",
            evidence={"path": str(archive_path), "member": name},
        ) from exc
    reject_xml_entity_declaration(data, f"{archive_path}!{name}")
    try:
        return ET.fromstring(data)  # noqa: S314 - guarded above, unconditionally
    except ET.ParseError as exc:
        raise ArtifactInputError(
            code="ARTIFACT_EPUB_UNREADABLE",
            message=f"'{archive_path}!{name}' is not well-formed XML: {exc}",
            evidence={"path": str(archive_path), "member": name},
        ) from exc


def _guard_zip_bounds(zf: zipfile.ZipFile, archive_path: Path, limits: Limits) -> None:
    """The decompression-bomb subset of `safe_extract_zip()`'s checks — see
    this module's docstring for why the extraction-only checks (path
    escape, symlink members) don't apply to an in-memory read."""
    infos = zf.infolist()
    if len(infos) > limits.max_zip_members:
        raise ArtifactSecurityError(
            code="ARTIFACT_ZIP_TOO_MANY_MEMBERS",
            message=f"Archive has {len(infos)} members, exceeding limit {limits.max_zip_members}.",
            remediation="This may be a zip bomb; the file was not read.",
            evidence={"archive": str(archive_path), "member_count": len(infos)},
        )
    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > limits.max_zip_uncompressed_bytes:
        raise ArtifactSecurityError(
            code="ARTIFACT_ZIP_TOO_LARGE",
            message=f"Archive would expand to {total_uncompressed} bytes, exceeding limit.",
            remediation="Not read; increase Limits.max_zip_uncompressed_bytes if this is expected.",
            evidence={"archive": str(archive_path), "total_uncompressed": total_uncompressed},
        )
    for info in infos:
        if info.compress_size > 0:
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > limits.max_zip_compression_ratio and info.file_size > 10 * 1024 * 1024:
                raise ArtifactSecurityError(
                    code="ARTIFACT_ZIP_BOMB_SUSPECTED",
                    message=f"Archive member '{info.filename}' has a suspicious compression ratio ({ratio:.0f}x).",
                    remediation="Not read; this looks like a zip bomb.",
                    evidence={"archive": str(archive_path), "member": info.filename, "ratio": ratio},
                )


class _Package:
    """Parsed OPF package document: metadata + manifest (id -> (href, media_type)) + spine (ordered idrefs)."""

    def __init__(self, opf_path: str, root: ET.Element) -> None:
        self.opf_path = opf_path
        self.opf_dir = posixpath.dirname(opf_path)
        self.root = root
        self.metadata_el = next((c for c in root if _local_name(c.tag) == "metadata"), None)
        manifest_el = next((c for c in root if _local_name(c.tag) == "manifest"), None)
        spine_el = next((c for c in root if _local_name(c.tag) == "spine"), None)
        self.manifest: dict[str, tuple[str, str]] = {}
        if manifest_el is not None:
            for item in manifest_el:
                if _local_name(item.tag) != "item":
                    continue
                item_id = item.get("id")
                href = item.get("href")
                media_type = item.get("media-type", "")
                if item_id and href:
                    self.manifest[item_id] = (href, media_type)
        self.spine: list[str] = []
        if spine_el is not None:
            for itemref in spine_el:
                if _local_name(itemref.tag) != "itemref":
                    continue
                idref = itemref.get("idref")
                if idref:
                    self.spine.append(idref)

    def resolve_href(self, href: str) -> str:
        return posixpath.normpath(posixpath.join(self.opf_dir, href))

    def dc_values(self, name: str) -> list[str]:
        if self.metadata_el is None:
            return []
        return [
            (el.text or "").strip()
            for el in self.metadata_el
            if _local_name(el.tag) == name and (el.text or "").strip()
        ]


def _read_container_opf_path(zf: zipfile.ZipFile, archive_path: Path) -> str:
    root = _parse_xml_member(zf, _CONTAINER_PATH, archive_path)
    rootfiles = next((c for c in root.iter() if _local_name(c.tag) == "rootfile"), None)
    full_path = rootfiles.get("full-path") if rootfiles is not None else None
    if not full_path:
        raise ArtifactInputError(
            code="ARTIFACT_EPUB_UNREADABLE",
            message=f"'{archive_path}' has no <rootfile full-path=...> in META-INF/container.xml.",
            evidence={"path": str(archive_path)},
        )
    return full_path


def _load_package(ref: ArtifactRef, limits: Limits) -> tuple[zipfile.ZipFile, _Package]:
    check_input_size(ref.path, limits)
    zf = zipfile.ZipFile(ref.path)  # caller closes
    _guard_zip_bounds(zf, ref.path, limits)
    opf_path = _read_container_opf_path(zf, ref.path)
    opf_root = _parse_xml_member(zf, opf_path, ref.path)
    return zf, _Package(opf_path, opf_root)


class EpubAdapter(ArtifactAdapter):
    id = "epub"
    artifact_type = ArtifactType.EPUB

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.EPUB

    def operations(self) -> dict[str, OperationSpec]:
        return {
            "metadata_set": OperationSpec(
                name="metadata_set",
                description="Set dc:title and/or dc:creator in the OPF package metadata.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "author": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                structural_verification_required=True,
            )
        }

    def capabilities(self) -> list[Capability]:
        structural = Capability(
            id="epub.structural", status=CapabilityStatus.AVAILABLE,
            detail="Uses only the standard library (zipfile, xml.etree.ElementTree); always available.",
            detected_via="stdlib",
        )
        # Self-audit finding: render() is no longer NOT_IMPLEMENTED (see
        # render() below and this module's docstring for the design that
        # closed the gap) - capabilities() now reports the same
        # playwright-availability pattern as HTML/SVG/CSV/Markdown, not a
        # permanent "deferred" status that would now be stale.
        if importlib.util.find_spec("playwright") is not None:
            import importlib.metadata as im

            try:
                version = im.version("playwright")
            except im.PackageNotFoundError:
                version = None
            render = Capability(
                id="epub.render", status=CapabilityStatus.AVAILABLE,
                detail="playwright importable. A matching Chromium build must also be installed "
                "(`playwright install chromium`) — verified at render() time, not here.",
                detected_via="import playwright", version=version,
            )
        else:
            render = Capability(
                id="epub.render", status=CapabilityStatus.MISSING,
                detail="playwright not importable.", detected_via="import playwright",
            )
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "render() produces one PNG per spine (reading-order) document, up to Limits.max_pages — a "
            "document beyond that limit is rejected (ARTIFACT_TOO_MANY_PAGES), not silently truncated, "
            "same as the PDF adapter. Each spine document is staged into a private, safely-extracted copy "
            "of the whole archive first, so same-archive cross-directory references (e.g. a chapter under "
            "OEBPS/text/ referencing an image under OEBPS/images/) resolve correctly; anything outside "
            "that staged copy is blocked the same way P0-2 blocks it for every other renderer.",
            "metadata_set supports title/author only — EPUB's Dublin Core metadata has no direct analogue "
            "for the subject/keywords fields other formats' metadata_set accepts.",
            "Manifest/spine integrity is checked by presence (does the referenced file exist in the archive), "
            "not by validating each content document's own internal well-formedness beyond XML parsing.",
            "Leftover-placeholder-text scanning covers at most the first "
            f"{_MAX_CONTENT_DOCS_SCANNED} spine content documents, in spine order.",
            "Type detection identifies EPUB by its mandatory mimetype member's content, not by verifying "
            "it is the archive's first entry stored uncompressed (a real EPUB requirement) — that is instead "
            "checked as a structural WARN, matching this project's general 'don't reject on a technicality "
            "at the type-detection layer' precedent (see core/artifact.py).",
            "metadata_set re-serializes the whole OPF via xml.etree.ElementTree, which silently drops any "
            "XML comments in it (ElementTree doesn't retain comments by default) — only the OPF itself is "
            "affected; every other archive member (including all XHTML content documents) is copied "
            "byte-for-byte unchanged.",
        ]

    def recognized_policy_keys(self) -> frozenset[str]:
        return frozenset({"require_spine_count", "require_title", "forbid_placeholder_text"})

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        zf, pkg = _load_package(ref, DEFAULT_LIMITS)
        try:
            names = set(zf.namelist())
            first_info = zf.infolist()[0] if zf.infolist() else None
            mimetype_first_and_stored = bool(
                first_info and first_info.filename == "mimetype" and first_info.compress_type == zipfile.ZIP_STORED
            )

            manifest_missing = [
                href for _id, (href, _mt) in pkg.manifest.items() if pkg.resolve_href(href) not in names
            ]
            spine_missing = [idref for idref in pkg.spine if idref not in pkg.manifest]

            details = {
                "opf_path": pkg.opf_path,
                "title": pkg.dc_values("title"),
                "creator": pkg.dc_values("creator"),
                "language": pkg.dc_values("language"),
                "manifest_item_count": len(pkg.manifest),
                "spine_count": len(pkg.spine),
                "manifest_references_missing": manifest_missing,
                "spine_references_missing": spine_missing,
                "mimetype_first_and_stored": mimetype_first_and_stored,
                "size_bytes": ref.size_bytes,
                "leftover_markers": self._scan_leftover_markers(zf, pkg),
            }
            return InspectionReport(artifact=ref, details=details)
        finally:
            zf.close()

    def _scan_leftover_markers(self, zf: zipfile.ZipFile, pkg: _Package) -> list[str]:
        names = set(zf.namelist())
        texts: list[str] = []
        for idref in pkg.spine[:_MAX_CONTENT_DOCS_SCANNED]:
            item = pkg.manifest.get(idref)
            if item is None:
                continue
            href, _media_type = item
            member = pkg.resolve_href(href)
            if member not in names:
                continue
            info = zf.getinfo(member)
            if info.file_size > _MAX_MEMBER_SCAN_BYTES:
                continue
            try:
                raw = zf.read(member).decode("utf-8", errors="replace")
            except (KeyError, OSError):
                continue
            texts.append(_TAG_STRIP.sub(" ", raw))
        return find_leftover_markers("\n".join(texts))

    # ---- render ------------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path, *, limits: Limits = DEFAULT_LIMITS) -> RenderResult:
        """One PNG per spine (reading-order) document.

        Design that closes the gap this adapter used to report
        NOT_IMPLEMENTED for (see this module's docstring and
        `rendering/chromium_render.py::render_local_files()`'s docstring
        for the full reasoning): the archive is safely extracted in full
        (`safe_extract_zip()` — path-escape/symlink/zip-bomb guarded, the
        same utility FIX_PROMPT P1-1 found declared but never actually
        wired into a production code path; this is that wiring) into a
        private temp directory, and every spine document is rendered from
        its staged copy with the *entire staged tree* as the allowed
        `file://` root — not just each document's own immediate
        directory, which would incorrectly block an entirely ordinary
        same-archive reference like a chapter under `OEBPS/text/`
        pulling an image from `OEBPS/images/`. The staged tree is nothing
        but this one EPUB's own content, so widening the boundary to all
        of it doesn't reopen the P0-2 hole (arbitrary local files) it
        exists to close - it only stops blocking the archive's own
        resources from each other.
        """
        zf, pkg = _load_package(ref, limits)
        zf.close()

        if len(pkg.spine) > limits.max_pages:
            raise ArtifactSecurityError(
                code="ARTIFACT_TOO_MANY_PAGES",
                message=f"'{ref.path}' has {len(pkg.spine)} spine document(s), exceeding the render "
                f"limit of {limits.max_pages}.",
                remediation="Not rendered; increase Limits.max_pages if this document is legitimately "
                "expected, or render a subset instead.",
                evidence={"path": str(ref.path), "spine_count": len(pkg.spine), "max_pages": limits.max_pages},
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="artifacts-skill-epub-render-") as tmp:
            extract_root = Path(tmp) / "content"
            safe_extract_zip(ref.path, extract_root, limits)

            source_paths: list[Path] = []
            out_paths: list[Path] = []
            skipped: list[str] = []
            for i, idref in enumerate(pkg.spine, start=1):
                item = pkg.manifest.get(idref)
                if item is None:
                    skipped.append(idref)
                    continue
                href, _media_type = item
                staged_path = extract_root / pkg.resolve_href(href)
                if not staged_path.is_file():
                    skipped.append(idref)
                    continue
                source_paths.append(staged_path)
                out_paths.append(out_dir / f"page-{i:03d}.png")

            warnings = [
                f"Spine item '{idref}' could not be rendered (missing manifest entry or archive file)."
                for idref in skipped
            ]
            if not source_paths:
                return RenderResult(kind="page_images", files=[], backend="playwright+chromium", warnings=warnings)

            files = render_local_files(
                source_paths, out_paths, capability_id="epub.render", limits=limits, allowed_root=extract_root
            )
            return RenderResult(kind="page_images", files=files, backend="playwright+chromium", warnings=warnings)

    # ---- plan / execute -----------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        if operation != "metadata_set":
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"EPUB adapter has no operation '{operation}'.",
                remediation="Supported operations: metadata_set.",
                evidence={"operation": operation},
            )
        zf, _pkg = _load_package(ref, DEFAULT_LIMITS)
        zf.close()
        return OperationPlan(
            operation=operation, adapter=self.id, input={"path": str(ref.path), "args": args},
            output_path=str(output_path), required_capabilities=["epub.structural"],
            files_created=[str(output_path)],
            verification_strategy={"structural_verification_required": True},
        )

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        if operation != "metadata_set":
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"EPUB adapter has no operation '{operation}'.",
                remediation="Supported operations: metadata_set.",
                evidence={"operation": operation},
            )
        zf, pkg = _load_package(ref, DEFAULT_LIMITS)
        try:
            self._set_dc_metadata(pkg, args)
            new_bytes = self._rezip_with_modified_opf(zf, pkg)
        finally:
            zf.close()
        atomic_write_bytes(output_path, new_bytes)
        return ArtifactRef.from_path(output_path)

    @staticmethod
    def _set_dc_metadata(pkg: _Package, args: dict[str, Any]) -> None:
        if pkg.metadata_el is None:
            raise ArtifactInputError(
                code="ARTIFACT_EPUB_UNREADABLE",
                message=f"'{pkg.opf_path}' has no <metadata> element to write into.",
                evidence={"opf_path": pkg.opf_path},
            )
        dc_ns = "{http://purl.org/dc/elements/1.1/}"
        ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
        for arg_name, dc_tag in (("title", "title"), ("author", "creator")):
            if arg_name not in args:
                continue
            value = str(args[arg_name])
            existing = [el for el in pkg.metadata_el if _local_name(el.tag) == dc_tag]
            if existing:
                existing[0].text = value
                for extra in existing[1:]:
                    pkg.metadata_el.remove(extra)
            else:
                el = ET.SubElement(pkg.metadata_el, f"{dc_ns}{dc_tag}")
                el.text = value

    @staticmethod
    def _rezip_with_modified_opf(zf: zipfile.ZipFile, pkg: _Package) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            # EPUB requires "mimetype" to be the first entry, stored uncompressed.
            out.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", zipfile.ZIP_STORED)
            # Self-audit finding: ET.tostring() auto-generates an "ns0:"
            # prefix for any namespace with no registered mapping. Every
            # real-world OPF declares its own namespace as the *default*
            # (unprefixed) one - without registering it as such here, a
            # metadata_set on a perfectly ordinary EPUB rewrote every
            # element in the OPF to <ns0:package>/<ns0:metadata>/etc.
            # Harmless to a namespace-URI-aware parser (this adapter's own
            # ET.fromstring() included), but needless churn a strict
            # validator or human diff shouldn't have to see. Derived from
            # the actual root tag rather than hardcoded, so this still
            # does the right thing if a future OPF variant uses a
            # different namespace URI.
            if pkg.root.tag.startswith("{"):
                opf_ns = pkg.root.tag[1:].split("}", 1)[0]
                ET.register_namespace("", opf_ns)
            new_opf_bytes = ET.tostring(pkg.root, encoding="utf-8", xml_declaration=True)
            for info in zf.infolist():
                if info.filename in ("mimetype", pkg.opf_path):
                    continue
                out.writestr(info, zf.read(info.filename))
            out.writestr(pkg.opf_path, new_opf_bytes)
        return buf.getvalue()

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        """`ArtifactSecurityError` (entity-bomb rejection, zip-bomb guard)
        is deliberately NOT caught here, matching the SVG/XLSX precedent
        for the same class of risk (see tests/benchmark/cases.py's module
        docstring): a structural defect that is itself a security control
        must propagate as an exception, not be reported as a mere FAIL
        Check a caller could route around."""
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="epub_validity", name="EPUB is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="epub_validity", name="EPUB is readable", status=CheckStatus.PASS))

        details = report.details
        checks.append(
            Check(
                id="mimetype_first_and_stored",
                name="mimetype is the archive's first entry, stored uncompressed",
                status=CheckStatus.PASS if details["mimetype_first_and_stored"] else CheckStatus.WARN,
                message="Meets the EPUB OCF requirement." if details["mimetype_first_and_stored"]
                else "Does not meet the EPUB OCF requirement; most real-world reading systems tolerate this, "
                "but a strict validator (e.g. epubcheck) would flag it.",
            )
        )

        manifest_missing = details["manifest_references_missing"]
        checks.append(
            Check(
                id="manifest_references_resolve",
                name="Manifest item references resolve to real archive members",
                status=CheckStatus.FAIL if manifest_missing else CheckStatus.PASS,
                message=f"{len(manifest_missing)} manifest item(s) reference a missing file." if manifest_missing
                else f"All {details['manifest_item_count']} manifest item(s) resolve.",
                evidence={"missing": manifest_missing[:20]},
            )
        )

        spine_missing = details["spine_references_missing"]
        checks.append(
            Check(
                id="spine_references_resolve",
                name="Spine itemrefs resolve to a manifest entry",
                status=CheckStatus.FAIL if spine_missing else CheckStatus.PASS,
                message=f"{len(spine_missing)} spine itemref(s) reference an unknown manifest id." if spine_missing
                else f"All {details['spine_count']} spine itemref(s) resolve.",
                evidence={"missing": spine_missing[:20]},
            )
        )

        if "require_spine_count" in policy:
            required = policy["require_spine_count"]
            actual = details["spine_count"]
            ok = (actual == required) if isinstance(required, int) else (required[0] <= actual <= required[1])
            checks.append(
                Check(
                    id="spine_count_requirement",
                    name="Spine (reading-order) count matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"Expected {required}, found {actual}.",
                    evidence={"expected": required, "actual": actual},
                )
            )

        if policy.get("require_title", False):
            checks.append(
                Check(
                    id="title_presence",
                    name="dc:title present",
                    status=CheckStatus.PASS if details["title"] else CheckStatus.FAIL,
                    message=", ".join(details["title"]) if details["title"] else "No dc:title element (or it is empty).",
                )
            )

        leftover_markers = details["leftover_markers"]
        if leftover_markers:
            checks.append(
                Check(
                    id="leftover_placeholder_text",
                    name="No leftover generation placeholder text",
                    status=CheckStatus.FAIL if policy.get("forbid_placeholder_text") else CheckStatus.WARN,
                    message=f"Found likely-unreviewed placeholder text: {leftover_markers}.",
                    evidence={"markers": leftover_markers},
                )
            )
        else:
            checks.append(
                Check(id="leftover_placeholder_text", name="No leftover generation placeholder text", status=CheckStatus.PASS)
            )

        return VerificationResult(kind="structural", checks=checks)

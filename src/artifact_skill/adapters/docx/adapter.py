"""DOCX adapter.

Structural read/write is pure Python via `python-docx` (MIT), no external
binary. Rendering converts DOCX -> PDF with LibreOffice headless via the
shared `rendering/office_convert.py` (same helper the PPTX adapter uses —
see that module's docstring for the real LibreOffice-reliability caveat
this project discovered, which applies identically here), then rasterizes
with `rendering/pdf_pages.py`.

Honest gap this format forces on us: DOCX has no fixed page count in its
XML. Pagination depends on the layout engine (fonts, margins, the actual
rendering pass) — python-docx cannot tell you how many pages a document
will be, only Word (or LibreOffice) can, by actually laying it out. So
`verify_structural()` on its own reports `page_count` as `UNKNOWN`
unconditionally, with an explanation, rather than fabricating a number
from paragraph count or silently omitting the check — it never renders
itself, keeping the structural/visual separation this project insists on
(see docs/architecture.md) intact.

That said (Issue #14): a real page count *is* available after `render()`
succeeds, and `execute`/`receipt`'s lifecycle already renders for visual
evidence in the common case anyway. `refine_structural_with_render()`
(see `adapters/base.py`'s hook) upgrades that specific `page_count` check
from `UNKNOWN` to a `PASS` reporting the measured count — but only when a
render already happened as part of *this* lifecycle run, never by having
`verify_structural()` render on its own. A bare `verify` call (no
`execute`/`receipt`) still reports `UNKNOWN`, honestly, since nothing
rendered.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from typing import Any

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactInputError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.office_convert import convert_to_pdf, soffice_binary
from artifact_skill.rendering.pdf_pages import render_pdf_pages
from artifact_skill.security.paths import atomic_copy, check_input_size


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _require_docx():
    if not _has("docx"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="python-docx is not installed; DOCX structural read/write is unavailable.",
            remediation="Install with: pip install 'artifact-skill[docx]' (or `pip install python-docx`).",
            evidence={"capability_id": "docx.structural"},
        )
    import docx

    return docx


class DocxAdapter(ArtifactAdapter):
    id = "docx"
    artifact_type = ArtifactType.DOCX

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.DOCX

    def operations(self) -> dict[str, OperationSpec]:
        return {
            "metadata_set": OperationSpec(
                name="metadata_set",
                description="Set core document properties (title, author, subject, keywords).",
                args_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "author": {"type": "string"},
                        "subject": {"type": "string"},
                        "keywords": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output paragraph count == input paragraph count", "requested metadata fields match"],
            ),
        }

    def capabilities(self) -> list[Capability]:
        caps = []
        if _has("docx"):
            import importlib.metadata as im

            try:
                version = im.version("python-docx")
            except im.PackageNotFoundError:
                version = None
            caps.append(
                Capability(
                    id="docx.structural",
                    status=CapabilityStatus.AVAILABLE,
                    detail="python-docx importable: structural inspect/verify/metadata_set available.",
                    detected_via="import docx",
                    version=version,
                )
            )
        else:
            caps.append(
                Capability(id="docx.structural", status=CapabilityStatus.MISSING, detail="python-docx not importable.")
            )

        soffice = soffice_binary()
        if soffice:
            caps.append(
                Capability(
                    id="docx.render",
                    status=CapabilityStatus.AVAILABLE,
                    detail=f"LibreOffice found on PATH ({soffice}). See adapters/pptx/adapter.py's docstring: "
                    "a present binary does not guarantee a specific document converts successfully.",
                    detected_via="which soffice",
                )
            )
        else:
            caps.append(
                Capability(
                    id="docx.render",
                    status=CapabilityStatus.MISSING,
                    detail="No soffice/libreoffice binary found on PATH.",
                    detected_via="which soffice",
                )
            )
        return caps

    def limitations(self) -> list[str]:
        return [
            "Page count cannot be determined structurally (DOCX has no fixed pagination in its XML); "
            "it is UNKNOWN until render() actually lays the document out.",
            "Hyperlink validity (broken/dangling links) is not checked.",
            "Numbering/list consistency is not checked beyond basic package readability.",
            "Rendering depends on an external LibreOffice install; a present binary does not guarantee "
            "a specific document converts successfully.",
        ]

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        docx = _require_docx()
        warnings: list[str] = []
        try:
            document = docx.Document(str(ref.path))
        except Exception as exc:
            raise ArtifactInputError(
                code="ARTIFACT_DOCX_UNREADABLE",
                message=f"python-docx could not open '{ref.path}': {exc}",
                remediation="The file may be corrupt or not a valid DOCX despite its OOXML content type.",
                evidence={"path": str(ref.path)},
            ) from exc

        paragraph_count = len(document.paragraphs)
        all_text = "\n".join(p.text for p in document.paragraphs)
        text_paragraphs = sum(1 for p in document.paragraphs if p.text.strip())
        table_count = len(document.tables)

        broken_media: list[str] = []
        image_count = 0
        for rel_id, part in document.part.related_parts.items():
            if not part.content_type.startswith("image/"):
                continue
            image_count += 1
            try:
                _ = part.blob
            except Exception:  # noqa: BLE001 - broken media reference
                broken_media.append(rel_id)

        meta = document.core_properties
        metadata = {
            "title": meta.title or "",
            "author": meta.author or "",
            "subject": meta.subject or "",
            "keywords": meta.keywords or "",
        }

        section = document.sections[0] if document.sections else None
        details = {
            "paragraph_count": paragraph_count,
            "text_paragraphs": text_paragraphs,
            "table_count": table_count,
            "image_count": image_count,
            "broken_media": broken_media,
            "metadata": metadata,
            "page_width_emu": section.page_width if section else None,
            "page_height_emu": section.page_height if section else None,
            # The list of markers found, not the full document text itself -
            # inspect()'s output shouldn't balloon with (or leak) a large
            # document's entire body just to report this one signal.
            "leftover_markers": find_leftover_markers(all_text),
        }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan --------------------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        specs = self.operations()
        if operation not in specs:
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"DOCX adapter has no operation '{operation}'.",
                remediation=f"Supported operations: {sorted(specs)}.",
                evidence={"operation": operation},
            )
        spec = specs[operation]
        report = self.inspect(ref)
        risks: list[str] = []
        if report.details["broken_media"]:
            risks.append(f"{len(report.details['broken_media'])} embedded image(s) are unreadable.")

        return OperationPlan(
            operation=f"docx.{operation}",
            adapter=self.id,
            input=ref.to_dict(),
            output_path=str(output_path),
            required_capabilities=["docx.structural"] + (["docx.render"] if spec.render_required else []),
            files_touched=[],
            files_created=[str(output_path)],
            rendering_strategy="soffice -> PDF -> pypdfium2 page images" if spec.render_required else None,
            verification_strategy={
                "structural_required": spec.structural_verification_required,
                "visual_required": spec.visual_verification_required,
            },
            risks=risks,
            warnings=list(report.warnings),
        )

    # ---- execute -------------------------------------------------------

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        docx = _require_docx()
        if operation != "metadata_set":
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"DOCX adapter has no operation '{operation}'.",
                evidence={"operation": operation},
            )
        document = docx.Document(str(ref.path))
        meta = document.core_properties
        if "title" in args:
            meta.title = args["title"]
        if "author" in args:
            meta.author = args["author"]
        if "subject" in args:
            meta.subject = args["subject"]
        if "keywords" in args:
            meta.keywords = args["keywords"]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(output_path.parent), suffix=".docx.tmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            document.save(str(tmp_path))
            atomic_copy(tmp_path, output_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return ArtifactRef.from_path(output_path)

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path) -> RenderResult:
        with tempfile.TemporaryDirectory(prefix="artifact-skill-docx-render-") as tmp:
            pdf_path = convert_to_pdf(ref.path, Path(tmp) / "pdf")
            return render_pdf_pages(pdf_path, out_dir)

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="docx_validity", name="DOCX is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="docx_validity", name="DOCX is readable", status=CheckStatus.PASS))

        details = report.details
        paragraph_count = details["paragraph_count"]

        checks.append(
            Check(
                id="paragraph_count",
                name="Paragraph count",
                status=CheckStatus.FAIL if paragraph_count == 0 else CheckStatus.PASS,
                message=f"{paragraph_count} paragraph(s).",
                evidence={"paragraph_count": paragraph_count},
            )
        )

        if "require_paragraph_count" in policy:
            expected = policy["require_paragraph_count"]
            ok = paragraph_count == expected
            checks.append(
                Check(
                    id="paragraph_count_requirement",
                    name="Paragraph count matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {expected}, got {paragraph_count}.",
                )
            )

        if "min_paragraphs" in policy or "max_paragraphs" in policy:
            lo = policy.get("min_paragraphs", 0)
            hi = policy.get("max_paragraphs", float("inf"))
            ok = lo <= paragraph_count <= hi
            checks.append(
                Check(
                    id="paragraph_count_range",
                    name="Paragraph count within range",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected [{lo}, {hi}], got {paragraph_count}.",
                )
            )

        if details["broken_media"]:
            checks.append(
                Check(
                    id="broken_media",
                    name="No broken media references",
                    status=CheckStatus.FAIL,
                    message=f"{len(details['broken_media'])} embedded image(s) are unreadable.",
                    evidence={"relationship_ids": details["broken_media"]},
                )
            )
        else:
            checks.append(Check(id="broken_media", name="No broken media references", status=CheckStatus.PASS))

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

        if paragraph_count > 0:
            if details["text_paragraphs"] == 0:
                checks.append(
                    Check(
                        id="text_presence",
                        name="Text is present",
                        status=CheckStatus.WARN,
                        message="No paragraph has any non-whitespace text.",
                    )
                )
            else:
                checks.append(
                    Check(
                        id="text_presence",
                        name="Text is present",
                        status=CheckStatus.PASS,
                        message=f"{details['text_paragraphs']}/{paragraph_count} paragraphs have text.",
                    )
                )

        if "require_metadata" in policy:
            for key, expected_value in policy["require_metadata"].items():
                actual = details["metadata"].get(key.lower())
                ok = actual == expected_value
                checks.append(
                    Check(
                        id=f"metadata_{key.lower()}",
                        name=f"Metadata field '{key}'",
                        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                        message=f"expected '{expected_value}', got '{actual}'.",
                    )
                )

        checks.append(
            Check(
                id="page_count",
                name="Page count",
                status=CheckStatus.UNKNOWN,
                message="DOCX has no fixed page count in its XML — pagination depends on layout (fonts, "
                "margins, the actual rendering pass). Call render() to get a real page count from the "
                "resulting PDF; this is a structural check and cannot determine it without rendering.",
            )
        )

        return VerificationResult(kind="structural", checks=checks)

    # ---- refine with render (Issue #14) -------------------------------

    def refine_structural_with_render(
        self, structural: VerificationResult, render: RenderResult | None
    ) -> VerificationResult:
        """`page_count` is honestly `UNKNOWN` from `verify_structural()`
        alone (see this module's docstring) — but `execute`/`receipt`'s
        lifecycle already renders for visual evidence when rendering is
        available, and that render's page count *is* a real, measured fact
        (just not a format-intrinsic one). Swap the `UNKNOWN` check for a
        `PASS` reporting that measured count, clearly labeled as
        render-derived rather than structural, when a render with at least
        one page actually happened this run. Leaves `structural` untouched
        (including the case where render failed or wasn't attempted) —
        this is upgrade-only, never a guess.
        """
        if render is None or not render.files:
            return structural
        new_checks = [
            Check(
                id="page_count",
                name="Page count (measured via render)",
                status=CheckStatus.PASS,
                message=f"{len(render.files)} page(s), as measured by this machine's LibreOffice-based render "
                "pipeline just now. Not a structural (format-intrinsic) fact — a different machine's "
                "LibreOffice version/fonts could measure a different count for the same input.",
                evidence={"page_count": len(render.files), "measured_via": render.backend},
            )
            if c.id == "page_count"
            else c
            for c in structural.checks
        ]
        return VerificationResult(
            kind=structural.kind,
            checks=new_checks,
            evidence_files=structural.evidence_files,
            inspected_by=structural.inspected_by,
        )

"""PPTX adapter.

Structural read/write is pure Python via `python-pptx` (MIT), no external
binary. Rendering converts PPTX -> PDF with LibreOffice headless, then
reuses `rendering/pdf_pages.py` (the same page-rasterizer the PDF adapter
uses) instead of reimplementing PNG export — see docs/adapters.md.

Honesty note on `pptx.render`: `capabilities()` reports it AVAILABLE when
the `soffice` binary is found on PATH, the same way `pdf.render` reports
AVAILABLE when `pypdfium2` imports successfully. Neither guarantees every
input converts cleanly — a binary being present is not a guarantee it can
process a *specific* file. `render()` itself is where a real failure (a
missing import filter, a broken LibreOffice profile, malformed input) gets
surfaced, as a structured `ARTIFACT_RENDER_BACKEND_FAILED` error carrying
soffice's own stdout/stderr, never a crash and never a silently-empty
result. This distinction was not theoretical: developing this adapter
against a LibreOffice install that launches but fails to load *any* input
file is exactly the failure mode this design accounts for.
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
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.paths import atomic_copy, check_input_size

_EMU_PER_INCH = 914400


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _require_pptx():
    if not _has("pptx"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="python-pptx is not installed; PPTX structural read/write is unavailable.",
            remediation="Install with: pip install 'artifacts-skill[pptx]' (or `pip install python-pptx`).",
            evidence={"capability_id": "pptx.structural"},
        )
    import pptx

    return pptx


class PptxAdapter(ArtifactAdapter):
    id = "pptx"
    artifact_type = ArtifactType.PPTX

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.PPTX

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
                postconditions=["output slide count == input slide count", "requested metadata fields match"],
            ),
            "strip_placeholders": OperationSpec(
                name="strip_placeholders",
                description="Remove every placeholder shape left empty (no text ever entered) — the fixer "
                "counterpart to the empty_placeholders structural check.",
                args_schema={"type": "object", "properties": {}, "additionalProperties": False},
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output slide count == input slide count", "empty_placeholders == 0"],
                known_limitations=[
                    "Removes the placeholder shape entirely (not just its prompt text) — a layout that "
                    "relies on that placeholder's position/formatting for a later fill-in will lose it. "
                    "This is a one-way structural edit, not a hide/show toggle."
                ],
            ),
        }

    def capabilities(self) -> list[Capability]:
        caps = []
        if _has("pptx"):
            import importlib.metadata as im

            try:
                version = im.version("python-pptx")
            except im.PackageNotFoundError:
                version = None
            caps.append(
                Capability(
                    id="pptx.structural",
                    status=CapabilityStatus.AVAILABLE,
                    detail="python-pptx importable: structural inspect/verify/metadata_set available.",
                    detected_via="import pptx",
                    version=version,
                )
            )
        else:
            caps.append(
                Capability(id="pptx.structural", status=CapabilityStatus.MISSING, detail="python-pptx not importable.")
            )

        soffice = soffice_binary()
        if soffice:
            caps.append(
                Capability(
                    id="pptx.render",
                    status=CapabilityStatus.AVAILABLE,
                    detail=f"LibreOffice found on PATH ({soffice}). A specific document may still fail to "
                    "convert for environment reasons (missing filters/fonts); render() surfaces that as "
                    "ARTIFACT_RENDER_BACKEND_FAILED with soffice's own diagnostic output, not silently.",
                    detected_via="which soffice",
                )
            )
        else:
            caps.append(
                Capability(
                    id="pptx.render",
                    status=CapabilityStatus.MISSING,
                    detail="No soffice/libreoffice binary found on PATH.",
                    detected_via="which soffice",
                )
            )
        return caps

    def limitations(self) -> list[str]:
        return [
            "Chart validity is only checked for presence (UNKNOWN), not internal correctness.",
            "'Leftover placeholder' detection is a heuristic (empty title/body placeholders) and can "
            "false-positive on intentionally blank section-header slides.",
            "Embedded font completeness is not checked.",
            "Rendering depends on an external LibreOffice install; a present binary does not guarantee "
            "a specific document converts successfully (see this module's docstring).",
        ]

    def recognized_policy_keys(self) -> frozenset[str]:
        return frozenset({
            "require_slide_count", "min_slides", "max_slides", "require_slide_aspect_ratio",
            "aspect_ratio_tolerance", "forbid_placeholder_text", "max_empty_placeholders", "require_metadata",
        })

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        pptx = _require_pptx()
        warnings: list[str] = []
        try:
            prs = pptx.Presentation(str(ref.path))
        except Exception as exc:
            raise ArtifactInputError(
                code="ARTIFACT_PPTX_UNREADABLE",
                message=f"python-pptx could not open '{ref.path}': {exc}",
                remediation="The file may be corrupt or not a valid PPTX despite its OOXML content type.",
                evidence={"path": str(ref.path)},
            ) from exc

        # Safe to import now: _require_pptx() above already confirmed
        # python-pptx is installed (this module never imports it at
        # module level, so PDF-only usage never needs it on PATH at all).
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        slide_count = len(prs.slides)
        empty_placeholders = 0
        broken_media: list[str] = []
        text_bearing_slides = 0
        has_chart = False
        has_notes = 0
        all_text_parts: list[str] = []

        for i, slide in enumerate(prs.slides):
            slide_has_text = False
            for shape in slide.shapes:
                if getattr(shape, "has_chart", False):
                    has_chart = True
                if shape.has_text_frame and shape.text_frame.text.strip():
                    slide_has_text = True
                    all_text_parts.append(shape.text_frame.text)
                if shape.is_placeholder and shape.has_text_frame and not shape.text_frame.text.strip():
                    empty_placeholders += 1
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        _ = shape.image.blob
                    except Exception:  # noqa: BLE001 - broken media reference
                        broken_media.append(f"slide {i + 1}: {shape.shape_id}")
            if slide_has_text:
                text_bearing_slides += 1
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                has_notes += 1

        meta = prs.core_properties
        metadata = {
            "title": meta.title or "",
            "author": meta.author or "",
            "subject": meta.subject or "",
            "keywords": meta.keywords or "",
        }

        details = {
            "slide_count": slide_count,
            "slide_width_emu": prs.slide_width,
            "slide_height_emu": prs.slide_height,
            "slide_width_in": round(prs.slide_width / _EMU_PER_INCH, 3) if prs.slide_width else None,
            "slide_height_in": round(prs.slide_height / _EMU_PER_INCH, 3) if prs.slide_height else None,
            "metadata": metadata,
            "empty_placeholders": empty_placeholders,
            "broken_media": broken_media,
            "text_bearing_slides": text_bearing_slides,
            "has_chart": has_chart,
            "slides_with_notes": has_notes,
            "leftover_markers": find_leftover_markers("\n".join(all_text_parts)),
        }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan --------------------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        specs = self.operations()
        if operation not in specs:
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"PPTX adapter has no operation '{operation}'.",
                remediation=f"Supported operations: {sorted(specs)}.",
                evidence={"operation": operation},
            )
        spec = specs[operation]
        report = self.inspect(ref)
        risks: list[str] = []
        if report.details["broken_media"]:
            risks.append(f"{len(report.details['broken_media'])} shape(s) reference unreadable media.")

        return OperationPlan(
            operation=f"pptx.{operation}",
            adapter=self.id,
            input=ref.to_dict(),
            output_path=str(output_path),
            required_capabilities=["pptx.structural"] + (["pptx.render"] if spec.render_required else []),
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
        pptx = _require_pptx()
        if operation not in ("metadata_set", "strip_placeholders"):
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"PPTX adapter has no operation '{operation}'.",
                evidence={"operation": operation},
            )
        prs = pptx.Presentation(str(ref.path))

        if operation == "metadata_set":
            meta = prs.core_properties
            if "title" in args:
                meta.title = args["title"]
            if "author" in args:
                meta.author = args["author"]
            if "subject" in args:
                meta.subject = args["subject"]
            if "keywords" in args:
                meta.keywords = args["keywords"]
        else:  # strip_placeholders
            for slide in prs.slides:
                for shape in list(slide.placeholders):
                    if shape.has_text_frame and not shape.text_frame.text.strip():
                        shape._element.getparent().remove(shape._element)

        # python-pptx only writes to a real filesystem path, not a byte
        # buffer we control atomically like the PDF adapter does — so save
        # to a private temp file, then move that into place atomically to
        # preserve the "no partial output" guarantee (docs/security.md).
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(output_path.parent), suffix=".pptx.tmp", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            prs.save(str(tmp_path))
            atomic_copy(tmp_path, output_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return ArtifactRef.from_path(output_path)

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path, *, limits: Limits = DEFAULT_LIMITS) -> RenderResult:
        with tempfile.TemporaryDirectory(prefix="artifacts-skill-pptx-render-") as tmp:
            pdf_path = convert_to_pdf(ref.path, Path(tmp) / "pdf", limits=limits)
            # render_pdf_pages needs the intermediate PDF to survive past
            # this `with` block's cleanup, so render directly from it now
            # rather than returning a path that's about to be deleted.
            return render_pdf_pages(pdf_path, out_dir)

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="pptx_validity", name="PPTX is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="pptx_validity", name="PPTX is readable", status=CheckStatus.PASS))

        details = report.details
        slide_count = details["slide_count"]

        checks.append(
            Check(
                id="slide_count",
                name="Slide count",
                status=CheckStatus.FAIL if slide_count == 0 else CheckStatus.PASS,
                message=f"{slide_count} slide(s).",
                evidence={"slide_count": slide_count},
            )
        )

        if "require_slide_count" in policy:
            expected = policy["require_slide_count"]
            ok = slide_count == expected
            checks.append(
                Check(
                    id="slide_count_requirement",
                    name="Slide count matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {expected}, got {slide_count}.",
                    evidence={"expected": expected, "actual": slide_count},
                )
            )

        if "min_slides" in policy or "max_slides" in policy:
            lo = policy.get("min_slides", 0)
            hi = policy.get("max_slides", float("inf"))
            ok = lo <= slide_count <= hi
            checks.append(
                Check(
                    id="slide_count_range",
                    name="Slide count within range",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected [{lo}, {hi}], got {slide_count}.",
                )
            )

        if "require_slide_aspect_ratio" in policy and details["slide_width_in"] and details["slide_height_in"]:
            expected_ratio = policy["require_slide_aspect_ratio"]
            tolerance = policy.get("aspect_ratio_tolerance", 0.02)
            actual_ratio = details["slide_width_in"] / details["slide_height_in"]
            ok = abs(actual_ratio - expected_ratio) <= tolerance
            checks.append(
                Check(
                    id="slide_aspect_ratio",
                    name="Slide aspect ratio matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {expected_ratio:.4f} (+/-{tolerance}), got {actual_ratio:.4f} "
                    f"({details['slide_width_in']}in x {details['slide_height_in']}in).",
                    evidence={"expected_ratio": expected_ratio, "actual_ratio": round(actual_ratio, 4)},
                )
            )

        if details["broken_media"]:
            checks.append(
                Check(
                    id="broken_media",
                    name="No broken media references",
                    status=CheckStatus.FAIL,
                    message=f"{len(details['broken_media'])} shape(s) reference unreadable media.",
                    evidence={"shapes": details["broken_media"]},
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

        max_empty = policy.get("max_empty_placeholders", 0)
        if details["empty_placeholders"] > max_empty:
            checks.append(
                Check(
                    id="empty_placeholders",
                    name="No leftover empty placeholders",
                    status=CheckStatus.WARN,
                    message=f"{details['empty_placeholders']} empty placeholder(s) found "
                    f"(allowed: {max_empty}). This is a heuristic — a blank section-header slide "
                    "can trigger it legitimately.",
                    evidence={"count": details["empty_placeholders"]},
                )
            )
        else:
            checks.append(Check(id="empty_placeholders", name="No leftover empty placeholders", status=CheckStatus.PASS))

        if slide_count > 0:
            if details["text_bearing_slides"] == 0:
                checks.append(
                    Check(
                        id="text_presence",
                        name="Text is present",
                        status=CheckStatus.WARN,
                        message="No slide has any text content.",
                    )
                )
            else:
                checks.append(
                    Check(
                        id="text_presence",
                        name="Text is present",
                        status=CheckStatus.PASS,
                        message=f"{details['text_bearing_slides']}/{slide_count} slides have text.",
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
                id="chart_validity",
                name="Chart validity",
                status=CheckStatus.UNKNOWN if details["has_chart"] else CheckStatus.SKIPPED,
                message="Deck contains chart(s); internal chart data validity is not checked by this adapter."
                if details["has_chart"] else "No charts present.",
            )
        )

        return VerificationResult(kind="structural", checks=checks)

"""PDF adapter.

Backends, chosen deliberately over PyMuPDF for licensing reasons (see
docs/research.md #5): structural read/write via `pypdf` (BSD-3-Clause),
rendering via `pypdfium2` (Apache-2.0/BSD dual, PDFium bindings, no external
binary required — keeps this adapter local-first with nothing to `apt-get
install`). Both are optional dependencies; capabilities() reports honestly
if either is missing rather than pretending the adapter fully works.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.rendering.pdf_pages import render_pdf_pages
from artifact_skill.security.paths import atomic_write_bytes, check_input_size

_PT_PER_INCH = 72.0


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _require_pypdf():
    if not _has("pypdf"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="pypdf is not installed; PDF structural read/write is unavailable.",
            remediation="Install with: pip install 'artifact-skill[pdf]' (or `pip install pypdf`).",
            evidence={"capability_id": "pdf.structural"},
        )
    import pypdf

    return pypdf


def _require_pypdfium2():
    if not _has("pypdfium2"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="pypdfium2 is not installed; PDF rendering is unavailable.",
            remediation="Install with: pip install 'artifact-skill[pdf]' (or `pip install pypdfium2`).",
            evidence={"capability_id": "pdf.render"},
        )
    import pypdfium2

    return pypdfium2


class PdfAdapter(ArtifactAdapter):
    id = "pdf"
    artifact_type = ArtifactType.PDF

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.PDF

    def operations(self) -> dict[str, OperationSpec]:
        return {
            "metadata_set": OperationSpec(
                name="metadata_set",
                description="Set document metadata fields (title, author, subject, keywords).",
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
                postconditions=["output page count == input page count", "requested metadata fields match"],
            ),
            "merge": OperationSpec(
                name="merge",
                description="Merge one or more additional PDFs after the input PDF, in order.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "additional_inputs": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    },
                    "required": ["additional_inputs"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=True,
                render_required=True,
                postconditions=["output page count == sum of input page counts"],
                known_limitations=["Does not attempt to de-duplicate shared fonts across inputs."],
            ),
        }

    def capabilities(self) -> list[Capability]:
        caps = []
        if _has("pypdf"):
            import pypdf

            caps.append(
                Capability(
                    id="pdf.structural",
                    status=CapabilityStatus.AVAILABLE,
                    detail="pypdf importable: structural inspect/verify/metadata_set/merge available.",
                    detected_via="import pypdf",
                    version=getattr(pypdf, "__version__", None),
                )
            )
        else:
            caps.append(
                Capability(
                    id="pdf.structural",
                    status=CapabilityStatus.MISSING,
                    detail="pypdf not importable.",
                    detected_via="import pypdf",
                )
            )
        if _has("pypdfium2"):
            import importlib.metadata as im

            try:
                version = im.version("pypdfium2")
            except im.PackageNotFoundError:
                version = None
            caps.append(
                Capability(
                    id="pdf.render",
                    status=CapabilityStatus.AVAILABLE,
                    detail="pypdfium2 importable: page-image rendering available.",
                    detected_via="import pypdfium2",
                    version=version,
                )
            )
        else:
            caps.append(
                Capability(
                    id="pdf.render",
                    status=CapabilityStatus.MISSING,
                    detail="pypdfium2 not importable; visual verification unavailable for PDF.",
                    detected_via="import pypdfium2",
                )
            )
        return caps

    def limitations(self) -> list[str]:
        return [
            "Font embedding completeness is not checked (reported as UNKNOWN).",
            "Encrypted PDFs are detected but not decrypted automatically.",
            "JavaScript actions are detected but not executed or analyzed further.",
        ]

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        pypdf = _require_pypdf()
        warnings: list[str] = []
        try:
            reader = pypdf.PdfReader(str(ref.path))
        except Exception as exc:  # noqa: BLE001 - surface as structured input error
            raise ArtifactInputError(
                code="ARTIFACT_PDF_UNREADABLE",
                message=f"pypdf could not open '{ref.path}': {exc}",
                remediation="The file may be corrupt or not a valid PDF despite its %PDF- header.",
                evidence={"path": str(ref.path)},
            ) from exc

        is_encrypted = reader.is_encrypted
        page_sizes: list[dict[str, float]] = []
        page_count = 0
        if not is_encrypted:
            page_count = len(reader.pages)
            for page in reader.pages:
                box = page.mediabox
                page_sizes.append(
                    {
                        "width_pt": round(float(box.width), 2),
                        "height_pt": round(float(box.height), 2),
                        "width_in": round(float(box.width) / _PT_PER_INCH, 3),
                        "height_in": round(float(box.height) / _PT_PER_INCH, 3),
                    }
                )
        else:
            warnings.append("Document is encrypted; page-level inspection was skipped.")

        meta = {}
        if not is_encrypted and reader.metadata:
            for key, value in reader.metadata.items():
                meta[key.lstrip("/")] = str(value)

        has_javascript = False
        has_forms = False
        if not is_encrypted:
            try:
                root = reader.trailer["/Root"]
                names = root.get("/Names")
                has_javascript = bool(names and "/JavaScript" in names)
            except Exception:  # noqa: BLE001 - best-effort detection, never fatal
                has_javascript = False
            try:
                has_forms = "/AcroForm" in reader.trailer["/Root"]
            except Exception:  # noqa: BLE001
                has_forms = False

        text_extractable_pages = 0
        if not is_encrypted:
            for page in reader.pages:
                try:
                    if page.extract_text().strip():
                        text_extractable_pages += 1
                except Exception:  # noqa: BLE001 - a single bad page must not abort inspect
                    pass

        details = {
            "page_count": page_count,
            "page_sizes": page_sizes,
            "is_encrypted": is_encrypted,
            "metadata": meta,
            "has_javascript": has_javascript,
            "has_forms": has_forms,
            "text_extractable_pages": text_extractable_pages,
            "pdf_version": getattr(reader, "pdf_header", None),
        }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan --------------------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        specs = self.operations()
        if operation not in specs:
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"PDF adapter has no operation '{operation}'.",
                remediation=f"Supported operations: {sorted(specs)}.",
                evidence={"operation": operation},
            )
        spec = specs[operation]
        report = self.inspect(ref)
        risks: list[str] = []
        warnings: list[str] = list(report.warnings)
        if report.details["is_encrypted"]:
            risks.append("Input PDF is encrypted; this operation may fail or produce an incomplete result.")

        files_touched: list[str] = []
        if operation == "merge":
            extra = args.get("additional_inputs", [])
            for p in extra:
                extra_path = Path(p)
                if not extra_path.is_file():
                    raise ArtifactInputError(
                        code="ARTIFACT_INPUT_NOT_FOUND",
                        message=f"Additional merge input does not exist: {extra_path}",
                        evidence={"path": str(extra_path)},
                    )
                files_touched.append(str(extra_path))

        return OperationPlan(
            operation=f"pdf.{operation}",
            adapter=self.id,
            input=ref.to_dict(),
            output_path=str(output_path),
            required_capabilities=["pdf.structural"] + (["pdf.render"] if spec.render_required else []),
            files_touched=files_touched,
            files_created=[str(output_path)],
            rendering_strategy="pypdfium2 page images" if spec.render_required else None,
            verification_strategy={
                "structural_required": spec.structural_verification_required,
                "visual_required": spec.visual_verification_required,
            },
            risks=risks,
            warnings=warnings,
        )

    # ---- execute -------------------------------------------------------

    def execute(
        self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path
    ) -> ArtifactRef:
        pypdf = _require_pypdf()
        if operation == "metadata_set":
            reader = pypdf.PdfReader(str(ref.path))
            if reader.is_encrypted:
                raise ArtifactExecutionError(
                    code="ARTIFACT_PDF_ENCRYPTED",
                    message="Cannot set metadata on an encrypted PDF without decrypting it first.",
                    evidence={"path": str(ref.path)},
                )
            writer = pypdf.PdfWriter()
            writer.append(reader)
            field_map = {"title": "/Title", "author": "/Author", "subject": "/Subject", "keywords": "/Keywords"}
            update = {field_map[k]: v for k, v in args.items() if k in field_map}
            writer.add_metadata(update)
            _write_pdf(writer, output_path)
        elif operation == "merge":
            reader = pypdf.PdfReader(str(ref.path))
            if reader.is_encrypted:
                raise ArtifactExecutionError(
                    code="ARTIFACT_PDF_ENCRYPTED",
                    message="Cannot merge an encrypted PDF without decrypting it first.",
                    evidence={"path": str(ref.path)},
                )
            writer = pypdf.PdfWriter()
            writer.append(reader)
            for extra in args.get("additional_inputs", []):
                extra_reader = pypdf.PdfReader(str(extra))
                if extra_reader.is_encrypted:
                    raise ArtifactExecutionError(
                        code="ARTIFACT_PDF_ENCRYPTED",
                        message=f"Cannot merge encrypted PDF: {extra}",
                        evidence={"path": str(extra)},
                    )
                writer.append(extra_reader)
            _write_pdf(writer, output_path)
        else:
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"PDF adapter has no operation '{operation}'.",
                evidence={"operation": operation},
            )
        return ArtifactRef.from_path(output_path)

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path) -> RenderResult:
        return render_pdf_pages(ref.path, out_dir)

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="pdf_validity", name="PDF is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="pdf_validity", name="PDF is readable", status=CheckStatus.PASS))

        details = report.details
        page_count = details["page_count"]

        if details["is_encrypted"]:
            checks.append(
                Check(
                    id="encryption",
                    name="Document encryption",
                    status=CheckStatus.FAIL if policy.get("require_no_encryption") else CheckStatus.WARN,
                    message="Document is encrypted; most structural checks were skipped.",
                )
            )
            return VerificationResult(kind="structural", checks=checks)

        checks.append(
            Check(
                id="page_count",
                name="Page count",
                status=CheckStatus.FAIL if page_count == 0 else CheckStatus.PASS,
                message=f"{page_count} page(s).",
                evidence={"page_count": page_count},
            )
        )

        if "require_page_count" in policy:
            expected = policy["require_page_count"]
            ok = page_count == expected
            checks.append(
                Check(
                    id="page_count_requirement",
                    name="Page count matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {expected}, got {page_count}.",
                    evidence={"expected": expected, "actual": page_count},
                )
            )

        if "min_pages" in policy or "max_pages" in policy:
            lo = policy.get("min_pages", 0)
            hi = policy.get("max_pages", float("inf"))
            ok = lo <= page_count <= hi
            checks.append(
                Check(
                    id="page_count_range",
                    name="Page count within range",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected [{lo}, {hi}], got {page_count}.",
                    evidence={"min_pages": lo, "max_pages": hi, "actual": page_count},
                )
            )

        sizes = {(s["width_pt"], s["height_pt"]) for s in details["page_sizes"]}
        if len(sizes) > 1 and not policy.get("allow_mixed_page_sizes", False):
            checks.append(
                Check(
                    id="page_size_consistency",
                    name="Page sizes are consistent",
                    status=CheckStatus.WARN,
                    message=f"Found {len(sizes)} distinct page sizes.",
                    evidence={"distinct_sizes": [list(s) for s in sizes]},
                )
            )
        else:
            checks.append(Check(id="page_size_consistency", name="Page sizes are consistent", status=CheckStatus.PASS))

        if "require_page_size_pt" in policy and details["page_sizes"]:
            expected_w, expected_h = policy["require_page_size_pt"]
            tolerance = policy.get("page_size_tolerance_pt", 1.0)
            first = details["page_sizes"][0]
            ok = abs(first["width_pt"] - expected_w) <= tolerance and abs(first["height_pt"] - expected_h) <= tolerance
            checks.append(
                Check(
                    id="page_size_requirement",
                    name="Page size matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected ({expected_w}, {expected_h})pt, got "
                    f"({first['width_pt']}, {first['height_pt']})pt.",
                )
            )

        if details["has_javascript"]:
            checks.append(
                Check(
                    id="javascript",
                    name="No embedded JavaScript",
                    status=CheckStatus.FAIL if policy.get("forbid_javascript", True) else CheckStatus.WARN,
                    message="Document contains embedded JavaScript actions.",
                )
            )
        else:
            checks.append(Check(id="javascript", name="No embedded JavaScript", status=CheckStatus.PASS))

        if page_count > 0:
            extractable_ratio = details["text_extractable_pages"] / page_count
            if extractable_ratio == 0:
                checks.append(
                    Check(
                        id="text_extractable",
                        name="Text is extractable",
                        status=CheckStatus.WARN,
                        message="No page yielded extractable text; document may be a scanned image without OCR.",
                    )
                )
            else:
                checks.append(
                    Check(
                        id="text_extractable",
                        name="Text is extractable",
                        status=CheckStatus.PASS,
                        message=f"{details['text_extractable_pages']}/{page_count} pages have extractable text.",
                    )
                )

        if "require_metadata" in policy:
            for key, expected_value in policy["require_metadata"].items():
                actual = details["metadata"].get(key)
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
                id="font_embedding",
                name="Font embedding",
                status=CheckStatus.UNKNOWN,
                message="Font embedding completeness is not checked by this adapter (see limitations()).",
            )
        )

        return VerificationResult(kind="structural", checks=checks)


def _write_pdf(writer: Any, output_path: Path) -> None:
    import io

    buf = io.BytesIO()
    writer.write(buf)
    atomic_write_bytes(output_path, buf.getvalue())

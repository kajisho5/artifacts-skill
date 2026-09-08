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
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.pdf_pages import render_pdf_pages
from artifact_skill.security.paths import atomic_write_bytes, check_input_size

_PT_PER_INCH = 72.0

# The 14 base fonts every PDF-conformant viewer is required to render
# correctly without an embedded font program (PDF spec Annex D). A font
# with one of these base names is not a defect when it isn't embedded —
# everything else is, because there is no guarantee the viewer has a
# matching font to substitute.
_STANDARD_14_FONTS = {
    "Courier", "Courier-Bold", "Courier-BoldOblique", "Courier-Oblique",
    "Helvetica", "Helvetica-Bold", "Helvetica-BoldOblique", "Helvetica-Oblique",
    "Times-Roman", "Times-Bold", "Times-BoldItalic", "Times-Italic",
    "Symbol", "ZapfDingbats",
}


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


def _collect_font_info(reader: Any) -> list[dict[str, Any]]:
    """One entry per distinct font resource referenced across all pages:
    `{name, subtype, embedded, is_standard14}`. `embedded` is determined
    by the presence of a `FontFile`/`FontFile2`/`FontFile3` stream on the
    font's `/FontDescriptor` — for a composite (`Type0`) font, that
    descriptor lives on the descendant font, not the Type0 wrapper itself.
    A font is only ever counted once (by its object id), so a font used
    on every page of a long document doesn't get repeated entries.
    """
    seen_ids: set[int] = set()
    fonts: list[dict[str, Any]] = []
    for page in reader.pages:
        resources = page.get("/Resources")
        if not resources:
            continue
        font_dict = resources.get("/Font")
        if not font_dict:
            continue
        for font_ref in font_dict.values():
            try:
                font_id = font_ref.idnum
            except AttributeError:
                font_id = id(font_ref)
            if font_id in seen_ids:
                continue
            seen_ids.add(font_id)
            font = font_ref.get_object()
            base_font = str(font.get("/BaseFont", "")).lstrip("/")
            subtype = str(font.get("/Subtype", "")).lstrip("/")

            descriptor = font.get("/FontDescriptor")
            if descriptor is None and subtype == "Type0":
                # Composite font: the real glyph data (and thus the
                # embedding evidence) lives on the descendant font.
                descendants = font.get("/DescendantFonts")
                if descendants:
                    try:
                        descriptor = descendants[0].get_object().get("/FontDescriptor")
                    except Exception:  # noqa: BLE001 - malformed descendant, treat as no descriptor
                        descriptor = None

            embedded = False
            if descriptor is not None:
                desc_obj = descriptor.get_object()
                embedded = any(key in desc_obj for key in ("/FontFile", "/FontFile2", "/FontFile3"))

            base_name = base_font.split("+", 1)[1] if "+" in base_font and len(base_font.split("+", 1)[0]) == 6 else base_font
            fonts.append(
                {
                    "name": base_font,
                    "subtype": subtype,
                    "embedded": embedded,
                    "is_standard14": base_name in _STANDARD_14_FONTS,
                }
            )
    return fonts


def _font_embedding_check(fonts: list[dict[str, Any]], policy: dict[str, Any]) -> Check:
    """PASS when every non-standard-14 font is embedded; standard-14 fonts
    are exempt since every conformant PDF viewer guarantees a correct
    rendering for them without an embedded font program. Missing embedding
    on anything else is `FAIL` when `policy["forbid_unembedded_fonts"]` is
    set, `WARN` otherwise — a non-embedded custom font usually still
    *displays* something (via viewer substitution), just not reliably the
    intended glyphs, so it's a real but not always fatal problem.
    """
    if not fonts:
        return Check(id="font_embedding", name="Font embedding", status=CheckStatus.PASS, message="No fonts referenced.")

    missing = [f for f in fonts if not f["embedded"] and not f["is_standard14"]]
    if not missing:
        return Check(
            id="font_embedding", name="Font embedding", status=CheckStatus.PASS,
            message=f"{len(fonts)} font(s) referenced; all embedded or standard-14.",
        )
    status = CheckStatus.FAIL if policy.get("forbid_unembedded_fonts") else CheckStatus.WARN
    names = [f["name"] for f in missing]
    return Check(
        id="font_embedding", name="Font embedding", status=status,
        message=f"{len(missing)} non-standard font(s) not embedded: {names}. Rendering may substitute a "
        "different font on a viewer without a matching one installed.",
        evidence={"unembedded_fonts": names},
    )


def _require_positive_page_size(args: dict[str, Any]) -> tuple[float, float]:
    try:
        width_pt = float(args["width_pt"])
        height_pt = float(args["height_pt"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message="fit_page_size requires numeric 'width_pt' and 'height_pt'.",
            evidence={"args": args},
        ) from exc
    if width_pt <= 0 or height_pt <= 0:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message=f"fit_page_size requires positive dimensions, got ({width_pt}, {height_pt}).",
            evidence={"width_pt": width_pt, "height_pt": height_pt},
        )
    return width_pt, height_pt


def _resolve_page_indices(pages_1indexed: Any, page_count: int, operation: str) -> list[int]:
    """Validate a 1-indexed 'pages' arg against the document's real page
    count and convert it to 0-indexed positions, preserving the caller's
    order (so extract_pages can reorder/repeat pages, not just subset
    them). Raises a structured error naming the exact bad value rather
    than letting pypdf raise a raw IndexError deep inside execute()."""
    if not isinstance(pages_1indexed, list) or not pages_1indexed:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message=f"{operation} requires a non-empty 'pages' list of 1-indexed page numbers.",
            evidence={"pages": pages_1indexed},
        )
    zero_indexed: list[int] = []
    for p in pages_1indexed:
        if not isinstance(p, int) or isinstance(p, bool) or p < 1 or p > page_count:
            raise ArtifactInputError(
                code="ARTIFACT_INVALID_ARGS",
                message=f"{operation}: page {p!r} is out of range — this document has {page_count} "
                "page(s), 1-indexed.",
                evidence={"pages": pages_1indexed, "page_count": page_count, "invalid_page": p},
            )
        zero_indexed.append(p - 1)
    return zero_indexed


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
            "fit_page_size": OperationSpec(
                name="fit_page_size",
                description="Scale every page's content and media box to an exact target size, in points.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "width_pt": {"type": "number", "exclusiveMinimum": 0},
                        "height_pt": {"type": "number", "exclusiveMinimum": 0},
                    },
                    "required": ["width_pt", "height_pt"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output page count == input page count"],
                known_limitations=[
                    "Scaling is non-uniform (width and height are stretched independently to hit the "
                    "target size exactly); it does not preserve aspect ratio on its own. Pass a "
                    "target that already matches the input's aspect ratio to avoid distortion."
                ],
            ),
            "extract_pages": OperationSpec(
                name="extract_pages",
                description="Produce a new PDF containing only the given 1-indexed pages, in the given order "
                "(so this can also reorder or repeat pages).",
                args_schema={
                    "type": "object",
                    "properties": {
                        "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1},
                    },
                    "required": ["pages"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output page count == len(pages)"],
            ),
            "delete_pages": OperationSpec(
                name="delete_pages",
                description="Produce a new PDF with the given 1-indexed pages removed; every other page is "
                "kept in its original order.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1},
                    },
                    "required": ["pages"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output page count == input page count - len(set(pages))"],
            ),
            "rotate_pages": OperationSpec(
                name="rotate_pages",
                description="Rotate the given 1-indexed pages (all pages if 'pages' is omitted) by a multiple "
                "of 90 degrees, clockwise for a positive value.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1},
                        "degrees": {"type": "integer", "enum": [90, 180, 270, -90, -180, -270]},
                    },
                    "required": ["degrees"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output page count == input page count"],
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
            "Encrypted PDFs are detected but not decrypted automatically.",
            "JavaScript actions are detected but not executed or analyzed further.",
            "blank_pages flags a page with neither extractable text nor an embedded image; a page of pure "
            "vector graphics (lines/shapes only) is a false positive this check cannot distinguish from a "
            "genuinely blank page.",
        ]

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        pypdf = _require_pypdf()
        warnings: list[str] = []
        try:
            reader = pypdf.PdfReader(str(ref.path))
        except Exception as exc:
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
        blank_pages: list[int] = []
        all_text_parts: list[str] = []
        if not is_encrypted:
            for i, page in enumerate(reader.pages):
                page_text = ""
                try:
                    page_text = page.extract_text()
                except Exception:  # noqa: BLE001, S110 - a single bad page must not abort inspect
                    pass
                has_text = bool(page_text.strip())
                if has_text:
                    text_extractable_pages += 1
                    all_text_parts.append(page_text)
                has_images = False
                try:
                    has_images = len(page.images) > 0
                except Exception:  # noqa: BLE001, S110 - same: a bad page's image list must not abort inspect
                    pass
                if not has_text and not has_images:
                    blank_pages.append(i + 1)

        fonts = [] if is_encrypted else _collect_font_info(reader)

        details = {
            "page_count": page_count,
            "page_sizes": page_sizes,
            "is_encrypted": is_encrypted,
            "metadata": meta,
            "has_javascript": has_javascript,
            "has_forms": has_forms,
            "text_extractable_pages": text_extractable_pages,
            "blank_pages": blank_pages,
            "pdf_version": getattr(reader, "pdf_header", None),
            "fonts": fonts,
            # Same rationale as the other adapters' leftover_markers: the
            # marker list found, not the full extracted text of every page.
            "leftover_markers": find_leftover_markers("\n".join(all_text_parts)),
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
        elif operation == "fit_page_size":
            _require_positive_page_size(args)
        elif operation in ("extract_pages", "delete_pages"):
            _resolve_page_indices(args.get("pages"), report.details["page_count"], operation)
        elif operation == "rotate_pages":
            if args.get("pages") is not None:
                _resolve_page_indices(args["pages"], report.details["page_count"], operation)

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
        elif operation == "fit_page_size":
            width_pt, height_pt = _require_positive_page_size(args)
            reader = pypdf.PdfReader(str(ref.path))
            if reader.is_encrypted:
                raise ArtifactExecutionError(
                    code="ARTIFACT_PDF_ENCRYPTED",
                    message="Cannot resize an encrypted PDF without decrypting it first.",
                    evidence={"path": str(ref.path)},
                )
            writer = pypdf.PdfWriter()
            writer.append(reader)
            for page in writer.pages:
                page.scale_to(width_pt, height_pt)
            _write_pdf(writer, output_path)
        elif operation == "extract_pages":
            reader = pypdf.PdfReader(str(ref.path))
            if reader.is_encrypted:
                raise ArtifactExecutionError(
                    code="ARTIFACT_PDF_ENCRYPTED",
                    message="Cannot extract pages from an encrypted PDF without decrypting it first.",
                    evidence={"path": str(ref.path)},
                )
            indices = _resolve_page_indices(args.get("pages"), len(reader.pages), operation)
            writer = pypdf.PdfWriter()
            for i in indices:
                writer.add_page(reader.pages[i])
            _write_pdf(writer, output_path)
        elif operation == "delete_pages":
            reader = pypdf.PdfReader(str(ref.path))
            if reader.is_encrypted:
                raise ArtifactExecutionError(
                    code="ARTIFACT_PDF_ENCRYPTED",
                    message="Cannot delete pages from an encrypted PDF without decrypting it first.",
                    evidence={"path": str(ref.path)},
                )
            page_count = len(reader.pages)
            to_remove = set(_resolve_page_indices(args.get("pages"), page_count, operation))
            if len(to_remove) == page_count:
                raise ArtifactInputError(
                    code="ARTIFACT_INVALID_ARGS",
                    message="delete_pages would remove every page, leaving a 0-page PDF.",
                    evidence={"pages": args.get("pages"), "page_count": page_count},
                )
            writer = pypdf.PdfWriter()
            for i in range(page_count):
                if i not in to_remove:
                    writer.add_page(reader.pages[i])
            _write_pdf(writer, output_path)
        elif operation == "rotate_pages":
            degrees = args.get("degrees")
            if degrees not in (90, 180, 270, -90, -180, -270):
                raise ArtifactInputError(
                    code="ARTIFACT_INVALID_ARGS",
                    message=f"rotate_pages requires 'degrees' to be one of 90/180/270/-90/-180/-270, got {degrees!r}.",
                    evidence={"degrees": degrees},
                )
            reader = pypdf.PdfReader(str(ref.path))
            if reader.is_encrypted:
                raise ArtifactExecutionError(
                    code="ARTIFACT_PDF_ENCRYPTED",
                    message="Cannot rotate pages in an encrypted PDF without decrypting it first.",
                    evidence={"path": str(ref.path)},
                )
            page_count = len(reader.pages)
            pages_arg = args.get("pages")
            rotate_indices = (
                set(_resolve_page_indices(pages_arg, page_count, operation))
                if pages_arg is not None
                else set(range(page_count))
            )
            writer = pypdf.PdfWriter()
            writer.append(reader)
            for i in rotate_indices:
                writer.pages[i].rotate(degrees)
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
                    # width_pt/height_pt here (not a nested pair) is what
                    # PdfAdapter.fix() reads to build corrected fit_page_size
                    # args — keep this shape stable, it's a fixer contract.
                    evidence={
                        "expected_width_pt": expected_w,
                        "expected_height_pt": expected_h,
                        "actual_width_pt": first["width_pt"],
                        "actual_height_pt": first["height_pt"],
                    },
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

            blank_pages = details.get("blank_pages", [])
            if blank_pages:
                checks.append(
                    Check(
                        id="blank_pages",
                        name="No structurally blank pages",
                        status=CheckStatus.FAIL if policy.get("forbid_blank_pages") else CheckStatus.WARN,
                        message=f"{len(blank_pages)}/{page_count} page(s) have neither extractable text nor "
                        f"an embedded image (page(s) {blank_pages}). A page of pure vector graphics (lines/"
                        "shapes with no text or raster image) is a known false positive this check can't "
                        "distinguish from a genuinely blank page — look at the rendered evidence to be sure.",
                        evidence={"blank_pages": blank_pages},
                    )
                )
            else:
                checks.append(Check(id="blank_pages", name="No structurally blank pages", status=CheckStatus.PASS))

            leftover_markers = details.get("leftover_markers", [])
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

        checks.append(_font_embedding_check(details.get("fonts", []), policy))

        return VerificationResult(kind="structural", checks=checks)

    # ---- fix -----------------------------------------------------------

    def fix(
        self, ref: ArtifactRef, operation: str, args: dict[str, Any], failed_result: VerificationResult
    ) -> dict[str, Any] | None:
        """The one real fixer this adapter has: `fit_page_size` re-run at
        whatever size `verify_structural`'s `page_size_requirement` check
        (driven by `policy["require_page_size_pt"]`) actually expected.

        This covers exactly one failure shape — the caller asked
        `fit_page_size` to scale to a size that turns out not to satisfy a
        separately-configured page-size policy (e.g. a unit mix-up, points
        vs. inches) — and nothing else. Any other structural failure on
        this operation (or any failure on a different operation) has no
        obvious safe correction here, so this returns None for it, per
        spec #16: no fixer is better than a fake one.
        """
        if operation != "fit_page_size":
            return None
        check = next((c for c in failed_result.checks if c.id == "page_size_requirement"), None)
        if check is None or check.status != CheckStatus.FAIL or not check.evidence:
            return None
        expected_w = check.evidence.get("expected_width_pt")
        expected_h = check.evidence.get("expected_height_pt")
        if expected_w is None or expected_h is None:
            return None
        if args.get("width_pt") == expected_w and args.get("height_pt") == expected_h:
            # Already targeting the expected size and still failing (e.g. a
            # rounding edge past the policy's tolerance) — retrying with the
            # same args would loop pointlessly. Honest "can't fix this" exit.
            return None
        return {"width_pt": expected_w, "height_pt": expected_h}


def _write_pdf(writer: Any, output_path: Path) -> None:
    import io

    buf = io.BytesIO()
    writer.write(buf)
    atomic_write_bytes(output_path, buf.getvalue())

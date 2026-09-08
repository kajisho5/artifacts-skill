"""Image adapter (PNG / JPEG / WebP).

One adapter implementation covers all three raster formats — Pillow (MIT,
already a hard dependency via the `pdf` extra's `pypdfium2`-adjacent
rendering path) treats them uniformly, and there's no meaningful
structural difference in what this adapter checks between them. It's
registered for three `ArtifactType`s via
`adapters/registry.py::register_for_types()`, not three separate adapter
classes — see `core/contract.py::_registered_adapter_ids()` for why the
contract's capability ids are `image.*`, not `image/png.*` etc.

No external binary: unlike the Office-family adapters (PPTX/DOCX/XLSX),
this one has nothing analogous to a LibreOffice dependency — Pillow reads
and writes all three formats natively.
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
from artifact_skill.security.paths import atomic_copy, check_input_size

_IMAGE_TYPES = {ArtifactType.IMAGE_PNG, ArtifactType.IMAGE_JPEG, ArtifactType.IMAGE_WEBP}
_FORMAT_BY_TYPE = {
    ArtifactType.IMAGE_PNG: "PNG",
    ArtifactType.IMAGE_JPEG: "JPEG",
    ArtifactType.IMAGE_WEBP: "WEBP",
}
# Pillow's EXIF orientation tag (0x0112) — any value other than 1 (or
# absent) means the stored pixel dimensions don't match the intended
# upright display orientation until a viewer applies the rotation/flip.
_EXIF_ORIENTATION_TAG = 274


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _require_pillow():
    if not _has("PIL"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="Pillow is not installed; image inspect/verify/operations are unavailable.",
            remediation="Install with: pip install 'artifact-skill[pdf]' (or `pip install Pillow`).",
            evidence={"capability_id": "image.structural"},
        )
    from PIL import Image

    return Image


class ImageAdapter(ArtifactAdapter):
    id = "image"
    artifact_type = ArtifactType.IMAGE_PNG  # nominal; also registered for JPEG/WebP, see registry.py

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type in _IMAGE_TYPES

    def operations(self) -> dict[str, OperationSpec]:
        return {
            "resize": OperationSpec(
                name="resize",
                description="Resize to the given pixel dimensions.",
                args_schema={
                    "type": "object",
                    "properties": {
                        "width": {"type": "integer", "minimum": 1},
                        "height": {"type": "integer", "minimum": 1},
                        "maintain_aspect_ratio": {"type": "boolean", "default": True},
                    },
                    "required": ["width", "height"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output dimensions match request (exactly, or within the fitted box)"],
            ),
            "convert_format": OperationSpec(
                name="convert_format",
                description="Re-save as a different image format (png, jpeg, or webp).",
                args_schema={
                    "type": "object",
                    "properties": {"format": {"type": "string", "enum": ["png", "jpeg", "webp"]}},
                    "required": ["format"],
                    "additionalProperties": False,
                },
                structural_verification_required=True,
                visual_verification_required=False,
                render_required=False,
                postconditions=["output format matches request"],
                known_limitations=[
                    "Converting to JPEG drops any alpha channel, compositing onto a white background.",
                    "The default output path keeps the input's extension; pass --output explicitly with "
                    "the new extension, or the saved file's content format and its filename extension "
                    "will disagree (the content itself is always the requested format).",
                ],
            ),
        }

    def capabilities(self) -> list[Capability]:
        if _has("PIL"):
            import importlib.metadata as im

            try:
                version = im.version("Pillow")
            except im.PackageNotFoundError:
                version = None
            detail = "Pillow importable: inspect/verify/resize/convert_format/render all available."
            structural = Capability(
                id="image.structural", status=CapabilityStatus.AVAILABLE, detail=detail,
                detected_via="import PIL", version=version,
            )
            render = Capability(
                id="image.render", status=CapabilityStatus.AVAILABLE, detail=detail,
                detected_via="import PIL", version=version,
            )
        else:
            structural = Capability(id="image.structural", status=CapabilityStatus.MISSING, detail="Pillow not importable.")
            render = Capability(id="image.render", status=CapabilityStatus.MISSING, detail="Pillow not importable.")
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "Animated images (APNG/animated WebP) are inspected by their first frame only; "
            "operations do not preserve additional frames.",
            "ICC color profiles are preserved on save where Pillow supports it, but not validated.",
        ]

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        Image = _require_pillow()
        warnings: list[str] = []
        try:
            with Image.open(ref.path) as img:
                img.verify()
            # verify() invalidates the file handle; a fresh open is required
            # to actually read pixel data / metadata afterwards.
            img = Image.open(ref.path)
        except Exception as exc:
            raise ArtifactInputError(
                code="ARTIFACT_IMAGE_UNREADABLE",
                message=f"Pillow could not open '{ref.path}': {exc}",
                remediation="The file may be corrupt or not a valid image despite its detected type.",
                evidence={"path": str(ref.path)},
            ) from exc

        with img:
            width, height = img.size
            exif = img.getexif() if hasattr(img, "getexif") else {}
            orientation = exif.get(_EXIF_ORIENTATION_TAG) if exif else None
            is_animated = bool(getattr(img, "is_animated", False))
            n_frames = int(getattr(img, "n_frames", 1))
            details = {
                "width": width,
                "height": height,
                "format": img.format,
                "mode": img.mode,
                "exif_orientation": orientation,
                "is_animated": is_animated,
                "frame_count": n_frames,
            }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan --------------------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        specs = self.operations()
        if operation not in specs:
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"Image adapter has no operation '{operation}'.",
                remediation=f"Supported operations: {sorted(specs)}.",
                evidence={"operation": operation},
            )
        spec = specs[operation]
        report = self.inspect(ref)
        risks: list[str] = []
        if operation == "convert_format" and args.get("format") == "jpeg" and report.details["mode"] in ("RGBA", "LA", "P"):
            risks.append("Source has an alpha channel; converting to JPEG will composite it onto white.")

        return OperationPlan(
            operation=f"image.{operation}",
            adapter=self.id,
            input=ref.to_dict(),
            output_path=str(output_path),
            required_capabilities=["image.structural"],
            files_touched=[],
            files_created=[str(output_path)],
            rendering_strategy=None,
            verification_strategy={
                "structural_required": spec.structural_verification_required,
                "visual_required": spec.visual_verification_required,
            },
            risks=risks,
            warnings=list(report.warnings),
        )

    # ---- execute -------------------------------------------------------

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        Image = _require_pillow()
        if operation not in self.operations():
            raise ArtifactInputError(
                code="ARTIFACT_OPERATION_UNKNOWN",
                message=f"Image adapter has no operation '{operation}'.",
                evidence={"operation": operation},
            )

        with Image.open(ref.path) as img:
            img.load()
            if operation == "resize":
                width, height = args["width"], args["height"]
                if args.get("maintain_aspect_ratio", True):
                    fitted = img.copy()
                    fitted.thumbnail((width, height), Image.LANCZOS)
                    result_img = fitted
                else:
                    result_img = img.resize((width, height), Image.LANCZOS)
                save_kwargs: dict[str, Any] = {}
                save_format = img.format
            elif operation == "convert_format":
                target = args["format"].upper()
                result_img = img
                if target == "JPEG" and img.mode in ("RGBA", "LA", "P"):
                    background = Image.new("RGB", img.size, (255, 255, 255))
                    rgba = img.convert("RGBA")
                    background.paste(rgba, mask=rgba.split()[-1])
                    result_img = background
                save_format = target
                save_kwargs = {}
            else:  # pragma: no cover - guarded above
                raise AssertionError(operation)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_suffix = f"{output_path.suffix or '.img'}.tmp"
            with tempfile.NamedTemporaryFile(
                dir=str(output_path.parent), suffix=tmp_suffix, delete=False
            ) as tmp:
                tmp_path = Path(tmp.name)
            try:
                result_img.save(str(tmp_path), format=save_format, **save_kwargs)
                atomic_copy(tmp_path, output_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        return ArtifactRef.from_path(output_path)

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path) -> RenderResult:
        """An image is already visual — "rendering" here means producing a
        normalized, upright PNG preview (EXIF-transposed) for evidence,
        the same role render() plays for every other adapter."""
        Image = _require_pillow()
        from PIL import ImageOps

        out_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(ref.path) as img:
            img.load()
            normalized = ImageOps.exif_transpose(img)
            out_path = out_dir / "page-001.png"
            normalized.convert("RGBA" if normalized.mode in ("RGBA", "LA", "P") else "RGB").save(out_path, format="PNG")
        return RenderResult(kind="page_images", files=[out_path], backend="Pillow")

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="image_validity", name="Image is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="image_validity", name="Image is readable", status=CheckStatus.PASS))

        details = report.details
        width, height = details["width"], details["height"]

        checks.append(
            Check(
                id="dimensions",
                name="Dimensions",
                status=CheckStatus.PASS,
                message=f"{width}x{height}px, {details['format']}, mode {details['mode']}.",
                evidence={"width": width, "height": height},
            )
        )

        if "require_width" in policy:
            ok = width == policy["require_width"]
            checks.append(
                Check(
                    id="width_requirement", name="Width matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {policy['require_width']}, got {width}.",
                )
            )
        if "require_height" in policy:
            ok = height == policy["require_height"]
            checks.append(
                Check(
                    id="height_requirement", name="Height matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {policy['require_height']}, got {height}.",
                )
            )
        if any(k in policy for k in ("min_width", "max_width", "min_height", "max_height")):
            lo_w, hi_w = policy.get("min_width", 0), policy.get("max_width", float("inf"))
            lo_h, hi_h = policy.get("min_height", 0), policy.get("max_height", float("inf"))
            ok = lo_w <= width <= hi_w and lo_h <= height <= hi_h
            checks.append(
                Check(
                    id="dimensions_range", name="Dimensions within range",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected width [{lo_w}, {hi_w}], height [{lo_h}, {hi_h}]; got {width}x{height}.",
                )
            )

        if "require_format" in policy:
            expected = policy["require_format"].upper()
            ok = details["format"] == expected
            checks.append(
                Check(
                    id="format_requirement", name="Format matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"expected {expected}, got {details['format']}.",
                )
            )

        orientation = details["exif_orientation"]
        if orientation is not None and orientation != 1:
            checks.append(
                Check(
                    id="exif_orientation",
                    name="No pending EXIF rotation",
                    status=CheckStatus.WARN,
                    message=f"EXIF orientation tag is {orientation} (not 1/normal) — the stored {width}x{height} "
                    "pixel grid does not match the intended upright display orientation until a viewer "
                    "applies the rotation/flip. render() corrects this in its output; the raw file does not.",
                    evidence={"exif_orientation": orientation},
                )
            )
        else:
            checks.append(Check(id="exif_orientation", name="No pending EXIF rotation", status=CheckStatus.PASS))

        return VerificationResult(kind="structural", checks=checks)

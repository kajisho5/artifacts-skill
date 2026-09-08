"""SVG adapter.

Structural inspection uses only the standard library
(`xml.etree.ElementTree`) — no optional dependency, so `svg.structural` is
always `AVAILABLE`, the same design as the HTML adapter
(`adapters/html/adapter.py`), which this adapter otherwise mirrors closely:
same "no mutating operations" reasoning (SVG's natural edit is markup),
same render backend (a bare `<svg>` root loads fine as its own page in
Chromium, so `rendering/chromium_render.py` — extracted from the HTML
adapter for exactly this reuse — needs no SVG-specific handling), same
active network-blocking at render time.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactInputError, ArtifactSecurityError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.chromium_render import render_local_file
from artifact_skill.security.paths import check_input_size

_XLINK_NS = "{http://www.w3.org/1999/xlink}href"
_REFERENCING_TAGS = {"image", "use", "script"}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _reject_xml_entities(path: Path) -> None:
    """SVG is XML, and `xml.etree.ElementTree` (like most `expat`-based
    parsers) is not hardened against entity-expansion DoS ("billion
    laughs") — a tiny file can decompress to gigabytes in memory before
    parsing ever completes, which `security/limits.py`'s file-size cap
    alone doesn't prevent. Legitimate SVGs essentially never declare
    custom DTD entities, so this project's mitigation is the same shape
    as its zip-bomb defense in `security/paths.py`: refuse outright rather
    than attempt to parse and hope expat's own limits save it.
    """
    head = path.read_bytes()[:65536]
    if b"<!ENTITY" in head or (b"<!DOCTYPE" in head and b"[" in head.split(b"<!DOCTYPE", 1)[1][:2048]):
        raise ArtifactSecurityError(
            code="ARTIFACT_XML_ENTITY_DECLARATION_REJECTED",
            message=f"'{path}' declares a DOCTYPE/ENTITY, which this adapter refuses to parse "
            "(entity-expansion DoS risk).",
            remediation="Remove the DOCTYPE/ENTITY declaration. Legitimate SVG files do not need one.",
            evidence={"path": str(path)},
        )


def _classify_resource(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") or url.startswith("//"):
        return "external"
    if parsed.scheme == "data" or url.startswith("#"):
        return "data"
    return "local"


class SvgAdapter(ArtifactAdapter):
    id = "svg"
    artifact_type = ArtifactType.SVG

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.SVG

    def operations(self) -> dict[str, OperationSpec]:
        return {}

    def capabilities(self) -> list[Capability]:
        structural = Capability(
            id="svg.structural", status=CapabilityStatus.AVAILABLE,
            detail="Uses only the standard library (xml.etree.ElementTree); always available.",
            detected_via="stdlib",
        )
        import importlib.util

        if importlib.util.find_spec("playwright") is not None:
            import importlib.metadata as im

            try:
                version = im.version("playwright")
            except im.PackageNotFoundError:
                version = None
            render = Capability(
                id="svg.render", status=CapabilityStatus.AVAILABLE,
                detail="playwright importable. A matching Chromium build must also be installed "
                "(`playwright install chromium`) — verified at render() time, not here; see "
                "adapters/html/adapter.py's docstring for the precedent this follows.",
                detected_via="import playwright", version=version,
            )
        else:
            render = Capability(
                id="svg.render", status=CapabilityStatus.MISSING,
                detail="playwright not importable.", detected_via="import playwright",
            )
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "No mutating operations: this adapter inspects/renders/verifies SVG, it does not edit markup.",
            "CSS referenced via <style> @import or url() is not scanned for external/local resource "
            "references — only element attributes (image/use/script href and xlink:href) are.",
            "External resources are never fetched (network policy default-off); render() blocks them, "
            "so a page relying on them will render with those elements visibly missing/broken.",
            "render() captures the viewport only (not a full-page screenshot, unlike the HTML adapter) — "
            "a standalone SVG document hangs Playwright's full-page screenshot; a very large SVG's evidence "
            "image may be cropped to the viewport rather than showing its entire content.",
        ]

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        _reject_xml_entities(ref.path)
        warnings: list[str] = []
        try:
            tree = ET.parse(ref.path)  # noqa: S314 - _reject_xml_entities() above already rejects DOCTYPE/entity payloads
        except ET.ParseError as exc:
            raise ArtifactInputError(
                code="ARTIFACT_SVG_UNREADABLE",
                message=f"'{ref.path}' is not well-formed XML: {exc}",
                remediation="The file may be corrupt or not a valid SVG despite its detected type.",
                evidence={"path": str(ref.path)},
            ) from exc

        root = tree.getroot()
        if _local_name(root.tag) != "svg":
            raise ArtifactInputError(
                code="ARTIFACT_SVG_UNREADABLE",
                message=f"Root element is <{_local_name(root.tag)}>, not <svg>.",
                evidence={"path": str(ref.path), "root_tag": root.tag},
            )

        width = root.get("width")
        height = root.get("height")
        view_box = root.get("viewBox")

        svg_dir = ref.path.parent
        external: list[str] = []
        local_missing: list[str] = []
        local_present = 0
        for elem in root.iter():
            if _local_name(elem.tag) not in _REFERENCING_TAGS:
                continue
            url = elem.get(_XLINK_NS) or elem.get("href")
            if not url:
                continue
            kind = _classify_resource(url)
            if kind == "external":
                external.append(url)
            elif kind == "local":
                candidate = (svg_dir / url.split("#")[0]).resolve()
                try:
                    candidate.relative_to(svg_dir.resolve())
                except ValueError:
                    warnings.append(f"Local resource reference escapes the document's directory: {url}")
                    continue
                if candidate.is_file():
                    local_present += 1
                else:
                    local_missing.append(url)

        text_parts = [elem.text for elem in root.iter() if _local_name(elem.tag) in ("text", "tspan") and elem.text]

        details = {
            "width": width,
            "height": height,
            "view_box": view_box,
            "has_explicit_size": bool(width and height) or bool(view_box),
            "external_resources": external,
            "local_resources_present": local_present,
            "local_resources_missing": local_missing,
            "size_bytes": ref.size_bytes,
            # Same rationale as the other adapters' leftover_markers: the
            # marker list found, not every <text>/<tspan>'s full content.
            "leftover_markers": find_leftover_markers("\n".join(text_parts)),
        }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan / execute -----------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"SVG adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"SVG adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path) -> RenderResult:
        check_input_size(ref.path)
        _reject_xml_entities(ref.path)
        # full_page=False: see rendering/chromium_render.py's docstring —
        # a standalone SVG document hangs Playwright's full-page screenshot.
        out_path = render_local_file(
            ref.path, out_dir / "page-001.png", capability_id="svg.render", full_page=False
        )
        return RenderResult(kind="page_images", files=[out_path], backend="playwright+chromium")

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="svg_validity", name="SVG is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="svg_validity", name="SVG is readable", status=CheckStatus.PASS))

        details = report.details
        checks.append(
            Check(
                id="explicit_size",
                name="Explicit size (width/height or viewBox)",
                status=CheckStatus.PASS if details["has_explicit_size"] else CheckStatus.WARN,
                message=f"width={details['width']}, height={details['height']}, viewBox={details['view_box']}."
                if details["has_explicit_size"]
                else "No width/height or viewBox — a viewer may fall back to an arbitrary default size.",
            )
        )

        if details["local_resources_missing"]:
            checks.append(
                Check(
                    id="local_resources",
                    name="Local resource references resolve",
                    status=CheckStatus.FAIL,
                    message=f"{len(details['local_resources_missing'])} local resource(s) referenced but "
                    "not found on disk.",
                    evidence={"missing": details["local_resources_missing"]},
                )
            )
        else:
            checks.append(Check(id="local_resources", name="Local resource references resolve", status=CheckStatus.PASS))

        if details["external_resources"]:
            checks.append(
                Check(
                    id="external_resources",
                    name="No external resource references",
                    status=CheckStatus.FAIL if policy.get("forbid_external_resources") else CheckStatus.WARN,
                    message=f"{len(details['external_resources'])} external resource(s) referenced; not "
                    "fetched (network access is off by default — see docs/security.md), and render() blocks "
                    "them too, so they will appear broken/missing in the rendered evidence.",
                    evidence={"urls": details["external_resources"]},
                )
            )
        else:
            checks.append(Check(id="external_resources", name="No external resource references", status=CheckStatus.PASS))

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

        return VerificationResult(kind="structural", checks=checks)

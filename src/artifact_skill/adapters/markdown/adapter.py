"""Markdown adapter.

Structural inspection uses only the standard library (line-based regex over
the decoded text) — no optional dependency, so `markdown.structural` is
always `AVAILABLE`, the same design as HTML/SVG/CSV: a full CommonMark
parser isn't needed to check headings, local link/image resolution, and
fenced-code-block balance, the same way `html.parser` (not a browser engine)
is enough for HTML's structural checks.

Rendering is different: producing a faithful visual preview genuinely needs
a real Markdown->HTML conversion, which is where the optional `markdown-it-py`
dependency comes in (MIT, pure Python — no external binary, matching this
project's LibreOffice/Chromium-only-when-actually-needed bias). The
resulting HTML is fed through the same `rendering/chromium_render.py`
backend HTML/SVG/CSV already use. `markdown.render` degrades to `MISSING`
when `markdown-it-py` isn't installed — structural verification stays fully
available regardless (see `capabilities()`).

No mutating operations, for the same reason HTML/SVG/CSV have none: a
Markdown document's natural "edit" is its prose, which is source-content
editing, not a property-set operation this Skill should own.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactInputError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.chromium_render import render_local_file
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.paths import check_input_size

_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:\s+(.*))?$", re.MULTILINE)
_FENCE_LINE = re.compile(r"^ {0,3}(```|~~~)", re.MULTILINE)
_FENCED_BLOCK = re.compile(r"^ {0,3}(```|~~~).*?\n.*?^ {0,3}\1", re.MULTILINE | re.DOTALL)
_LINK_OR_IMAGE = re.compile(r"(!?)\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _classify_link(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") or url.startswith("//"):
        return "external"
    if parsed.scheme in ("mailto", "data") or url.startswith("#"):
        return "other"
    return "local"


def _strip_fenced_code(text: str) -> str:
    """Remove fenced code block *bodies* before scanning for leftover
    placeholder text — a "TODO" inside a code sample meant to demonstrate a
    todo app is not an unreviewed generation artifact, the same reasoning
    the HTML adapter applies to <script>/<style> content."""
    return _FENCED_BLOCK.sub("", text)


class MarkdownAdapter(ArtifactAdapter):
    id = "markdown"
    artifact_type = ArtifactType.MARKDOWN

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.MARKDOWN

    def operations(self) -> dict[str, OperationSpec]:
        return {}

    def capabilities(self) -> list[Capability]:
        structural = Capability(
            id="markdown.structural", status=CapabilityStatus.AVAILABLE,
            detail="Uses only the standard library (regex over decoded text); always available.",
            detected_via="stdlib",
        )
        if _has("markdown_it") and _has("playwright"):
            import importlib.metadata as im

            def _version_or_none(pkg: str) -> str | None:
                try:
                    return im.version(pkg)
                except im.PackageNotFoundError:
                    return None

            versions = {pkg: _version_or_none(pkg) for pkg in ("markdown-it-py", "playwright")}
            render = Capability(
                id="markdown.render", status=CapabilityStatus.AVAILABLE,
                detail="markdown-it-py and playwright both importable. A matching Chromium build must also be "
                "installed (`playwright install chromium`) — verified at render() time, not here.",
                detected_via="import markdown_it, playwright", version=str(versions),
            )
        else:
            missing = [n for n, mod in (("markdown-it-py", "markdown_it"), ("playwright", "playwright")) if not _has(mod)]
            render = Capability(
                id="markdown.render", status=CapabilityStatus.MISSING,
                detail=f"Missing: {', '.join(missing)}.", detected_via="import markdown_it, playwright",
            )
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "No mutating operations: this adapter inspects/renders/verifies Markdown, it does not edit prose.",
            "Type detection for Markdown is a heuristic (a minimum number of CommonMark-ish syntax signals), "
            "not a magic-byte match — plain text has no fixed signature. A file that doesn't clear that bar "
            "(e.g. prose with no headings/links/lists/fences) is reported UNKNOWN rather than guessed.",
            "Structural checks are regex-based, not a full CommonMark parser — indented code blocks, reference-"
            "style links ([text][ref]), and raw HTML blocks are not specifically recognized.",
            "render() requires the optional markdown-it-py dependency in addition to playwright; without it, "
            "markdown.render reports MISSING while structural verification stays fully available.",
        ]

    def recognized_policy_keys(self) -> frozenset[str]:
        return frozenset({"require_heading", "forbid_external_resources", "forbid_placeholder_text"})

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        try:
            # utf-8-sig strips a leading BOM if present - self-audit
            # finding: plain "utf-8" left it glued onto the first line,
            # breaking the ATX-heading regex's "^#" anchor on that line
            # and silently misdetecting an otherwise-obvious document.
            text = ref.path.read_text(encoding="utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise ArtifactInputError(
                code="ARTIFACT_MARKDOWN_UNREADABLE",
                message=f"'{ref.path}' is not valid UTF-8 text: {exc}",
                remediation="Markdown files are expected to be UTF-8 encoded.",
                evidence={"path": str(ref.path)},
            ) from exc

        headings = [(len(m.group(1)), (m.group(2) or "").strip()) for m in _ATX_HEADING.finditer(text)]
        fence_line_count = len(_FENCE_LINE.findall(text))

        md_dir = ref.path.parent
        external: list[str] = []
        local_missing: list[str] = []
        local_present = 0
        warnings: list[str] = []
        for _is_image, _label, url in _LINK_OR_IMAGE.findall(text):
            kind = _classify_link(url)
            if kind == "external":
                external.append(url)
            elif kind == "local":
                candidate = (md_dir / url.split("#")[0]).resolve()
                try:
                    candidate.relative_to(md_dir.resolve())
                except ValueError:
                    warnings.append(f"Local reference escapes the document's directory: {url}")
                    continue
                if candidate.is_file():
                    local_present += 1
                else:
                    local_missing.append(url)

        details = {
            "heading_count": len(headings),
            "headings": headings[:50],
            "fenced_code_block_balanced": fence_line_count % 2 == 0,
            "fence_line_count": fence_line_count,
            "external_resources": external,
            "local_resources_present": local_present,
            "local_resources_missing": local_missing,
            "size_bytes": ref.size_bytes,
            "leftover_markers": find_leftover_markers(_strip_fenced_code(text)),
        }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan / execute -----------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"Markdown adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"Markdown adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path, *, limits: Limits = DEFAULT_LIMITS) -> RenderResult:
        check_input_size(ref.path)
        if not _has("markdown_it"):
            raise ArtifactCapabilityError(
                code="ARTIFACT_CAPABILITY_MISSING",
                message="markdown-it-py is not installed; cannot render Markdown to an image.",
                remediation="Install with: pip install 'artifacts-skill[markdown]'.",
                evidence={"capability_id": "markdown.render"},
            )
        from markdown_it import MarkdownIt

        text = ref.path.read_text(encoding="utf-8-sig", errors="strict")
        body_html = MarkdownIt("commonmark").render(text)
        doc = (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "body{font-family:sans-serif;max-width:800px;margin:2em auto;padding:0 1em;line-height:1.5}"
            "code,pre{background:#f4f4f4}pre{padding:8px;overflow-x:auto}"
            "img{max-width:100%}</style></head><body>" + body_html + "</body></html>"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        render_html_path = out_dir / "_render.html"
        render_html_path.write_text(doc, encoding="utf-8")

        out_path = render_local_file(
            render_html_path, out_dir / "page-001.png", capability_id="markdown.render", limits=limits
        )
        return RenderResult(kind="page_images", files=[out_path], backend="markdown-it-py+playwright+chromium")

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(
                Check(id="markdown_validity", name="Markdown is readable", status=CheckStatus.FAIL, message=str(exc))
            )
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="markdown_validity", name="Markdown is readable", status=CheckStatus.PASS))

        details = report.details
        checks.append(
            Check(
                id="fenced_code_block_balance",
                name="Fenced code blocks are balanced",
                status=CheckStatus.PASS if details["fenced_code_block_balanced"] else CheckStatus.WARN,
                message="Every fence (``` or ~~~) has a matching close."
                if details["fenced_code_block_balanced"]
                else f"{details['fence_line_count']} fence line(s) found — an odd count means an unclosed "
                "fence, which will render the rest of the document as code.",
            )
        )

        if policy.get("require_heading", False):
            checks.append(
                Check(
                    id="heading_presence",
                    name="At least one heading present",
                    status=CheckStatus.PASS if details["heading_count"] > 0 else CheckStatus.FAIL,
                    message=f"{details['heading_count']} heading(s) found.",
                )
            )

        if details["local_resources_missing"]:
            checks.append(
                Check(
                    id="local_resources",
                    name="Local link/image references resolve",
                    status=CheckStatus.FAIL,
                    message=f"{len(details['local_resources_missing'])} local reference(s) not found on disk.",
                    evidence={"missing": details["local_resources_missing"]},
                )
            )
        else:
            checks.append(Check(id="local_resources", name="Local link/image references resolve", status=CheckStatus.PASS))

        if details["external_resources"]:
            checks.append(
                Check(
                    id="external_resources",
                    name="No external link/image references",
                    status=CheckStatus.FAIL if policy.get("forbid_external_resources") else CheckStatus.WARN,
                    message=f"{len(details['external_resources'])} external reference(s) found; not fetched "
                    "(network access is off by default — see docs/security.md).",
                    evidence={"urls": details["external_resources"]},
                )
            )
        else:
            checks.append(Check(id="external_resources", name="No external link/image references", status=CheckStatus.PASS))

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

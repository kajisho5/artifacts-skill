"""HTML adapter.

Structural inspection uses only the standard library (`html.parser`) — no
optional dependency, so `html.structural` is always `AVAILABLE`. Rendering
(`html.render`) uses Playwright/Chromium, which is where the real risk and
the real value both live: turning a document only a browser can fully
interpret into something an agent can actually look at.

No mutating operations in this MVP. Every other adapter in this project
edits a well-defined property set (Office core metadata, image pixels);
HTML's natural "edit" is changing markup, which is source-code editing, not
a property-set operation this Skill should own. `operations()` returns an
empty dict on purpose — see docs/adapters.md.

## Network policy at render time, not just in the structural report

Spec §27 and docs/security.md say external network access stays off by
default. For every other adapter that's purely a matter of *reporting* an
external reference without fetching it, because nothing else in the
pipeline would fetch it either. A real browser is different: navigating to
an HTML page and *not* blocking its own outgoing requests would silently
violate that policy, since Chromium will happily fetch every external
`<script>`/`<img>`/`<link>` it finds. So `render()` installs a Playwright
route handler that aborts any request that isn't `file://`/`data:`/
`about:` — external assets simply fail to load during the screenshot,
which is the *safe* failure mode (a visibly broken image/style is better
than a silent network fetch), and `verify_structural()`'s
`external_resources` check tells the caller why up front.

## Why Playwright's own subprocess isn't run through security/subprocess_exec.py

docs/security.md's stated invariant is "every subprocess call goes through
security/subprocess_exec.py" — this is the one adapter where that needs an
explicit, documented exception rather than a silent one. Playwright's
Python API manages its own browser process lifecycle internally (spawn,
IPC, teardown); this project calls into that library's API, the same way
the PDF adapter calls into `pypdfium2`'s API without wrapping *its*
internal PDFium calls in `security/subprocess_exec.py` either. The
wrapper's rationale — no shell strings, no attacker-controlled argv built
by this project's own code — doesn't apply to a well-audited library
managing its own child process through its own API, only to argv this
project constructs itself (as `rendering/office_convert.py` does for
`soffice`).
"""

from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError, ArtifactInputError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.security.paths import check_input_size

_RESOURCE_ATTRS = {"img": "src", "script": "src", "link": "href", "iframe": "src", "source": "src"}


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


class _ResourceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._in_title = False
        self.resources: list[tuple[str, str]] = []  # (tag, url)
        self.parse_errors = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "title":
            self._in_title = True
        target_attr = _RESOURCE_ATTRS.get(tag)
        if target_attr and attr_map.get(target_attr):
            self.resources.append((tag, attr_map[target_attr]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title = (self.title or "") + data


def _classify_resource(url: str, html_dir: Path) -> str:
    """'external' (http/https/protocol-relative), 'data' (inline), or
    'local' (relative/absolute filesystem path)."""
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") or url.startswith("//"):
        return "external"
    if parsed.scheme == "data":
        return "data"
    return "local"


class HtmlAdapter(ArtifactAdapter):
    id = "html"
    artifact_type = ArtifactType.HTML

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.HTML

    def operations(self) -> dict[str, OperationSpec]:
        return {}

    def capabilities(self) -> list[Capability]:
        structural = Capability(
            id="html.structural", status=CapabilityStatus.AVAILABLE,
            detail="Uses only the standard library (html.parser); always available.",
            detected_via="stdlib",
        )
        if _has("playwright"):
            import importlib.metadata as im

            try:
                version = im.version("playwright")
            except im.PackageNotFoundError:
                version = None
            render = Capability(
                id="html.render", status=CapabilityStatus.AVAILABLE,
                detail="playwright importable. A matching Chromium build must also be installed "
                "(`playwright install chromium`) — that is verified at render() time, not here, the same "
                "way a present `soffice` binary doesn't guarantee a specific document converts (see "
                "adapters/pptx/adapter.py's docstring for that precedent).",
                detected_via="import playwright", version=version,
            )
        else:
            render = Capability(
                id="html.render", status=CapabilityStatus.MISSING,
                detail="playwright not importable.", detected_via="import playwright",
            )
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "No mutating operations: this adapter inspects/renders/verifies HTML, it does not edit markup.",
            "JavaScript-driven content that renders asynchronously after load may not be captured — "
            "render() waits for the 'load' event, not arbitrary client-side rendering completion.",
            "External resources are never fetched (network policy default-off); a page relying on them "
            "will render with those elements visibly missing/broken, by design.",
        ]

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        warnings: list[str] = []
        try:
            text = ref.path.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ArtifactInputError(
                code="ARTIFACT_HTML_UNREADABLE",
                message=f"'{ref.path}' is not valid UTF-8 text: {exc}",
                remediation="HTML files are expected to be UTF-8 encoded.",
                evidence={"path": str(ref.path)},
            ) from exc

        parser = _ResourceCollector()
        try:
            parser.feed(text)
        except Exception as exc:  # noqa: BLE001 - html.parser is very tolerant; a real failure is notable
            raise ArtifactInputError(
                code="ARTIFACT_HTML_UNREADABLE",
                message=f"Could not parse '{ref.path}' as HTML: {exc}",
                evidence={"path": str(ref.path)},
            ) from exc

        html_dir = ref.path.parent
        external: list[str] = []
        local_missing: list[str] = []
        local_present = 0
        for _tag, url in parser.resources:
            kind = _classify_resource(url, html_dir)
            if kind == "external":
                external.append(url)
            elif kind == "local":
                candidate = (html_dir / url.split("#")[0].split("?")[0]).resolve()
                try:
                    candidate.relative_to(html_dir.resolve())
                except ValueError:
                    # Escapes the HTML file's own directory — not a
                    # rendering concern (render() blocks nothing local,
                    # only external), but worth surfacing as a warning.
                    warnings.append(f"Local resource reference escapes the document's directory: {url}")
                    continue
                if candidate.is_file():
                    local_present += 1
                else:
                    local_missing.append(url)

        details = {
            "title": parser.title.strip() if parser.title else None,
            "external_resources": external,
            "local_resources_present": local_present,
            "local_resources_missing": local_missing,
            "size_bytes": ref.size_bytes,
        }
        return InspectionReport(artifact=ref, details=details, warnings=warnings)

    # ---- plan / execute -----------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"HTML adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"HTML adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path) -> RenderResult:
        if not _has("playwright"):
            raise ArtifactCapabilityError(
                code="ARTIFACT_CAPABILITY_MISSING",
                message="playwright is not installed; cannot render HTML to an image.",
                remediation="Install with: pip install 'artifact-skill[html]' (or `pip install playwright` "
                "then `playwright install chromium`).",
                evidence={"capability_id": "html.render"},
            )
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "page-001.png"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 800})

                    def _block_external(route: Any) -> None:
                        url = route.request.url
                        if url.startswith(("file://", "data:", "about:")):
                            route.continue_()
                        else:
                            route.abort()

                    page.route("**/*", _block_external)
                    page.goto(f"file://{ref.path.resolve()}", wait_until="load", timeout=30_000)
                    page.screenshot(path=str(out_path), full_page=True)
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise ArtifactExecutionError(
                code="ARTIFACT_RENDER_BACKEND_FAILED",
                message=f"Playwright/Chromium failed to render '{ref.path}': {exc}",
                remediation="If this mentions a missing executable, run `playwright install chromium`. "
                "A present `playwright` package does not guarantee a matching browser build is installed.",
                evidence={"error": str(exc)},
            ) from exc

        return RenderResult(kind="page_images", files=[out_path], backend="playwright+chromium")

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="html_validity", name="HTML is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="html_validity", name="HTML is readable", status=CheckStatus.PASS))

        details = report.details
        if policy.get("require_title", False):
            has_title = bool(details["title"])
            checks.append(
                Check(
                    id="title_presence", name="Title present",
                    status=CheckStatus.PASS if has_title else CheckStatus.FAIL,
                    message=details["title"] or "No <title> element (or it is empty).",
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

        return VerificationResult(kind="structural", checks=checks)

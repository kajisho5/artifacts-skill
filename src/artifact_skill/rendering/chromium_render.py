"""Shared Playwright/Chromium page screenshot, used by every adapter that
renders a browser-interpretable document directly (HTML today; SVG, since
a bare `<svg>` root loads fine as its own page — see
`adapters/svg/adapter.py`). Extracted out of the HTML adapter once a
second adapter needed the identical navigate-and-screenshot logic, the
same way `rendering/office_convert.py` was extracted out of the PPTX
adapter for DOCX/XLSX.

See `adapters/html/adapter.py`'s module docstring for why this project's
"every subprocess through security/subprocess_exec.py" invariant has a
documented exception here (Playwright manages its own browser process
through its own API), and `docs/security.md` for the same reasoning.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError

if TYPE_CHECKING:
    # playwright is an optional dependency (see require_playwright() below) -
    # only imported for type annotations, never at runtime.
    from playwright.sync_api import ViewportSize

_VIEWPORT: ViewportSize = {"width": 1280, "height": 800}
_NAV_TIMEOUT_MS = 30_000


def require_playwright(capability_id: str) -> None:
    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="playwright is not installed; cannot render to an image.",
            remediation="Install with: pip install 'artifact-skill[html]' (or `pip install playwright` "
            "then `playwright install chromium`).",
            evidence={"capability_id": capability_id},
        )


def render_local_file(source_path: Path, out_path: Path, *, capability_id: str, full_page: bool = True) -> Path:
    """Screenshot `source_path` (loaded via a `file://` URL) to `out_path`.

    Blocks every outgoing request that isn't `file://`/`data:`/`about:` —
    see `adapters/html/adapter.py`'s module docstring for why that's an
    active enforcement of this project's network-off-by-default policy,
    not just a documented intention.

    `full_page=False` for a standalone SVG document, not just HTML: an SVG
    navigated to directly (not embedded in an `<html><body>`) is Chromium's
    synthetic-wrapper case, and `Page.screenshot(full_page=True)` hangs
    until timeout against it — confirmed directly while building the SVG
    adapter, not a hypothetical. A plain viewport screenshot works fine.
    The caller (an SVG adapter) is expected to pass `full_page=False`.
    """
    require_playwright(capability_id)
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport=_VIEWPORT)

                def _block_external(route: Any) -> None:
                    url = route.request.url
                    if url.startswith(("file://", "data:", "about:")):
                        route.continue_()
                    else:
                        route.abort()

                page.route("**/*", _block_external)
                page.goto(f"file://{source_path.resolve()}", wait_until="load", timeout=_NAV_TIMEOUT_MS)
                page.screenshot(path=str(out_path), full_page=full_page, timeout=_NAV_TIMEOUT_MS)
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ArtifactExecutionError(
            code="ARTIFACT_RENDER_BACKEND_FAILED",
            message=f"Playwright/Chromium failed to render '{source_path}': {exc}",
            remediation="If this mentions a missing executable, run `playwright install chromium`. "
            "A present `playwright` package does not guarantee a matching browser build is installed.",
            evidence={"error": str(exc)},
        ) from exc
    return out_path

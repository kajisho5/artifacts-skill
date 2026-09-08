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
from urllib.parse import unquote, urlparse

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactExecutionError
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits

if TYPE_CHECKING:
    # playwright is an optional dependency (see require_playwright() below) -
    # only imported for type annotations, never at runtime.
    from playwright.sync_api import ViewportSize

_VIEWPORT: ViewportSize = {"width": 1280, "height": 800}


def _file_url_is_within(url: str, allowed_root: Path) -> bool:
    """True iff `url` is a `file://` URL whose path resolves to somewhere
    inside `allowed_root` (Grok review P0-2: previously *any* `file://`
    URL was allowed through unconditionally — a hostile HTML/SVG document
    could pull in an arbitrary local file, e.g.
    `<iframe src="file:///etc/passwd">`, and have its content end up in
    the rendered PNG evidence, a real local-file-disclosure path, not a
    hypothetical one; reproduced directly against a real Chromium launch
    before this fix). `.resolve()` follows symlinks, so a symlink placed
    inside `allowed_root` that points outside it is caught the same way a
    literal `../` escape is.
    """
    if not url.startswith("file://"):
        return False
    try:
        requested = Path(unquote(urlparse(url).path)).resolve()
    except (OSError, ValueError):
        return False
    return requested == allowed_root or requested.is_relative_to(allowed_root)


def require_playwright(capability_id: str) -> None:
    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="playwright is not installed; cannot render to an image.",
            remediation="Install with: pip install 'artifacts-skill[html]' (or `pip install playwright` "
            "then `playwright install chromium`).",
            evidence={"capability_id": capability_id},
        )


def render_local_file(
    source_path: Path,
    out_path: Path,
    *,
    capability_id: str,
    full_page: bool = True,
    limits: Limits = DEFAULT_LIMITS,
    allowed_root: Path | None = None,
) -> Path:
    """Screenshot `source_path` (loaded via a `file://` URL) to `out_path`.

    Blocks every outgoing request that isn't `data:`/`about:`, or a
    `file://` reference that stays inside `allowed_root` (default:
    `source_path`'s own directory — Issue P0-2) — see
    `adapters/html/adapter.py`'s module docstring for why that's an active
    enforcement of this project's network-off-by-default policy, not just
    a documented intention. A `file://` request is not "external" in the
    network sense, but an arbitrary one is just as much a confidentiality
    problem: a hostile document referencing `file:///etc/passwd` (or
    anything outside the allowed root) would otherwise have that file's
    content end up in the rendered PNG evidence.

    `allowed_root` is overridable (default: `source_path.resolve().parent`)
    for a caller that has already staged a document's *own* resources into
    a private directory tree wider than that one file's immediate parent —
    the EPUB adapter's `render()` is the one caller that needs this: a
    spine document at `OEBPS/text/ch1.xhtml` referencing an image at
    `OEBPS/images/cover.jpg` (an entirely ordinary EPUB layout, sibling
    subdirectories under a shared root) would otherwise have that
    same-archive, already-safely-extracted image request rejected by a
    parent-directory-only boundary — not a security gap, since the whole
    staged tree is still nothing but that one EPUB's own content, but a
    real breakage this override exists to avoid. Every other caller
    (HTML/SVG) passes `None` and gets the original, narrower boundary.

    `full_page=False` for a standalone SVG document, not just HTML: an SVG
    navigated to directly (not embedded in an `<html><body>`) is Chromium's
    synthetic-wrapper case, and `Page.screenshot(full_page=True)` hangs
    until timeout against it — confirmed directly while building the SVG
    adapter, not a hypothetical. A plain viewport screenshot works fine.
    The caller (an SVG adapter) is expected to pass `full_page=False`.

    `limits.render_timeout_seconds` (Issue #28) governs both the page
    navigation and the screenshot call — previously a hardcoded module
    constant with no connection to `security/limits.py` at all, despite
    `docs/security.md` claiming `Limits` was the single place these
    numbers live. `Limits.subprocess_timeout_seconds` governs the
    LibreOffice-backed render path instead (`rendering/office_convert.py`);
    this is the equivalent knob for the Chromium-backed one.
    """
    files = render_local_files(
        [source_path], [out_path], capability_id=capability_id, full_page=full_page,
        limits=limits, allowed_root=allowed_root,
    )
    return files[0]


def render_local_files(
    source_paths: list[Path],
    out_paths: list[Path],
    *,
    capability_id: str,
    full_page: bool = True,
    limits: Limits = DEFAULT_LIMITS,
    allowed_root: Path | None = None,
) -> list[Path]:
    """Batch form of `render_local_file()`: screenshots every entry in
    `source_paths` (1:1 with `out_paths`) using a single shared Chromium
    launch, instead of one launch per file.

    Exists for the EPUB adapter's render() — one PNG per spine document,
    the same "one image per page" contract every other multi-page adapter
    (PDF, PPTX/DOCX/XLSX via LibreOffice) already honors. Launching a
    fresh Chromium process per spine document (what calling
    `render_local_file()` in a loop would do) is real, measured overhead
    (see docs/performance.md: ~500ms per call, dominated by browser
    launch) that multiplies with spine count for no benefit — page
    navigation within one already-launched browser is comparatively
    cheap. `render_local_file()` itself is now a thin single-file wrapper
    around this.
    """
    if len(source_paths) != len(out_paths):
        raise ValueError(f"source_paths ({len(source_paths)}) and out_paths ({len(out_paths)}) must match 1:1.")
    require_playwright(capability_id)
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    nav_timeout_ms = limits.render_timeout_seconds * 1000
    produced: list[Path] = []

    def _make_route_handler(root: Path):
        # A real closure (not a default-arg loop-variable trick): Playwright's
        # Python binding inspects the handler's own parameter count and
        # calls it with (route, request) instead of just (route) when it
        # accepts 2 positional parameters — a default-arg trick to bind the
        # loop variable (`def h(route, _root=root)`) collides with that
        # introspection and silently receives the real `request` object in
        # the second slot instead of the intended default (confirmed by
        # direct reproduction against a real Chromium launch: `_root` came
        # back as a Playwright `Request`, not a `Path`, immediately raising
        # inside `_file_url_is_within`). A single-parameter closure sidesteps
        # both the late-binding bug and the introspection collision.
        def _block_external(route: Any) -> None:
            url = route.request.url
            if url.startswith(("data:", "about:")) or _file_url_is_within(url, root):
                route.continue_()
            else:
                route.abort()

        return _block_external

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for source_path, out_path in zip(source_paths, out_paths, strict=True):
                    root = allowed_root if allowed_root is not None else source_path.resolve().parent
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    page = browser.new_page(viewport=_VIEWPORT)
                    try:
                        page.route("**/*", _make_route_handler(root))
                        # Path.as_uri() (self-audit finding, FIX_PROMPT
                        # P1-3): a naive f"file://{path}" string doesn't
                        # percent-encode the path — confirmed directly
                        # that a filename containing "#" (a URL fragment
                        # separator) made Chromium navigate to a
                        # truncated path and fail with
                        # net::ERR_FILE_NOT_FOUND, not merely a
                        # theoretical concern. Also the only correct way
                        # to build a Windows file:// URI ("file:///C:/...").
                        page.goto(source_path.resolve().as_uri(), wait_until="load", timeout=nav_timeout_ms)
                        page.screenshot(path=str(out_path), full_page=full_page, timeout=nav_timeout_ms)
                        produced.append(out_path)
                    finally:
                        page.close()
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise ArtifactExecutionError(
            code="ARTIFACT_RENDER_BACKEND_FAILED",
            message=f"Playwright/Chromium failed to render one of {len(source_paths)} document(s): {exc}",
            remediation="If this mentions a missing executable, run `playwright install chromium`. "
            "A present `playwright` package does not guarantee a matching browser build is installed.",
            evidence={"error": str(exc), "completed_before_failure": len(produced)},
        ) from exc
    return produced

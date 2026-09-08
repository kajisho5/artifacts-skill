"""Shared PDF -> page-image rendering, used directly by the PDF adapter and
by any adapter that converts its own format to PDF first (PPTX/DOCX/XLSX
via LibreOffice) rather than rasterizing natively. Keeping this in one place
means "render a PDF to PNGs" has exactly one implementation project-wide.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from artifact_skill.adapters.base import RenderResult
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactInputError, ArtifactSecurityError
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def require_pypdfium2():
    if not _has("pypdfium2"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="pypdfium2 is not installed; PDF page rendering is unavailable.",
            remediation="Install with: pip install 'artifacts-skill[pdf]' (or `pip install pypdfium2`).",
            evidence={"capability_id": "pdf.render"},
        )
    import pypdfium2

    return pypdfium2


def render_pdf_pages(
    pdf_path: Path, out_dir: Path, dpi: int = 150, *, limits: Limits = DEFAULT_LIMITS
) -> RenderResult:
    """Render every page of `pdf_path` to a PNG in `out_dir`.

    Checks encryption/page-count via `pypdf` first, because pypdfium2 raises
    its own opaque exception (rather than "0 usable pages") for a 0-page or
    still-encrypted document — see the PDF adapter's regression test for the
    concrete crash this avoids.

    Rejects, rather than silently rendering, a document with more than
    `limits.max_pages` pages (Grok review P0-3): `Limits.max_pages` was
    declared in `security/limits.py` but read nowhere — the only other
    `max_pages` in this codebase is the unrelated PDF adapter policy key
    (a caller-supplied *verification* constraint, checked only when a
    caller opts into it, not a resource limit this project itself
    enforces). Rendering is unconditional: a 2000+ page PDF, or a
    LibreOffice-converted PPTX/DOCX/XLSX that happens to produce one,
    would burn unbounded disk and time with no cap at all. A partial
    render that silently stops partway would be worse than an honest
    rejection — it would look like a complete, verified render.
    """
    if not _has("pypdf"):
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="pypdf is not installed; cannot pre-check the PDF before rendering.",
            remediation="Install with: pip install 'artifacts-skill[pdf]'.",
            evidence={"capability_id": "pdf.structural"},
        )
    import pypdf

    pdfium = require_pypdfium2()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception as exc:
        raise ArtifactInputError(
            code="ARTIFACT_PDF_UNREADABLE",
            message=f"pypdf could not open '{pdf_path}' for rendering: {exc}",
            evidence={"path": str(pdf_path)},
        ) from exc

    if reader.is_encrypted:
        return RenderResult(
            kind="page_images", files=[], backend="pypdfium2",
            warnings=["Document is encrypted; rendering was skipped."],
        )
    if len(reader.pages) == 0:
        return RenderResult(kind="page_images", files=[], backend="pypdfium2", warnings=["0 pages to render."])
    if len(reader.pages) > limits.max_pages:
        raise ArtifactSecurityError(
            code="ARTIFACT_TOO_MANY_PAGES",
            message=f"'{pdf_path}' has {len(reader.pages)} pages, exceeding the render limit of "
            f"{limits.max_pages}.",
            remediation="Not rendered; increase Limits.max_pages if this document is legitimately expected, "
            "or render a subset instead.",
            evidence={"path": str(pdf_path), "page_count": len(reader.pages), "max_pages": limits.max_pages},
        )

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        files: list[Path] = []
        for i, page in enumerate(doc):
            bitmap = page.render(scale=dpi / 72)
            pil_image = bitmap.to_pil()
            out_path = out_dir / f"page-{i + 1:03d}.png"
            pil_image.save(out_path)
            files.append(out_path)
        return RenderResult(kind="page_images", files=files, backend="pypdfium2")
    finally:
        doc.close()

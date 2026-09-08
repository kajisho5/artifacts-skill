"""Capability / environment detection (`artifact-skill doctor`).

Rule (spec #11): detection failure is never silently collapsed into MISSING.
A probe that could not run at all (e.g. `subprocess` itself unavailable in
some sandboxed environment) reports UNKNOWN, not MISSING — those mean
different things to a caller deciding whether to retry.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import shutil
import subprocess

from artifact_skill.core.capability import Capability, CapabilityReport, CapabilityStatus


def _module_capability(cap_id: str, module: str, required: bool) -> Capability:
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return Capability(cap_id, CapabilityStatus.UNKNOWN, detail=f"probe for '{module}' raised an error.")
    if not found:
        status = CapabilityStatus.MISSING if required else CapabilityStatus.NOT_REQUIRED
        return Capability(
            cap_id,
            status,
            detail=f"Python module '{module}' not importable.",
            detected_via=f"importlib.util.find_spec('{module}')",
        )
    try:
        dist_name = {"fitz": "PyMuPDF", "pptx": "python-pptx", "docx": "python-docx", "PIL": "Pillow"}.get(
            module, module
        )
        version = importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return Capability(
        cap_id,
        CapabilityStatus.AVAILABLE,
        detail=f"Python module '{module}' is importable.",
        detected_via=f"import {module}",
        version=version,
    )


def _binary_capability(cap_id: str, names: list[str], required: bool = False) -> Capability:
    for name in names:
        path = shutil.which(name)
        if path:
            version = _probe_version(path)
            return Capability(
                cap_id,
                CapabilityStatus.AVAILABLE,
                detail=f"Found on PATH: {path}",
                detected_via=f"which {name}",
                version=version,
            )
    status = CapabilityStatus.MISSING if required else CapabilityStatus.NOT_REQUIRED
    return Capability(
        cap_id,
        status,
        detail=f"None of {names} found on PATH.",
        detected_via=f"which {'/'.join(names)}",
    )


def _probe_version(path: str) -> str | None:
    for flag in ("--version", "-version", "--Version"):
        try:
            proc = subprocess.run([path, flag], capture_output=True, text=True, timeout=5)
            out = (proc.stdout or proc.stderr).strip().splitlines()
            if out:
                return out[0][:120]
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _chromium_capability() -> Capability:
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path and os.path.isdir(browsers_path):
        entries = [e for e in os.listdir(browsers_path) if e.startswith("chromium")]
        if entries:
            return Capability(
                "backend.chromium",
                CapabilityStatus.AVAILABLE,
                detail=f"Found under PLAYWRIGHT_BROWSERS_PATH: {entries}",
                detected_via="PLAYWRIGHT_BROWSERS_PATH",
            )
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        path = shutil.which(name)
        if path:
            return Capability(
                "backend.chromium",
                CapabilityStatus.AVAILABLE,
                detail=f"Found on PATH: {path}",
                detected_via=f"which {name}",
            )
    return Capability(
        "backend.chromium",
        CapabilityStatus.NOT_REQUIRED,
        detail="No Chromium/Chrome binary found (only needed for the not-yet-implemented HTML/SVG adapters).",
    )


def detect_environment() -> CapabilityReport:
    """Probe everything Artifact Skill can currently use *or* will use once
    later-phase adapters land, so `doctor` output stays honest ahead of time
    (spec #11, #25)."""
    report = CapabilityReport()

    report.add(
        Capability(
            "env.python",
            CapabilityStatus.AVAILABLE,
            detail=platform.python_version(),
            detected_via="platform.python_version()",
            version=platform.python_version(),
        )
    )
    report.add(_binary_capability("env.node", ["node"]))
    report.add(_binary_capability("backend.libreoffice", ["soffice", "libreoffice"]))
    report.add(_binary_capability("backend.poppler_pdftoppm", ["pdftoppm"]))
    report.add(_binary_capability("backend.poppler_pdftotext", ["pdftotext"]))
    report.add(_binary_capability("backend.qpdf", ["qpdf"]))
    report.add(_binary_capability("backend.imagemagick", ["magick", "convert"]))
    report.add(_chromium_capability())

    # Adapter-facing libraries. `required=True` only for what the currently
    # implemented (PDF) adapter needs; everything else is informational so
    # `doctor` can tell a user what to install ahead of a future phase.
    report.add(_module_capability("library.pypdf", "pypdf", required=True))
    report.add(_module_capability("library.pypdfium2", "pypdfium2", required=True))
    report.add(_module_capability("library.pillow", "PIL", required=True))
    report.add(_module_capability("library.python_pptx", "pptx", required=False))
    report.add(_module_capability("library.python_docx", "docx", required=False))
    report.add(_module_capability("library.openpyxl", "openpyxl", required=False))

    # Fold in each *implemented* adapter's own self-reported capabilities.
    from artifact_skill.adapters.registry import registered_types, get_adapter

    for artifact_type in registered_types():
        adapter = get_adapter(artifact_type)
        for cap in adapter.capabilities():
            report.add(cap)

    return report

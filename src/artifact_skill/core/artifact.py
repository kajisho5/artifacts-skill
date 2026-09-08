"""The Artifact abstraction: what file is this, really?

Type detection never trusts the file extension alone (spec #7: "'.pptx'
だから正常なPPTX' と仮定しない"). It sniffs magic bytes, and for zip-based
Office Open XML containers it opens the archive and reads the content-type
declarations to tell PPTX/DOCX/XLSX apart from a generic .zip or a corrupt one.
"""

from __future__ import annotations

import enum
import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024


class ArtifactType(str, enum.Enum):
    PDF = "pdf"
    PPTX = "pptx"
    DOCX = "docx"
    XLSX = "xlsx"
    HTML = "html"
    SVG = "svg"
    IMAGE_PNG = "image/png"
    IMAGE_JPEG = "image/jpeg"
    IMAGE_WEBP = "image/webp"
    ZIP = "zip"
    UNKNOWN = "unknown"


# OOXML main-document content types, used to disambiguate a zip container.
_OOXML_CONTENT_TYPE_MARKERS: dict[str, ArtifactType] = {
    "presentationml.presentation": ArtifactType.PPTX,
    "wordprocessingml.document": ArtifactType.DOCX,
    "spreadsheetml.sheet": ArtifactType.XLSX,
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _sniff_zip_ooxml(path: Path) -> ArtifactType:
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                content_types = zf.read("[Content_Types].xml").decode("utf-8", errors="replace")
            except KeyError:
                return ArtifactType.ZIP
            for marker, atype in _OOXML_CONTENT_TYPE_MARKERS.items():
                if marker in content_types:
                    return atype
            return ArtifactType.ZIP
    except zipfile.BadZipFile:
        return ArtifactType.UNKNOWN


def detect_type(path: Path) -> ArtifactType:
    """Detect artifact type from magic bytes (+ OOXML content-type sniff)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return ArtifactType.UNKNOWN

    if head.startswith(b"%PDF-"):
        return ArtifactType.PDF
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _sniff_zip_ooxml(path)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ArtifactType.IMAGE_PNG
    if head[:3] == b"\xff\xd8\xff":
        return ArtifactType.IMAGE_JPEG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ArtifactType.IMAGE_WEBP
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if stripped.startswith(b"<?xml") and b"<svg" in stripped[:2048]:
        return ArtifactType.SVG
    if stripped.startswith(b"<svg"):
        return ArtifactType.SVG
    if stripped.startswith((b"<!doctype html", b"<html")):
        return ArtifactType.HTML
    return ArtifactType.UNKNOWN


@dataclass(frozen=True)
class ArtifactRef:
    """A concrete, on-disk artifact plus everything needed for provenance."""

    path: Path
    type: ArtifactType
    sha256: str
    size_bytes: int

    @classmethod
    def from_path(cls, path: Path | str) -> ArtifactRef:
        p = Path(path)
        if not p.is_file():
            from artifact_skill.core.errors import ArtifactInputError

            raise ArtifactInputError(
                code="ARTIFACT_INPUT_NOT_FOUND",
                message=f"Input file does not exist or is not a regular file: {p}",
                remediation="Check the path and try again.",
                evidence={"path": str(p)},
            )
        return cls(
            path=p,
            type=detect_type(p),
            sha256=sha256_of(p),
            size_bytes=p.stat().st_size,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "type": self.type.value,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class InspectionReport:
    """Format-agnostic envelope; adapters populate `details` with their own
    structured fields (page_count, slide_count, fonts, hyperlinks, ...)."""

    artifact: ArtifactRef
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "details": self.details,
            "warnings": self.warnings,
        }

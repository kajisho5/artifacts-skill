"""Adapter registry — the only place that maps ArtifactType -> adapter class.

Adapters not yet implemented (spec Tier 2/3 in MVP) are registered as
`NotImplementedAdapter` placeholders, never silently absent and never a
fake pass-through. `get_adapter()` always returns *something* that can at
least explain what is missing and why.
"""

from __future__ import annotations

from artifact_skill.adapters.base import ArtifactAdapter
from artifact_skill.core.artifact import ArtifactRef, ArtifactType
from artifact_skill.core.errors import ArtifactCapabilityError

_REGISTRY: dict[ArtifactType, type[ArtifactAdapter]] = {}
_PLANNED: dict[ArtifactType, str] = {
    ArtifactType.PPTX: "Phase 2 (see docs/roadmap.md)",
    ArtifactType.DOCX: "Phase 3 (see docs/roadmap.md)",
    ArtifactType.XLSX: "Phase 3 (see docs/roadmap.md)",
    ArtifactType.HTML: "Phase 6 (see docs/roadmap.md)",
    ArtifactType.SVG: "Phase 6 (see docs/roadmap.md)",
    ArtifactType.IMAGE_PNG: "Phase 6 (see docs/roadmap.md)",
    ArtifactType.IMAGE_JPEG: "Phase 6 (see docs/roadmap.md)",
    ArtifactType.IMAGE_WEBP: "Phase 6 (see docs/roadmap.md)",
}


def register(adapter_cls: type[ArtifactAdapter]) -> type[ArtifactAdapter]:
    _REGISTRY[adapter_cls.artifact_type] = adapter_cls
    return adapter_cls


def registered_types() -> list[ArtifactType]:
    return list(_REGISTRY.keys())


def get_adapter(artifact_type: ArtifactType) -> ArtifactAdapter:
    adapter_cls = _REGISTRY.get(artifact_type)
    if adapter_cls is not None:
        return adapter_cls()
    planned = _PLANNED.get(artifact_type)
    if planned is not None:
        raise ArtifactCapabilityError(
            code="ARTIFACT_ADAPTER_NOT_IMPLEMENTED",
            message=f"No adapter is implemented yet for artifact type '{artifact_type.value}'.",
            remediation=f"Planned for {planned}. Structural inspection of the raw container may still "
            "be possible via generic tools, but no adapter-backed inspect/plan/execute/verify exists.",
            evidence={"artifact_type": artifact_type.value},
        )
    raise ArtifactCapabilityError(
        code="ARTIFACT_TYPE_UNSUPPORTED",
        message=f"Artifact type '{artifact_type.value}' is not recognized and has no adapter.",
        remediation="This file could not be identified as a supported format from its content.",
        evidence={"artifact_type": artifact_type.value},
    )


def adapter_for(ref: ArtifactRef) -> ArtifactAdapter:
    return get_adapter(ref.type)


def _register_builtin_adapters() -> None:
    from artifact_skill.adapters.pdf.adapter import PdfAdapter

    register(PdfAdapter)


_register_builtin_adapters()

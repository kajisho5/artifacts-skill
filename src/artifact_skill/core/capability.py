"""Capability detection model.

A capability is a fine-grained, independently-checkable ability, e.g.
"pdf.structural_inspect" vs "pdf.render". Losing one optional dependency must
not collapse an entire format to "unusable" — see docs/architecture.md #25.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class CapabilityStatus(str, enum.Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"
    # Distinct from MISSING: no external dependency is absent, the engine
    # itself has not implemented this yet (e.g. the pptx adapter in MVP).
    NOT_IMPLEMENTED = "not_implemented"


@dataclass(frozen=True)
class Capability:
    id: str
    status: CapabilityStatus
    detail: str = ""
    detected_via: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "detail": self.detail,
            "detected_via": self.detected_via,
            "version": self.version,
        }


@dataclass
class CapabilityReport:
    capabilities: dict[str, Capability] = field(default_factory=dict)

    def add(self, cap: Capability) -> None:
        self.capabilities[cap.id] = cap

    def get(self, capability_id: str) -> Capability | None:
        return self.capabilities.get(capability_id)

    def status_of(self, capability_id: str) -> CapabilityStatus:
        cap = self.capabilities.get(capability_id)
        return cap.status if cap else CapabilityStatus.UNKNOWN

    def require(self, capability_id: str) -> Capability:
        """Raise ArtifactCapabilityError unless the capability is AVAILABLE."""
        from artifact_skill.core.errors import ArtifactCapabilityError

        cap = self.capabilities.get(capability_id)
        if cap is None or cap.status != CapabilityStatus.AVAILABLE:
            status = cap.status.value if cap else "unknown"
            detail = cap.detail if cap else "capability was never probed"
            raise ArtifactCapabilityError(
                code="ARTIFACT_CAPABILITY_MISSING",
                message=f"Required capability '{capability_id}' is {status}: {detail}",
                remediation="Run `artifact-skill doctor --json` to see how to satisfy this capability.",
                evidence={"capability_id": capability_id, "status": status},
            )
        return cap

    def to_dict(self) -> dict[str, Any]:
        return {cid: cap.to_dict() for cid, cap in sorted(self.capabilities.items())}

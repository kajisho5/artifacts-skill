"""ArtifactAdapter — the one interface Core is allowed to know about.

Core never imports a format-specific library. Every format-specific concern
(python-pptx, pypdf, LibreOffice invocation, ...) lives behind this
interface in `adapters/<format>/`. Adding a format means adding an adapter,
not touching `core/` or `cli/` (spec #4, #24).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityReport
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import VerificationResult


@dataclass(frozen=True)
class OperationSpec:
    """Declares one mutating operation an adapter supports, and — per spec
    #15 — the verification policy that operation is bound to. This is what
    `contract.py` reads to build the machine-readable contract; an adapter
    cannot silently skip verification the contract promised.
    """

    name: str  # e.g. "metadata_set", "merge"
    description: str
    args_schema: dict[str, Any]
    structural_verification_required: bool = True
    visual_verification_required: bool = False
    render_required: bool = False
    postconditions: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)


@dataclass
class RenderResult:
    """Rendered representations of an artifact, e.g. one PNG per PDF page."""

    kind: str  # "page_images" | "screenshot" | ...
    files: list[Path]
    backend: str  # which renderer actually produced these, e.g. "pypdfium2"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "files": [str(f) for f in self.files],
            "backend": self.backend,
            "warnings": self.warnings,
        }


class ArtifactAdapter(ABC):
    """One adapter instance is stateless and reusable across artifacts."""

    id: str
    artifact_type: ArtifactType

    @classmethod
    @abstractmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        """Return True if this adapter should handle `ref`."""

    @abstractmethod
    def operations(self) -> dict[str, OperationSpec]:
        """All mutating operations this adapter supports, keyed by name."""

    @abstractmethod
    def capabilities(self) -> list[Capability]:
        """Probe and return this adapter's capabilities right now (spec #11)."""

    def limitations(self) -> list[str]:
        return []

    @abstractmethod
    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        """Read-only. Must never write to disk or spawn a mutating process."""

    @abstractmethod
    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        """Pure/read-only. Describes what execute() *would* do."""

    @abstractmethod
    def execute(
        self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path
    ) -> ArtifactRef:
        """Perform `operation`, writing only to `output_path` (never to `ref.path`)."""

    def render(self, ref: ArtifactRef, out_dir: Path) -> RenderResult:
        """Produce a visual representation for Agent/human inspection.

        Default: not implemented. Adapters that can render must override.
        """
        from artifact_skill.core.errors import ArtifactCapabilityError

        raise ArtifactCapabilityError(
            code="ARTIFACT_RENDER_NOT_IMPLEMENTED",
            message=f"Adapter '{self.id}' does not implement rendering.",
            remediation="Visual verification is unavailable for this format; structural verification still applies.",
            evidence={"adapter": self.id},
        )

    @abstractmethod
    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        """Structural checks only. Visual checks are handled by the engine
        from render() output plus (optionally) an Agent's judgment."""

    def fix(
        self, ref: ArtifactRef, operation: str, args: dict[str, Any], failed_result: VerificationResult
    ) -> dict[str, Any] | None:
        """Given a failed structural verification, return revised `args` to
        retry `execute()` with, or None if this adapter has no automatic fix
        for this failure. Default: no fixer (spec #16 forbids pretending a
        fix loop exists when it doesn't — silence here is the honest answer).
        """
        return None

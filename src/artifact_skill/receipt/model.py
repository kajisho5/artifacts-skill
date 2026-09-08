"""Production Receipt — schema `artifact-receipt/v1` (spec #18).

The receipt is the one artifact meant to outlive this process: other agents,
CI systems, or a future orchestration OS should be able to read it without
ever invoking Artifact Skill itself. Keep it boring, flat where possible, and
never add a field whose meaning depends on reading the code.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_skill import __version__
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.capability import CapabilityReport
from artifact_skill.core.operation import OperationRecord
from artifact_skill.core.verification import CheckStatus, VerificationResult, aggregate

RECEIPT_SCHEMA = "artifact-receipt/v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProductionReceipt:
    schema: str
    status: CheckStatus
    input: dict[str, Any]
    operations: list[dict[str, Any]]
    verification: dict[str, Any]
    artifacts: list[dict[str, Any]]
    warnings: list[str]
    limitations: list[str]
    environment: dict[str, Any]
    capabilities: dict[str, Any]
    timestamp: str
    tool_version: str
    iterations: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status.value,
            "input": self.input,
            "operations": self.operations,
            "verification": self.verification,
            "artifacts": self.artifacts,
            "warnings": self.warnings,
            "limitations": self.limitations,
            "environment": self.environment,
            "capabilities": self.capabilities,
            "timestamp": self.timestamp,
            "tool_version": self.tool_version,
            "iterations": self.iterations,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path


class ReceiptBuilder:
    """Accumulates evidence across the lifecycle, then emits one receipt."""

    def __init__(self, input_artifact: ArtifactRef, capabilities: CapabilityReport) -> None:
        self._input = input_artifact
        self._capabilities = capabilities
        self._operations: list[OperationRecord] = []
        self._structural: VerificationResult | None = None
        self._visual: VerificationResult | None = None
        self._artifacts: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._limitations: list[str] = []
        self._iterations = 1

    def add_operation(self, record: OperationRecord) -> None:
        self._operations.append(record)
        self._warnings.extend(record.warnings)

    def set_structural(self, result: VerificationResult) -> None:
        self._structural = result
        self._register_evidence(result)

    def set_visual(self, result: VerificationResult) -> None:
        self._visual = result
        self._register_evidence(result)

    def _register_evidence(self, result: VerificationResult) -> None:
        for f in result.evidence_files:
            self._artifacts.append({"path": f, "role": f"{result.kind}_evidence"})

    def add_artifact(self, path: str, role: str) -> None:
        self._artifacts.append({"path": path, "role": role})

    def add_warning(self, warning: str) -> None:
        self._warnings.append(warning)

    def add_limitation(self, limitation: str) -> None:
        self._limitations.append(limitation)

    def set_iterations(self, n: int) -> None:
        self._iterations = n

    def _overall_status(self) -> CheckStatus:
        statuses: list[CheckStatus] = []
        if any(not op.succeeded for op in self._operations):
            statuses.append(CheckStatus.FAIL)
        if self._structural is not None:
            statuses.append(self._structural.status)
        else:
            statuses.append(CheckStatus.NOT_CHECKED)
        if self._visual is not None:
            statuses.append(self._visual.status)
        # visual omission is not automatically a problem; policy decides
        # whether visual verification was required at all (tracked via
        # verification_strategy in the plan/receipt "verification" block).
        return aggregate(statuses)

    def build(self) -> ProductionReceipt:
        structural_dict = self._structural.to_dict() if self._structural else {
            "kind": "structural",
            "status": CheckStatus.NOT_CHECKED.value,
            "checks": [],
            "evidence_files": [],
            "inspected_by": None,
        }
        visual_dict = self._visual.to_dict() if self._visual else {
            "kind": "visual",
            "status": CheckStatus.NOT_CHECKED.value,
            "checks": [],
            "evidence_files": [],
            "inspected_by": None,
        }
        return ProductionReceipt(
            schema=RECEIPT_SCHEMA,
            status=self._overall_status(),
            input=self._input.to_dict(),
            operations=[op.to_dict() for op in self._operations],
            verification={"structural": structural_dict, "visual": visual_dict},
            artifacts=self._artifacts,
            warnings=self._warnings,
            limitations=self._limitations,
            environment=_environment_info(),
            capabilities=self._capabilities.to_dict(),
            timestamp=_now_iso(),
            tool_version=__version__,
            iterations=self._iterations,
        )


def _environment_info() -> dict[str, Any]:
    return {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python_version": platform.python_version(),
        "architecture": platform.machine(),
    }

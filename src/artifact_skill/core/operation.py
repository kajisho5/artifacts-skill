"""Operation planning and recording — the Plan-before-mutate contract.

A `plan()` call must be pure: it inspects and reasons, but never writes to
disk, never spawns a mutating subprocess. `OperationPlan` is the JSON that
`artifacts-skill plan` returns and that `execute()` re-derives internally
before actually touching anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OperationPlan:
    operation: str
    adapter: str
    input: dict[str, Any]
    output_path: str
    required_capabilities: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    rendering_strategy: str | None = None
    verification_strategy: dict[str, Any] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "adapter": self.adapter,
            "input": self.input,
            "output_path": self.output_path,
            "required_capabilities": self.required_capabilities,
            "files_touched": self.files_touched,
            "files_created": self.files_created,
            "rendering_strategy": self.rendering_strategy,
            "verification_strategy": self.verification_strategy,
            "risks": self.risks,
            "warnings": self.warnings,
        }


@dataclass
class OperationRecord:
    """What actually happened during one `execute()` call — goes into the receipt."""

    operation: str
    adapter: str
    args: dict[str, Any]
    started_at: str
    finished_at: str
    input_sha256: str
    output_sha256: str | None
    output_path: str | None
    succeeded: bool
    warnings: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "adapter": self.adapter,
            "args": self.args,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "output_path": self.output_path,
            "succeeded": self.succeeded,
            "warnings": self.warnings,
            "error": self.error,
        }


def default_output_path(input_path: Path, operation: str, suffix: str | None = None) -> Path:
    """Original Protection: never default to overwriting the input (spec #28).

    `report.pptx` + operation "resize" -> `report_artifact_resize.pptx`
    (suffix lets an adapter pick a different extension, e.g. pdf->png for render).
    """
    ext = suffix if suffix is not None else input_path.suffix
    safe_op = operation.replace("/", "_").replace(".", "_")
    return input_path.with_name(f"{input_path.stem}_artifact_{safe_op}{ext}")

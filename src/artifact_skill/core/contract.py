"""The Contract — single source of truth for CLI, MCP, and docs (spec #20/#21).

Nobody hand-writes a second copy of a tool's name/schema/policy anywhere
else in this codebase. `cli/main.py` builds its argparse subcommands by
reading `TOOLS`; `mcp/server.py` builds `tools/list` by reading `TOOLS`.
`tests/contract/` asserts the two never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from artifact_skill import __version__
from artifact_skill.core.errors import EXIT_CODE_BY_CATEGORY, EXIT_FAIL, EXIT_OK, ErrorCategory

CONTRACT_SCHEMA = "artifact-contract/v1"


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    artifact_types: list[str]
    capabilities: list[str]
    mutates_input: bool
    side_effects: bool
    dry_run_supported: bool
    verification_policy: dict[str, Any]
    visual_requirement: str
    evidence: str
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "artifact_types": self.artifact_types,
            "capabilities": self.capabilities,
            "side_effects": {
                "mutates_input": self.mutates_input,
                "writes_files": self.side_effects,
                "dry_run_supported": self.dry_run_supported,
            },
            "verification_policy": self.verification_policy,
            "visual_requirement": self.visual_requirement,
            "evidence": self.evidence,
            "errors": self.errors,
        }


_ARTIFACT_REF_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "type": {"type": "string"},
        "sha256": {"type": "string"},
        "size_bytes": {"type": "integer"},
    },
}


def _registered_type_values() -> list[str]:
    """Which artifact types currently have a real adapter registered.

    Computed from `adapters/registry.py` rather than hand-maintained here,
    specifically so a new adapter landing (see docs/roadmap.md) can't leave
    the contract silently out of date the way a hardcoded `["pdf"]` list
    would — this bit Core once already during PPTX's own rollout.
    """
    from artifact_skill.adapters.registry import registered_types

    return sorted(t.value for t in registered_types())


def _capability_ids(suffix: str) -> list[str]:
    """e.g. suffix='structural' -> ['pdf.structural', 'pptx.structural', ...]
    across every currently-registered artifact type."""
    return [f"{t}.{suffix}" for t in _registered_type_values()]


_INPUT_PATH_PROPERTY = {"input": {"type": "string", "description": "Path to the input artifact."}}

TOOLS: list[ToolContract] = [
    ToolContract(
        name="doctor",
        description="Detect local capabilities (libraries, renderers, external tools) and report "
        "AVAILABLE/MISSING/UNKNOWN/NOT_REQUIRED/NOT_IMPLEMENTED for each. Never mutates anything.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"capabilities": {"type": "object"}, "tool_version": {"type": "string"}},
        },
        artifact_types=["any"],
        capabilities=[],
        mutates_input=False,
        side_effects=False,
        dry_run_supported=True,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="not_applicable",
        evidence="capability report (JSON)",
        errors=[],
    ),
    ToolContract(
        name="contract",
        description="Emit this machine-readable contract — the single source of truth CLI and MCP "
        "are both generated from.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "object", "properties": {"schema": {"type": "string"}, "tools": {"type": "array"}}},
        artifact_types=["any"],
        capabilities=[],
        mutates_input=False,
        side_effects=False,
        dry_run_supported=True,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="not_applicable",
        evidence="contract document (JSON)",
        errors=[],
    ),
    ToolContract(
        name="inspect",
        description="Read-only discovery of what an artifact actually is: type (by content, not "
        "extension), structure, metadata, embedded assets, and anything suspicious. Never mutates.",
        input_schema={
            "type": "object",
            "properties": _INPUT_PATH_PROPERTY,
            "required": ["input"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"artifact": _ARTIFACT_REF_SCHEMA, "details": {"type": "object"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("structural"),
        mutates_input=False,
        side_effects=False,
        dry_run_supported=True,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="not_applicable",
        evidence="inspection report (JSON)",
        errors=["ARTIFACT_INPUT_NOT_FOUND", "ARTIFACT_PDF_UNREADABLE", "ARTIFACT_CAPABILITY_MISSING"],
    ),
    ToolContract(
        name="plan",
        description="Pure, read-only description of what `execute` would do for a given operation: "
        "adapter, required capabilities, files touched/created, rendering and verification strategy, "
        "risks. Never writes to disk.",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "operation": {"type": "string"},
                "args": {"type": "object"},
                "output": {"type": "string"},
            },
            "required": ["input", "operation"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"operation": {"type": "string"}, "risks": {"type": "array"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("structural"),
        mutates_input=False,
        side_effects=False,
        dry_run_supported=True,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="not_applicable",
        evidence="operation plan (JSON)",
        errors=["ARTIFACT_OPERATION_UNKNOWN", "ARTIFACT_INPUT_NOT_FOUND"],
    ),
    ToolContract(
        name="execute",
        description="Perform a planned mutating operation, writing only to an explicit output path. "
        "The input file is never overwritten (Original Protection, spec #28). `--dry-run` runs the "
        "identical plan/validation path but performs no I/O and spawns no subprocess.",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "operation": {"type": "string"},
                "args": {"type": "object"},
                "output": {"type": "string"},
            },
            "required": ["input", "operation"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"output": _ARTIFACT_REF_SCHEMA, "operation": {"type": "object"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("structural"),
        mutates_input=False,
        side_effects=True,
        dry_run_supported=True,
        verification_policy={"structural_required": "operation-dependent", "visual_required": "operation-dependent"},
        visual_requirement="operation-dependent (see per-operation OperationSpec in contract output)",
        evidence="operation record (JSON) + new output artifact",
        errors=[
            "ARTIFACT_OPERATION_UNKNOWN",
            "ARTIFACT_PDF_ENCRYPTED",
            "ARTIFACT_CAPABILITY_MISSING",
            "ARTIFACT_PATH_ESCAPE",
        ],
    ),
    ToolContract(
        name="render",
        description="Produce a visual representation of an artifact (e.g. one PNG per PDF page) so an "
        "Agent or human can look at it. Renders never mutate the source and are written to an explicit "
        "output directory.",
        input_schema={
            "type": "object",
            "properties": {**_INPUT_PATH_PROPERTY, "out_dir": {"type": "string"}},
            "required": ["input"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"files": {"type": "array"}, "backend": {"type": "string"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("render"),
        mutates_input=False,
        side_effects=True,
        dry_run_supported=True,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="produces_evidence_for_agent_to_inspect",
        evidence="page images (PNG)",
        errors=["ARTIFACT_CAPABILITY_MISSING", "ARTIFACT_RENDER_NOT_IMPLEMENTED"],
    ),
    ToolContract(
        name="verify",
        description="Run structural verification against an artifact and an optional policy (e.g. "
        "required page count/size, forbidden JavaScript, required metadata). Read-only. Visual "
        "verification evidence (if requested) is produced by `render`/`look` for the caller to judge; "
        "this command reports PASS/WARN/FAIL/UNKNOWN/NOT_CHECKED/SKIPPED per check, never inventing a "
        "verdict for something it could not check.",
        input_schema={
            "type": "object",
            "properties": {**_INPUT_PATH_PROPERTY, "policy": {"type": "object"}},
            "required": ["input"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"status": {"type": "string"}, "checks": {"type": "array"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("structural"),
        mutates_input=False,
        side_effects=False,
        dry_run_supported=True,
        verification_policy={"structural_required": True, "visual_required": False},
        visual_requirement="not_included_use_render_and_look",
        evidence="verification result (JSON)",
        errors=["ARTIFACT_INPUT_NOT_FOUND", "ARTIFACT_CAPABILITY_MISSING"],
    ),
    ToolContract(
        name="look",
        description="Convenience over `render`: produces a contact sheet (and, given a second artifact, "
        "a before/after comparison) sized for quick Agent visual inspection. This command only produces "
        "evidence images — it never itself judges quality; `verification.visual.inspected_by` in the "
        "receipt records that the verdict, if any, came from the Agent looking at these files.",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "compare_to": {"type": "string"},
                "out_dir": {"type": "string"},
            },
            "required": ["input"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"contact_sheet": {"type": "string"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("render"),
        mutates_input=False,
        side_effects=True,
        dry_run_supported=True,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="produces_evidence_for_agent_to_inspect",
        evidence="contact sheet image (PNG)",
        errors=["ARTIFACT_CAPABILITY_MISSING", "ARTIFACT_RENDER_NOT_IMPLEMENTED"],
    ),
    ToolContract(
        name="receipt",
        description="Run the full lifecycle (inspect -> plan -> execute -> render -> structural verify "
        "-> [fix loop] -> receipt) for one operation, or assemble a receipt from an already-produced "
        "evidence directory, and emit a Production Receipt (schema artifact-receipt/v1).",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "operation": {"type": "string"},
                "args": {"type": "object"},
                "output": {"type": "string"},
                "policy": {"type": "object"},
                "max_iterations": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["input", "operation"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"schema": {"type": "string"}, "status": {"type": "string"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("structural"),
        mutates_input=False,
        side_effects=True,
        dry_run_supported=True,
        verification_policy={"structural_required": True, "visual_required": "operation-dependent"},
        visual_requirement="operation-dependent; evidence files always produced when pdf.render is available",
        evidence="reports/receipt.json + reports/rendered/*.png",
        errors=[
            "ARTIFACT_OPERATION_UNKNOWN",
            "ARTIFACT_CAPABILITY_MISSING",
            "ARTIFACT_PDF_ENCRYPTED",
            "ARTIFACT_PATH_ESCAPE",
        ],
    ),
]

_TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def get_tool(name: str) -> ToolContract | None:
    return _TOOLS_BY_NAME.get(name)


def build_contract() -> dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA,
        "tool_name": "artifact-skill",
        "tool_version": __version__,
        "description": "Local-first execution and verification engine for AI-generated artifacts: "
        "inspect -> plan -> execute -> render -> structural verify -> visual verify -> fix -> "
        "re-verify -> receipt.",
        "capability_id_prefix": "artifact-skill",
        "tools": [t.to_dict() for t in TOOLS],
        "exit_codes": {
            "ok": EXIT_OK,
            "fail": EXIT_FAIL,
            **{cat.value: code for cat, code in EXIT_CODE_BY_CATEGORY.items()},
        },
        "network_policy": "off_by_default",
        "input_mutation_policy": "never",
    }

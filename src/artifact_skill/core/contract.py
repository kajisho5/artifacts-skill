"""The Contract — single source of truth for tool identity, MCP, and docs
(spec #20/#21).

`mcp/server.py` builds `tools/list` — including every `inputSchema` — by
reading `TOOLS` directly at runtime; there is no second copy of an MCP
tool's schema anywhere (`tests/contract/test_cli_mcp_consistency.py`'s
`test_mcp_tools_have_input_schema_matching_contract` asserts this by
comparing the live objects, not by convention). `mcp/server.py::call_tool()`
also *validates* every incoming call's arguments against this same
`input_schema` (via `core/schema_validate.py`) before dispatching — the
schema is an enforced contract, not just advertised metadata a caller
could ignore.

`cli/main.py`'s argparse subcommands are NOT generated from `TOOLS` at
runtime (a previous version of this docstring claimed they were, which
was inaccurate) — `build_parser()` hand-declares each subcommand's flags.
What keeps this from silently drifting is
`tests/contract/test_cli_mcp_consistency.py::test_cli_flags_match_input_schema_properties_in_both_directions`,
a mechanical check (not a convention) asserting every `input_schema`
property has a matching CLI flag and every CLI flag is either a schema
property or on an explicit CLI-only allowlist (`--json`, `--dry-run`,
etc. — invocation/output-format flags with no MCP equivalent). This is
still generation-by-hand rather than the "derive the schema from the one
thing that has to be correct" pattern SPEC uses (kajisho5/ffmpeg-skill's
README) — CLI-side generic `--args`/`--policy` JSON blobs have no
per-operation `argparse` parser to derive a schema *from* the way
ffmpeg-skill's per-tool scripts do — but drift is now caught by CI, not
merely possible to catch.

Each adapter's `OperationSpec.args_schema` (`adapters/base.py`) is still a
hand-authored JSON Schema dict declared in `operations()`, describing what
`execute()`/`plan()` expect out of `args` — but `core/engine.py`'s
`build_plan()` now validates every call's `args` against it
(`ARTIFACT_INVALID_ARGS` on a mismatch) before an adapter's `plan()` or
`execute()` ever runs, and a fixer's (`ArtifactAdapter.fix()`) proposed
retry args are validated the same way before being retried. The schema is
enforced, even though it is still hand-kept-in-sync with the code that
consumes it (no `jsonschema` dependency — see `core/schema_validate.py`'s
module docstring for why not).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artifact_skill import __version__
from artifact_skill.core.errors import EXIT_CODE_BY_CATEGORY, EXIT_FAIL, EXIT_OK

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
    # True only when this specific CLI/MCP tool accepts an explicit
    # dry_run/--dry-run parameter that short-circuits before any I/O
    # (execute/render/look/receipt). A read-only tool with side_effects=False
    # (doctor/contract/inspect/plan/verify) reports False here, not True -
    # there is nothing to preview because it already performs zero I/O, and
    # none of them expose a --dry-run flag a caller could actually pass.
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


def _registered_adapter_ids() -> list[str]:
    """Distinct adapter ids currently registered — distinct from
    `_registered_type_values()` because one adapter can cover several
    `ArtifactType`s (the image adapter handles PNG/JPEG/WebP as one
    implementation with one `image.*` capability, not three separate
    `image/png.*`/`image/jpeg.*`/`image/webp.*` ones)."""
    from artifact_skill.adapters.registry import get_adapter, registered_types

    seen: list[str] = []
    for artifact_type in registered_types():
        adapter_id = get_adapter(artifact_type).id
        if adapter_id not in seen:
            seen.append(adapter_id)
    return sorted(seen)


def _capability_ids(suffix: str) -> list[str]:
    """e.g. suffix='structural' -> ['docx.structural', 'pdf.structural', ...]
    across every currently-registered adapter (not type — see
    `_registered_adapter_ids()`)."""
    return [f"{a}.{suffix}" for a in _registered_adapter_ids()]


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
        dry_run_supported=False,
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
        dry_run_supported=False,
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
        dry_run_supported=False,
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
        dry_run_supported=False,
        verification_policy={"structural_required": False, "visual_required": False},
        visual_requirement="not_applicable",
        evidence="operation plan (JSON)",
        errors=["ARTIFACT_OPERATION_UNKNOWN", "ARTIFACT_INPUT_NOT_FOUND", "ARTIFACT_OUTPUT_OVERWRITES_INPUT"],
    ),
    ToolContract(
        name="execute",
        description="Perform a planned mutating operation, writing only to an explicit output path. "
        "The input file is never overwritten (Original Protection, spec #28). `--dry-run` runs the "
        "identical plan/validation path but performs no I/O and spawns no subprocess. Also runs "
        "structural verification against 'policy'/'policy_preset' (default: empty policy, which barely "
        "gates anything) and writes the result into its own reports/receipt.json - use `receipt` "
        "instead when you need max_iterations-driven auto-fixing on top of that verification.",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "operation": {"type": "string"},
                "args": {"type": "object"},
                "output": {"type": "string"},
                "policy": {"type": "object"},
                "policy_preset": {
                    "type": "string",
                    "description": "Named starting policy from policies.py (e.g. \"print-a4\", "
                    "\"web-no-external\"); 'policy' fields override it on conflict. See docs/verification.md.",
                },
                "evidence_dir": {
                    "type": "string",
                    "description": "Directory for execute's own reports/receipt.json (default: "
                    "\"./reports\", same default as `receipt` — FIX_PROMPT P2-5).",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Run the identical plan/validation path but perform no I/O and spawn "
                    "no subprocess (FIX_PROMPT P2-4: dry_run_supported=true means this MCP tool actually "
                    "accepts this field, not just the CLI's --dry-run). Returns the operation plan instead "
                    "of a real output/receipt.",
                },
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
            "ARTIFACT_OUTPUT_OVERWRITES_INPUT",
        ],
    ),
    ToolContract(
        name="render",
        description="Produce a visual representation of an artifact (e.g. one PNG per PDF page) so an "
        "Agent or human can look at it. Renders never mutate the source and are written to an explicit "
        "output directory.",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "out_dir": {"type": "string"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Report where output would be written and an estimated file count "
                    "without actually rendering (FIX_PROMPT P2-4).",
                },
            },
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
        errors=["ARTIFACT_CAPABILITY_MISSING", "ARTIFACT_RENDER_NOT_IMPLEMENTED", "ARTIFACT_TOO_MANY_PAGES"],
    ),
    ToolContract(
        name="verify",
        description="Run structural verification against an artifact and an optional policy (e.g. "
        "required page count/size, forbidden JavaScript, required metadata). Read-only, and never "
        "renders anything itself, so a fact that can only be measured by rendering (e.g. DOCX "
        "page_count, which has no fixed pagination in the XML) stays honestly UNKNOWN here even when "
        "`execute`/`receipt` would report it as a real, measured PASS (their lifecycle already renders "
        "for visual evidence, and reuses that render for exactly this) - use `receipt` (or `render` "
        "then `verify`) instead of bare `verify` if you need that fact measured. Visual verification "
        "evidence (if requested) is produced by `render`/`look` for the caller to judge; this command "
        "reports PASS/WARN/FAIL/UNKNOWN/NOT_CHECKED/SKIPPED per check, never inventing a verdict for "
        "something it could not check.",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "policy": {"type": "object"},
                "policy_preset": {
                    "type": "string",
                    "description": "Named starting policy from policies.py (e.g. \"print-a4\", "
                    "\"web-no-external\"); 'policy' fields override it on conflict. See docs/verification.md.",
                },
            },
            "required": ["input"],
            "additionalProperties": False,
        },
        output_schema={"type": "object", "properties": {"status": {"type": "string"}, "checks": {"type": "array"}}},
        artifact_types=_registered_type_values(),
        capabilities=_capability_ids("structural"),
        mutates_input=False,
        side_effects=False,
        dry_run_supported=False,
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
                "dry_run": {
                    "type": "boolean",
                    "description": "Report where the contact sheet would be written without actually "
                    "rendering (FIX_PROMPT P2-4).",
                },
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
        errors=["ARTIFACT_CAPABILITY_MISSING", "ARTIFACT_RENDER_NOT_IMPLEMENTED", "ARTIFACT_TOO_MANY_PAGES"],
    ),
    ToolContract(
        name="receipt",
        description="Run the full lifecycle (inspect -> plan -> execute -> render -> structural verify "
        "-> [fix loop] -> receipt) for one mutating operation and emit a Production Receipt (schema "
        "artifact-receipt/v1). 'operation' is optional: omit it for a verify-only receipt (inspect -> "
        "render -> structural verify -> receipt, no mutation, no fix loop) - the only way to get a "
        "Production Receipt for a format with zero mutating operations (HTML, SVG). 'output' is invalid "
        "without 'operation' (nothing is written without a mutation).",
        input_schema={
            "type": "object",
            "properties": {
                **_INPUT_PATH_PROPERTY,
                "operation": {"type": "string"},
                "args": {"type": "object"},
                "output": {"type": "string"},
                "policy": {"type": "object"},
                "policy_preset": {
                    "type": "string",
                    "description": "Named starting policy from policies.py (e.g. \"print-a4\", "
                    "\"web-no-external\"); 'policy' fields override it on conflict. See docs/verification.md.",
                },
                "max_iterations": {
                    "type": "integer", "minimum": 1, "maximum": 10,
                    "description": "Fix-loop retry cap. Default (when omitted): Limits.max_fix_iterations "
                    "(currently 3) - omitting this does NOT mean 'don't retry.' Meaningless without "
                    "'operation' (there is no fix loop for a verify-only receipt).",
                },
                "evidence_dir": {
                    "type": "string",
                    "description": "Directory for the receipt and evidence (default: \"./reports\"). "
                    "FIX_PROMPT P2-5/self-audit: this was already read by the MCP handler but was never a "
                    "declared schema property, so additionalProperties:false silently rejected every real "
                    "MCP caller that tried to pass it — dead code in practice, now reachable.",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Run the identical plan/validation path but perform no I/O and spawn "
                    "no subprocess (FIX_PROMPT P2-4). Returns the operation plan instead of a real receipt.",
                },
            },
            "required": ["input"],
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
            "ARTIFACT_OUTPUT_OVERWRITES_INPUT",
        ],
    ),
]

_TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def get_tool(name: str) -> ToolContract | None:
    return _TOOLS_BY_NAME.get(name)


def build_contract() -> dict[str, Any]:
    return {
        "schema": CONTRACT_SCHEMA,
        "tool_name": "artifacts-skill",
        "tool_version": __version__,
        "description": "Local-first execution and verification engine for AI-generated artifacts: "
        "inspect -> plan -> execute -> render -> structural verify -> visual verify -> fix -> "
        "re-verify -> receipt.",
        "capability_id_prefix": "artifacts-skill",
        "tools": [t.to_dict() for t in TOOLS],
        "exit_codes": {
            "ok": EXIT_OK,
            "fail": EXIT_FAIL,
            **{cat.value: code for cat, code in EXIT_CODE_BY_CATEGORY.items()},
        },
        "network_policy": "off_by_default",
        "input_mutation_policy": "never",
    }

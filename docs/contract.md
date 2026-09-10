# Contract

`artifacts-skill contract --json` is the single source of truth for every
tool this project exposes. `src/artifact_skill/core/contract.py` defines it
as a plain Python list of `ToolContract` dataclasses (`TOOLS`); `cli/main.py`
and `mcp/server.py` both read from it instead of hand-declaring schemas, and
`tests/contract/test_cli_mcp_consistency.py` asserts the CLI subcommand set,
the MCP `tools/list` set, and `TOOLS` never disagree.

Run `artifacts-skill contract --json` for the live, exact document; what
follows is a guide to reading it, not a copy that can drift out of date.

**Published JSON Schema** (Issue #16): [`schemas/artifact-contract-v1.schema.json`](../schemas/artifact-contract-v1.schema.json)
is a real, standalone JSON Schema file an external consumer can validate a
contract document against without running this tool or reading its source.
`tests/schemas/test_schema_files.py::test_real_contract_document_matches_its_schema`
validates a freshly-generated real `build_contract()` output against it on
every CI run — the schema file can't silently drift from what the tool
actually emits.

## Top-level shape

```json
{
  "schema": "artifact-contract/v1",
  "tool_name": "artifacts-skill",
  "tool_version": "0.2.0",
  "description": "...",
  "capability_id_prefix": "artifacts-skill",
  "tools": [ /* ToolContract[] */ ],
  "exit_codes": { "ok": 0, "fail": 1, "input": 2, "capability": 3, "security": 4, "execution": 1, "verification": 1, "internal": 5 },
  "network_policy": "off_by_default",
  "input_mutation_policy": "never"
}
```

## Per-tool shape (`ToolContract.to_dict()`)

```json
{
  "name": "execute",
  "description": "...",
  "input_schema": { "...": "JSON Schema" },
  "output_schema": { "...": "JSON Schema" },
  "artifact_types": ["pdf", "pptx"],
  "capabilities": ["pdf.structural", "pptx.structural"],
  "side_effects": {
    "mutates_input": false,
    "writes_files": true,
    "dry_run_supported": true
  },
  "verification_policy": { "structural_required": "operation-dependent", "visual_required": "operation-dependent" },
  "visual_requirement": "operation-dependent (see per-operation OperationSpec in contract output)",
  "evidence": "operation record (JSON) + new output artifact",
  "errors": ["ARTIFACT_OPERATION_UNKNOWN", "ARTIFACT_PDF_ENCRYPTED", "ARTIFACT_CAPABILITY_MISSING", "ARTIFACT_PATH_ESCAPE"]
}
```

`mutates_input` is `false` for every tool in this contract, unconditionally
— Original Protection (spec §28) is a project-wide invariant, not a
per-tool option. `side_effects.writes_files` distinguishes read-only tools
(`doctor`, `contract`, `inspect`, `plan`, `verify`) from tools that create
new files (`execute`, `render`, `look`, `receipt`) — all of which still
never touch the input path itself.

For `execute` and `receipt`, the *actual* per-operation verification policy
(whether structural/visual verification is required, whether rendering is
required, and known limitations) comes from the adapter's own
`OperationSpec` — see `adapters/base.py` — not from the tool-level contract,
because that policy is genuinely operation-specific (e.g. `pdf.metadata_set`
requires structural verification but not visual; `pdf.merge` requires both).
`plan` and `execute` responses both surface this via `verification_strategy`.

## The nine tools

| Tool | Mutates input | Writes files | Dry-run |
|---|---|---|---|
| `doctor` | no | no | n/a (always read-only) |
| `contract` | no | no | n/a |
| `inspect` | no | no | n/a |
| `plan` | no | no | n/a (plan is always a dry run) |
| `execute` | no | yes | yes — zero I/O, zero subprocess calls |
| `render` | no | yes | yes — reports intended output, writes nothing |
| `verify` | no | no | n/a |
| `look` | no | yes | yes — reports intended output, writes nothing |
| `receipt` | no | yes | yes — returns the plan only |

## Capability IDs

Format: `<format>.<capability>`, e.g. `pdf.structural`, `pdf.render`. These
are the ids used in `required_capabilities` (plan/execute output),
`ArtifactCapabilityError.evidence.capability_id`, and the `doctor` report.
They are namespaced by format, not by tool — `pdf.structural` covers
`inspect`, `verify`, `metadata_set`, and `merge` alike, because that is the
actual dependency (pypdf), not an artificial per-tool split.

`ToolContract.artifact_types` and `.capabilities` are computed at import
time from `adapters/registry.py::registered_types()`, not hand-listed —
see `core/contract.py::_registered_type_values()`. A new adapter landing
(PPTX did, see `docs/roadmap.md`) automatically appears in every tool's
contract entry without a second place to remember to update it.

Per spec §40, the longer-term capability-id convention for cross-tool
addressing (e.g. from an external orchestrator) is
`artifacts-skill.<tool>` for whole tools (see `mcp/server.py`'s
`_mcp_tool_name`) and `artifacts-skill.<format>.<capability>` for finer
capability probing — the latter is not yet exposed as a separate lookup
API beyond the `doctor` report, since nothing outside this repo consumes it
yet; `docs/roadmap.md` tracks this under ecosystem integration.

**Dogfooded, not just asserted**: `examples/standalone_contract_consumer.py`
is a script that imports nothing from `artifact_skill` and discovers
which tool to call purely from `contract --json`'s declared semantics —
proof that an external, unfamiliar consumer really can drive this tool
from the contract alone (Issue #10). Run on every CI push, not just once
by hand; see `docs/roadmap.md`'s "Phase 7" writeup.

## Versioning

- `artifact-contract/v1` — this document's schema.
- `artifact-receipt/v1` — the receipt schema (`docs/verification.md`,
  `receipt/model.py`).
- Tool version (`tool_version` in the contract and every receipt) follows
  the package's own SemVer (`artifact_skill.__version__`).

A breaking change to a tool's input/output shape bumps `artifact-contract`
to `v2` and both schemas are supported side-by-side for one deprecation
window; additive changes (a new optional field, a new tool, a new
capability id) do not bump the schema version. No breaking change has
happened yet — this project is at `0.2.0`.

## Relationship to SPEC (kajisho5/ffmpeg-skill)

This project's author also authored `kajisho5/ffmpeg-skill`, which coined
**SPEC** (Self-Producing Execution Contract): each tool's `input_schema` is
derived at runtime from the one thing that has to be correct for the CLI
to work at all — the script's own `argparse` parser — rather than
hand-authored beside the code. Asked directly whether this project follows
that pattern, the honest answer, checked against the code rather than
assumed:

- **MCP is SPEC-shaped.** `mcp/server.py::build_tools_list()` derives every
  `inputSchema` from `TOOLS` at runtime, and `call_tool()` validates
  incoming arguments against that same schema (`core/schema_validate.py`)
  before dispatch — there is no second, driftable copy, and the schema is
  enforced, not just advertised.
- **The CLI is not, and structurally can't fully be**, because
  `cli/main.py`'s tools take a generic `--operation`/`--args {json}` shape
  rather than ffmpeg-skill's one-`argparse`-parser-per-tool design — there
  is no live per-operation parser to capture a schema *from* the way SPEC
  does. What this project has instead: `input_schema` and `OperationSpec.args_schema`
  are both hand-authored (as they always were), but now both are
  **enforced** — `core/engine.py::build_plan()` validates operation `args`
  against `OperationSpec.args_schema` before any adapter runs (including a
  fixer's proposed retry args), and a mechanical test
  (`tests/contract/test_cli_mcp_consistency.py::test_cli_flags_match_input_schema_properties_in_both_directions`)
  asserts every CLI flag and every `input_schema` property still agree,
  in both directions, so a drift now fails CI instead of only being
  possible.
- **What's still open**: `cli/main.py`'s flags remain hand-declared rather
  than generated, so keeping them in sync with `TOOLS` is still a human
  responsibility the test merely checks after the fact, not a class of bug
  SPEC eliminates outright. Closing that gap fully would mean generating
  `build_parser()` from `TOOLS` — not done, since the generic `--args`
  JSON blob shape means most of what would be generated is boilerplate
  (`--operation`, `--args`, `--output`) rather than the rich, tool-specific
  flag sets SPEC was designed for.

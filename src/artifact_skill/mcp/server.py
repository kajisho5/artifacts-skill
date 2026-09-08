"""Minimal, dependency-free MCP server (stdio, newline-delimited JSON-RPC).

`tools/list` is generated from `core/contract.py` — nobody hand-writes a
second tool schema here. Tool execution reuses the exact same functions
`cli/main.py` calls (`core/engine.py`, `adapters/registry.py`,
`doctor/detect.py`), so CLI and MCP can never silently diverge in behavior,
only in schema — and `tests/contract/` checks the schema stays in sync too.

No `mcp` SDK dependency: the stdio transport is a small enough protocol
(newline-delimited JSON-RPC 2.0 messages) that adding a dependency for it
would cut against this project's local-first, few-dependencies stance.

This implements the **legacy**, `initialize`-handshake-based MCP protocol
(spec terminology: any revision `2025-11-25` or earlier — this server was
built against `2024-11-05`). Verified directly against the current spec
(`modelcontextprotocol.io/specification`) while fixing Issue #12: a much
larger revision landed at `2026-07-28` that drops the handshake entirely in
favor of per-request version metadata (`server/discover`,
`UnsupportedProtocolVersionError`, etc.) — real MCP clients as of that
revision are "dual-era" and fall back to `initialize` against a server like
this one, so this server keeps working, but it does not itself implement
`server/discover` or per-request metadata. That would be a separate,
substantially larger project, not a natural extension of Issue #12's
narrower "negotiate the version this server already claims to speak" scope
— left as a deliberate, documented gap rather than a half-implemented
attempt at the newer spec.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from artifact_skill import __version__
from artifact_skill.adapters.registry import adapter_for
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.contract import TOOLS, build_contract, get_tool
from artifact_skill.core.engine import build_plan, run_lifecycle
from artifact_skill.core.errors import ArtifactError, ArtifactInputError, ErrorCategory
from artifact_skill.core.operation import default_output_path
from artifact_skill.core.schema_validate import validate_against_schema
from artifact_skill.doctor.detect import detect_environment
from artifact_skill.rendering.contact_sheet import build_before_after, build_contact_sheet

SERVER_NAME = "artifact-skill"
CAPABILITY_PREFIX = "artifact-skill"
PROTOCOL_VERSION = "2024-11-05"

_VERSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _negotiate_protocol_version(requested: Any) -> str:
    """Echo back the client's requested legacy protocol version if it looks
    like a real one, rather than always claiming this server's own default
    regardless of what was asked (the bug this closes: initialize() used to
    ignore `params` entirely). This server's implemented method surface
    (`initialize`/`tools/list`/`tools/call`/`ping`) hasn't changed shape
    across the legacy protocol era, so it can honestly speak whatever
    legacy `YYYY-MM-DD` version the client names — there's nothing
    version-gated here to actually be incompatible about. Falls back to
    `PROTOCOL_VERSION` when the client didn't send one, or sent something
    that isn't a plausible date-string version identifier.
    """
    if isinstance(requested, str) and _VERSION_RE.match(requested):
        return requested
    return PROTOCOL_VERSION


def _mcp_tool_name(tool_name: str) -> str:
    return f"{CAPABILITY_PREFIX}.{tool_name}"


def build_tools_list() -> list[dict[str, Any]]:
    """Derive MCP `tools/list` entries from the contract. This is the only
    function allowed to know the mapping between a ToolContract and MCP's
    {name, description, inputSchema} shape."""
    return [
        {
            "name": _mcp_tool_name(t.name),
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        for t in TOOLS
    ]


def _text_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": is_error}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    short_name = name[len(CAPABILITY_PREFIX) + 1 :] if name.startswith(CAPABILITY_PREFIX + ".") else name
    try:
        handler = _HANDLERS.get(short_name)
        if handler is None:
            raise ArtifactError(
                code="ARTIFACT_MCP_TOOL_UNKNOWN",
                category=ErrorCategory.INPUT,
                message=f"Unknown tool '{name}'.",
            )
        # Unlike the CLI (where argparse enforces required flags/types before a
        # handler ever runs), MCP `arguments` arrives as a raw, untyped JSON
        # object from an external caller — nothing upstream of this call
        # guaranteed e.g. "input" is even present. Validating against the same
        # `input_schema` MCP itself advertises via `tools/list` turns that
        # schema into an enforced contract instead of descriptive-only
        # metadata, and replaces what would otherwise be an unhandled KeyError
        # (crashing request handling) with a structured error response.
        tool = get_tool(short_name)
        if tool is not None:
            errors = validate_against_schema(arguments, tool.input_schema)
            if errors:
                raise ArtifactInputError(
                    code="ARTIFACT_INVALID_ARGS",
                    message=f"Arguments for tool '{name}' do not match its input_schema: {'; '.join(errors)}",
                    remediation="Check this server's tools/list response for the tool's inputSchema.",
                    evidence={"tool": name, "errors": errors},
                )
        result = handler(arguments)
        return _text_result(result)
    except ArtifactError as exc:
        return _text_result({"error": exc.to_dict()}, is_error=True)


def _h_doctor(_args: dict[str, Any]) -> dict[str, Any]:
    report = detect_environment()
    return {"tool_version": __version__, "capabilities": report.to_dict()}


def _h_contract(_args: dict[str, Any]) -> dict[str, Any]:
    return build_contract()


def _h_inspect(args: dict[str, Any]) -> dict[str, Any]:
    ref = ArtifactRef.from_path(args["input"])
    adapter = adapter_for(ref)
    return adapter.inspect(ref).to_dict()


def _h_plan(args: dict[str, Any]) -> dict[str, Any]:
    input_path = Path(args["input"])
    operation = args["operation"]
    output_path = Path(args["output"]) if args.get("output") else default_output_path(input_path, operation)
    _ref, _adapter, plan = build_plan(input_path, operation, args.get("args", {}), output_path)
    return plan.to_dict()


def _h_execute(args: dict[str, Any]) -> dict[str, Any]:
    input_path = Path(args["input"])
    operation = args["operation"]
    output_path = Path(args["output"]) if args.get("output") else default_output_path(input_path, operation)
    evidence_dir = output_path.parent / "reports"
    result = run_lifecycle(input_path, operation, args.get("args", {}), output_path, evidence_dir=evidence_dir)
    return {
        "output": result.output.to_dict() if result.output else None,
        "receipt": result.receipt.to_dict() if result.receipt else None,
    }


def _h_render(args: dict[str, Any]) -> dict[str, Any]:
    ref = ArtifactRef.from_path(args["input"])
    adapter = adapter_for(ref)
    out_dir = Path(args["out_dir"]) if args.get("out_dir") else Path("reports") / "rendered"
    return adapter.render(ref, out_dir).to_dict()


def _h_verify(args: dict[str, Any]) -> dict[str, Any]:
    ref = ArtifactRef.from_path(args["input"])
    adapter = adapter_for(ref)
    return adapter.verify_structural(ref, args.get("policy", {})).to_dict()


def _h_look(args: dict[str, Any]) -> dict[str, Any]:
    ref = ArtifactRef.from_path(args["input"])
    adapter = adapter_for(ref)
    out_dir = Path(args["out_dir"]) if args.get("out_dir") else Path("reports")
    rendered = adapter.render(ref, out_dir / "rendered")
    if args.get("compare_to"):
        other_ref = ArtifactRef.from_path(args["compare_to"])
        other_adapter = adapter_for(other_ref)
        other_rendered = other_adapter.render(other_ref, out_dir / "rendered_compare")
        sheet_path = build_before_after(rendered.files, other_rendered.files, out_dir / "before-after.png")
    else:
        sheet_path = build_contact_sheet(rendered.files, out_dir / "contact-sheet.png")
    return {"contact_sheet": str(sheet_path), "source_pages": len(rendered.files)}


def _h_receipt(args: dict[str, Any]) -> dict[str, Any]:
    input_path = Path(args["input"])
    operation = args["operation"]
    output_path = Path(args["output"]) if args.get("output") else default_output_path(input_path, operation)
    evidence_dir = Path(args.get("evidence_dir", "reports"))
    result = run_lifecycle(
        input_path, operation, args.get("args", {}), output_path,
        policy=args.get("policy", {}), evidence_dir=evidence_dir,
        max_iterations=args.get("max_iterations", 1),
    )
    return result.receipt.to_dict() if result.receipt else {"status": "fail", "error": "execution did not complete"}


_HANDLERS = {
    "doctor": _h_doctor,
    "contract": _h_contract,
    "inspect": _h_inspect,
    "plan": _h_plan,
    "execute": _h_execute,
    "render": _h_render,
    "verify": _h_verify,
    "look": _h_look,
    "receipt": _h_receipt,
}


def _handle_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        result = {
            "protocolVersion": _negotiate_protocol_version(params.get("protocolVersion")),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        result = {"tools": build_tools_list()}
    elif method == "tools/call":
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    elif method == "ping":
        result = {}
    else:
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = _handle_request(msg)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()

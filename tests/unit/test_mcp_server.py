from __future__ import annotations

import json

from artifact_skill.mcp.server import CAPABILITY_PREFIX, call_tool


def test_missing_required_argument_returns_structured_error_not_a_crash(good_pdf):
    """Regression guard: before input_schema was enforced at the MCP
    boundary, `_h_inspect`'s `args["input"]` would raise an uncaught
    KeyError for a malformed call — this asserts a well-formed
    ARTIFACT_INVALID_ARGS error comes back instead of an exception
    escaping call_tool()."""
    result = call_tool(f"{CAPABILITY_PREFIX}.inspect", {})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"


def test_wrong_type_argument_is_rejected(good_pdf):
    result = call_tool(f"{CAPABILITY_PREFIX}.inspect", {"input": 12345})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"


def test_valid_call_still_works(good_pdf):
    result = call_tool(f"{CAPABILITY_PREFIX}.inspect", {"input": str(good_pdf)})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["details"]["page_count"] == 2


def test_extra_argument_rejected_when_schema_forbids_additional_properties():
    """doctor's input_schema declares additionalProperties: False — a
    stray argument must be rejected, not silently ignored."""
    result = call_tool(f"{CAPABILITY_PREFIX}.doctor", {"unexpected": True})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"


def test_doctor_with_empty_arguments_still_works():
    result = call_tool(f"{CAPABILITY_PREFIX}.doctor", {})
    assert result["isError"] is False


def test_unknown_tool_name_still_reports_its_own_error_code():
    result = call_tool(f"{CAPABILITY_PREFIX}.not_a_real_tool", {})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_MCP_TOOL_UNKNOWN"


def test_execute_rejects_operation_args_that_violate_operation_schema(good_pdf, tmp_path):
    """Two layers of schema now apply to an MCP execute call: the
    top-level input_schema (checked in call_tool itself) and the
    operation-specific args_schema (checked inside engine.build_plan via
    the operation's own OperationSpec) once dispatch reaches the handler.
    """
    result = call_tool(
        f"{CAPABILITY_PREFIX}.execute",
        {
            "input": str(good_pdf),
            "operation": "fit_page_size",
            "args": {"width_pt": "not a number", "height_pt": 100},
            "output": str(tmp_path / "out.pdf"),
        },
    )
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"

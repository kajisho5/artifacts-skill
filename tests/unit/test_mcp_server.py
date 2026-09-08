from __future__ import annotations

import json

from artifact_skill.mcp.server import (
    CAPABILITY_PREFIX,
    PROTOCOL_VERSION,
    _handle_request,
    _negotiate_protocol_version,
    call_tool,
    serve,
)


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


# --- protocol version negotiation (Issue #12) -----------------------------


def test_negotiate_echoes_a_plausible_client_version():
    assert _negotiate_protocol_version("2025-06-18") == "2025-06-18"


def test_negotiate_falls_back_when_client_sends_nothing():
    assert _negotiate_protocol_version(None) == PROTOCOL_VERSION


def test_negotiate_falls_back_on_garbage_input():
    assert _negotiate_protocol_version("not-a-version") == PROTOCOL_VERSION
    assert _negotiate_protocol_version(12345) == PROTOCOL_VERSION
    assert _negotiate_protocol_version("") == PROTOCOL_VERSION


def test_negotiate_falls_back_on_the_last_legacy_version():
    assert _negotiate_protocol_version("2025-11-25") == "2025-11-25"


def test_negotiate_does_not_echo_a_post_legacy_version():
    """Regression guard for a real bug in the first version-negotiation fix:
    matching only the YYYY-MM-DD *shape* let this server tell a modern
    client "yes, let's talk 2026-07-28" (the server/discover-based
    revision) when it only implements the initialize-handshake protocol -
    a worse lie than always claiming its own fixed version, since the
    client would now expect server/discover support that doesn't exist."""
    assert _negotiate_protocol_version("2026-07-28") == PROTOCOL_VERSION
    assert _negotiate_protocol_version("2099-01-01") == PROTOCOL_VERSION


def test_initialize_response_echoes_the_requested_version():
    """Regression guard: initialize() used to always return the server's
    own fixed PROTOCOL_VERSION regardless of what the client actually
    asked for in its initialize request's params."""
    response = _handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    assert response["result"]["protocolVersion"] == "2025-03-26"


def test_initialize_response_falls_back_without_a_requested_version():
    response = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION


def test_initialize_response_falls_back_with_no_params_at_all():
    response = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION


# --- verify-only receipt (Issue #18) ----------------------------------------


def test_receipt_without_operation_is_a_real_verify_only_receipt(good_html):
    """HTML has zero mutating operations - before Issue #18 there was no
    way to get a Production Receipt for it at all over MCP either."""
    result = call_tool(f"{CAPABILITY_PREFIX}.receipt", {"input": str(good_html)})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "pass"
    assert payload["operations"] == []


def test_receipt_without_operation_rejects_output(good_html):
    result = call_tool(f"{CAPABILITY_PREFIX}.receipt", {"input": str(good_html), "output": "out.html"})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"


# --- policy presets over MCP (Issue #14) -----------------------------------


def test_execute_gates_its_own_receipt_with_a_policy(good_pdf, tmp_path):
    """Regression guard: _h_execute() used to call run_lifecycle() with no
    policy at all, so execute's own receipt always verified against an
    empty policy no matter what the caller wanted."""
    output = str(tmp_path / "out.pdf")
    result = call_tool(
        f"{CAPABILITY_PREFIX}.execute",
        {
            "input": str(good_pdf), "operation": "metadata_set", "args": {"title": "x"},
            "output": output, "policy": {"require_page_count": 999},
        },
    )
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    checks = payload["receipt"]["verification"]["structural"]["checks"]
    check = next(c for c in checks if c["id"] == "page_count_requirement")
    assert check["status"] == "fail"


def test_verify_accepts_a_policy_preset(good_pdf):
    """good_2page.pdf is US Letter; print-a4 requires A4, so this preset
    alone should be enough to fail the page-size check over MCP too."""
    result = call_tool(f"{CAPABILITY_PREFIX}.verify", {"input": str(good_pdf), "policy_preset": "print-a4"})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    check = next(c for c in payload["checks"] if c["id"] == "page_size_requirement")
    assert check["status"] == "fail"


def test_verify_unknown_policy_preset_returns_structured_error(good_pdf):
    result = call_tool(f"{CAPABILITY_PREFIX}.verify", {"input": str(good_pdf), "policy_preset": "not-a-real-preset"})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"
    assert "not-a-real-preset" in payload["error"]["message"]


def test_verify_misspelled_policy_key_returns_structured_error_not_a_silent_pass(good_pdf):
    """Issue #24: same guarantee as the CLI - a typo'd policy key over MCP
    must be rejected, not silently ignored by every adapter's policy.get()."""
    result = call_tool(f"{CAPABILITY_PREFIX}.verify", {"input": str(good_pdf), "policy": {"min_pagess": 1}})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_INVALID_ARGS"
    assert "min_pagess" in payload["error"]["message"]


# --- non-ArtifactError exceptions must not crash the session (Issue #23) ---


def test_unexpected_non_artifact_exception_is_reported_not_raised(good_pdf, monkeypatch):
    """Regression guard: call_tool() used to only catch ArtifactError, so any
    other exception type (e.g. a third-party parsing error) propagated out of
    call_tool() uncaught - fatal for serve()'s stdio loop, which would crash
    the whole long-running MCP session over a single bad request."""
    import artifact_skill.mcp.server as server_module

    def _boom(_args):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setitem(server_module._HANDLERS, "inspect", _boom)

    result = call_tool(f"{CAPABILITY_PREFIX}.inspect", {"input": str(good_pdf)})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "ARTIFACT_MCP_TOOL_INTERNAL_ERROR"
    assert payload["error"]["category"] == "internal"
    assert "simulated unexpected failure" in payload["error"]["message"]


# --- malformed JSON-RPC input gets a real parse-error response (Issue #32) --


def test_malformed_json_line_gets_a_parse_error_response_not_silence():
    import io

    stdin = io.StringIO("not valid json at all\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1
    response = json.loads(lines[0])
    assert response == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}


def test_malformed_json_line_does_not_stop_the_session_from_handling_the_next_line(good_pdf):
    import io

    valid_request = json.dumps(
        {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": f"{CAPABILITY_PREFIX}.inspect", "arguments": {"input": str(good_pdf)}},
        }
    )
    stdin = io.StringIO(f"{{broken json\n{valid_request}\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    parse_error, real_response = (json.loads(line) for line in lines)
    assert parse_error["error"]["code"] == -32700
    assert real_response["id"] == 1
    assert real_response["result"]["isError"] is False

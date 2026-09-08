"""Enforces spec #52/#53: CLI, MCP, and the contract can never silently
drift apart. This is the test that makes `core/contract.py` a real single
source of truth rather than a nice idea.
"""

from __future__ import annotations

from artifact_skill.cli.main import build_parser
from artifact_skill.core.contract import TOOLS, build_contract
from artifact_skill.mcp.server import CAPABILITY_PREFIX, build_tools_list


def test_contract_has_every_tool_exactly_once():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    assert set(names) == {"doctor", "contract", "inspect", "plan", "execute", "render", "verify", "look", "receipt"}


def test_cli_subcommands_match_contract_tools():
    parser = build_parser()
    subparsers_action = next(a for a in parser._subparsers._group_actions if a.dest == "command")
    cli_commands = set(subparsers_action.choices.keys())
    contract_names = {t.name for t in TOOLS}
    assert cli_commands == contract_names


def test_mcp_tools_list_matches_contract():
    mcp_tools = {t["name"] for t in build_tools_list()}
    expected = {f"{CAPABILITY_PREFIX}.{t.name}" for t in TOOLS}
    assert mcp_tools == expected


def test_mcp_tools_have_input_schema_matching_contract():
    contract_by_name = {t.name: t for t in TOOLS}
    for entry in build_tools_list():
        short_name = entry["name"].split(".", 1)[1]
        assert entry["inputSchema"] == contract_by_name[short_name].input_schema
        assert entry["description"] == contract_by_name[short_name].description


def test_every_tool_declares_mutates_input_false():
    """Original Protection is a project-wide invariant, not per-tool opt-in."""
    for tool in build_contract()["tools"]:
        assert tool["side_effects"]["mutates_input"] is False


def test_every_tool_has_a_dry_run_flag_declared():
    for tool in build_contract()["tools"]:
        assert "dry_run_supported" in tool["side_effects"]


def test_contract_schema_and_exit_codes_present():
    contract = build_contract()
    assert contract["schema"] == "artifact-contract/v1"
    assert contract["exit_codes"]["ok"] == 0
    assert contract["exit_codes"]["fail"] == 1
    assert contract["network_policy"] == "off_by_default"
    assert contract["input_mutation_policy"] == "never"

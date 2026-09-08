#!/usr/bin/env python3
"""Standalone consumer of `artifacts-skill contract --json`.

This is the dogfood proof for Issue #10 / spec #40 ("ecosystem
integration"): can an external, unfamiliar orchestrator — the kind of
thing the original design brief called an "AI-video-production-OS" —
actually drive this tool by reading nothing but the machine-readable
contract, without importing this package or reading any of its source?

Deliberately imports NOTHING from `artifact_skill` and uses only the
Python standard library. It shells out to the installed `artifacts-skill`
console script exactly the way an unrelated external process would.

It also doesn't hardcode the tool name "inspect" — it *discovers* a
suitable read-only, single-artifact-input tool from the contract's own
declared semantics (`side_effects.mutates_input`,
`side_effects.writes_files`, `input_schema`), which is a stronger proof
than just knowing this repo's tool names in advance.

Usage:
    python3 examples/standalone_contract_consumer.py <path-to-an-artifact>

Exit code 0 on success, 1 if the discovered tool reported a structured
error, 2 on a usage/discovery problem.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys


def _run_cli(*args: str) -> dict:
    binary = shutil.which("artifacts-skill")
    cmd = [binary, *args] if binary else [sys.executable, "-m", "artifact_skill.cli.main", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)  # noqa: S603 - cmd[0] resolved via shutil.which()
    if not proc.stdout.strip():
        raise SystemExit(f"error: '{' '.join(cmd)}' produced no stdout.\nstderr: {proc.stderr}")
    return json.loads(proc.stdout)


def _discover_readonly_single_input_tool(contract: dict) -> str:
    """Pick a tool purely from what the contract *says* about it — never
    by assuming a name like "inspect" exists."""
    candidates = [
        t["name"]
        for t in contract["tools"]
        if t["side_effects"]["mutates_input"] is False
        and t["side_effects"]["writes_files"] is False
        and "input" in t["input_schema"].get("properties", {})
        and t["input_schema"].get("required") == ["input"]  # *only* input required — no operation/etc. to supply
    ]
    if not candidates:
        raise SystemExit("error: the contract advertises no read-only, single-artifact-input tool.")
    print(f"Discovered read-only, single-input tool(s) from the contract alone: {candidates}")
    return candidates[0]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <path-to-an-artifact>", file=sys.stderr)
        return 2
    target = argv[1]

    # Step 1: the only thing this script knows ahead of time is how to ask
    # for the contract and how to invoke a discovered tool by name
    # (`<tool> <input> --json`, per docs/contract.md) — everything else
    # about what this tool can do is read from the JSON below.
    contract = _run_cli("contract", "--json")
    print(f"Read contract '{contract['schema']}' for '{contract['tool_name']}' {contract['tool_version']}.")

    # Step 2: discover a tool to call from the contract's declared
    # semantics, not a hardcoded name.
    tool_name = _discover_readonly_single_input_tool(contract)

    # Step 3: call it.
    result = _run_cli(tool_name, target, "--json")
    if "error" in result:
        error = result["error"]
        print(f"'{tool_name}' reported a structured error: {error['code']} — {error['message']}")
        return 1

    artifact = result.get("artifact", {})
    print(f"Success. '{tool_name}' (discovered from the contract) reports:")
    print(f"  type:    {artifact.get('type')}")
    print(f"  sha256:  {artifact.get('sha256')}")
    print(f"  size:    {artifact.get('size_bytes')} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""`artifact-skill` CLI — a thin, honest layer over core/engine.py.

Every subcommand here corresponds 1:1 to a `ToolContract` in
`core/contract.py`. Two mechanical checks in
`tests/contract/test_cli_mcp_consistency.py` keep this file from silently
drifting from the contract:
`test_cli_subcommands_match_contract_tools` (subcommand names) and
`test_cli_flags_match_input_schema_properties_in_both_directions` (each
subcommand's actual flags against `TOOLS[*].input_schema`'s properties,
both directions, modulo an explicit CLI-only allowlist for flags like
`--json`/`--dry-run` that have no MCP equivalent). The flags below are
still hand-declared, not generated from `TOOLS` at runtime (see
`core/contract.py`'s module docstring for why, and for the SPEC
comparison) — but an edit that breaks the correspondence now fails CI
instead of only failing silently at runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from artifact_skill import __version__
from artifact_skill.adapters.registry import adapter_for
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.contract import build_contract
from artifact_skill.core.engine import build_plan, run_lifecycle
from artifact_skill.core.errors import EXIT_CODE_BY_CATEGORY, EXIT_FAIL, EXIT_OK, ArtifactError, ErrorCategory
from artifact_skill.core.operation import default_output_path
from artifact_skill.core.verification import CheckStatus
from artifact_skill.doctor.detect import detect_environment
from artifact_skill.policies import PRESETS as _PRESET_NAMES
from artifact_skill.policies import resolve_policy
from artifact_skill.rendering.contact_sheet import build_before_after, build_contact_sheet
from artifact_skill.security.subprocess_exec import treat_sigterm_as_interrupt


def _eprint(*a: Any, **kw: Any) -> None:
    print(*a, file=sys.stderr, **kw)


def _emit(result: dict[str, Any], as_json: bool, human: str | None = None) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=False))
    elif human is not None:
        print(human)
    else:
        print(json.dumps(result, indent=2, sort_keys=False))


def _exit_for_status(status: CheckStatus) -> int:
    # FAIL is the actionable failure signal (exit 1). UNKNOWN gets its own
    # non-zero exit (3, matching the capability-missing category) so a
    # caller can tell "we know this failed" from "some aspect could not be
    # checked at all" without silently rounding UNKNOWN up to a pass
    # (spec #17, #43 — "Unknown is first-class"). See docs/verification.md.
    if status == CheckStatus.FAIL:
        return EXIT_FAIL
    if status == CheckStatus.UNKNOWN:
        return EXIT_CODE_BY_CATEGORY[ErrorCategory.CAPABILITY]
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="artifact-skill", description="Artifact production + verification engine.")
    parser.add_argument("--version", action="version", version=f"artifact-skill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        p.add_argument("--verbose", action="store_true", help="Print progress to stderr.")
        p.add_argument("--progress", action="store_true", help="Print lifecycle stage markers to stderr.")

    p_doctor = sub.add_parser("doctor", help="Detect local capabilities.")
    common(p_doctor)

    p_contract = sub.add_parser("contract", help="Print the machine-readable tool contract.")
    common(p_contract)

    p_inspect = sub.add_parser("inspect", help="Read-only inspection of an artifact.")
    p_inspect.add_argument("input")
    common(p_inspect)

    p_plan = sub.add_parser("plan", help="Pure, read-only plan for an operation.")
    p_plan.add_argument("input")
    p_plan.add_argument("--operation", required=True)
    p_plan.add_argument("--args", default="{}", help="JSON object of operation arguments.")
    p_plan.add_argument("--output", help="Intended output path (default: derived from input).")
    common(p_plan)

    p_execute = sub.add_parser("execute", help="Perform an operation, writing a new output artifact.")
    p_execute.add_argument("input")
    p_execute.add_argument("--operation", required=True)
    p_execute.add_argument("--args", default="{}")
    p_execute.add_argument("--output", help="Output path (default: derived, never the input path).")
    p_execute.add_argument(
        "--policy", default="{}",
        help="JSON object to gate execute's own reports/receipt.json structural verification. "
        "Without this, execute always verifies against an empty policy (barely gates anything) - "
        "use `receipt` instead of `execute` when you need a real submission gate.",
    )
    p_execute.add_argument(
        "--policy-preset",
        help=f"Named starting policy (see policies.py); explicit --policy fields override it. "
        f"Choices: {sorted(_PRESET_NAMES)}.",
    )
    p_execute.add_argument("--dry-run", action="store_true")
    common(p_execute)

    p_render = sub.add_parser("render", help="Produce page images for visual inspection.")
    p_render.add_argument("input")
    p_render.add_argument("--out-dir", help="Directory for rendered images (default: ./reports/rendered).")
    p_render.add_argument("--dry-run", action="store_true")
    common(p_render)

    p_verify = sub.add_parser("verify", help="Run structural verification against an optional policy.")
    p_verify.add_argument("input")
    p_verify.add_argument("--policy", default="{}", help="JSON object, e.g. '{\"min_pages\": 1}'.")
    p_verify.add_argument(
        "--policy-preset",
        help=f"Named starting policy (see policies.py); explicit --policy fields override it. "
        f"Choices: {sorted(_PRESET_NAMES)}.",
    )
    common(p_verify)

    p_look = sub.add_parser("look", help="Build a contact sheet (or before/after) for Agent visual review.")
    p_look.add_argument("input")
    p_look.add_argument("--compare-to", help="A second artifact to render as a before/after comparison.")
    p_look.add_argument("--out-dir", help="Directory for evidence images (default: ./reports).")
    p_look.add_argument("--dry-run", action="store_true")
    common(p_look)

    p_receipt = sub.add_parser("receipt", help="Run the full lifecycle and emit a Production Receipt.")
    p_receipt.add_argument("input")
    p_receipt.add_argument(
        "--operation",
        help="Omit for a verify-only receipt (inspect -> render -> structural verify -> receipt, no "
        "mutation, no fix loop) — the only way to get a Production Receipt for a format with zero "
        "mutating operations (HTML, SVG). --output is invalid without --operation.",
    )
    p_receipt.add_argument("--args", default="{}")
    p_receipt.add_argument("--output")
    p_receipt.add_argument("--policy", default="{}")
    p_receipt.add_argument(
        "--policy-preset",
        help=f"Named starting policy (see policies.py); explicit --policy fields override it. "
        f"Choices: {sorted(_PRESET_NAMES)}.",
    )
    p_receipt.add_argument(
        "--max-iterations", type=int, default=None,
        help="Fix-loop retry cap (default: Limits.max_fix_iterations, currently 3).",
    )
    p_receipt.add_argument("--evidence-dir", help="Directory for the receipt and evidence (default: ./reports).")
    p_receipt.add_argument("--dry-run", action="store_true")
    common(p_receipt)

    return parser


def _load_json_arg(raw: str, flag: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {flag} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"error: {flag} must be a JSON object.")
    return value


def _resolve_policy_arg(args: argparse.Namespace) -> dict[str, Any]:
    policy = _load_json_arg(args.policy, "--policy")
    try:
        return resolve_policy(getattr(args, "policy_preset", None), policy)
    except KeyError as exc:
        raise SystemExit(f"error: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    treat_sigterm_as_interrupt()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "contract":
            return _cmd_contract(args)
        if args.command == "inspect":
            return _cmd_inspect(args)
        if args.command == "plan":
            return _cmd_plan(args)
        if args.command == "execute":
            return _cmd_execute(args)
        if args.command == "render":
            return _cmd_render(args)
        if args.command == "verify":
            return _cmd_verify(args)
        if args.command == "look":
            return _cmd_look(args)
        if args.command == "receipt":
            return _cmd_receipt(args)
    except ArtifactError as exc:
        if args.json:
            print(json.dumps({"error": exc.to_dict()}, indent=2))
        else:
            _eprint(f"error: {exc}")
            if exc.remediation:
                _eprint(f"remediation: {exc.remediation}")
        return EXIT_CODE_BY_CATEGORY[exc.category]
    parser.error(f"unknown command: {args.command}")
    return 2


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = detect_environment()
    result = {"tool_version": __version__, "capabilities": report.to_dict()}
    if args.json:
        _emit(result, True)
        return EXIT_OK
    lines = [f"artifact-skill doctor — tool_version {__version__}", ""]
    for cap_id, cap in sorted(report.capabilities.items()):
        lines.append(f"  [{cap.status.value.upper():13}] {cap_id:28} {cap.detail}")
    print("\n".join(lines))
    return EXIT_OK


def _cmd_contract(args: argparse.Namespace) -> int:
    result = build_contract()
    _emit(result, True)
    return EXIT_OK


def _cmd_inspect(args: argparse.Namespace) -> int:
    ref = ArtifactRef.from_path(args.input)
    adapter = adapter_for(ref)
    report = adapter.inspect(ref)
    result = report.to_dict()
    if args.json:
        _emit(result, True)
    else:
        _emit(result, False, human=json.dumps(result, indent=2))
    return EXIT_OK


def _cmd_plan(args: argparse.Namespace) -> int:
    op_args = _load_json_arg(args.args, "--args")
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path(input_path, args.operation)
    _ref, _adapter, plan = build_plan(input_path, args.operation, op_args, output_path)
    result = plan.to_dict()
    _emit(result, args.json, human=None if args.json else json.dumps(result, indent=2))
    return EXIT_OK


def _cmd_execute(args: argparse.Namespace) -> int:
    op_args = _load_json_arg(args.args, "--args")
    policy = _resolve_policy_arg(args)
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_output_path(input_path, args.operation)
    evidence_dir = output_path.parent / "reports"

    result = run_lifecycle(
        input_path, args.operation, op_args, output_path,
        policy=policy, evidence_dir=evidence_dir, dry_run=args.dry_run,
    )
    if args.dry_run:
        payload = {"dry_run": True, "plan": result.plan.to_dict()}
        _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
        return EXIT_OK

    payload = {
        "output": result.output.to_dict() if result.output else None,
        "receipt": result.receipt.to_dict() if result.receipt else None,
    }
    _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
    if result.receipt is None:
        return EXIT_FAIL
    return _exit_for_status(result.receipt.status)


def _cmd_render(args: argparse.Namespace) -> int:
    ref = ArtifactRef.from_path(args.input)
    adapter = adapter_for(ref)
    out_dir = Path(args.out_dir) if args.out_dir else Path("reports") / "rendered"
    if args.dry_run:
        report = adapter.inspect(ref)
        page_count = report.details.get("page_count", "unknown")
        payload = {"dry_run": True, "would_render_to": str(out_dir), "estimated_files": page_count}
        _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
        return EXIT_OK
    rendered = adapter.render(ref, out_dir)
    payload = rendered.to_dict()
    _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace) -> int:
    policy = _resolve_policy_arg(args)
    ref = ArtifactRef.from_path(args.input)
    adapter = adapter_for(ref)
    result = adapter.verify_structural(ref, policy)
    payload = result.to_dict()
    _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
    return _exit_for_status(result.status)


def _cmd_look(args: argparse.Namespace) -> int:
    ref = ArtifactRef.from_path(args.input)
    adapter = adapter_for(ref)
    out_dir = Path(args.out_dir) if args.out_dir else Path("reports")
    if args.dry_run:
        payload = {"dry_run": True, "would_write_to": str(out_dir / "contact-sheet.png")}
        _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
        return EXIT_OK

    rendered = adapter.render(ref, out_dir / "rendered")
    if args.compare_to:
        other_ref = ArtifactRef.from_path(args.compare_to)
        other_adapter = adapter_for(other_ref)
        other_rendered = other_adapter.render(other_ref, out_dir / "rendered_compare")
        sheet_path = build_before_after(rendered.files, other_rendered.files, out_dir / "before-after.png")
    else:
        sheet_path = build_contact_sheet(rendered.files, out_dir / "contact-sheet.png")
    payload = {"contact_sheet": str(sheet_path), "source_pages": len(rendered.files)}
    _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
    return EXIT_OK


def _cmd_receipt(args: argparse.Namespace) -> int:
    op_args = _load_json_arg(args.args, "--args")
    policy = _resolve_policy_arg(args)
    input_path = Path(args.input)
    if args.operation is None:
        if args.output:
            raise SystemExit("error: --output requires --operation (nothing is written without a mutation).")
        output_path = None
    else:
        output_path = Path(args.output) if args.output else default_output_path(input_path, args.operation)
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else Path("reports")

    result = run_lifecycle(
        input_path, args.operation, op_args, output_path,
        policy=policy, evidence_dir=evidence_dir, dry_run=args.dry_run,
        max_iterations=args.max_iterations,
    )
    if args.dry_run:
        payload = {"dry_run": True, "plan": result.plan.to_dict()}
        _emit(payload, args.json, human=None if args.json else json.dumps(payload, indent=2))
        return EXIT_OK

    if result.receipt is None:
        return EXIT_FAIL

    if args.json:
        _emit(result.receipt.to_dict(), True)
    else:
        print(_human_report(result, evidence_dir))
    return _exit_for_status(result.receipt.status)


def _human_report(result, evidence_dir: Path) -> Any:  # LifecycleResult, avoid import cycle in the type hint
    r = result.receipt
    lines = [
        "ARTIFACT REPORT", "",
        f"Input:  {r.input['path']}",
        f"Output: {result.output.path if result.output else '(none)'}",
        "",
        f"Status:     {r.status.value.upper()}",
        f"Structural: {r.verification['structural']['status'].upper()}",
        f"Visual:     {r.verification['visual']['status'].upper()}",
        "",
        f"Iterations: {r.iterations}",
        f"Warnings:   {len(r.warnings)}",
    ]
    lines.extend(f"  - {w}" for w in r.warnings)
    if r.limitations:
        lines.append(f"Limitations: {len(r.limitations)}")
        lines.extend(f"  - {lim}" for lim in r.limitations)
    lines += ["", "Evidence:", f"  {evidence_dir / 'receipt.json'}"]
    for art in r.artifacts:
        lines.append(f"  {art['path']} ({art['role']})")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())

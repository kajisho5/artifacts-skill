"""Lifecycle orchestration: inspect -> plan -> execute -> render ->
structural verify -> visual verify (evidence only) -> fix -> re-verify ->
receipt (spec #6).

This module is format-agnostic: it drives whatever `ArtifactAdapter` the
registry hands it and never imports a format-specific library itself.

Brain/Hands split (spec #41, #55): this engine never decides whether an
artifact is "good" in any subjective sense. Visual verification here means
"evidence was produced" (or wasn't, and why) — a `VerificationResult` with
`inspected_by=None` and status reflecting *evidence availability*, not
visual correctness. An Agent (or a human) that actually looks at the
rendered images is the one who can set `inspected_by="agent"` and a real
pass/fail judgment; nothing in this codebase fabricates that judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_skill.adapters.base import ArtifactAdapter, RenderResult
from artifact_skill.adapters.registry import adapter_for
from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.capability import CapabilityReport, CapabilityStatus
from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactError, ArtifactInputError
from artifact_skill.core.operation import OperationPlan, OperationRecord
from artifact_skill.core.schema_validate import validate_against_schema
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.policies import unknown_policy_keys
from artifact_skill.receipt.model import ProductionReceipt, ReceiptBuilder
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class LifecycleResult:
    plan: OperationPlan
    receipt: ProductionReceipt | None  # None for a dry-run: no execution happened
    output: ArtifactRef | None
    render: RenderResult | None


def build_plan(input_path: Path, operation: str, args: dict[str, Any], output_path: Path) -> tuple[ArtifactRef, ArtifactAdapter, OperationPlan]:
    ref = ArtifactRef.from_path(input_path)
    adapter = adapter_for(ref)
    _validate_operation_args(adapter, operation, args)
    plan = adapter.plan(ref, operation, args, output_path)
    return ref, adapter, plan


def _validate_operation_args(adapter: ArtifactAdapter, operation: str, args: dict[str, Any]) -> None:
    """Enforce `OperationSpec.args_schema` as a real, checked contract
    rather than descriptive-only metadata (see `core/schema_validate.py`'s
    module docstring for why this matters). An unknown operation is left
    to the adapter's own `plan()`/`execute()` to reject with
    `ARTIFACT_OPERATION_UNKNOWN` — this only validates args for an
    operation the adapter actually declares.
    """
    spec = adapter.operations().get(operation)
    if spec is None:
        return
    errors = validate_against_schema(args, spec.args_schema)
    if errors:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message=f"Arguments for '{adapter.id}.{operation}' do not match its declared schema: "
            f"{'; '.join(errors)}",
            remediation="Check `artifact-skill contract --json` for the expected shape of --args.",
            evidence={"operation": f"{adapter.id}.{operation}", "errors": errors, "args": args},
        )


def run_lifecycle(
    input_path: Path,
    operation: str | None,
    args: dict[str, Any],
    output_path: Path | None,
    *,
    policy: dict[str, Any] | None = None,
    evidence_dir: Path,
    dry_run: bool = False,
    max_iterations: int | None = None,
    capability_report: CapabilityReport | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> LifecycleResult:
    policy = policy or {}
    bad_keys = unknown_policy_keys(policy)
    if bad_keys:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message=f"Unknown policy key(s): {bad_keys}. No adapter recognizes "
            f"{'this key' if len(bad_keys) == 1 else 'these keys'} — check for a typo.",
            remediation="Run `artifact-skill contract --json` or see docs/verification.md for valid policy keys.",
            evidence={"unknown_keys": bad_keys},
        )
    # None means "use the real default", not "run the fix loop once and stop" -
    # a bare int default here previously meant every caller that didn't pass
    # max_iterations explicitly silently disabled the fix loop entirely
    # (Limits.max_fix_iterations=3 was never reached). Deriving the default
    # from limits keeps it in sync automatically instead of duplicating the
    # literal 3 in the CLI and MCP callers too.
    if max_iterations is None:
        max_iterations = limits.max_fix_iterations
    max_iterations = max(1, min(max_iterations, limits.max_fix_iterations))

    if operation is None:
        # No mutation requested (Issue #18): inspect -> render -> structural
        # verify -> receipt, with no execute() call and no fix loop (there is
        # no operation to retry). The only route in for a format with zero
        # mutating operations (HTML, SVG) to get a real Production Receipt at
        # all, rather than an agent hand-assembling one from separate
        # inspect/render/verify calls.
        return _run_verify_only_lifecycle(
            input_path, policy=policy, evidence_dir=evidence_dir, dry_run=dry_run,
            capability_report=capability_report, limits=limits,
        )

    if output_path is None:
        raise ArtifactInputError(
            code="ARTIFACT_INVALID_ARGS",
            message="output_path is required when 'operation' is given.",
            evidence={"operation": operation},
        )

    ref, adapter, plan = build_plan(input_path, operation, args, output_path)

    if dry_run:
        return LifecycleResult(plan=plan, receipt=None, output=None, render=None)

    if capability_report is None:
        capability_report = CapabilityReport()
        for adapter_cap in adapter.capabilities():
            capability_report.add(adapter_cap)

    for cap_id in plan.required_capabilities:
        cap = capability_report.get(cap_id)
        if cap is not None and cap.status not in (CapabilityStatus.AVAILABLE, CapabilityStatus.NOT_REQUIRED):
            raise ArtifactCapabilityError(
                code="ARTIFACT_CAPABILITY_MISSING",
                message=f"Operation '{operation}' requires capability '{cap_id}' which is {cap.status.value}.",
                remediation="Run `artifact-skill doctor --json` for install guidance.",
                evidence={"capability_id": cap_id, "status": cap.status.value},
            )

    spec = adapter.operations().get(operation)
    builder = ReceiptBuilder(ref, capability_report)
    output_ref: ArtifactRef | None = None
    render_result: RenderResult | None = None
    iterations_used = 0

    for iteration in range(1, max_iterations + 1):
        iterations_used = iteration
        started = _now_iso()
        try:
            output_ref = adapter.execute(ref, operation, args, output_path)
            record = OperationRecord(
                operation=f"{adapter.id}.{operation}",
                adapter=adapter.id,
                args=args,
                started_at=started,
                finished_at=_now_iso(),
                input_sha256=ref.sha256,
                output_sha256=output_ref.sha256,
                output_path=str(output_ref.path),
                succeeded=True,
            )
        except ArtifactError as exc:
            record = OperationRecord(
                operation=f"{adapter.id}.{operation}",
                adapter=adapter.id,
                args=args,
                started_at=started,
                finished_at=_now_iso(),
                input_sha256=ref.sha256,
                output_sha256=None,
                output_path=None,
                succeeded=False,
                error=exc.to_dict(),
            )
            builder.add_operation(record)
            break
        builder.add_operation(record)

        structural = adapter.verify_structural(output_ref, policy)
        visual, rendered = _visual_evidence_result(
            adapter=adapter, ref=output_ref, evidence_dir=evidence_dir,
            capability_report=capability_report, spec_render_required=bool(spec and spec.render_required),
            limits=limits,
        )
        if rendered is not None:
            render_result = rendered

        if spec is not None and not spec.structural_verification_required:
            structural = VerificationResult(
                kind="structural",
                checks=[Check(id="not_required", name="Structural verification", status=CheckStatus.SKIPPED,
                               message="This operation's contract does not require structural verification.")],
            )
        else:
            structural = adapter.refine_structural_with_render(structural, rendered)

        should_retry = structural.status == CheckStatus.FAIL and iteration < max_iterations
        if should_retry:
            fix_args = adapter.fix(ref, operation, args, structural)
            if fix_args is not None and spec is not None:
                fix_errors = validate_against_schema(fix_args, spec.args_schema)
                if fix_errors:
                    # A fixer that hands back args violating its own operation's
                    # declared schema is a bug, not a fix — never retry with
                    # something the schema itself would reject. Same honest
                    # "no fixer for this" exit as fix_args is None.
                    builder.add_limitation(
                        f"Adapter '{adapter.id}' fixer for operation '{operation}' returned args "
                        f"that violate its own args_schema ({'; '.join(fix_errors)}); stopping rather "
                        "than retrying with them."
                    )
                    fix_args = None
            if fix_args is None:
                builder.add_limitation(
                    f"Structural verification failed on iteration {iteration} but adapter "
                    f"'{adapter.id}' has no automatic fixer for operation '{operation}'; stopping."
                )
                builder.set_structural(structural)
                builder.set_visual(visual)
                break
            args = fix_args
            continue

        builder.set_structural(structural)
        builder.set_visual(visual)
        break

    for lim in adapter.limitations():
        builder.add_limitation(lim)
    if spec is not None:
        for lim in spec.known_limitations:
            builder.add_limitation(lim)

    builder.set_iterations(iterations_used)
    receipt = builder.build()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt.write(evidence_dir / "receipt.json")
    return LifecycleResult(plan=plan, receipt=receipt, output=output_ref, render=render_result)


def _run_verify_only_lifecycle(
    input_path: Path,
    *,
    policy: dict[str, Any],
    evidence_dir: Path,
    dry_run: bool,
    capability_report: CapabilityReport | None,
    limits: Limits = DEFAULT_LIMITS,
) -> LifecycleResult:
    ref = ArtifactRef.from_path(input_path)
    adapter = adapter_for(ref)
    plan = OperationPlan(
        operation="(none: verify-only)",
        adapter=adapter.id,
        input=ref.to_dict(),
        output_path="",
        required_capabilities=[f"{adapter.id}.structural"],
        files_touched=[],
        files_created=[],
        rendering_strategy=f"{adapter.id}.render() against the original input; no mutation is planned.",
        verification_strategy={"structural_required": True, "visual_required": False},
        risks=[],
        warnings=[],
    )

    if dry_run:
        return LifecycleResult(plan=plan, receipt=None, output=None, render=None)

    if capability_report is None:
        capability_report = CapabilityReport()
        for adapter_cap in adapter.capabilities():
            capability_report.add(adapter_cap)

    builder = ReceiptBuilder(ref, capability_report)
    structural = adapter.verify_structural(ref, policy)
    # spec_render_required=False: no operation was chosen, so there is no
    # OperationSpec.visual_verification_required to consult - this matches
    # the majority of registered operations today (every metadata_set-style
    # operation across every format sets it False too; PDF's fit_page_size
    # is the one exception, since it's the one operation that changes page
    # geometry). A missing render capability reports SKIPPED, not UNKNOWN.
    visual, rendered = _visual_evidence_result(
        adapter=adapter, ref=ref, evidence_dir=evidence_dir,
        capability_report=capability_report, spec_render_required=False, limits=limits,
    )
    structural = adapter.refine_structural_with_render(structural, rendered)
    builder.set_structural(structural)
    builder.set_visual(visual)
    for lim in adapter.limitations():
        builder.add_limitation(lim)
    # One verification pass happened - there is no fix loop to count
    # iterations of (nothing to retry without an operation).
    builder.set_iterations(1)
    receipt = builder.build()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    receipt.write(evidence_dir / "receipt.json")
    return LifecycleResult(plan=plan, receipt=receipt, output=None, render=rendered)


def _visual_evidence_result(
    *,
    adapter: ArtifactAdapter,
    ref: ArtifactRef,
    evidence_dir: Path,
    capability_report: CapabilityReport,
    spec_render_required: bool,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[VerificationResult, RenderResult | None]:
    """Returns the visual VerificationResult plus the real RenderResult (or
    None if nothing was rendered) — the caller must use the latter's own
    `backend` rather than assuming one. `render()` implementations report
    different backends per adapter (`pypdfium2` for PDF/PPTX/DOCX/XLSX,
    `playwright+chromium` for HTML/SVG, Pillow for images); a caller that
    hardcoded `backend="pypdfium2"` here previously mislabeled every
    non-PDF-family adapter's evidence.
    """
    render_cap_id = f"{adapter.id}.render"
    render_cap = capability_report.get(render_cap_id)
    if render_cap is None or render_cap.status != CapabilityStatus.AVAILABLE:
        status = CheckStatus.SKIPPED if not spec_render_required else CheckStatus.UNKNOWN
        return (
            VerificationResult(
                kind="visual",
                checks=[
                    Check(
                        id="visual_evidence",
                        name="Visual evidence produced",
                        status=status,
                        message=f"Rendering capability '{render_cap_id}' is "
                        f"{render_cap.status.value if render_cap else 'unknown'}; no page images were produced.",
                    )
                ],
            ),
            None,
        )
    try:
        rendered = adapter.render(ref, evidence_dir / "rendered", limits=limits)
    except ArtifactError as exc:
        return (
            VerificationResult(
                kind="visual",
                checks=[
                    Check(id="visual_evidence", name="Visual evidence produced", status=CheckStatus.UNKNOWN, message=str(exc))
                ],
            ),
            None,
        )
    return (
        VerificationResult(
            kind="visual",
            checks=[
                Check(
                    id="visual_evidence",
                    name="Visual evidence produced",
                    status=CheckStatus.PASS if rendered.files else CheckStatus.WARN,
                    message=f"{len(rendered.files)} page image(s) produced via {rendered.backend}. "
                    "This confirms evidence exists, not that the content is visually correct — "
                    "an Agent must review the images to make that judgment.",
                )
            ],
            evidence_files=[str(f) for f in rendered.files],
            inspected_by=None,
        ),
        rendered,
    )

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
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
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
    plan = adapter.plan(ref, operation, args, output_path)
    return ref, adapter, plan


def run_lifecycle(
    input_path: Path,
    operation: str,
    args: dict[str, Any],
    output_path: Path,
    *,
    policy: dict[str, Any] | None = None,
    evidence_dir: Path,
    dry_run: bool = False,
    max_iterations: int = 1,
    capability_report: CapabilityReport | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> LifecycleResult:
    policy = policy or {}
    max_iterations = max(1, min(max_iterations, limits.max_fix_iterations))

    ref, adapter, plan = build_plan(input_path, operation, args, output_path)

    if dry_run:
        return LifecycleResult(plan=plan, receipt=None, output=None, render=None)

    if capability_report is None:
        capability_report = CapabilityReport()
        for cap in adapter.capabilities():
            capability_report.add(cap)

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
        visual = _visual_evidence_result(
            adapter=adapter, ref=output_ref, evidence_dir=evidence_dir,
            capability_report=capability_report, spec_render_required=bool(spec and spec.render_required),
        )
        if visual.evidence_files:
            render_result = RenderResult(kind="page_images", files=[Path(f) for f in visual.evidence_files], backend="pypdfium2")

        if spec is not None and not spec.structural_verification_required:
            structural = VerificationResult(
                kind="structural",
                checks=[Check(id="not_required", name="Structural verification", status=CheckStatus.SKIPPED,
                               message="This operation's contract does not require structural verification.")],
            )

        should_retry = structural.status == CheckStatus.FAIL and iteration < max_iterations
        if should_retry:
            fix_args = adapter.fix(ref, operation, args, structural)
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


def _visual_evidence_result(
    *,
    adapter: ArtifactAdapter,
    ref: ArtifactRef,
    evidence_dir: Path,
    capability_report: CapabilityReport,
    spec_render_required: bool,
) -> VerificationResult:
    render_cap_id = f"{adapter.id}.render"
    render_cap = capability_report.get(render_cap_id)
    if render_cap is None or render_cap.status != CapabilityStatus.AVAILABLE:
        status = CheckStatus.SKIPPED if not spec_render_required else CheckStatus.UNKNOWN
        return VerificationResult(
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
        )
    try:
        rendered = adapter.render(ref, evidence_dir / "rendered")
    except ArtifactError as exc:
        return VerificationResult(
            kind="visual",
            checks=[Check(id="visual_evidence", name="Visual evidence produced", status=CheckStatus.UNKNOWN, message=str(exc))],
        )
    return VerificationResult(
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
    )

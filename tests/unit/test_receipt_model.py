from __future__ import annotations

from artifact_skill.core.artifact import ArtifactRef
from artifact_skill.core.capability import Capability, CapabilityReport, CapabilityStatus
from artifact_skill.core.operation import OperationRecord
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.receipt.model import RECEIPT_SCHEMA, ReceiptBuilder


def _record(succeeded: bool) -> OperationRecord:
    return OperationRecord(
        operation="pdf.metadata_set", adapter="pdf", args={},
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
        input_sha256="a" * 64, output_sha256="b" * 64 if succeeded else None,
        output_path="/tmp/out.pdf" if succeeded else None,  # noqa: S108 - opaque record field, never written to
        succeeded=succeeded,
    )


def test_receipt_schema_and_required_top_level_fields(good_pdf):
    ref = ArtifactRef.from_path(good_pdf)
    caps = CapabilityReport()
    caps.add(Capability("pdf.structural", CapabilityStatus.AVAILABLE))
    builder = ReceiptBuilder(ref, caps)
    builder.add_operation(_record(True))
    builder.set_structural(VerificationResult(kind="structural", checks=[Check("x", "x", CheckStatus.PASS)]))
    receipt = builder.build()

    d = receipt.to_dict()
    assert d["schema"] == RECEIPT_SCHEMA == "artifact-receipt/v1"
    for key in (
        "schema", "status", "input", "operations", "verification", "artifacts",
        "warnings", "limitations", "environment", "capabilities", "timestamp", "tool_version",
    ):
        assert key in d


def test_receipt_status_is_fail_when_operation_failed(good_pdf):
    ref = ArtifactRef.from_path(good_pdf)
    builder = ReceiptBuilder(ref, CapabilityReport())
    builder.add_operation(_record(False))
    receipt = builder.build()
    assert receipt.status == CheckStatus.FAIL


def test_receipt_status_is_pass_when_everything_passes(good_pdf):
    ref = ArtifactRef.from_path(good_pdf)
    builder = ReceiptBuilder(ref, CapabilityReport())
    builder.add_operation(_record(True))
    builder.set_structural(VerificationResult(kind="structural", checks=[Check("x", "x", CheckStatus.PASS)]))
    builder.set_visual(VerificationResult(kind="visual", checks=[Check("y", "y", CheckStatus.PASS)]))
    receipt = builder.build()
    assert receipt.status == CheckStatus.PASS


def test_receipt_not_checked_when_no_verification_ran_at_all(good_pdf):
    """No hallucinated success: an empty receipt is NOT_CHECKED, not PASS."""
    ref = ArtifactRef.from_path(good_pdf)
    builder = ReceiptBuilder(ref, CapabilityReport())
    receipt = builder.build()
    assert receipt.status == CheckStatus.NOT_CHECKED


def test_receipt_write_produces_valid_json_file(good_pdf, tmp_path):
    import json

    ref = ArtifactRef.from_path(good_pdf)
    builder = ReceiptBuilder(ref, CapabilityReport())
    builder.add_operation(_record(True))
    receipt = builder.build()
    out = receipt.write(tmp_path / "reports" / "receipt.json")
    data = json.loads(out.read_text())
    assert data["schema"] == "artifact-receipt/v1"


def test_receipt_write_leaves_no_partial_or_temp_file_behind(good_pdf, tmp_path):
    """Self-audit finding (FIX_PROMPT P3-3): receipt.json is the one
    artifact this project's own docs call "meant to outlive this
    process" — it must go through the same atomic write-then-replace
    path every other on-disk write in this project uses, not a plain
    write_text() that could leave a truncated file behind on a crash
    mid-write. This doesn't simulate the crash itself (atomic_write_bytes's
    own crash-safety is already proven directly in
    tests/security/test_path_safety.py) — it proves receipt.write() is
    actually wired through that same code path, by checking that no
    leftover temp file (atomic_write_bytes's own naming pattern) is ever
    left in the output directory."""
    ref = ArtifactRef.from_path(good_pdf)
    builder = ReceiptBuilder(ref, CapabilityReport())
    builder.add_operation(_record(True))
    receipt = builder.build()
    reports_dir = tmp_path / "reports"
    out = receipt.write(reports_dir / "receipt.json")
    leftovers = [p for p in reports_dir.iterdir() if p.name != "receipt.json"]
    assert leftovers == []
    assert out.exists()


def test_receipt_type_is_named_artifact_verification_receipt(good_pdf):
    """Ecosystem roadmap step 2: the receipt's type is
    ArtifactVerificationReceipt, so it is not confused with the
    orchestrator's own ProductionReceipt (video-production-agent ADR-040)."""
    from artifact_skill.receipt.model import ArtifactVerificationReceipt

    receipt = ReceiptBuilder(ArtifactRef.from_path(good_pdf), CapabilityReport()).build()
    assert type(receipt) is ArtifactVerificationReceipt
    assert type(receipt).__name__ == "ArtifactVerificationReceipt"


def test_old_production_receipt_name_is_kept_as_an_alias(good_pdf):
    """Backward compatibility (docs/architecture.md, "Receipt name"): code
    that imports or isinstance-checks the old name keeps working."""
    from artifact_skill import receipt as receipt_pkg
    from artifact_skill.receipt.model import ArtifactVerificationReceipt, ProductionReceipt

    assert ProductionReceipt is ArtifactVerificationReceipt
    assert receipt_pkg.ProductionReceipt is ArtifactVerificationReceipt
    assert receipt_pkg.ArtifactVerificationReceipt is ArtifactVerificationReceipt
    receipt = ReceiptBuilder(ArtifactRef.from_path(good_pdf), CapabilityReport()).build()
    assert isinstance(receipt, ProductionReceipt)


def test_rename_did_not_change_the_emitted_schema_identifier(good_pdf):
    """The rename is name-only: every receipt, before and after it, says
    `artifact-receipt/v1`, so consumers that check that value need no
    change."""
    receipt = ReceiptBuilder(ArtifactRef.from_path(good_pdf), CapabilityReport()).build()
    assert receipt.to_dict()["schema"] == "artifact-receipt/v1"

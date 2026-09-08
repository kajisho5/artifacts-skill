"""Validates real, freshly-generated documents against the published JSON
Schema files (Issue #16) — proving the schemas describe what the tool
actually emits, not a stale approximation of it. Uses this project's own
core/schema_validate.py rather than a jsonschema dependency (see that
module's docstring).
"""

from __future__ import annotations

import json
from pathlib import Path

from artifact_skill.core.contract import build_contract
from artifact_skill.core.engine import run_lifecycle
from artifact_skill.core.schema_validate import validate_against_schema

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text())


def test_real_contract_document_matches_its_schema():
    schema = _load_schema("artifact-contract-v1.schema.json")
    contract = build_contract()
    errors = validate_against_schema(contract, schema)
    assert errors == []


def test_real_successful_receipt_matches_its_schema(good_pdf, tmp_path):
    schema = _load_schema("artifact-receipt-v1.schema.json")
    result = run_lifecycle(
        good_pdf, "metadata_set", {"title": "Schema Test"}, tmp_path / "out.pdf",
        evidence_dir=tmp_path / "reports", dry_run=False,
    )
    errors = validate_against_schema(result.receipt.to_dict(), schema)
    assert errors == []


def test_real_failed_receipt_matches_its_schema(empty_pdf, tmp_path):
    """A different code path from the success case above: a structural FAIL
    (empty_pdf has 0 pages), which exercises the "no automatic fixer"
    limitation, a non-null 'error'-shaped operation is NOT hit here (the
    operation itself still succeeds; only verification fails) — covered
    together with the success case, this and the next test exercise both
    `output_sha256`/`output_path` populated and null, and both `error: null`
    and `error: {...}` shapes."""
    schema = _load_schema("artifact-receipt-v1.schema.json")
    result = run_lifecycle(
        empty_pdf, "metadata_set", {"title": "x"}, tmp_path / "out.pdf",
        evidence_dir=tmp_path / "reports", dry_run=False,
    )
    errors = validate_against_schema(result.receipt.to_dict(), schema)
    assert errors == []


def test_real_receipt_with_a_failed_operation_matches_its_schema(encrypted_pdf, tmp_path):
    """Drives an operation whose execute() itself raises (metadata_set on
    an encrypted PDF, ARTIFACT_PDF_ENCRYPTED) — inspect()/plan() succeed
    for an encrypted PDF (it's a legitimate, readable file), so this
    reaches run_lifecycle's per-iteration error handling and populates
    OperationRecord.error while leaving output_sha256/output_path null —
    the nullable-field shapes the success-case test above can't reach.
    (A file inspect()/plan() themselves can't even open, e.g. corrupt.pdf,
    raises before any OperationRecord is built at all — no receipt is
    produced for that case, so it's out of scope for this schema check.)
    """
    schema = _load_schema("artifact-receipt-v1.schema.json")
    result = run_lifecycle(
        encrypted_pdf, "metadata_set", {"title": "x"}, tmp_path / "out.pdf",
        evidence_dir=tmp_path / "reports", dry_run=False,
    )
    assert result.receipt is not None
    assert result.receipt.operations[0]["error"] is not None  # sanity: this really exercises the null-vs-populated case
    errors = validate_against_schema(result.receipt.to_dict(), schema)
    assert errors == []


def test_real_verify_only_receipt_matches_its_schema(good_pdf, tmp_path):
    """Issue #18: an empty `operations` array and a receipt built with no
    execute() call at all is a real, reachable shape now, not just a
    theoretical one the schema happened to already allow."""
    schema = _load_schema("artifact-receipt-v1.schema.json")
    result = run_lifecycle(good_pdf, None, {}, None, evidence_dir=tmp_path / "reports", dry_run=False)
    assert result.receipt is not None
    assert result.receipt.operations == []  # sanity: this really exercises the empty-operations shape
    errors = validate_against_schema(result.receipt.to_dict(), schema)
    assert errors == []


def test_schema_files_are_self_consistent_json():
    """Just confirms both files actually parse — a malformed schema file
    would otherwise only be caught the next time someone opens it."""
    for name in ("artifact-contract-v1.schema.json", "artifact-receipt-v1.schema.json"):
        doc = _load_schema(name)
        assert doc["$schema"]
        assert doc["type"] == "object"

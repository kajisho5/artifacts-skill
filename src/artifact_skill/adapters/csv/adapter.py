"""CSV adapter.

Structural inspection uses only the standard library (`csv`) — no optional
dependency, so `csv.structural` is always `AVAILABLE`, the same design as
HTML/SVG. Rendering builds a small, fully self-contained HTML `<table>` (no
external or local resource references at all — every cell is inlined and
HTML-escaped) and reuses `rendering/chromium_render.py`, the same backend
HTML/SVG already use — nothing new to trust there.

No mutating operations, for the same reason HTML/SVG have none: a CSV's
natural "edit" is changing cell values, which is source-data editing, not a
property-set operation this Skill should own (see docs/adapters.md).
"""

from __future__ import annotations

import csv
import html
import importlib.util
import io
from pathlib import Path
from typing import Any

from artifact_skill.adapters.base import ArtifactAdapter, OperationSpec, RenderResult
from artifact_skill.core.artifact import ArtifactRef, ArtifactType, InspectionReport
from artifact_skill.core.capability import Capability, CapabilityStatus
from artifact_skill.core.errors import ArtifactInputError
from artifact_skill.core.operation import OperationPlan
from artifact_skill.core.verification import Check, CheckStatus, VerificationResult
from artifact_skill.leftover_text import find_leftover_markers
from artifact_skill.rendering.chromium_render import render_local_file
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits
from artifact_skill.security.paths import check_input_size

# A rendered preview beyond this many rows would produce an unreasonably
# tall page for little benefit; structural checks below still cover every
# row in the file regardless of this cap.
_MAX_RENDER_ROWS = 500


def _sniff_dialect(sample: str) -> Any:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return csv.get_dialect("excel")


def _read_rows(path: Path) -> tuple[list[list[str]], Any]:
    text = path.read_text(encoding="utf-8", errors="strict")
    sample = "\n".join(text.splitlines()[:50])
    dialect = _sniff_dialect(sample)
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if row]
    return rows, dialect


class CsvAdapter(ArtifactAdapter):
    id = "csv"
    artifact_type = ArtifactType.CSV

    @classmethod
    def detect(cls, ref: ArtifactRef) -> bool:
        return ref.type == ArtifactType.CSV

    def operations(self) -> dict[str, OperationSpec]:
        return {}

    def capabilities(self) -> list[Capability]:
        structural = Capability(
            id="csv.structural", status=CapabilityStatus.AVAILABLE,
            detail="Uses only the standard library (csv); always available.",
            detected_via="stdlib",
        )
        if importlib.util.find_spec("playwright") is not None:
            import importlib.metadata as im

            try:
                version = im.version("playwright")
            except im.PackageNotFoundError:
                version = None
            render = Capability(
                id="csv.render", status=CapabilityStatus.AVAILABLE,
                detail="playwright importable. A matching Chromium build must also be installed "
                "(`playwright install chromium`) — verified at render() time, not here.",
                detected_via="import playwright", version=version,
            )
        else:
            render = Capability(
                id="csv.render", status=CapabilityStatus.MISSING,
                detail="playwright not importable.", detected_via="import playwright",
            )
        return [structural, render]

    def limitations(self) -> list[str]:
        return [
            "No mutating operations: this adapter inspects/renders/verifies CSV, it does not edit cell values.",
            "Type detection for CSV is a heuristic (csv.Sniffer plus a consistent-field-count check across "
            "sampled rows), not a magic-byte match — CSV has no fixed signature at all. A file that doesn't "
            "clear that bar is reported UNKNOWN rather than guessed.",
            "render() previews only the first "
            f"{_MAX_RENDER_ROWS} rows as an HTML table; structural checks (row/column counts, leftover text) "
            "still cover the entire file regardless.",
            "Encoding is assumed UTF-8; a CSV saved in another encoding (e.g. Shift-JIS, Windows-1252) is "
            "reported as unreadable rather than being guessed at.",
        ]

    def recognized_policy_keys(self) -> frozenset[str]:
        return frozenset({"require_row_count", "forbid_placeholder_text"})

    # ---- inspect ---------------------------------------------------

    def inspect(self, ref: ArtifactRef) -> InspectionReport:
        check_input_size(ref.path)
        try:
            rows, dialect = _read_rows(ref.path)
        except UnicodeDecodeError as exc:
            raise ArtifactInputError(
                code="ARTIFACT_CSV_UNREADABLE",
                message=f"'{ref.path}' is not valid UTF-8 text: {exc}",
                remediation="CSV files are expected to be UTF-8 encoded.",
                evidence={"path": str(ref.path)},
            ) from exc

        header = rows[0] if rows else []
        column_count = len(header)
        ragged_rows = [i for i, r in enumerate(rows) if len(r) != column_count]
        flat_text = "\n".join(cell for row in rows for cell in row)

        details = {
            "row_count": len(rows),
            "column_count": column_count,
            "header": header,
            "delimiter": dialect.delimiter,
            "ragged_row_indices": ragged_rows[:20],  # capped — a signal, not a full dump
            "ragged_row_count": len(ragged_rows),
            "size_bytes": ref.size_bytes,
            "leftover_markers": find_leftover_markers(flat_text),
        }
        return InspectionReport(artifact=ref, details=details)

    # ---- plan / execute -----------------------------------------------

    def plan(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> OperationPlan:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"CSV adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    def execute(self, ref: ArtifactRef, operation: str, args: dict[str, Any], output_path: Path) -> ArtifactRef:
        raise ArtifactInputError(
            code="ARTIFACT_OPERATION_UNKNOWN",
            message=f"CSV adapter has no mutating operations (requested '{operation}').",
            remediation="This adapter supports inspect/render/verify only — see its module docstring.",
            evidence={"operation": operation},
        )

    # ---- render ----------------------------------------------------

    def render(self, ref: ArtifactRef, out_dir: Path, *, limits: Limits = DEFAULT_LIMITS) -> RenderResult:
        check_input_size(ref.path)
        rows, _dialect = _read_rows(ref.path)
        warnings: list[str] = []
        preview_rows = rows[:_MAX_RENDER_ROWS]
        if len(rows) > _MAX_RENDER_ROWS:
            warnings.append(f"Rendered preview truncated to the first {_MAX_RENDER_ROWS} of {len(rows)} rows.")

        row_html = []
        for i, row in enumerate(preview_rows):
            cell_tag = "th" if i == 0 else "td"
            cells = "".join(f"<{cell_tag}>{html.escape(c)}</{cell_tag}>" for c in row)
            row_html.append(f"<tr>{cells}</tr>")
        doc = (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            "table{border-collapse:collapse;font-family:sans-serif;font-size:13px}"
            "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
            "th{background:#eee}</style></head><body><table>" + "".join(row_html) + "</table></body></html>"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        render_html_path = out_dir / "_render.html"
        render_html_path.write_text(doc, encoding="utf-8")

        out_path = render_local_file(
            render_html_path, out_dir / "page-001.png", capability_id="csv.render", limits=limits
        )
        return RenderResult(kind="page_images", files=[out_path], backend="playwright+chromium(generated_html)", warnings=warnings)

    # ---- verify ------------------------------------------------------

    def verify_structural(self, ref: ArtifactRef, policy: dict[str, Any]) -> VerificationResult:
        checks: list[Check] = []
        try:
            report = self.inspect(ref)
        except ArtifactInputError as exc:
            checks.append(Check(id="csv_validity", name="CSV is readable", status=CheckStatus.FAIL, message=str(exc)))
            return VerificationResult(kind="structural", checks=checks)

        checks.append(Check(id="csv_validity", name="CSV is readable", status=CheckStatus.PASS))

        details = report.details
        checks.append(
            Check(
                id="column_count_consistency",
                name="Every row has the same number of columns",
                status=CheckStatus.FAIL if details["ragged_row_count"] else CheckStatus.PASS,
                message=f"{details['ragged_row_count']} row(s) have a different column count than the header."
                if details["ragged_row_count"]
                else f"All {details['row_count']} row(s) have {details['column_count']} column(s).",
                evidence={"ragged_row_indices": details["ragged_row_indices"]},
            )
        )

        if "require_row_count" in policy:
            required = policy["require_row_count"]
            actual = details["row_count"]
            ok = (actual == required) if isinstance(required, int) else (required[0] <= actual <= required[1])
            checks.append(
                Check(
                    id="row_count_requirement",
                    name="Row count matches requirement",
                    status=CheckStatus.PASS if ok else CheckStatus.FAIL,
                    message=f"Expected {required}, found {actual}.",
                    evidence={"expected": required, "actual": actual},
                )
            )

        leftover_markers = details["leftover_markers"]
        if leftover_markers:
            checks.append(
                Check(
                    id="leftover_placeholder_text",
                    name="No leftover generation placeholder text",
                    status=CheckStatus.FAIL if policy.get("forbid_placeholder_text") else CheckStatus.WARN,
                    message=f"Found likely-unreviewed placeholder text: {leftover_markers}.",
                    evidence={"markers": leftover_markers},
                )
            )
        else:
            checks.append(
                Check(id="leftover_placeholder_text", name="No leftover generation placeholder text", status=CheckStatus.PASS)
            )

        return VerificationResult(kind="structural", checks=checks)

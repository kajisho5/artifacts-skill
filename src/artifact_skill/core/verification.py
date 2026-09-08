"""Verification result model — PASS/WARN/FAIL/UNKNOWN/NOT_CHECKED/SKIPPED.

These six states are never collapsed into each other. In particular:
  - SKIPPED   = policy said this check does not apply to this operation.
  - NOT_CHECKED = it should have run but nothing ran it (bug, or caller opted out).
  - UNKNOWN   = it ran but could not reach a verdict (e.g. renderer unavailable).
Conflating any of these with PASS is exactly the "hallucinated success" this
project exists to prevent (spec #17, #42, #43).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

# Precedence used to aggregate many checks into one status: the leftmost
# status present "wins" (is reported) when several appear together.
#   FAIL       - an actual, checked failure. Always wins.
#   UNKNOWN    - couldn't reach a verdict. Worse than a mere warning because
#                it's a strictly weaker claim than "probably fine".
#   NOT_CHECKED- something that should have run but nothing ran it. A
#                process gap, worse than a caveat but not a known failure.
#   WARN       - checked, passed, but flagged.
#   PASS       - checked and clean.
#   SKIPPED    - policy said this check doesn't apply here. Deliberately
#                ranked alongside/after PASS, not worse than it: an
#                intentional "not applicable" must not drag a clean result
#                down the same way an *unintentional* gap (NOT_CHECKED) does.
_PRECEDENCE = [
    "fail",
    "unknown",
    "not_checked",
    "warn",
    "pass",
    "skipped",
]


class CheckStatus(str, enum.Enum):
    PASS = "pass"  # noqa: S105 - an enum value, not a credential
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"
    SKIPPED = "skipped"

    @property
    def _rank(self) -> int:
        return _PRECEDENCE.index(self.value)


@dataclass
class Check:
    id: str
    name: str
    status: CheckStatus
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class VerificationResult:
    kind: str  # "structural" | "visual"
    checks: list[Check] = field(default_factory=list)
    evidence_files: list[str] = field(default_factory=list)
    # Who looked at visual evidence: None for structural, "engine" for an
    # automated pixel/semantic check, "agent" when only a human/LLM viewing
    # the rendered images can complete the judgment.
    inspected_by: str | None = None

    @property
    def status(self) -> CheckStatus:
        return aggregate([c.status for c in self.checks])

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "evidence_files": self.evidence_files,
            "inspected_by": self.inspected_by,
        }


def aggregate(statuses: list[CheckStatus]) -> CheckStatus:
    """Worst-status-wins aggregation. No checks at all -> NOT_CHECKED."""
    if not statuses:
        return CheckStatus.NOT_CHECKED
    worst = min(statuses, key=lambda s: s._rank)
    return worst

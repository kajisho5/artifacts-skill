"""Structured error model.

Every operator-facing failure carries a machine-readable code/category plus a
human remediation string, so a CLI, an MCP client, and an Agent can all react
programmatically instead of parsing prose. See docs/architecture.md #35.
"""

from __future__ import annotations

import enum
from typing import Any


class ErrorCategory(str, enum.Enum):
    INPUT = "input"
    CAPABILITY = "capability"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    SECURITY = "security"
    INTERNAL = "internal"


class ArtifactError(Exception):
    """Base error for all Artifact Skill failures.

    code: stable machine-readable identifier, e.g. "ARTIFACT_CAPABILITY_MISSING".
    category: coarse bucket used to pick a CLI exit code.
    remediation: what a human/agent can do about it. May be None if there is
        genuinely nothing actionable (e.g. malformed input the caller must fix).
    evidence: structured context (paths, detected values, limits hit).
    """

    def __init__(
        self,
        code: str,
        category: ErrorCategory,
        message: str,
        remediation: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.remediation = remediation
        self.evidence = evidence or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category.value,
            "message": self.message,
            "remediation": self.remediation,
            "evidence": self.evidence,
        }


class ArtifactInputError(ArtifactError):
    def __init__(self, code: str, message: str, remediation: str | None = None, evidence: dict | None = None):
        super().__init__(code, ErrorCategory.INPUT, message, remediation, evidence)


class ArtifactCapabilityError(ArtifactError):
    def __init__(self, code: str, message: str, remediation: str | None = None, evidence: dict | None = None):
        super().__init__(code, ErrorCategory.CAPABILITY, message, remediation, evidence)


class ArtifactExecutionError(ArtifactError):
    def __init__(self, code: str, message: str, remediation: str | None = None, evidence: dict | None = None):
        super().__init__(code, ErrorCategory.EXECUTION, message, remediation, evidence)


class ArtifactVerificationError(ArtifactError):
    def __init__(self, code: str, message: str, remediation: str | None = None, evidence: dict | None = None):
        super().__init__(code, ErrorCategory.VERIFICATION, message, remediation, evidence)


class ArtifactSecurityError(ArtifactError):
    def __init__(self, code: str, message: str, remediation: str | None = None, evidence: dict | None = None):
        super().__init__(code, ErrorCategory.SECURITY, message, remediation, evidence)


# Exit code mapping — the single source of truth for CLI exit codes.
# Kept here (not duplicated in cli/main.py) so contract.py can expose it too.
EXIT_CODE_BY_CATEGORY: dict[ErrorCategory, int] = {
    ErrorCategory.INPUT: 2,
    ErrorCategory.CAPABILITY: 3,
    ErrorCategory.SECURITY: 4,
    ErrorCategory.EXECUTION: 1,
    ErrorCategory.VERIFICATION: 1,
    ErrorCategory.INTERNAL: 5,
}
EXIT_OK = 0
EXIT_FAIL = 1

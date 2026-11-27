"""Checks that compare a document's text with other files the policy
names, for every format: `event_source_match` (ecosystem roadmap step 6,
`event_check.py`), `source_grounding` (step 7, `source_grounding.py`) and
`credits_attribution` (step 8, `credits_check.py`). Every verify path (CLI, MCP, lifecycle) goes through
`verify_structural_with_text_checks()`, so no adapter has to know about
them; adapters only expose their text through `InspectionReport.text`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from artifact_skill.core.verification import VerificationResult
from artifact_skill.credits_check import CREDITS_POLICY_KEYS, credits_check, validate_credits_policy
from artifact_skill.event_check import EVENT_POLICY_KEYS, event_check, validate_event_policy
from artifact_skill.source_grounding import GROUNDING_POLICY_KEYS, grounding_check, validate_grounding_policy

if TYPE_CHECKING:
    from artifact_skill.adapters.base import ArtifactAdapter
    from artifact_skill.core.artifact import ArtifactRef

TEXT_CHECK_POLICY_KEYS = GROUNDING_POLICY_KEYS | CREDITS_POLICY_KEYS | EVENT_POLICY_KEYS


def validate_text_check_policy(policy: dict[str, Any]) -> None:
    validate_grounding_policy(policy)
    validate_credits_policy(policy)
    validate_event_policy(policy)


def verify_structural_with_text_checks(
    adapter: ArtifactAdapter, ref: ArtifactRef, policy: dict[str, Any]
) -> VerificationResult:
    """`adapter.verify_structural()` plus each text check the policy asks for."""
    result = adapter.verify_structural(ref, policy)
    for check in (
        grounding_check(adapter, ref, policy),
        credits_check(adapter, ref, policy),
        event_check(adapter, ref, policy),
    ):
        if check is not None:
            result.checks.append(check)
    return result

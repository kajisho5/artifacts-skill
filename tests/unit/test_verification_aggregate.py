from __future__ import annotations

import pytest

from artifact_skill.core.verification import Check, CheckStatus, aggregate


def _check(status: CheckStatus) -> Check:
    return Check(id="x", name="x", status=status)


def test_empty_checks_aggregate_to_not_checked():
    assert aggregate([]) == CheckStatus.NOT_CHECKED


def test_all_pass_aggregates_to_pass():
    assert aggregate([CheckStatus.PASS, CheckStatus.PASS]) == CheckStatus.PASS


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([CheckStatus.PASS, CheckStatus.FAIL], CheckStatus.FAIL),
        ([CheckStatus.PASS, CheckStatus.WARN], CheckStatus.WARN),
        ([CheckStatus.PASS, CheckStatus.UNKNOWN], CheckStatus.UNKNOWN),
        ([CheckStatus.WARN, CheckStatus.UNKNOWN], CheckStatus.UNKNOWN),
        ([CheckStatus.FAIL, CheckStatus.UNKNOWN], CheckStatus.FAIL),
        ([CheckStatus.PASS, CheckStatus.SKIPPED], CheckStatus.PASS),
        ([CheckStatus.SKIPPED, CheckStatus.NOT_CHECKED], CheckStatus.NOT_CHECKED),
        ([CheckStatus.SKIPPED], CheckStatus.SKIPPED),
    ],
)
def test_worst_status_wins(statuses, expected):
    assert aggregate(statuses) == expected


def test_verification_result_status_property_matches_aggregate():
    from artifact_skill.core.verification import VerificationResult

    result = VerificationResult(kind="structural", checks=[_check(CheckStatus.PASS), _check(CheckStatus.FAIL)])
    assert result.status == CheckStatus.FAIL

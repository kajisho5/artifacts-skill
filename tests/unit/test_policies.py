from __future__ import annotations

import pytest

from artifact_skill.adapters.registry import all_adapter_classes
from artifact_skill.policies import PRESETS, known_policy_keys, resolve_policy, unknown_policy_keys


def test_every_preset_is_a_plain_dict():
    for name, policy in PRESETS.items():
        assert isinstance(policy, dict), f"preset '{name}' is not a dict"


def test_resolve_policy_with_no_preset_and_no_explicit_returns_empty():
    assert resolve_policy(None, None) == {}
    assert resolve_policy(None, {}) == {}


def test_resolve_policy_returns_preset_verbatim_when_no_override():
    assert resolve_policy("web-no-external", None) == PRESETS["web-no-external"]


def test_resolve_policy_explicit_fields_override_preset():
    result = resolve_policy("web-no-external", {"forbid_external_resources": False})
    assert result["forbid_external_resources"] is False
    assert result["require_title"] is True  # untouched preset field survives


def test_resolve_policy_explicit_only_when_no_preset():
    result = resolve_policy(None, {"min_pages": 2})
    assert result == {"min_pages": 2}


def test_resolve_policy_unknown_preset_raises_naming_valid_choices():
    with pytest.raises(KeyError) as exc_info:
        resolve_policy("not-a-real-preset", None)
    assert "not-a-real-preset" in str(exc_info.value)
    for name in PRESETS:
        assert name in str(exc_info.value)


def test_print_presets_use_real_pdf_policy_keys():
    for name in ("print-a4", "print-letter"):
        p = PRESETS[name]
        assert "require_page_size_pt" in p
        assert len(p["require_page_size_pt"]) == 2
        assert p["forbid_unembedded_fonts"] is True


def test_slides_preset_targets_16x9():
    p = PRESETS["slides-16x9"]
    assert abs(p["require_slide_aspect_ratio"] - 16 / 9) < 1e-9


def test_web_preset_forbids_external_resources():
    assert PRESETS["web-no-external"]["forbid_external_resources"] is True


def test_every_preset_forbids_leftover_placeholder_text():
    """An external review correctly caught that none of the original
    presets set forbid_placeholder_text, so leftover generation artifacts
    stayed a WARN (exit 0) even under a named preset - the single check
    this whole marker list exists for never actually gated a submission.
    Every preset now sets it; it's a harmless no-op for a format the
    preset wasn't written for (each adapter's verify_structural() only
    ever does policy.get(...), never assumes every key applies)."""
    for name, p in PRESETS.items():
        assert p.get("forbid_placeholder_text") is True, f"preset '{name}' doesn't forbid placeholder text"


def test_print_presets_are_a_real_submission_gate_not_just_page_size():
    for name in ("print-a4", "print-letter"):
        p = PRESETS[name]
        assert p["require_no_encryption"] is True
        assert p["forbid_blank_pages"] is True
        assert p["min_pages"] == 1


def test_slides_preset_requires_at_least_one_slide():
    assert PRESETS["slides-16x9"]["min_slides"] == 1


def test_spreadsheet_preset_is_named_for_what_it_actually_promises():
    """Issue #19: renamed from spreadsheet-no-errors, since the preset
    cannot actually promise 'no errors' - formula_recalculation is
    UNKNOWN whenever any formula exists (openpyxl never recalculates),
    and UNKNOWN outranks PASS in aggregation, so a workbook with formulas
    but zero real problems still shows an overall UNKNOWN under this
    preset. 'no-cached-errors' matches what forbid_placeholder_text/
    forbid_external_links/formula_cached_errors can actually verify."""
    assert "spreadsheet-no-errors" not in PRESETS
    p = PRESETS["spreadsheet-no-cached-errors"]
    assert p["forbid_external_links"] is True


# --- unknown/misspelled policy keys are rejected, not silently ignored -----
# (Issue #24: policy.get("min_pagess") used to just look like "not specified"
# and quietly PASS instead of erroring on the caller's typo.)


def test_a_misspelled_policy_key_is_rejected():
    with pytest.raises(KeyError) as exc_info:
        resolve_policy(None, {"min_pagess": 1})
    assert "min_pagess" in str(exc_info.value)


def test_a_real_key_from_one_adapter_is_accepted_even_alone():
    # min_pages is a PDF-only key; nothing else about the call says "PDF" -
    # resolve_policy() has no artifact type to check against, only whether
    # the key is real *somewhere*, so this must not raise.
    assert resolve_policy(None, {"min_pages": 2}) == {"min_pages": 2}


def test_every_named_preset_only_uses_real_policy_keys():
    for name in PRESETS:
        assert unknown_policy_keys(PRESETS[name]) == [], f"preset '{name}' references an unrecognized policy key"


def test_a_key_valid_for_a_different_adapter_is_not_flagged_as_unknown():
    """The documented cross-adapter-reuse case (policies.py's module
    docstring): web-no-external's require_title is real (HTML), just inert
    when applied to SVG - resolve_policy() must not reject it, only a key
    that's not real for ANY adapter."""
    result = resolve_policy("web-no-external", None)
    assert unknown_policy_keys(result) == []


def test_multiple_unknown_keys_are_all_named_in_the_error():
    with pytest.raises(KeyError) as exc_info:
        resolve_policy(None, {"min_pagess": 1, "totally_made_up": True})
    message = str(exc_info.value)
    assert "min_pagess" in message
    assert "totally_made_up" in message


def test_known_policy_keys_is_the_union_across_every_registered_adapter():
    expected: set[str] = set()
    for adapter_cls in all_adapter_classes():
        expected |= adapter_cls().recognized_policy_keys()
    assert known_policy_keys() == frozenset(expected)
    assert "forbid_placeholder_text" in known_policy_keys()  # sanity: a real, widely-shared key is present

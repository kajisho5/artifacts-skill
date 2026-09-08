from __future__ import annotations

import pytest

from artifact_skill.policies import PRESETS, resolve_policy


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

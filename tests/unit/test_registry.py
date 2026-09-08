"""Tests for adapters/registry.py's dispatch logic.

All six known ArtifactTypes now have a real adapter (see
docs/roadmap.md — Tier 1 + Image/HTML/SVG from Tier 2 are all
implemented), so `_PLANNED` is currently empty in production. Its code
path — a format that's a real, recognized type but genuinely not
implemented yet — still needs to stay correct for whenever a future
format (Tier 3: EPUB, CSV-as-a-real-format, etc.) is added there before
its adapter lands. Exercised here via a temporary monkeypatched entry
rather than a real fixture, since none currently exists.
"""

from __future__ import annotations

import pytest

from artifact_skill.adapters import registry
from artifact_skill.core.artifact import ArtifactType
from artifact_skill.core.errors import ArtifactCapabilityError


def test_get_adapter_returns_a_working_instance_for_every_registered_type():
    for artifact_type in registry.registered_types():
        adapter = registry.get_adapter(artifact_type)
        assert adapter.id
        assert callable(adapter.inspect)


def test_get_adapter_raises_not_implemented_for_a_planned_type(monkeypatch):
    monkeypatch.setitem(registry._PLANNED, ArtifactType.PDF, "Phase 99 (test-only)")
    monkeypatch.delitem(registry._REGISTRY, ArtifactType.PDF, raising=False)
    with pytest.raises(ArtifactCapabilityError) as exc_info:
        registry.get_adapter(ArtifactType.PDF)
    assert exc_info.value.code == "ARTIFACT_ADAPTER_NOT_IMPLEMENTED"
    assert "Phase 99" in exc_info.value.remediation


def test_get_adapter_raises_type_unsupported_for_unknown_type():
    with pytest.raises(ArtifactCapabilityError) as exc_info:
        registry.get_adapter(ArtifactType.UNKNOWN)
    assert exc_info.value.code == "ARTIFACT_TYPE_UNSUPPORTED"


def test_register_for_types_maps_one_adapter_to_several_types():
    from artifact_skill.adapters.image.adapter import ImageAdapter

    for t in (ArtifactType.IMAGE_PNG, ArtifactType.IMAGE_JPEG, ArtifactType.IMAGE_WEBP):
        assert registry.get_adapter(t).id == ImageAdapter.id


def test_get_adapter_raises_a_specific_error_for_a_cfb_ole2_container():
    """Issue #27: a password-protected Office file (or a legacy pre-2007
    binary Office file) is a CFB/OLE2 container, which the type detector
    now recognizes as its own distinct type rather than lumping it in
    with a genuinely unrecognized file. This must get a specific,
    actionable error — not the generic ARTIFACT_TYPE_UNSUPPORTED message,
    which would tell a user nothing about *why* their real .pptx file
    (just password-protected) isn't opening."""
    with pytest.raises(ArtifactCapabilityError) as exc_info:
        registry.get_adapter(ArtifactType.OLE_COMPOUND_FILE)
    assert exc_info.value.code == "ARTIFACT_OLE_COMPOUND_FILE_UNSUPPORTED"
    assert exc_info.value.code != "ARTIFACT_TYPE_UNSUPPORTED"
    assert "password" in exc_info.value.remediation.lower()

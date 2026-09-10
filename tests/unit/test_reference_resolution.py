from __future__ import annotations

from artifact_skill.reference_resolution import resolve_local_reference


def test_existing_local_file_resolves_present(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"fake")
    outcome = resolve_local_reference(tmp_path, "logo.png")
    assert outcome.escaped is False
    assert outcome.exists is True


def test_missing_local_file_resolves_missing(tmp_path):
    outcome = resolve_local_reference(tmp_path, "does_not_exist.png")
    assert outcome.escaped is False
    assert outcome.exists is False


def test_escaping_reference_is_reported_escaped_not_missing(tmp_path):
    (tmp_path / "child").mkdir()
    outcome = resolve_local_reference(tmp_path / "child", "../../etc/passwd")
    assert outcome.escaped is True
    assert outcome.exists is False


def test_fragment_is_stripped_before_resolution(tmp_path):
    (tmp_path / "page.svg").write_bytes(b"fake")
    outcome = resolve_local_reference(tmp_path, "page.svg#section")
    assert outcome.escaped is False
    assert outcome.exists is True


def test_query_string_is_kept_by_default(tmp_path):
    # SVG/Markdown's own pre-existing behavior (never stripped a query
    # string) - only HTML opts into strip_query=True.
    (tmp_path / "photo.png").write_bytes(b"fake")
    outcome = resolve_local_reference(tmp_path, "photo.png?v=2")
    assert outcome.exists is False


def test_query_string_is_stripped_when_requested(tmp_path):
    (tmp_path / "photo.png").write_bytes(b"fake")
    outcome = resolve_local_reference(tmp_path, "photo.png?v=2", strip_query=True)
    assert outcome.exists is True

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from artifact_skill.core.errors import ArtifactSecurityError
from artifact_skill.security.limits import Limits
from artifact_skill.security.paths import (
    atomic_write_bytes,
    check_input_size,
    resolve_within,
    safe_extract_zip,
)


def test_resolve_within_allows_nested_path(tmp_path):
    target = resolve_within(tmp_path, Path("a/b/c.txt"))
    assert target == (tmp_path / "a/b/c.txt").resolve()


def test_resolve_within_rejects_dotdot_escape(tmp_path):
    with pytest.raises(ArtifactSecurityError) as exc_info:
        resolve_within(tmp_path, Path("../../etc/passwd"))
    assert exc_info.value.code == "ARTIFACT_PATH_ESCAPE"


def test_resolve_within_rejects_absolute_escape(tmp_path):
    with pytest.raises(ArtifactSecurityError):
        resolve_within(tmp_path, Path("/etc/passwd"))


def test_atomic_write_never_leaves_partial_file(tmp_path):
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello world")
    assert target.read_bytes() == b"hello world"
    # no leftover temp files
    leftovers = [p for p in tmp_path.iterdir() if p.name != "out.bin"]
    assert leftovers == []


def test_safe_extract_zip_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../etc/passwd", "pwned")
    with pytest.raises(ArtifactSecurityError) as exc_info:
        safe_extract_zip(archive, tmp_path / "dest")
    assert exc_info.value.code == "ARTIFACT_ZIP_PATH_ESCAPE"


def test_safe_extract_zip_rejects_too_many_members(tmp_path):
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(5):
            zf.writestr(f"file{i}.txt", "x")
    limits = Limits(max_zip_members=3)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        safe_extract_zip(archive, tmp_path / "dest", limits=limits)
    assert exc_info.value.code == "ARTIFACT_ZIP_TOO_MANY_MEMBERS"


def test_safe_extract_zip_rejects_bomb_like_compression_ratio(tmp_path):
    archive = tmp_path / "bomb.zip"
    # 20MB of zeros compresses far beyond the 200x default ratio.
    payload = b"\x00" * (20 * 1024 * 1024)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.bin", payload)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        safe_extract_zip(archive, tmp_path / "dest")
    assert exc_info.value.code == "ARTIFACT_ZIP_BOMB_SUSPECTED"


def test_safe_extract_zip_extracts_a_normal_archive(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "hello")
        zf.writestr("dir/b.txt", "world")
    dest = tmp_path / "dest"
    extracted = safe_extract_zip(archive, dest)
    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "dir" / "b.txt").read_text() == "world"
    assert len(extracted) == 2


def test_check_input_size_rejects_oversized_file(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x00" * 1000)
    with pytest.raises(ArtifactSecurityError) as exc_info:
        check_input_size(big, limits=Limits(max_input_bytes=100))
    assert exc_info.value.code == "ARTIFACT_INPUT_TOO_LARGE"

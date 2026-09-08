"""Path safety: traversal/symlink-escape prevention, atomic writes,
and zip-bomb-resistant extraction. See docs/security.md.
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from artifact_skill.core.errors import ArtifactSecurityError
from artifact_skill.security.limits import DEFAULT_LIMITS, Limits


def reject_output_overwrites_input(input_path: Path, output_path: Path) -> None:
    """Refuse an `output_path` that would overwrite `input_path`.

    Original Protection is documented everywhere (README, SKILL.md,
    docs/security.md) as an absolute guarantee: the input file is never
    modified, no matter what `--output` a caller passes. Before this
    check, that guarantee only held by accident — every adapter's
    `execute()` writes straight to whatever `output_path` it's given, so
    `--output` equal to the input path (or a relative/absolute pair that
    resolves to it, or a pre-existing hard link/symlink aliasing the same
    file) silently overwrote the input instead of being rejected.

    Two independent checks, since a symlink at `output_path` pointing at
    `input_path` already collapses to equal strings under `.resolve()`
    (which follows symlinks), but a *hard* link doesn't — same inode,
    different path text:
    - `.resolve()` equality catches the literal-same-path and
      relative/absolute/symlink-aliasing cases.
    - `(st_dev, st_ino)` equality (checked only when both paths already
      exist as real files) catches a hard link `.resolve()` can't see.
    """
    input_resolved = input_path.resolve()
    output_resolved = output_path.resolve()
    same_path = input_resolved == output_resolved

    same_inode = False
    if not same_path and input_path.exists() and output_path.exists():
        try:
            in_stat = input_path.stat()
            out_stat = output_path.stat()
            same_inode = (in_stat.st_dev, in_stat.st_ino) == (out_stat.st_dev, out_stat.st_ino)
        except OSError:
            same_inode = False

    if same_path or same_inode:
        raise ArtifactSecurityError(
            code="ARTIFACT_OUTPUT_OVERWRITES_INPUT",
            message=f"Output path '{output_path}' would overwrite the input file '{input_path}'.",
            remediation="Choose a different --output path. Original Protection never lets an operation "
            "write back to its own input, even via a symlink, a hard link, or an equivalent relative path.",
            evidence={"input_path": str(input_path), "output_path": str(output_path)},
        )


def resolve_within(base_dir: Path, candidate: Path) -> Path:
    """Resolve `candidate` and assert it stays inside `base_dir`.

    Raises ArtifactSecurityError on escape (`..`, an absolute path outside
    base_dir, or a symlink that resolves outside base_dir).

    Its one call site (Issue #30) is `safe_extract_zip()`, guarding an
    archive member's path — the untrusted-input case a hostile zip can
    actually exploit (a member name like `../../etc/passwd`). It is not
    applied to every output/input path in the system: the CLI/MCP output
    path a caller supplies (`--output`/`output`) is not run through it —
    that's a trusted boundary in this project's threat model (the caller
    invoking this tool, not a hostile file being processed), not an
    oversight. A prior version of this docstring — and the "Filesystem"
    section of `docs/security.md` — overclaimed a blanket "every output
    path and every archive-member path" scope this function never actually
    had; broader enforcement of arbitrary output paths would be a
    deliberate, separate feature decision, not something to silently
    imply is already in place.
    """
    base_resolved = base_dir.resolve()
    target = (base_dir / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise ArtifactSecurityError(
            code="ARTIFACT_PATH_ESCAPE",
            message=f"Path '{candidate}' escapes the allowed directory '{base_dir}'.",
            remediation="Use a path relative to and contained within the workspace directory.",
            evidence={"base_dir": str(base_dir), "candidate": str(candidate)},
        ) from exc
    return target


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    """Write `data` to `path` atomically: write to a temp file in the same
    directory, fsync, then os.replace. Never leaves a partial file at `path`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


def atomic_copy(src: Path, dst: Path) -> Path:
    return atomic_write_bytes(dst, src.read_bytes())


def safe_extract_zip(archive_path: Path, dest_dir: Path, limits: Limits = DEFAULT_LIMITS) -> list[Path]:
    """Extract a zip archive with path-escape and zip-bomb protection.

    Rejects: absolute member paths, `..` traversal, symlink members, more
    members than `max_zip_members`, more total uncompressed bytes than
    `max_zip_uncompressed_bytes`, and any single member whose uncompressed
    size exceeds its compressed size by more than `max_zip_compression_ratio`.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(archive_path) as zf:
        infos = zf.infolist()
        if len(infos) > limits.max_zip_members:
            raise ArtifactSecurityError(
                code="ARTIFACT_ZIP_TOO_MANY_MEMBERS",
                message=f"Archive has {len(infos)} members, exceeding limit {limits.max_zip_members}.",
                remediation="This may be a zip bomb; the file was not extracted.",
                evidence={"archive": str(archive_path), "member_count": len(infos)},
            )
        total_uncompressed = 0
        for info in infos:
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise ArtifactSecurityError(
                    code="ARTIFACT_ZIP_PATH_ESCAPE",
                    message=f"Archive member '{name}' attempts path traversal.",
                    remediation="This archive is malformed or malicious and was not extracted.",
                    evidence={"archive": str(archive_path), "member": name},
                )
            mode = (info.external_attr >> 16) & 0xFFFF
            is_symlink = mode != 0 and (mode & 0o170000) == 0o120000
            if is_symlink:
                raise ArtifactSecurityError(
                    code="ARTIFACT_ZIP_SYMLINK_MEMBER",
                    message=f"Archive member '{name}' is a symlink, which is not permitted.",
                    remediation="Symlink members can escape the extraction directory; not extracted.",
                    evidence={"archive": str(archive_path), "member": name},
                )
            total_uncompressed += info.file_size
            if info.compress_size > 0:
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > limits.max_zip_compression_ratio and info.file_size > 10 * 1024 * 1024:
                    raise ArtifactSecurityError(
                        code="ARTIFACT_ZIP_BOMB_SUSPECTED",
                        message=f"Archive member '{name}' has a suspicious compression ratio ({ratio:.0f}x).",
                        remediation="Not extracted; this looks like a zip bomb.",
                        evidence={"archive": str(archive_path), "member": name, "ratio": ratio},
                    )
        if total_uncompressed > limits.max_zip_uncompressed_bytes:
            raise ArtifactSecurityError(
                code="ARTIFACT_ZIP_TOO_LARGE",
                message=f"Archive would expand to {total_uncompressed} bytes, exceeding limit.",
                remediation="Not extracted; increase Limits.max_zip_uncompressed_bytes if this is expected.",
                evidence={"archive": str(archive_path), "total_uncompressed": total_uncompressed},
            )
        for info in infos:
            target = resolve_within(dest_dir, Path(info.filename))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, zf.read(info))
            extracted.append(target)
    return extracted


def check_input_size(path: Path, limits: Limits = DEFAULT_LIMITS) -> None:
    size = path.stat().st_size
    if size > limits.max_input_bytes:
        raise ArtifactSecurityError(
            code="ARTIFACT_INPUT_TOO_LARGE",
            message=f"Input file is {size} bytes, exceeding limit {limits.max_input_bytes}.",
            remediation="Increase Limits.max_input_bytes if this file is legitimately expected.",
            evidence={"path": str(path), "size_bytes": size},
        )

"""Central, overridable resource limits. See docs/security.md.

Nothing in this project should hardcode a magic number for "too big" or
"too many" outside this module — that is what makes the limits auditable
and lets a caller raise them explicitly (and knowingly) instead of a
per-adapter constant drifting out of sync.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 500 * 1024 * 1024  # 500 MB
    max_zip_members: int = 20_000
    max_zip_uncompressed_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB
    # A single member decompressing to more than this many times its
    # compressed size is treated as a probable zip bomb.
    max_zip_compression_ratio: int = 200
    max_pages: int = 2000
    subprocess_timeout_seconds: int = 120
    render_timeout_seconds: int = 180
    max_fix_iterations: int = 3


DEFAULT_LIMITS = Limits()

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
    # Governs rendering/chromium_render.py's Chromium navigation + screenshot
    # calls (Issue #28 — this field used to be declared here but never
    # actually read anywhere; Chromium had its own separate, disconnected
    # hardcoded 30s timeout instead). 30s, not subprocess_timeout_seconds'
    # 120s, because it's the value that was already empirically validated
    # against a real hang case while building the SVG adapter (see
    # chromium_render.py's module docstring) — wiring the field up for real
    # shouldn't silently change existing runtime behavior along with it.
    render_timeout_seconds: int = 30
    max_fix_iterations: int = 3
    # Security-review finding, verified by direct reproduction: a video
    # container can declare an enormous *decoded* frame size while
    # compressing to almost nothing (a solid-color frame is trivially
    # compressible) - a 12000x12000 H.264 MP4 built this way is ~28KB on
    # disk (well under max_input_bytes) but made ffprobe alone peak at
    # ~396MB RSS, and the adapter's full render() path (probe + frame
    # decode) peak at ~1.4GB RSS and take ~16s — a decompression-bomb
    # shape analogous to the zip-bomb guard above, just for pixels
    # instead of bytes. adapters/media/adapter.py checks width*height
    # against this immediately after ffprobe returns, in inspect() (so
    # verify_structural() and render() both inherit the guard, not just
    # render()'s own more expensive decode step). Default (~64 megapixels)
    # comfortably covers real-world video up to 8K UHD (7680x4320 =
    # ~33 megapixels) with headroom, while still rejecting the reproduced
    # bomb shape (144 megapixels) well before ffmpeg/ffprobe pay the cost
    # of decoding it.
    max_video_pixels: int = 64_000_000


DEFAULT_LIMITS = Limits()

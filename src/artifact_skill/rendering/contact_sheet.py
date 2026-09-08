"""Contact sheet / before-after comparison image assembly.

Format-agnostic: takes a list of already-rendered page images (PNG paths,
whatever adapter produced them) and lays them out into one grid image sized
for quick Agent visual inspection (spec #13). This module never decides
whether the result looks *correct* — it only assembles pixels.
"""

from __future__ import annotations

import math
from pathlib import Path

from artifact_skill.core.errors import ArtifactCapabilityError, ArtifactInputError

_THUMB_SIZE = (300, 400)
_MARGIN = 8
_LABEL_HEIGHT = 20


def _require_pillow():
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ArtifactCapabilityError(
            code="ARTIFACT_CAPABILITY_MISSING",
            message="Pillow is not installed; contact sheet assembly is unavailable.",
            remediation="Install with: pip install 'artifact-skill[pdf]' (or `pip install Pillow`).",
            evidence={"capability_id": "rendering.pillow"},
        ) from exc
    return Image, ImageDraw


def build_contact_sheet(image_paths: list[Path], out_path: Path, columns: int = 4) -> Path:
    Image, ImageDraw = _require_pillow()
    if not image_paths:
        raise ArtifactInputError(
            code="ARTIFACT_INPUT_NOT_FOUND", message="No rendered images were provided to build a contact sheet from."
        )
    columns = max(1, min(columns, len(image_paths)))
    rows = math.ceil(len(image_paths) / columns)
    cell_w = _THUMB_SIZE[0] + _MARGIN
    cell_h = _THUMB_SIZE[1] + _MARGIN + _LABEL_HEIGHT
    sheet = Image.new("RGB", (columns * cell_w + _MARGIN, rows * cell_h + _MARGIN), color="white")
    draw = ImageDraw.Draw(sheet)

    for i, img_path in enumerate(image_paths):
        img = Image.open(img_path)
        img.thumbnail(_THUMB_SIZE)
        col, row = i % columns, i // columns
        x = _MARGIN + col * cell_w
        y = _MARGIN + row * cell_h
        sheet.paste(img, (x, y))
        draw.text((x, y + _THUMB_SIZE[1] + 2), img_path.name, fill="black")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def build_before_after(before_paths: list[Path], after_paths: list[Path], out_path: Path) -> Path:
    Image, ImageDraw = _require_pillow()
    if not before_paths or not after_paths:
        raise ArtifactInputError(
            code="ARTIFACT_INPUT_NOT_FOUND", message="Both `before` and `after` need at least one rendered page."
        )
    pairs = list(zip(before_paths, after_paths))
    cell_w = _THUMB_SIZE[0] * 2 + _MARGIN * 3
    cell_h = _THUMB_SIZE[1] + _MARGIN + _LABEL_HEIGHT
    sheet = Image.new("RGB", (cell_w, len(pairs) * cell_h + _MARGIN), color="white")
    draw = ImageDraw.Draw(sheet)

    for i, (before, after) in enumerate(pairs):
        y = _MARGIN + i * cell_h
        b_img = Image.open(before)
        b_img.thumbnail(_THUMB_SIZE)
        a_img = Image.open(after)
        a_img.thumbnail(_THUMB_SIZE)
        sheet.paste(b_img, (_MARGIN, y))
        sheet.paste(a_img, (_MARGIN * 2 + _THUMB_SIZE[0], y))
        draw.text((_MARGIN, y + _THUMB_SIZE[1] + 2), f"before: {before.name}", fill="black")
        draw.text((_MARGIN * 2 + _THUMB_SIZE[0], y + _THUMB_SIZE[1] + 2), f"after: {after.name}", fill="black")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path

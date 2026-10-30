"""Captions are not this skill's job (docs/architecture.md, "Captions
(ecosystem roadmap step 4)"): the AI Video Production OS ownership map
gives caption files to subtitle-skill and caption timing checks to
qc-skill. The contract must not claim any caption capability, must say so
in `not_provided`, and a caption file must be rejected loudly rather than
passing an empty check.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from artifact_skill.core.artifact import ArtifactType, detect_type
from artifact_skill.core.contract import build_contract

_CAPTION_WORDS = ("caption", "subtitle", "srt", "vtt", "webvtt", "ass")

_CAPTION_FILES = {
    "captions.srt": "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n",
    "captions.vtt": "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello\n",
    "captions.ass": (
        "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello\n"
    ),
}


def _mentions_captions(value: str) -> bool:
    tokens = value.lower().replace("-", ".").replace("_", ".").replace("/", ".").split(".")
    return any(word in tokens for word in _CAPTION_WORDS)


def test_contract_claims_no_caption_capability():
    contract = build_contract()
    for entry in contract["provides"]:
        assert not _mentions_captions(entry["id"]), entry
    for tool in contract["tools"]:
        for value in tool["artifact_types"] + tool["capabilities"]:
            assert not _mentions_captions(value), (tool["name"], value)
    assert not any(_mentions_captions(t.value) for t in ArtifactType)


def test_not_provided_hands_captions_to_their_owners():
    not_provided = build_contract()["not_provided"]
    assert all(isinstance(item, str) and item for item in not_provided)
    caption_items = [item for item in not_provided if "caption" in item.lower()]
    assert any(item.endswith(": subtitle-skill") for item in caption_items)
    assert any(item.endswith(": qc-skill") for item in caption_items)
    assert all(fmt in " ".join(caption_items) for fmt in ("SRT", "WebVTT", "ASS"))


@pytest.mark.parametrize("name", sorted(_CAPTION_FILES))
def test_caption_files_are_rejected_not_silently_checked(name, tmp_path):
    path = tmp_path / name
    path.write_text(_CAPTION_FILES[name], encoding="utf-8")
    assert detect_type(path) is ArtifactType.UNKNOWN

    cli = shutil.which("artifacts-skill")
    cmd = [cli] if cli else [sys.executable, "-m", "artifact_skill.cli.main"]
    proc = subprocess.run(
        [*cmd, "inspect", str(path), "--json"], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode != 0
    assert json.loads(proc.stdout)["error"]["code"] == "ARTIFACT_TYPE_UNSUPPORTED"

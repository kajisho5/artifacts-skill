"""Unit tests for core/run_dir.py (Issue #47)."""

from __future__ import annotations

import json
import re
from multiprocessing import get_context
from pathlib import Path

import pytest

from artifact_skill.core import run_dir
from artifact_skill.core.run_dir import evidence_dir_for, new_run_dir, new_run_id
from artifact_skill.mcp.server import CAPABILITY_PREFIX, call_tool


def test_run_id_is_timestamp_plus_random_suffix():
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", new_run_id())


def test_new_run_dir_reserves_a_directory_under_the_root(tmp_path):
    root = tmp_path / "reports"
    d = new_run_dir(root)
    assert d.is_dir()
    assert d.parent == root


def test_new_run_dir_picks_again_when_the_name_is_taken(tmp_path, monkeypatch):
    root = tmp_path / "reports"
    (root / "taken").mkdir(parents=True)
    names = iter(["taken", "taken", "free"])
    monkeypatch.setattr(run_dir, "new_run_id", lambda: next(names))
    assert new_run_dir(root) == root / "free"


def test_new_run_dir_without_create_writes_nothing(tmp_path):
    d = new_run_dir(tmp_path / "reports", create=False)
    assert d.parent == tmp_path / "reports"
    assert not (tmp_path / "reports").exists()


def _reserve(root: str) -> str:
    return str(new_run_dir(Path(root)))


def test_concurrent_processes_never_get_the_same_directory(tmp_path):
    root = tmp_path / "reports"
    with get_context("spawn").Pool(8) as pool:
        dirs = pool.map(_reserve, [str(root)] * 50)
    assert len(set(dirs)) == 50


def test_explicit_dir_is_used_as_given_and_kept(tmp_path):
    with evidence_dir_for(tmp_path / "mine", root=tmp_path / "reports") as d:
        assert d == tmp_path / "mine"
    assert not (tmp_path / "reports").exists()


def test_empty_default_dir_is_removed_and_a_used_one_kept(tmp_path):
    root = tmp_path / "reports"
    with pytest.raises(ValueError), evidence_dir_for(None, root=root) as empty:
        raise ValueError("input rejected")
    assert not empty.exists()

    with evidence_dir_for(None, root=root) as used:
        (used / "receipt.json").write_text("{}")
    assert (used / "receipt.json").exists()


def test_mcp_receipt_calls_get_separate_directories(good_pdf, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for _ in range(2):
        result = call_tool(f"{CAPABILITY_PREFIX}.receipt", {"input": str(good_pdf)})
        assert result["isError"] is False
    assert len(list((tmp_path / "reports").glob("*/receipt.json"))) == 2

    result = call_tool(
        f"{CAPABILITY_PREFIX}.execute",
        {"input": str(good_pdf), "operation": "metadata_set", "args": {"title": "x"},
         "output": str(tmp_path / "out.pdf")},
    )
    payload = json.loads(result["content"][0]["text"])
    assert (Path(payload["evidence_dir"]) / "receipt.json").exists()

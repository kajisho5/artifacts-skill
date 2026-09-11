from __future__ import annotations

from artifact_skill.core.capability import CapabilityStatus
from artifact_skill.doctor.detect import detect_environment


def test_ffmpeg_skill_capability_not_required_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    report = detect_environment()
    cap = report.get("companion.ffmpeg_skill")
    assert cap is not None
    assert cap.status == CapabilityStatus.NOT_REQUIRED


def test_ffmpeg_skill_capability_available_when_skill_md_present_in_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / ".claude" / "skills" / "ffmpeg-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# ffmpeg-skill")

    report = detect_environment()
    cap = report.get("companion.ffmpeg_skill")
    assert cap is not None
    assert cap.status == CapabilityStatus.AVAILABLE
    assert str(skill_dir) in cap.detail


def test_ffmpeg_skill_capability_available_when_skill_md_present_in_project(tmp_path, monkeypatch):
    other_home = tmp_path / "home"
    other_home.mkdir()
    monkeypatch.setenv("HOME", str(other_home))
    project_dir = tmp_path / "project"
    skill_dir = project_dir / ".claude" / "skills" / "ffmpeg-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# ffmpeg-skill")
    monkeypatch.chdir(project_dir)

    report = detect_environment()
    cap = report.get("companion.ffmpeg_skill")
    assert cap is not None
    assert cap.status == CapabilityStatus.AVAILABLE

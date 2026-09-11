from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "bump_version", Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
)
assert _SPEC and _SPEC.loader
bump_version = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bump_version)


@pytest.mark.parametrize(
    ("version", "level", "expected"),
    [
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("0.2.0", "minor", "0.3.0"),
    ],
)
def test_bump(version, level, expected):
    assert bump_version.bump(version, level) == expected


def test_classify_feat_bumps_minor_and_lists_under_added():
    level, sections = bump_version.classify([("feat: add a thing", "")])
    assert level == "minor"
    assert sections == {"Added": ["add a thing"]}


def test_classify_fix_bumps_patch_and_lists_under_fixed():
    level, sections = bump_version.classify([("fix: correct a thing", "")])
    assert level == "patch"
    assert sections == {"Fixed": ["correct a thing"]}


def test_classify_bang_suffix_is_breaking():
    level, _ = bump_version.classify([("feat!: change the contract", "")])
    assert level == "major"


def test_classify_breaking_change_footer_is_breaking():
    level, _ = bump_version.classify([("fix: patch something", "BREAKING CHANGE: it moves a flag")])
    assert level == "major"


def test_classify_breaking_change_mentioned_in_prose_is_not_breaking():
    """Confirmed by direct reproduction on this script's own first real
    commit: "BREAKING CHANGE" merely mentioned mid-sentence, describing the
    feature rather than declaring an actual footer trailer, was
    misclassified as a real breaking change before this fix."""
    body = (
        "Verified end-to-end against disposable git worktrees before writing this\n"
        "commit message: a feat:+fix: pair produces a correct minor bump with a\n"
        "grouped changelog section, a feat!:/BREAKING CHANGE: commit produces a\n"
        "major bump, and chore:/docs:/untyped commits alone produce no bump at\n"
        "all."
    )
    level, _ = bump_version.classify([("feat: automate version bumps from Conventional Commits PR titles", body)])
    assert level == "minor"


def test_classify_chore_and_untyped_commits_never_bump():
    level, sections = bump_version.classify(
        [("chore: tidy up", ""), ("Merge pull request #1 from x/y", ""), ("random commit message", "")]
    )
    assert level is None
    assert sections == {}


def test_classify_highest_bump_wins_regardless_of_order():
    level, _ = bump_version.classify([("fix: a", ""), ("feat: b", ""), ("chore: c", "")])
    assert level == "minor"

    level, _ = bump_version.classify([("feat: a", ""), ("fix!: b", "")])
    assert level == "major"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _init_fake_repo(root: Path) -> None:
    _git("init", "-q", cwd=root)
    _git("-c", "user.email=t@t.com", "-c", "user.name=t", "config", "commit.gpgsign", "false", cwd=root)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.2.0"\n')
    (root / "package.json").write_text('{\n  "name": "x",\n  "version": "0.2.0"\n}\n')
    (root / "src" / "artifact_skill").mkdir(parents=True)
    (root / "src" / "artifact_skill" / "__init__.py").write_text('__version__ = "0.2.0"\n')
    (root / "CHANGELOG.md").write_text("# Changelog\n\nIntro text.\n\n## v0.2.0\n\n- initial\n")
    _git("add", "-A", cwd=root)
    _git("-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "chore: initial", cwd=root)
    _git("tag", "v0.2.0", cwd=root)


def test_end_to_end_bump_writes_all_three_version_files_and_changelog(tmp_path, monkeypatch):
    _init_fake_repo(tmp_path)
    _git(
        "-c", "user.email=t@t.com", "-c", "user.name=t",
        "commit", "--allow-empty", "-q", "-m", "feat: add a widget",
        cwd=tmp_path,
    )
    _git(
        "-c", "user.email=t@t.com", "-c", "user.name=t",
        "commit", "--allow-empty", "-q", "-m", "fix: correct the widget\n\nno body needed",
        cwd=tmp_path,
    )

    monkeypatch.setattr(bump_version, "ROOT", tmp_path)
    monkeypatch.setattr(bump_version, "PYPROJECT", tmp_path / "pyproject.toml")
    monkeypatch.setattr(bump_version, "PACKAGE_JSON", tmp_path / "package.json")
    monkeypatch.setattr(bump_version, "INIT_PY", tmp_path / "src" / "artifact_skill" / "__init__.py")
    monkeypatch.setattr(bump_version, "CHANGELOG", tmp_path / "CHANGELOG.md")

    exit_code = bump_version.main()

    assert exit_code == 0
    assert 'version = "0.3.0"' in (tmp_path / "pyproject.toml").read_text()
    assert '"version": "0.3.0"' in (tmp_path / "package.json").read_text()
    assert '__version__ = "0.3.0"' in (tmp_path / "src" / "artifact_skill" / "__init__.py").read_text()
    changelog = (tmp_path / "CHANGELOG.md").read_text()
    assert changelog.index("## v0.3.0") < changelog.index("## v0.2.0")
    assert "add a widget" in changelog
    assert "correct the widget" in changelog


def test_end_to_end_no_bump_when_no_conventional_commit_landed(tmp_path, monkeypatch):
    _init_fake_repo(tmp_path)
    _git(
        "-c", "user.email=t@t.com", "-c", "user.name=t",
        "commit", "--allow-empty", "-q", "-m", "just a plain commit message",
        cwd=tmp_path,
    )

    monkeypatch.setattr(bump_version, "ROOT", tmp_path)
    monkeypatch.setattr(bump_version, "PYPROJECT", tmp_path / "pyproject.toml")
    monkeypatch.setattr(bump_version, "PACKAGE_JSON", tmp_path / "package.json")
    monkeypatch.setattr(bump_version, "INIT_PY", tmp_path / "src" / "artifact_skill" / "__init__.py")
    monkeypatch.setattr(bump_version, "CHANGELOG", tmp_path / "CHANGELOG.md")

    bump_version.main()

    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text()

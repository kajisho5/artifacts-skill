#!/usr/bin/env python3
"""Compute the next Conventional-Commits-driven version bump and, if one is
warranted, write it into pyproject.toml/package.json/__init__.py and add a
CHANGELOG.md section.

Adopted going forward only - see the "Automated versioning" section this
same change adds to docs/roadmap.md for the full policy. Nothing before
this script existed used Conventional Commits titles, so "no bump found"
is the expected, correct answer until a properly-typed PR merges after it.

Intentionally does not touch git (no commit, no push, no PR) - the CI
workflow that calls this script (.github/workflows/auto-version-bump.yml)
owns that, so this stays testable as a plain script and dry-run-able
locally without any GitHub-side side effects.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_JSON = ROOT / "package.json"
INIT_PY = ROOT / "src" / "artifact_skill" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

# Conventional Commits type -> changelog section heading. Types not listed
# here (chore, ci, style, test, build, ...) are tracked in no section and
# never contribute to the bump - by design: a version bump means "there is
# something in this release a user of the package would care about."
SECTION_HEADINGS = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "docs": "Documentation",
}
_TYPE_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(\([^)]*\))?(?P<breaking>!)?:\s*(?P<desc>.+)$")


def _run(*args: str) -> str:
    # rstrip("\n") only, not the default str.strip() - the git log format
    # below uses \x1e/\x1f as record/field separators, and Python's default
    # whitespace set for strip() includes those control characters, which
    # silently corrupted the final record's field count (confirmed by
    # direct reproduction before this fix).
    return subprocess.run(  # noqa: S603
        args, cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.rstrip("\n")


def read_version() -> str:
    match = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit("Could not find a version = \"...\" line in pyproject.toml")
    return match.group(1)


def latest_tag() -> str | None:
    """The newest vX.Y.Z tag reachable from HEAD, or None if there isn't
    one yet. Deliberately does not assume `v{pyproject's current version}`
    exists as a tag - the version-bump PR this script drives updates
    pyproject.toml before the release workflow (a separate, independently
    triggered workflow) gets around to creating that tag, so relying on it
    existing would race against that workflow."""
    try:
        output = _run("git", "tag", "--merged", "HEAD", "--sort=-version:refname")
    except subprocess.CalledProcessError:
        return None
    for line in output.splitlines():
        if re.match(r"^v\d+\.\d+\.\d+$", line.strip()):
            return line.strip()
    return None


def commits_since(tag: str | None) -> list[tuple[str, str]]:
    """Returns (subject, body) for every commit reachable from HEAD but not
    from `tag`, oldest first. An empty/missing `tag` means "the repo has no
    tags yet" - use the full history instead of failing."""
    rev_range = f"{tag}..HEAD" if tag else "HEAD"
    log = _run("git", "log", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", rev_range)
    commits = []
    for raw_record in log.split("\x1e"):
        record = raw_record.strip("\n")
        if not record:
            continue
        _sha, subject, body = record.split("\x1f")
        commits.append((subject.strip(), body.strip()))
    return commits


def classify(commits: list[tuple[str, str]]) -> tuple[str | None, dict[str, list[str]]]:
    """Returns (bump_level, {section_heading: [description, ...]}).
    bump_level is one of "major"/"minor"/"patch"/None (no version-worthy
    commit found)."""
    level: str | None = None
    sections: dict[str, list[str]] = {}
    for subject, body in commits:
        match = _TYPE_RE.match(subject)
        if not match:
            continue
        commit_type = match.group("type").lower()
        breaking = bool(match.group("breaking")) or "BREAKING CHANGE" in body
        desc = match.group("desc").strip()
        if breaking:
            level = "major"
        elif commit_type == "feat" and level != "major":
            level = "minor"
        elif commit_type == "fix" and level not in ("major", "minor"):
            level = "patch"
        heading = SECTION_HEADINGS.get(commit_type)
        if heading:
            sections.setdefault(heading, []).append(desc)
    return level, sections


def bump(version: str, level: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version_files(new_version: str) -> None:
    PYPROJECT.write_text(
        re.sub(r'^version = "[^"]+"', f'version = "{new_version}"', PYPROJECT.read_text(), count=1, flags=re.MULTILINE)
    )
    package = json.loads(PACKAGE_JSON.read_text())
    package["version"] = new_version
    PACKAGE_JSON.write_text(json.dumps(package, indent=2) + "\n")
    INIT_PY.write_text(re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{new_version}"', INIT_PY.read_text(), count=1))


def insert_changelog_section(new_version: str, sections: dict[str, list[str]]) -> None:
    lines = [f"## v{new_version}", ""]
    for heading, items in sections.items():
        lines.append(f"### {heading}")
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    section_text = "\n".join(lines).rstrip() + "\n\n"

    text = CHANGELOG.read_text()
    marker = "\n## "
    insert_at = text.index(marker) + 1  # right before the first existing "## " heading
    CHANGELOG.write_text(text[:insert_at] + section_text + text[insert_at:])


def main() -> int:
    current = read_version()
    level, sections = classify(commits_since(latest_tag()))
    output_path = os.environ.get("GITHUB_OUTPUT")

    def emit(**kv: str) -> None:
        if output_path:
            with open(output_path, "a") as f:
                for key, value in kv.items():
                    f.write(f"{key}={value}\n")
        for key, value in kv.items():
            print(f"{key}={value}")

    if level is None:
        emit(bumped="false")
        return 0

    new_version = bump(current, level)
    write_version_files(new_version)
    insert_changelog_section(new_version, sections)
    emit(bumped="true", version=new_version, level=level)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Prepare a release commit: bump the version and write the CHANGELOG section.

Used by ``.github/workflows/release-cut.yml`` (a manual, maintainer-only
dispatch — a release never happens by itself) and runnable locally::

    python scripts/ci/cut_release.py --bump patch      # 2.3.2 -> 2.3.3
    python scripts/ci/cut_release.py --version 2.4.0

The version lives in exactly two places, ``pyproject.toml`` and
``jarvis/__init__.py``. The CHANGELOG section takes the ``[Unreleased]``
notes when there are any; otherwise it is built from the Conventional Commit
subjects since the previous tag (feat -> Added, fix -> Fixed, perf/refactor ->
Changed). The notes are also written to ``--notes-out`` for the GitHub
Release body. Prints the new version on the last line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.release_admit import versions  # noqa: E402

_SECTIONS = {"feat": "Added", "fix": "Fixed", "perf": "Changed", "refactor": "Changed"}
_SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?(?P<bang>!)?: (?P<text>.+)$")


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def notes_from_commits(subjects: list[str]) -> str:
    grouped: dict[str, list[str]] = {"Added": [], "Changed": [], "Fixed": []}
    breaking: list[str] = []
    for subject in subjects:
        match = _SUBJECT.match(subject.strip())
        if not match:
            continue
        section = _SECTIONS.get(match.group("type"))
        text = match.group("text").strip()
        text = text[0].upper() + text[1:]
        if match.group("bang"):
            breaking.append(text)
        if section and text not in grouped[section]:
            grouped[section].append(text)
    parts: list[str] = []
    if breaking:
        parts.append("### Breaking\n\n" + "\n".join(f"- {t}" for t in breaking))
    for section, items in grouped.items():
        if items:
            parts.append(f"### {section}\n\n" + "\n".join(f"- {t}" for t in items))
    return "\n\n".join(parts) or "### Changed\n\n- Maintenance release."


def split_unreleased(changelog: str) -> tuple[str, str, str]:
    """Return (head incl. '## [Unreleased]' line, unreleased body, rest)."""
    match = re.search(r"^## \[Unreleased\][^\n]*\n", changelog, re.MULTILINE)
    if not match:
        raise SystemExit("CHANGELOG.md has no '## [Unreleased]' heading")
    after = changelog[match.end() :]
    nxt = re.search(r"^## \[", after, re.MULTILINE)
    body = after[: nxt.start()] if nxt else after
    rest = after[nxt.start() :] if nxt else ""
    return changelog[: match.end()], body, rest


def section_notes(changelog: str, version: str) -> str:
    """The body of ``## [version]`` without its trailing ``---`` rule."""
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog, re.S | re.M
    )
    return (match.group(1) if match else "").strip().rstrip("-").strip()


def apply(version: str, notes: str, today: str, root: Path = REPO_ROOT) -> None:
    pyproject = root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^version = "[^"]+"', f'version = "{version}"', text, count=1)
    pyproject.write_bytes(text.encode("utf-8"))
    init = root / "jarvis" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    text = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text, count=1)
    init.write_bytes(text.encode("utf-8"))
    changelog = root / "CHANGELOG.md"
    head, _body, rest = split_unreleased(changelog.read_text(encoding="utf-8"))
    section = f"## [{version}] — {today}\n\n{notes.strip()}\n\n---\n\n"
    changelog.write_bytes((head + "\n---\n\n" + section + rest).encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bump", choices=("patch", "minor", "major"))
    group.add_argument("--version")
    group.add_argument("--print-notes", metavar="X.Y.Z", help="Print a released section.")
    parser.add_argument("--notes-out", type=Path, default=Path("release-notes.md"))
    args = parser.parse_args(argv)
    if args.print_notes:
        print(
            section_notes(
                (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), args.print_notes
            )
        )
        return 0

    current, package = versions()
    if current != package:
        raise SystemExit(f"version drift: pyproject {current} vs jarvis {package}")
    new = args.version or bump(current, args.bump)
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise SystemExit(f"not a SemVer version: {new}")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    _head, body, _rest = split_unreleased(changelog)
    unreleased = body.strip().strip("-").strip()
    if not unreleased:
        last = subprocess.run(  # noqa: S603
            ["git", "describe", "--tags", "--abbrev=0", "--match", "v*"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        span = f"{last}..HEAD" if last else "HEAD"
        subjects = subprocess.run(  # noqa: S603
            ["git", "log", "--no-merges", "--format=%s", span],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.splitlines()
        unreleased = notes_from_commits(subjects)
    apply(new, unreleased, dt.date.today().isoformat())
    args.notes_out.write_text(unreleased.strip() + "\n", encoding="utf-8")
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())

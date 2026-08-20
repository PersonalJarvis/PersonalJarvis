#!/usr/bin/env python3
"""Project the ``.claude/agents/`` definitions into the ``.codex/agents/`` twin.

The repo carries THREE copies of the same agent knowledge:

* ``.claude/agents/*.md``  — canonical, YAML front matter + Markdown body.
* ``.agents/agents/*.md``  — byte-identical twin, kept by ``sync_agents_dir.py``.
* ``.codex/agents/*.toml`` — the same agents in the shape Codex reads.

The first two were synced by a script from the day they existed; the third was
maintained by hand and drifted silently for months — by 2026-08-20 its
``code-reviewer`` still named a tool set (``SUB_TOOLS``) that AP-14 forbids and
a plan file that no longer exists, while its Markdown twin had moved on. A
mirror nothing verifies is not a mirror. This script closes that gap.

Direction is one-way on purpose: ``.claude/`` is canonical, ``.codex/`` is a
projection. Editing a ``.toml`` by hand is not supported — the next run
overwrites it, and ``--check`` fails the build until the Markdown side carries
the change instead.

Mapping:

    front matter ``name``        -> ``name``
    front matter ``description`` -> ``description``
    Markdown body                -> ``developer_instructions``

Everything else in the front matter (``tools``, ``model``, ``must_read``, …) is
Claude-Code-specific and has no Codex counterpart, so it is deliberately
dropped rather than invented.

Usage::

    python scripts/ci/sync_codex_agents.py            # write the projection
    python scripts/ci/sync_codex_agents.py --check    # fail if it is stale
    python scripts/ci/sync_codex_agents.py --stage    # write, then `git add` it
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / ".claude" / "agents"
TARGET_DIR = REPO_ROOT / ".codex" / "agents"

# ``INDEX.md`` is a human-facing catalogue of the other files, not an agent
# definition — it has no Codex counterpart and never had one.
SKIP_STEMS = frozenset({"INDEX", "README"})


class FrontMatterError(RuntimeError):
    """A source file does not carry the front matter this projection needs."""


def split_front_matter(text: str, source: Path) -> tuple[dict[str, str], str]:
    """Return ``(scalar front-matter fields, body)`` for one definition.

    Only top-level scalars are read. List values (``tools``, ``must_read``)
    are skipped rather than half-parsed — nothing downstream consumes them, and
    a partial YAML parser is a bug waiting for its first multi-line value.
    """
    if not text.startswith("---"):
        raise FrontMatterError(f"{source}: missing opening '---' front-matter fence")

    closing = text.find("\n---", 3)
    if closing == -1:
        raise FrontMatterError(f"{source}: missing closing '---' front-matter fence")

    head = text[3:closing]
    body = text[closing + 4 :].lstrip("\n")

    fields: dict[str, str] = {}
    for line in head.splitlines():
        if not line or line.startswith((" ", "\t", "#", "-")):
            continue  # continuation of a list value, comment, or blank
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value:  # a bare "key:" introduces a list — skip it
            fields[key.strip()] = value

    for required in ("name", "description"):
        if required not in fields:
            raise FrontMatterError(f"{source}: front matter has no '{required}'")

    return fields, body


def toml_basic_string(value: str) -> str:
    """Quote a single-line value as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_toml(name: str, description: str, body: str) -> str:
    """Render one agent as Codex-shaped TOML.

    The body goes into a *literal* multi-line string (``'''``): literal strings
    perform no escape processing, so Windows paths and regex snippets survive
    verbatim. The previous hand-maintained files used basic strings and had
    stray ``\\r`` escapes baked into every line as a result.
    """
    if "'''" in body:
        raise FrontMatterError(
            f"{name}: body contains ''' and cannot go into a TOML literal string"
        )

    # A literal string cannot end with a quote character; a trailing newline
    # before the fence keeps that safe and reads better in a diff.
    body = body.rstrip() + "\n"

    return (
        f"name = {toml_basic_string(name)}\n"
        f"description = {toml_basic_string(description)}\n"
        f"developer_instructions = '''\n{body}'''\n"
    )


def git_add(paths: list[Path]) -> None:
    """Stage exactly the files this script touched.

    Scoped to explicit paths on purpose: the working tree is frequently shared
    with other agent sessions, and a broad ``git add`` sweeps their in-flight
    work into someone else's commit (CLAUDE.md §9).
    """
    if not paths:
        return
    try:
        subprocess.run(
            ["git", "add", "--", *(str(p) for p in paths)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        )
    except OSError:
        pass  # no git on PATH: the projection is still written, just not staged


def project(check_only: bool, stage: bool = False, quiet: bool = False) -> int:
    def say(message: str) -> None:
        """Progress output. ``--quiet`` silences it; errors never route here."""
        if not quiet:
            print(message)

    if not SOURCE_DIR.is_dir():
        print(f"sync_codex_agents: {SOURCE_DIR} does not exist", file=sys.stderr)
        return 1

    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    expected: dict[Path, str] = {}
    for source in sorted(SOURCE_DIR.glob("*.md")):
        if source.stem in SKIP_STEMS:
            continue
        fields, body = split_front_matter(source.read_text(encoding="utf-8"), source)
        target = TARGET_DIR / f"{source.stem}.toml"
        expected[target] = render_toml(fields["name"], fields["description"], body)

    stale: list[str] = []
    orphaned: list[str] = []

    for target, content in expected.items():
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current == content:
            continue
        stale.append(target.name)
        if not check_only:
            target.write_text(content, encoding="utf-8", newline="\n")

    for existing in sorted(TARGET_DIR.glob("*.toml")):
        if existing in expected:
            continue
        orphaned.append(existing.name)
        if not check_only:
            existing.unlink()

    if check_only:
        if stale or orphaned:
            for name in stale:
                print(f"sync_codex_agents: STALE    .codex/agents/{name}")
            for name in orphaned:
                print(f"sync_codex_agents: ORPHANED .codex/agents/{name}")
            print(
                "\nThe Codex mirror is out of date. Edit the Markdown side under "
                ".claude/agents/, then run:\n"
                "    python scripts/ci/sync_codex_agents.py",
                file=sys.stderr,
            )
            return 1
        say(f"sync_codex_agents: {len(expected)} agents in sync.")
        return 0

    for name in stale:
        say(f"sync_codex_agents: rewrote .codex/agents/{name}")
    for name in orphaned:
        say(f"sync_codex_agents: removed .codex/agents/{name}")
    if not stale and not orphaned:
        say(f"sync_codex_agents: {len(expected)} agents already in sync.")

    if stage:
        touched = [TARGET_DIR / n for n in stale]
        touched += [TARGET_DIR / n for n in orphaned]
        git_add(touched)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero instead of rewriting",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="git add the files this run rewrote or removed (for pre-commit)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the per-file lines (errors still print)",
    )
    args = parser.parse_args()
    try:
        return project(check_only=args.check, stage=args.stage, quiet=args.quiet)
    except FrontMatterError as exc:
        print(f"sync_codex_agents: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

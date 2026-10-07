#!/usr/bin/env python3
"""Install a CI test environment from the committed ``uv.lock``.

``pip install -e ".[dev,telephony]"`` resolves every version range again on
each run. An upstream release could then turn main red with no code change,
and CI never tested the dependency set the product ships. This script installs
exactly the locked set instead:

1. ``uv export --frozen`` writes the requested extras from ``uv.lock`` as a
   hashed, platform-universal requirements file. ``--frozen`` never resolves;
   the deps lane (``check_portable_install_matrix.py``) proves the lock is
   current.
2. ``uv pip install --require-hashes`` installs that file into the running
   interpreter. On the two architectures without an upstream cryptography
   wheel the reviewed native wheel page is added, as the product installer
   does (``scripts/native_crypto_index.py``).
3. The project itself is added as an editable install without dependencies.
4. ``--with`` installs extra test tools (``pytest`` ...) for jobs that keep a
   slim base install. Their versions, and every dependency they pull, are
   constrained to the lock; a tool that is not in the lock is refused.

Usage::

    python scripts/ci/install_locked.py --extra dev --extra telephony
    python scripts/ci/install_locked.py --with pytest --with pytest-asyncio
    python scripts/ci/install_locked.py --extra dev --dry-run
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402
from scripts.native_crypto_index import pip_options  # noqa: E402

_PINNED = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", re.MULTILINE)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _uv(python: str, *args: str) -> list[str]:
    return [python, "-m", "uv", *args]


def export_command(
    python: str, output: Path, *, extras: Sequence[str] = (), all_extras: bool = False
) -> list[str]:
    """The lock export: never re-resolves, never emits the project itself."""
    command = _uv(
        python,
        "export",
        "--frozen",
        "--no-emit-project",
        "--format",
        "requirements.txt",
        "--output-file",
        str(output),
        "--quiet",
    )
    if all_extras:
        command += ["--all-extras", "--no-hashes"]
    for extra in extras:
        command += ["--extra", extra]
    return command


def install_commands(
    python: str,
    locked: Path,
    *,
    find_links: Sequence[str] = (),
    tools: Sequence[str] = (),
    constraints: Path | None = None,
) -> list[list[str]]:
    """Locked set with hashes enforced, then the editable project, then tools."""
    commands = [
        _uv(python, "pip", "install", "--python", python, "--require-hashes", "-r", str(locked))
        + list(find_links),
        _uv(python, "pip", "install", "--python", python, "--no-deps", "-e", str(ROOT)),
    ]
    if tools:
        if constraints is None:
            raise ValueError("test tools need the lock as constraints")
        commands.append(
            _uv(python, "pip", "install", "--python", python, "-c", str(constraints))
            + list(find_links)
            + list(tools)
        )
    return commands


def unlocked_tools(tools: Sequence[str], constraints_text: str) -> list[str]:
    """Tools the lock does not pin; installing them would resolve fresh."""
    pinned = {_canonical(name) for name in _PINNED.findall(constraints_text)}
    return [tool for tool in tools if _canonical(tool) not in pinned]


def _run(command: list[str], dry_run: bool) -> None:
    print("+ " + shlex.join(command), flush=True)
    if dry_run:
        return
    result = subprocess.run(
        command,
        cwd=ROOT,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--extra", action="append", default=[], help="project extra to install")
    parser.add_argument(
        "--with",
        dest="tools",
        action="append",
        default=[],
        help="extra test tool, pinned through the lock",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the commands only")
    args = parser.parse_args(argv)

    python = sys.executable
    find_links = pip_options()
    with tempfile.TemporaryDirectory(prefix="locked-install-") as tmp:
        locked = Path(tmp) / "locked.txt"
        constraints = Path(tmp) / "constraints.txt" if args.tools else None
        _run(export_command(python, locked, extras=args.extra), args.dry_run)
        if constraints is not None:
            _run(export_command(python, constraints, all_extras=True), args.dry_run)
            if not args.dry_run:
                missing = unlocked_tools(args.tools, constraints.read_text(encoding="utf-8"))
                if missing:
                    print(f"error: not pinned by uv.lock: {', '.join(missing)}", file=sys.stderr)
                    return 1
        for command in install_commands(
            python, locked, find_links=find_links, tools=args.tools, constraints=constraints
        ):
            _run(command, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

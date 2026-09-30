#!/usr/bin/env python3
"""Pick the test files a change can plausibly affect (impact selection).

Used where running everything is expensive and a full run follows anyway:
the Windows leg of a pull request (the full Windows suite runs on every push
to main and nightly) and ``scripts/agent_land.py``'s fast local check.

Selection is textual and deliberately generous — FAIL OPEN:

* a changed test file selects itself;
* a changed ``jarvis/a/b.py`` selects every test that names ``jarvis.a.b``
  (import or monkeypatch target) and every ``test_b*.py``;
* any other changed file (frontend source, docs, assets) selects the tests
  that mention its path or its file stem;
* conftest, packaging, lockfiles or the pipeline itself select EVERYTHING,
  and so does a selection larger than :data:`ALL_RATIO` of the suite.

Writes the file list to ``--out`` and ``mode=all|selected|none`` plus
``count=N`` to ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.run_tests_parallel import discover  # noqa: E402

ALL_RATIO = 0.35
_EVERYTHING_PREFIXES = (".github/workflows/", "scripts/ci/", "tests/fakes/", "tests/fixtures/")
_EVERYTHING_FILES = {
    "conftest.py",
    "tests/conftest.py",
    "tests/unit/conftest.py",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "jarvis/__init__.py",
}
# The contract guards AGENTS.md names: cheap, and they pin the invariants a
# change anywhere in jarvis/ can break without importing them by name.
SMOKE = (
    "tests/unit/brain/test_routing.py",
    "tests/unit/brain/test_output_filter.py",
    "tests/unit/sessions/test_hangup_reason_parity.py",
    "tests/unit/core/test_turn_language.py",
)
_MIN_STEM = 5


def _needles(path: str) -> list[str]:
    stem = Path(path).stem
    if path.startswith("jarvis/") and path.endswith(".py"):
        dotted = path[:-3].replace("/", ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
        parent, _, leaf = dotted.rpartition(".")
        needles = [dotted, f"from {parent} import {leaf}" if parent else dotted]
        return needles
    needles = [path]
    if len(stem) >= _MIN_STEM and stem not in {"index", "__init__", "README", "types"}:
        needles.append(stem)
    return needles


def select(changed: list[str], tests: list[str], texts: dict[str, str]) -> tuple[str, list[str]]:
    changed = [c.strip().replace("\\", "/") for c in changed if c.strip()]
    if not changed:
        return "all", list(tests)
    if any(c.startswith(_EVERYTHING_PREFIXES) or c in _EVERYTHING_FILES for c in changed):
        return "all", list(tests)
    if any(Path(c).name == "conftest.py" for c in changed):
        return "all", list(tests)
    chosen: set[str] = set()
    touches_product = False
    test_set = set(tests)
    for path in changed:
        if path in test_set:
            chosen.add(path)
            continue
        if path.startswith("jarvis/"):
            touches_product = True
        stem = Path(path).stem
        if path.endswith(".py") and stem != "__init__":
            pattern = re.compile(rf"(^|/)test_{re.escape(stem)}(_[^/]*)?\.py$")
            chosen.update(t for t in tests if pattern.search(t))
        needles = _needles(path)
        for test, text in texts.items():
            if any(needle in text for needle in needles):
                chosen.add(test)
    if touches_product:
        chosen.update(t for t in SMOKE if t in test_set)
    if len(chosen) > ALL_RATIO * max(1, len(tests)):
        return "all", list(tests)
    if not chosen:
        return "none", []
    return "selected", sorted(chosen)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changed-from", type=Path, help="File with changed paths (else stdin).")
    parser.add_argument("--out", type=Path, default=Path("selected-tests.txt"))
    args = parser.parse_args(argv)

    if args.changed_from:
        changed = args.changed_from.read_text(encoding="utf-8").splitlines()
    else:
        changed = sys.stdin.read().splitlines()
    tests = discover(["tests"])
    texts = {
        t: (REPO_ROOT / t).read_text(encoding="utf-8", errors="replace").replace("\\", "/")
        for t in tests
    }
    mode, chosen = select(changed, tests, texts)
    args.out.write_text("\n".join(chosen) + ("\n" if chosen else ""), encoding="utf-8")
    print(f"mode={mode} count={len(chosen)} of {len(tests)}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"mode={mode}\ncount={len(chosen)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

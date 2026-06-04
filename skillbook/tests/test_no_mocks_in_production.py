"""Structural enforcement of ADR-0010: no test doubles in src/skillbook/.

The original skillbook delivery (self-audit verdict (C) at
``skillbook/AUDIT.md``) shipped ``MockLLM``, ``MockSymconActor``, and
``InProcessTransport`` inside ``src/`` and wired ``MockLLM()`` as the
factory default. The DoD grep for ``TODO|FIXME|NotImplementedError`` did
not catch this because the keywords looked nothing like the real pattern.

These two tests close that gap by scanning the entire production source
tree for class definitions that match the Meszaros test-double naming
prefixes (``Mock``, ``Fake``, ``Stub``, ``Dummy``, ``InProcess``) and for
imports from ``tests``. Both must return zero matches. They are deliberately
strict — if you think your case is the exception, rename the class or
relocate the file. The rationalization that produced the original
violation is exactly the rationalization to resist here.
"""

from __future__ import annotations

import re
from pathlib import Path


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "skillbook"

_TEST_DOUBLE_CLASS_PATTERN = re.compile(
    r"^\s*class\s+(Mock|Fake|Stub|Dummy|InProcess)\w*\b",
    re.MULTILINE,
)

_TEST_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from\s+tests(?:\.|\s+import)|import\s+tests(?:\.|\s|$)|from\s+skillbook\.tests)",
    re.MULTILINE,
)


def _iter_production_python_files() -> list[Path]:
    return sorted(p for p in _SRC_ROOT.rglob("*.py") if p.is_file())


def test_no_test_doubles_in_src() -> None:
    """No class named Mock*/Fake*/Stub*/Dummy*/InProcess* may live under src/skillbook/."""
    hits: list[str] = []
    for path in _iter_production_python_files():
        text = path.read_text(encoding="utf-8")
        for match in _TEST_DOUBLE_CLASS_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            rel = path.relative_to(_SRC_ROOT.parent.parent)
            hits.append(f"{rel.as_posix()}:{line_no}: {match.group(0).strip()}")

    assert not hits, (
        "Test-double classes were found in production src/. Move them to "
        "tests/fakes/ per ADR-0010 before re-running:\n  "
        + "\n  ".join(hits)
    )


def test_no_test_double_imports_in_src() -> None:
    """Production src/ must not import from the tests/ tree."""
    hits: list[str] = []
    for path in _iter_production_python_files():
        text = path.read_text(encoding="utf-8")
        for match in _TEST_IMPORT_PATTERN.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            rel = path.relative_to(_SRC_ROOT.parent.parent)
            hits.append(f"{rel.as_posix()}:{line_no}: {match.group(0).strip()}")

    assert not hits, (
        "Production src/ files import from tests/. ADR-0010 forbids this:\n  "
        + "\n  ".join(hits)
    )

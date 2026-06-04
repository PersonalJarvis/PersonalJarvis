#!/usr/bin/env python3
"""Import-cleanliness gate (Wave 0, sub-task 0.4; HN-7, locks in PC-2).

Two checks:

1. ``import jarvis`` must succeed in the current interpreter. On a Linux/macOS
   CI leg a module-scope ``import win32pipe`` / ``winreg`` / ``global_hotkeys``
   would crash here — this proves the package imports clean on the €5-VPS target.

2. Belt-and-braces AST walk: no ``.py`` under ``jarvis/`` may import a
   platform-only package at MODULE scope. The same import is allowed inside a
   function body (lazy) or inside a ``try:``/``except ImportError:`` block (the
   guarded pattern at ``jarvis/plugins/tool/app_resolver.py:24``).

Exit 0 = clean. Exit non-zero = a regression that would break Mac/Linux.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_MODULE_SCOPE = {
    "win32api",
    "win32pipe",
    "win32file",
    "win32security",
    "win32con",
    "win32job",
    "win32event",
    "win32process",
    "win32com",
    "winreg",
    "global_hotkeys",
    "pywinauto",
    "winpty",
    "pywintypes",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
JARVIS_DIR = REPO_ROOT / "jarvis"


def _root_name(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _module_scope_violations(tree: ast.Module) -> list[tuple[str, int]]:
    """Return (module, lineno) for forbidden imports directly in module.body.

    Imports nested inside FunctionDef/AsyncFunctionDef/ClassDef (lazy) or inside
    a Try block (guarded) are intentionally NOT walked.
    """
    violations: list[tuple[str, int]] = []
    for node in tree.body:  # ONLY top-level statements, not nested.
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _root_name(alias.name) in FORBIDDEN_MODULE_SCOPE:
                    violations.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _root_name(mod) in FORBIDDEN_MODULE_SCOPE:
                violations.append((mod, node.lineno))
    return violations


def main() -> int:
    failures: list[str] = []

    # Check 1 — the package imports in this interpreter.
    try:
        import jarvis  # noqa: F401

        print(f"OK   import jarvis -> {jarvis.__file__}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL import jarvis raised: {exc!r}")
        failures.append("import jarvis failed")

    # Check 2 — AST walk.
    scanned = 0
    for py in sorted(JARVIS_DIR.rglob("*.py")):
        scanned += 1
        try:
            # utf-8-sig tolerates a leading BOM (a few legacy files carry one).
            tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        except SyntaxError as exc:
            failures.append(f"{py}: syntax error {exc}")
            continue
        for mod, lineno in _module_scope_violations(tree):
            rel = py.relative_to(REPO_ROOT)
            failures.append(
                f"{rel}:{lineno}: module-scope import of platform-only '{mod}' "
                f"(move it inside a function or a try/except ImportError block)"
            )

    print(f"OK   scanned {scanned} files under jarvis/")

    if failures:
        print("\nIMPORT-CLEANLINESS FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nimport-cleanliness gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

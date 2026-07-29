"""Dead-config-switch gate.

A config field the user can set but that no code ever reads is worse than a
missing feature: the settings screen and ``jarvis.toml`` promise behaviour that
does not exist, and the user cannot tell the difference from the outside. A
whole ``[jarvis_agents]`` section shipped that way.

This gate enforces the invariant: **every field declared on a config model in
``jarvis/core/config.py`` is read by at least one line of shipped code.**

What counts as reading it, deliberately narrowly:

* an attribute access (``cfg.section.field``),
* a name or keyword argument (``field=``, ``field``),
* a string that is EXACTLY the field name (``getattr(cfg, "field")``,
  ``payload["field"]``).

Prose does not count. Field names are collected from the AST, so a comment or
docstring merely *mentioning* a switch never keeps it alive — that is the
difference between a switch being documented and a switch being wired. Tests do
not count either: a switch only a test knows about is dead in the product,
which is exactly the bug class this gate exists to catch. ``jarvis.toml`` does
not count, because writing a value is not reading it.

Static analysis only — it never imports the app, so it is cheap and
dependency-free. Run from a pre-push hook and in CI; also covered by
``tests/unit/core/test_config_switches_wired.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CONFIG = _REPO / "jarvis" / "core" / "config.py"
_SELF = Path(__file__).resolve()
_BASELINE = Path(__file__).resolve().parent / "dead-config-switches-baseline.json"

#: Fields that are genuinely read through a dynamic path this gate cannot see.
#: Every entry names WHY. An entry here is a promise that the switch really
#: works — not a way to silence the gate. Distinct from the baseline file,
#: which is the opposite: switches known to be dead and not yet cleaned up.
_ALLOWLIST: dict[str, str] = {}

#: Directories whose Python counts as a "reader" of a config field.
_CODE_ROOTS = ("jarvis", "scripts")

#: Frontend sources — a switch may be surfaced only by the settings UI.
_FRONTEND = Path("jarvis") / "ui" / "web" / "frontend" / "src"

_TS_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_TS_TOKEN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _strip_docstrings(tree: ast.AST) -> None:
    """Drop docstring nodes so their text cannot pass as a usage."""
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        body = node.body
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            body.pop(0)


def _read(path: Path) -> str:
    """Read source as text, tolerating a UTF-8 BOM.

    Twelve tracked files (``manager.py``, ``desktop_app.py``, ``server.py``, …)
    start with a BOM. Reading those as plain ``utf-8`` leaves U+FEFF in the
    string and ``ast.parse`` rejects the file — which silently hid the largest
    modules in the repo from an earlier version of this gate and produced
    confident, wrong findings. ``utf-8-sig`` strips the BOM when present and is
    a no-op otherwise.
    """
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def config_fields() -> dict[str, str]:
    """Map every config-model field name to the model class declaring it.

    Walks the AST rather than matching text, so a multi-line ``Field(...)``
    declaration is handled exactly and a comment is never read as a
    declaration.
    """
    tree = ast.parse(_read(_CONFIG))
    fields: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            # A model field is an annotated assignment: ``name: type = ...``.
            if not isinstance(stmt, ast.AnnAssign) or not isinstance(
                stmt.target, ast.Name
            ):
                continue
            name = stmt.target.id
            # Private/dunder attributes are implementation detail, not switches.
            if not name.startswith("_"):
                fields.setdefault(name, node.name)
    return fields


class UnparseableSource(RuntimeError):
    """A reader file could not be parsed, so no verdict can be trusted."""


def _python_usages(names: frozenset[str]) -> set[str]:
    """Field names actually used by Python under the code roots.

    A file that cannot be parsed is a hard error, never a skip: skipping one
    makes every switch it reads look dead, and the gate would report that with
    full confidence. Failing loudly is the whole point.
    """
    used: set[str] = set()
    for root in _CODE_ROOTS:
        for path in (_REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts or path in (_CONFIG, _SELF):
                continue
            try:
                tree = ast.parse(_read(path))
            except SyntaxError as exc:
                raise UnparseableSource(
                    f"{path.relative_to(_REPO)} cannot be parsed ({exc}). "
                    "Fix the file — this gate cannot report on a corpus it "
                    "could not read.",
                ) from exc
            _strip_docstrings(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    used.add(node.attr)
                elif isinstance(node, ast.Name):
                    used.add(node.id)
                elif isinstance(node, ast.keyword) and node.arg:
                    used.add(node.arg)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # Only an EXACT match is a usage: a docstring mentioning the
                    # name in a sentence is prose, not a read.
                    if node.value in names:
                        used.add(node.value)
    return used


def _frontend_usages() -> set[str]:
    """Identifiers the frontend sources reference, comments removed."""
    used: set[str] = set()
    fe = _REPO / _FRONTEND
    if not fe.is_dir():
        return used
    for pattern in ("*.ts", "*.tsx"):
        for path in fe.rglob(pattern):
            src = _TS_COMMENT.sub(" ", _read(path))
            used.update(_TS_TOKEN.findall(src))
    return used


def unread_fields() -> dict[str, str]:
    """Config fields no shipped code reads, mapped to their model class."""
    fields = config_fields()
    names = frozenset(fields)
    used = _python_usages(names) | _frontend_usages()
    return {
        name: owner
        for name, owner in sorted(fields.items())
        if name not in _ALLOWLIST and name not in used
    }


def load_baseline() -> set[str]:
    """Field names already known to be dead when the gate was introduced."""
    if not _BASELINE.exists():
        return set()
    return set(json.loads(_BASELINE.read_text(encoding="utf-8"))["fields"])


def save_baseline(dead: dict[str, str]) -> None:
    payload = {
        "_comment": (
            "Config fields no shipped code reads, recorded when the gate was "
            "introduced so the backlog does not block every push. Generated by "
            "scripts/ci/check_config_switches_wired.py --update. Entries may be "
            "REMOVED freely (that means the switch was wired up or deleted); a "
            "new entry is a gate failure."
        ),
        "count": len(dead),
        "fields": {name: owner for name, owner in sorted(dead.items())},
    }
    _BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dead-config-switch gate.")
    parser.add_argument(
        "--report",
        action="store_true",
        help="list every dead switch, baseline included, and exit 0",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="record the current findings as the accepted backlog",
    )
    args = parser.parse_args()

    dead = unread_fields()

    if args.report:
        print(f"{len(dead)} dead config switch(es) of {len(config_fields())} fields.\n")
        for name, owner in dead.items():
            print(f"  {owner}.{name}")
        return 0

    if args.update:
        save_baseline(dead)
        print(f"Baseline written: {len(dead)} dead switch(es) recorded as backlog.")
        return 0

    baseline = load_baseline()
    fresh = {name: owner for name, owner in dead.items() if name not in baseline}
    cleaned = baseline - set(dead)

    if not fresh:
        msg = f"OK: no new dead config switch ({len(dead)} in the known backlog)."
        if cleaned:
            msg += f" {len(cleaned)} fixed since the baseline - run --update to record it."
        print(msg)
        return 0

    print(f"New dead config switch(es): {len(fresh)}\n")
    for name, owner in fresh.items():
        print(f"  {owner}.{name}")
    print(
        "\nEach one lets a user set something that does nothing. Wire it up, "
        "remove it, or -- only if it is read through a dynamic path this gate "
        "cannot see -- add it to _ALLOWLIST with the reason.",
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

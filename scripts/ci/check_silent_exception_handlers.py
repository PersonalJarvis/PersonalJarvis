"""Silent-exception-handler ratchet.

An ``except`` block that neither logs nor re-raises makes a failure invisible:
the feature simply does nothing, and nobody — user or maintainer — can tell it
failed. That is the single largest source of "it only half works" in this repo.
Nearly every entry in the bug register is an instance of it: dictation dropping
audio, a keybind that saved but never fired, the wiki that "captured nothing",
an embed lane dead for 19 hours. In every case the code caught the error and
said nothing.

The rule is NOT "never swallow". Plenty of handlers are legitimately quiet — an
optional import, a best-effort cleanup on teardown. The rule is that the count
may never GROW. This gate stores a per-file baseline and fails when any file
gains a silent handler, so the existing backlog is grandfathered while new ones
are blocked at the door.

A handler counts as silent when its body contains no ``raise`` and no call that
looks like reporting (``log``/``logger``/``print``/``warn``/``emit``/...). When
you must stay quiet, say why in a comment on the ``except`` line and lower the
baseline elsewhere — or better, add the one log line that would have made the
last bug a five-minute fix.

Usage::

    python scripts/ci/check_silent_exception_handlers.py            # gate
    python scripts/ci/check_silent_exception_handlers.py --report   # triage
    python scripts/ci/check_silent_exception_handlers.py --update   # re-baseline

``--update`` only ever lowers a count (or drops a deleted file). Raising a
baseline is deliberate work: pass ``--update --allow-increase`` and say why in
the commit message.

Static analysis only — it never imports the app. Covered by
``tests/unit/core/test_silent_exception_handlers.py``.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BASELINE = Path(__file__).resolve().parent / "silent-handlers-baseline.json"

#: Trees scanned. Tests are excluded: a quiet ``except`` in a test is a test
#: concern, and the product surface is what this gate protects.
_ROOTS = ("jarvis",)

#: A call whose dotted name contains one of these is treated as reporting the
#: error. Deliberately generous — the goal is "somebody could find out", not a
#: particular logging framework.
_REPORTING = (
    "log",
    "print",
    "warn",
    "error",
    "exception",
    "critical",
    "info",
    "debug",
    "emit",
    "notify",
    "report",
    "capture",
    "record",
    "publish",
    "trace",
)


class UnparseableSource(RuntimeError):
    """A scanned file could not be parsed, so its count cannot be trusted."""


def _read(path: Path) -> str:
    """Read source as text, tolerating a UTF-8 BOM.

    Twelve tracked files start with a BOM; reading them as plain ``utf-8``
    leaves U+FEFF in the string and ``ast.parse`` rejects the file. That would
    silently exclude the largest modules in the repo from this gate — the very
    failure mode the gate is about.
    """
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def _dotted(node: ast.AST) -> str:
    """Best-effort dotted name of a call target, lowercased."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts)).lower()


def _reports(handler: ast.ExceptHandler) -> bool:
    """True when the handler re-raises or tells anyone the error happened."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if any(token in name for token in _REPORTING):
                return True
    return False


def silent_handlers(path: Path) -> list[int]:
    """Line numbers of the silent ``except`` handlers in ``path``."""
    try:
        tree = ast.parse(_read(path))
    except SyntaxError as exc:
        raise UnparseableSource(f"{path.relative_to(_REPO)} cannot be parsed ({exc}).") from exc
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and not _reports(node)
    ]


def scan() -> dict[str, int]:
    """Per-file count of silent handlers, keyed by repo-relative POSIX path."""
    counts: dict[str, int] = {}
    for root in _ROOTS:
        for path in sorted((_REPO / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            found = silent_handlers(path)
            if found:
                counts[path.relative_to(_REPO).as_posix()] = len(found)
    return counts


def load_baseline() -> dict[str, int]:
    if not _BASELINE.exists():
        return {}
    return json.loads(_BASELINE.read_text(encoding="utf-8"))["files"]


def save_baseline(counts: dict[str, int]) -> None:
    payload = {
        "_comment": (
            "Per-file count of exception handlers that neither log nor re-raise. "
            "Generated by scripts/ci/check_silent_exception_handlers.py --update. "
            "These numbers may go DOWN freely; an increase is a gate failure."
        ),
        "total": sum(counts.values()),
        "files": dict(sorted(counts.items())),
    }
    _BASELINE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def regressions(current: dict[str, int], baseline: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Files that gained silent handlers, mapped to ``(baseline, current)``."""
    out: dict[str, tuple[int, int]] = {}
    for path, count in sorted(current.items()):
        was = baseline.get(path, 0)
        if count > was:
            out[path] = (was, count)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Silent-exception-handler ratchet.")
    parser.add_argument("--report", action="store_true", help="show the worst files, exit 0")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    parser.add_argument(
        "--allow-increase",
        action="store_true",
        help="with --update: permit a higher count (say why in the commit)",
    )
    args = parser.parse_args()

    current = scan()
    total = sum(current.values())

    if args.report:
        print(f"{total} silent exception handlers across {len(current)} files.\n")
        for path, count in sorted(current.items(), key=lambda kv: -kv[1])[:25]:
            print(f"  {count:4d}  {path}")
        return 0

    baseline = load_baseline()

    if args.update:
        # An absent baseline is the first run, not a regression: there is
        # nothing to raise yet, so recording the status quo is the point.
        if baseline and not args.allow_increase:
            bad = regressions(current, baseline)
            if bad:
                print(f"Refusing to raise the baseline for {len(bad)} file(s):\n")
                for path, (was, now) in list(bad.items())[:20]:
                    print(f"  {path}: {was} -> {now}")
                if len(bad) > 20:
                    print(f"  ... and {len(bad) - 20} more")
                print("\nAdd the missing log line, or pass --allow-increase deliberately.")
                return 1
        save_baseline(current)
        print(f"Baseline written: {total} silent handlers across {len(current)} files.")
        return 0

    if not baseline:
        print("No baseline yet. Run with --update to create one.")
        return 1

    bad = regressions(current, baseline)
    if not bad:
        print(f"OK: no file gained a silent exception handler (total {total}).")
        return 0

    print("New silent exception handlers - a failure here would be invisible:\n")
    for path, (was, now) in list(bad.items())[:20]:
        print(f"  {path}: {was} -> {now}")
    if len(bad) > 20:
        print(f"  ... and {len(bad) - 20} more")
    print(
        "\nLog the error (feature name + what was skipped) or re-raise it. If the "
        "silence is genuinely correct, say why on the except line and re-baseline "
        "with --update --allow-increase.",
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Gate new static findings while reporting drift already present on the base.

Compare flagged handlers with the exact base commit. A per-file count cannot
detect one old finding replaced by one new finding, and a file-level exemption
charges unrelated edits for old drift in the same file. The original ratchets
still run and log their full findings; this wrapper decides whether a PR added
one. Missing base or detector errors fail closed.
"""

from __future__ import annotations

import argparse
import ast
import collections
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis.core.process_utils import (  # noqa: E402
    NO_WINDOW_CREATIONFLAGS,
    ensure_standard_streams,
)
from scripts.ci import check_async_routes as async_gate  # noqa: E402
from scripts.ci import check_silent_exception_handlers as silent_gate  # noqa: E402

REFERENCE_PATHS = {
    "jarvis/commands/registry.py",
    "scripts/ci/gen_commands_reference.py",
    "docs/commands-reference.md",
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def changed_paths(base: str) -> set[str]:
    if run("git", "cat-file", "-e", f"{base}^{{commit}}").returncode:
        raise ValueError(f"Cannot resolve base commit {base!r}.")
    diff = run("git", "diff", "--name-only", "--diff-filter=ACMRT", base, "HEAD")
    if diff.returncode:
        raise ValueError(f"Cannot compare HEAD with base commit {base!r}: {diff.stderr}")
    return set(diff.stdout.splitlines())


def source_at_base(base: str, path: str) -> str:
    spec = f"{base}:{path}"
    if run("git", "cat-file", "-e", spec).returncode:
        return ""  # Added file: there can be no grandfathered finding.
    shown = run("git", "show", spec)
    if shown.returncode:
        raise ValueError(f"Cannot read {path} at {base}: {shown.stderr}")
    return shown.stdout.lstrip("\ufeff")


def silent_findings(source: str) -> collections.Counter[str]:
    tree = ast.parse(source)
    lines = source.splitlines()
    found: collections.Counter[str] = collections.Counter()

    class Visitor(ast.NodeVisitor):
        scope: list[str] = []

        def _scoped(self, node: ast.AST, name: str) -> None:
            self.scope.append(name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._scoped(node, node.name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._scoped(node, node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._scoped(node, node.name)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if not silent_gate._reports(node) and not silent_gate._justified(lines, node):
                key = ".".join(self.scope) + ":" + ast.dump(node, include_attributes=False)
                found[key] += 1
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def async_findings(source: str) -> collections.Counter[str]:
    tree = ast.parse(source)
    return collections.Counter(
        node.name
        + ":"
        + ",".join(ast.dump(dec, include_attributes=False) for dec in node.decorator_list)
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and async_gate._is_route_handler(node)
        and not async_gate._body_awaits(node)
    )


def added_findings(base: str, changed: set[str], gate: str) -> list[str]:
    findings = silent_findings if gate == "silent" else async_findings
    paths = (
        path
        for path in changed
        if path.startswith("jarvis/")
        and path.endswith(".py")
        and (gate == "silent" or path.startswith("jarvis/ui/web/") and path.endswith("_routes.py"))
    )
    added: list[str] = []
    for path in sorted(paths):
        head_source = (ROOT / path).read_text(encoding="utf-8-sig", errors="ignore")
        new = findings(head_source) - findings(source_at_base(base, path))
        if new:
            added.append(f"{path}: {sum(new.values())} new {gate} finding(s)")
    return added


def reference_drift_is_new(changed: set[str]) -> bool:
    return bool(changed & REFERENCE_PATHS)


def main() -> int:
    ensure_standard_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Exact PR base commit; omit for strict full scan")
    args = parser.parse_args()
    try:
        changed = changed_paths(args.base) if args.base else None
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    failed = False
    for gate, script, baseline in (
        ("silent", "check_silent_exception_handlers.py", "silent-handlers-baseline.json"),
        ("async", "check_async_routes.py", "async-routes-baseline.json"),
    ):
        result = run(sys.executable, f"scripts/ci/{script}")
        print(f"[{script}]\n{result.stdout}{result.stderr}")
        if result.returncode not in (0, 1):
            failed = True
            continue
        if changed is None:
            failed |= result.returncode != 0
            continue
        if f"scripts/ci/{script}" in changed or f"scripts/ci/{baseline}" in changed:
            failed |= result.returncode != 0
            continue
        try:
            additions = added_findings(args.base, changed, gate)
        except (SyntaxError, ValueError, OSError) as exc:
            print(f"FAIL: cannot compare {gate} findings: {exc}")
            failed = True
            continue
        if additions:
            print("FAIL: " + "; ".join(additions))
            failed = True
        elif result.returncode:
            print("BASELINE: all flagged findings existed at the exact base commit.")

    reference = run(sys.executable, "scripts/ci/gen_commands_reference.py", "--check")
    print(f"[gen_commands_reference.py]\n{reference.stdout}{reference.stderr}")
    if reference.returncode:
        if reference.returncode != 1 or changed is None or reference_drift_is_new(changed):
            failed = True
        else:
            print("BASELINE: command registry and generated reference are unchanged.")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

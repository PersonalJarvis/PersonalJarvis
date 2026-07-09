"""Danger-metadata gate.

Destructive REST routes must declare themselves: a mutating route (POST/PUT/
PATCH) whose path matches one of the known destructive markers MUST carry
``openapi_extra={"x-jarvis-dangerous": True}`` on its decorator. The flag is
what the CLI's dynamic ``jarvis api ...`` layer (and, via the Command
Registry, every other surface) uses to demand an explicit ``--yes`` /
confirmation — without it, a new destructive route silently ships under-gated
(the exact failure mode of a hand-maintained denylist).

DELETE routes are exempt: the method alone marks them dangerous everywhere
(``jarvis.cli_ctl.safety.is_dangerous``), no per-route flag needed.

The marker list is a deliberate stdlib-only copy of
``jarvis.cli_ctl.safety._DANGEROUS_MARKERS`` (this gate must stay importable
without the package installed); a parity test
(``tests/unit/cli_ctl/test_danger_metadata.py``) asserts the two never drift.

Static analysis only — never boots the app. Run from pre-push and CI.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WEB = _REPO / "jarvis" / "ui" / "web"

# Copy of jarvis.cli_ctl.safety._DANGEROUS_MARKERS — parity-tested, see above.
DANGEROUS_MARKERS: tuple[str, ...] = (
    "/restart",
    "/call",
    "/outbound",
    "/dispatch",
    "/rerun",
    "/kill",
    "/cancel",
    "/config/set",
    "/secret",
)

FLAG = "x-jarvis-dangerous"

_MUTATING_DECORATOR = re.compile(r"@\w+\.(post|put|patch)\(")
_ROUTER_PREFIX = re.compile(r"APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']")
_FIRST_STRING = re.compile(r"[\"']([^\"']*)[\"']")


def _decorator_blocks(src: str) -> list[tuple[str, str]]:
    """(method, full-argument-text) for every mutating route decorator,
    balanced-paren scanned so multi-line decorators are captured whole."""
    blocks: list[tuple[str, str]] = []
    for m in _MUTATING_DECORATOR.finditer(src):
        i = m.end()
        depth = 1
        start = i
        while i < len(src) and depth > 0:
            ch = src[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        blocks.append((m.group(1), src[start : i - 1]))
    return blocks


def _matches_marker(path: str, prefixes: list[str]) -> bool:
    candidates = [path] + [prefix + path for prefix in prefixes]
    low = [c.lower() for c in candidates]
    return any(marker in c for c in low for marker in DANGEROUS_MARKERS)


def unflagged_dangerous_routes() -> list[str]:
    """'file:METHOD path' for every marker-matching mutating route without
    the x-jarvis-dangerous flag."""
    offenders: list[str] = []
    for module in sorted(_WEB.glob("*_routes.py")):
        src = module.read_text(encoding="utf-8")
        prefixes = _ROUTER_PREFIX.findall(src)
        for method, block in _decorator_blocks(src):
            path_match = _FIRST_STRING.search(block)
            if not path_match:
                continue
            path = path_match.group(1)
            if _matches_marker(path, prefixes) and FLAG not in block:
                offenders.append(f"{module.name}: {method.upper()} {path}")
    return offenders


def main() -> int:
    offenders = unflagged_dangerous_routes()
    if offenders:
        print("Danger-metadata gate FAILED — destructive route(s) missing the flag:")
        for off in offenders:
            print(f"  - {off}")
        print(
            "\nAdd openapi_extra={\"x-jarvis-dangerous\": True} to the route "
            "decorator so the CLI safety gate and the Command Registry treat "
            "it as destructive (explicit --yes / confirmation required)."
        )
        return 1
    print("Danger-metadata gate OK — every destructive route declares the flag.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI coverage gate.

The Jarvis CLI's dynamic ``jarvis api ...`` layer turns every mounted REST route
into a command automatically, so "every WebUI action is a CLI command" holds as
long as every route module is actually mounted. This gate enforces exactly that:
every ``jarvis/ui/web/*_routes.py`` that defines a ``router = APIRouter(...)`` MUST
be imported (and thus mounted) by ``server.py``.

It catches the "route defined but never ``include_router``'d" bug class — a route
that exists in a file but is unreachable from both the WebUI and the CLI (this
happened with the frontier routes). Run from a pre-push hook and in CI; also
covered by ``tests/unit/cli_ctl/test_cli_coverage.py``.

Static analysis only — it never boots the app, so it is cheap and dependency-free.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WEB = _REPO / "jarvis" / "ui" / "web"
_SERVER = _WEB / "server.py"

# Route modules that are intentionally NOT mounted from server.py. Add a stem
# here only with a written reason; the gate stays fail-closed otherwise.
_ALLOWLIST: set[str] = {
    # Known-unmounted as of the CLI landing: their /api/frontier and
    # /api/self-mod routers are not yet include_router'd in server.py. Mounting
    # them is owned by the concurrent server.py rework; remove from this
    # allowlist once that lands so the gate fully covers them again.
    "frontier_routes",
    "self_mod_routes",
}

_ROUTER_DEF = re.compile(r"^\s*\w+\s*=\s*APIRouter\(", re.MULTILINE)


def route_modules() -> list[str]:
    """Stems of every *_routes.py under jarvis/ui/web that defines an APIRouter."""
    out: list[str] = []
    for path in sorted(_WEB.glob("*_routes.py")):
        if _ROUTER_DEF.search(path.read_text(encoding="utf-8")):
            out.append(path.stem)
    return out


def unmounted_modules() -> list[str]:
    """Route module stems that server.py does not import (hence never mounts)."""
    server_src = _SERVER.read_text(encoding="utf-8")
    missing: list[str] = []
    for stem in route_modules():
        if stem in _ALLOWLIST:
            continue
        # server.py mounts a router by importing it: `from .<stem> import ...`.
        if f"from .{stem} import" not in server_src:
            missing.append(stem)
    return missing


def main() -> int:
    missing = unmounted_modules()
    if missing:
        print("CLI coverage gate FAILED — route module(s) defined but not mounted:")
        for stem in missing:
            print(f"  - jarvis/ui/web/{stem}.py  (no `from .{stem} import` in server.py)")
        print(
            "\nA route that is not include_router'd is unreachable from the WebUI "
            "and the `jarvis` CLI. Mount it in jarvis/ui/web/server.py (or add it to "
            "the allowlist in scripts/ci/check_cli_coverage.py with a reason)."
        )
        return 1
    print(f"CLI coverage gate OK — all {len(route_modules())} route modules are mounted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

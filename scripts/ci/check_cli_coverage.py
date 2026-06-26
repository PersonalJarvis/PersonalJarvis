"""CLI coverage gate.

The Jarvis CLI's dynamic ``jarvis api ...`` layer turns every mounted REST route
into a command automatically, so "every WebUI action is a CLI command" holds as
long as every route module is actually mounted. This gate enforces exactly that:
every ``jarvis/ui/web/*_routes.py`` that defines a ``router = APIRouter(...)`` MUST
be imported (and thus mounted) by ``server.py``.

It enforces two things: (1) every router is mounted — catching the "route defined
but never ``include_router``'d" bug class (this happened with the frontier routes);
and (2) every ``APIRouter(...)`` declares ``tags=`` so its operations land in a
clean ``jarvis api <tag>`` group instead of the ``default`` bucket. Run from a
pre-push hook and in CI; also covered by ``tests/unit/cli_ctl/test_cli_coverage.py``.

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
_ALLOWLIST: set[str] = set()
# Intentionally empty: frontier_routes and self_mod_routes were the last two
# unmounted modules and are now include_router'd in server.py, so the gate fully
# covers every route module again. Add a stem here only with a written reason.

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


def _apirouter_arg_blocks(src: str) -> list[str]:
    """Return the argument text of every ``APIRouter(...)`` call in ``src``.

    Uses balanced-paren scanning so a multi-line constructor or a nested call
    (e.g. ``dependencies=[Depends(x)]``) is captured whole, not truncated at the
    first ``)``.
    """
    blocks: list[str] = []
    for m in re.finditer(r"APIRouter\(", src):
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
        blocks.append(src[start : i - 1])
    return blocks


def untagged_modules() -> list[str]:
    """Route module stems with >=1 ``APIRouter(...)`` that lacks ``tags=``.

    An untagged router is grouped under ``default`` in the dynamic
    ``jarvis api <tag> <op>`` tree, which breaks the gcloud-style command
    grouping. Every router must declare ``tags=["<domain>"]`` so its operations
    land in a clean ``jarvis api <tag>`` group.
    """
    out: list[str] = []
    for stem in route_modules():
        if stem in _ALLOWLIST:
            continue
        src = (_WEB / f"{stem}.py").read_text(encoding="utf-8")
        if any("tags=" not in block for block in _apirouter_arg_blocks(src)):
            out.append(stem)
    return out


def main() -> int:
    status = 0
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
        status = 1
    untagged = untagged_modules()
    if untagged:
        print("CLI coverage gate FAILED — route module(s) with an untagged APIRouter:")
        for stem in untagged:
            print(f"  - jarvis/ui/web/{stem}.py  (an APIRouter(...) has no tags=[...])")
        print(
            "\nAn untagged router is grouped under `default` in the dynamic "
            "`jarvis api <tag> <op>` tree, breaking gcloud-style grouping. Add "
            'tags=["<domain>"] to the APIRouter(...) call.'
        )
        status = 1
    if status == 0:
        print(
            f"CLI coverage gate OK — all {len(route_modules())} route modules are "
            "mounted and tagged."
        )
    return status


if __name__ == "__main__":
    sys.exit(main())

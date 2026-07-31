"""CLI auth probes must never run on the event loop.

``ClaudeAuthService.status`` / ``CodexAuthService.status`` /
``GoogleCliAuthService.status`` and the matching ``logout_blocking`` SPAWN the
real CLI binary. That costs a few hundred milliseconds, and on the event loop
it freezes everything else this process is doing.

Live forensic 2026-07-27 (realtime voice, gemini-live): the UI's provider
panels poll these endpoints while a reply is being spoken. Each inline probe
stalled the loop 311-375 ms, the realtime audio socket went unread, the device
buffer drained, and the maintainer heard a hole in the middle of a sentence —
logged by ``RealtimeSession._note_audio_flow`` as "this process's event loop
stalled ... the audio likely sat unread in the socket".

This is a structural guard, not a behavioural one: it walks the route modules
and fails on any ``async def`` that reaches a blocking probe without handing it
to a worker thread. Adding a new provider CLI is exactly when this regresses.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: Auth services whose probes spawn a subprocess.
BLOCKING_SERVICES = (
    "ClaudeAuthService",
    "CodexAuthService",
    "GoogleCliAuthService",
)

#: Route modules that expose those services to the UI.
ROUTE_MODULES = (
    "jarvis/ui/web/provider_routes.py",
    "jarvis/ui/web/antigravity_routes.py",
    "jarvis/ui/web/claude_routes.py",
    "jarvis/ui/web/server.py",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _async_defs_touching_a_blocking_service(path: Path):
    """Yield (function name, source) for async defs naming a blocking service."""
    # utf-8-sig: several route modules are BOM-prefixed (the config writer's
    # BOM-safe contract, AP-7), which plain utf-8 parsing rejects.
    source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        body = "\n".join(lines[node.lineno - 1 : node.end_lineno or node.lineno])
        if any(service in body for service in BLOCKING_SERVICES):
            yield node.name, body


@pytest.mark.parametrize("module", ROUTE_MODULES)
def test_route_handlers_offload_cli_auth_probes(module: str) -> None:
    path = _REPO_ROOT / module
    if not path.exists():  # pragma: no cover - module renamed
        pytest.skip(f"{module} is gone")

    offenders = [
        name
        for name, body in _async_defs_touching_a_blocking_service(path)
        if "to_thread" not in body and "run_in_executor" not in body
    ]

    assert not offenders, (
        f"{module}: {offenders} call a CLI auth probe inline. The probe spawns "
        "a binary and blocks the event loop for hundreds of milliseconds, "
        "which is heard as a hole in the middle of a spoken answer. Wrap it: "
        "`await asyncio.to_thread(service.status)`."
    )

"""Boundaries of the permission layer that only a ratchet can hold.

* P9, "an AI agent never answers a system dialog": no tool and no agent-reachable
  package may trigger or answer a permission request, open a pane, reset a row or
  talk to the permission routes. A later tool or MCP wrapper that forwards to
  ``POST /api/permissions/*`` would otherwise go unnoticed.
* Nothing inbound on ``/ws`` can publish ``PermissionNeeded`` or ``PermissionResolved``:
  only a short list of backend modules constructs them, and the socket accepts only
  two frame types.
* Boot: the service gets the bus before anything can ask, and a fresh Mac with every
  permission undecided boots with no request and no implicit prompt.
* The module-level functions are thin wrappers; each one is pinned so that a dropped
  or swapped argument cannot go unnoticed.

The AST scans are static on purpose: they hold for code paths no test can execute.
"""

from __future__ import annotations

import ast
import asyncio
import re
import threading
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import ErrorOccurred, Event, PermissionNeeded, PermissionResolved
from jarvis.platform import permission_service as service_module
from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.ui.web.server import WebServer
from tests.fakes.fake_tcc import DialogPolicy, FakeTCC, install_port

_ROOT = Path(__file__).resolve().parents[3]
_JARVIS = _ROOT / "jarvis"

# Packages an agent (the router, a worker, an MCP client, a tool) can reach.
_AGENT_REACHABLE = (
    "plugins/tool",
    "brain",
    "mcp",
    "agent_chat",
    "harness",
    "agents",
    "tools",
    "missions",
    "society",
)
# Entry points that trigger or answer a system dialog, open a pane or reset a row.
_FORBIDDEN_ATTRIBUTES = frozenset(
    {"open_settings", "note_reset", "reset_row", "request_native", "open_pane"}
)
_FORBIDDEN_TEXT = ("/api/permissions", "permissions_routes")
# The only backend modules that build the two permission events: the service, the
# event definitions, and the microphone capture (design 3.5: its watchdog and the
# digital-silence guard report a revoked or muted microphone). A new producer is a
# conscious decision: add it here, in the same change.
_EVENT_CONSTRUCTORS = frozenset(
    {
        "jarvis/platform/permission_service.py",
        "jarvis/core/events.py",
        "jarvis/audio/capture.py",
    }
)
_PERMISSION_TOOL_NAME = re.compile(r"permission|privacy|tcc|consent", re.IGNORECASE)


def _python_files(*relative: str) -> Iterator[Path]:
    for directory in relative:
        base = _JARVIS / directory
        if base.is_dir():
            yield from sorted(base.rglob("*.py"))


def _tool_entry_point_names() -> list[str]:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return sorted(pyproject["project"]["entry-points"]["jarvis.tool"])


# ----------------------------------------------------------------------
# P9: an agent never answers a system dialog
# ----------------------------------------------------------------------


def test_no_registered_tool_is_named_after_a_permission_operation() -> None:
    from jarvis.brain.factory import ROUTER_TOOLS

    names = [*_tool_entry_point_names(), *ROUTER_TOOLS]

    assert names, "the tool entry points could not be read"
    assert [name for name in names if _PERMISSION_TOOL_NAME.search(name)] == []


def test_no_agent_reachable_package_triggers_or_answers_a_permission_request() -> None:
    offenders: list[str] = []
    for path in _python_files(*_AGENT_REACHABLE):
        text = path.read_text(encoding="utf-8")
        if not (
            any(token in text for token in _FORBIDDEN_TEXT)
            or any(name in text for name in _FORBIDDEN_ATTRIBUTES)
        ):
            continue
        relative = path.relative_to(_ROOT).as_posix()
        for token in _FORBIDDEN_TEXT:
            if token in text:
                offenders.append(f"{relative}: mentions {token}")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRIBUTES:
                offenders.append(f"{relative}:{node.lineno}: .{node.attr}")
            if isinstance(node, ast.ImportFrom) and any(
                alias.name in _FORBIDDEN_ATTRIBUTES for alias in node.names
            ):
                offenders.append(f"{relative}:{node.lineno}: imports a permission operation")

    assert offenders == [], "\n".join(offenders)


# ----------------------------------------------------------------------
# Nothing inbound can publish a permission event
# ----------------------------------------------------------------------


def test_only_the_known_backend_modules_construct_the_permission_events() -> None:
    offenders: list[str] = []
    for path in _JARVIS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "PermissionNeeded(" not in text and "PermissionResolved(" not in text:
            continue
        relative = path.relative_to(_ROOT).as_posix()
        if relative in _EVENT_CONSTRUCTORS:
            continue
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in ("PermissionNeeded", "PermissionResolved"):
                offenders.append(f"{relative}:{node.lineno}")

    assert offenders == [], "\n".join(offenders)


async def test_inbound_ws_frames_cannot_publish_a_permission_event() -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus.subscribe_all(collect)
    server = WebServer(JarvisConfig(), bus=bus)
    lock = asyncio.Lock()
    forged: list[dict[str, Any]] = [
        {"type": "event", "event": "PermissionResolved", "permissions": ["microphone"]},
        {"type": "PermissionNeeded", "permissions": ["microphone"], "feature": "voice"},
        {"type": "permission_resolved", "granted": True},
        {"type": "command", "action": "permission.resolved", "payload": {"granted": True}},
        {"type": "command", "action": "bus.publish", "payload": {"event": "PermissionResolved"}},
        {"type": "command", "action": "permissions.grant", "payload": {"permission": "microphone"}},
    ]

    for frame in forged:
        await server._route_incoming("session-1", frame, lock)
    await asyncio.sleep(0.05)

    assert not [e for e in seen if isinstance(e, PermissionNeeded | PermissionResolved)]
    assert seen, "the forged frames should at least have been rejected with an error event"
    assert all(isinstance(e, ErrorOccurred) for e in seen), [type(e).__name__ for e in seen]


# ----------------------------------------------------------------------
# Boot
# ----------------------------------------------------------------------


def _calls_in(node: ast.AST) -> list[ast.Call]:
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return getattr(func, "attr", "")


def test_the_web_server_attaches_the_bus_before_anything_else_in_start() -> None:
    tree = ast.parse((_JARVIS / "ui/web/server.py").read_text(encoding="utf-8"))
    start = next(
        node
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == "WebServer"
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start"
    )

    first_call = min(_calls_in(start), key=lambda call: (call.lineno, call.col_offset))
    first_await = min(
        (n.lineno for n in ast.walk(start) if isinstance(n, ast.Await)), default=10**9
    )
    attach = next(c for c in _calls_in(start) if _call_name(c) == "_attach_permission_bus")

    assert _call_name(first_call) in ("_attach_permission_bus", "get_running_loop")
    assert attach.lineno < first_await  # before the init chain can ask for anything
    args = [ast.unparse(arg) for arg in attach.args]
    assert args == ["self.bus", "asyncio.get_running_loop()"]


def test_the_desktop_app_attaches_the_bus_before_the_speech_task_can_ask() -> None:
    tree = ast.parse((_JARVIS / "ui/desktop_app.py").read_text(encoding="utf-8"))
    attach = [c for c in _calls_in(tree) if _call_name(c) == "_attach_permission_bus"]
    speech = [c for c in _calls_in(tree) if _call_name(c) == "_start_speech_and_orb"]

    assert len(attach) == 1 and speech
    assert attach[0].lineno < min(call.lineno for call in speech)
    assert [ast.unparse(arg) for arg in attach[0].args] == ["server.bus", "loop"]


@pytest.mark.parametrize("wake_word_on", [False, True], ids=["wake-word-off", "wake-word-on"])
async def test_a_fresh_mac_boots_without_a_request_or_an_implicit_prompt(
    monkeypatch: pytest.MonkeyPatch, wake_word_on: bool
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.ui.web.control_auth import require_control_key_or_session
    from jarvis.ui.web.permissions_routes import router

    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)  # every permission undecided
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    bus = EventBus()
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus.subscribe_all(collect)

    service_module.attach_bus(bus, asyncio.get_running_loop())  # what both boot paths do
    if wake_word_on:
        # The wake loop only READS: parked until the microphone is granted.
        assert service_module.check(PermissionId.MICROPHONE) is PermissionState.NOT_DETERMINED
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_control_key_or_session] = lambda: None
    client = TestClient(app)
    status = await asyncio.to_thread(client.get, "/api/permissions/status")
    await asyncio.sleep(0.05)

    assert status.status_code == 200 and status.json()["needed"] == []
    tcc.assert_no_prompts()
    assert tcc.requests() == [] and tcc.implicit_prompts() == []
    assert service_module.outstanding() == []
    assert not [e for e in seen if isinstance(e, PermissionNeeded | PermissionResolved)]


# ----------------------------------------------------------------------
# The module-level wrappers pass everything through
# ----------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTCC]:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    yield tcc


async def test_the_module_level_functions_reach_the_singleton_with_every_argument(
    world: FakeTCC,
) -> None:
    bus = EventBus()
    seen: list[Event] = []

    async def collect(event: Event) -> None:
        seen.append(event)

    bus.subscribe_all(collect)
    service_module.attach_bus(bus, asyncio.get_running_loop())
    fired = threading.Event()
    unsubscribe = service_module.add_listener(PermissionId.EVENT_POSTING, fired.set)

    # ensure: feature and interactive reach the service (a background call asks nothing).
    background = service_module.ensure(
        PermissionId.ACCESSIBILITY, feature="window_control", interactive=False
    )
    assert not background.asked and world.requests() == []
    # ensure_all: coalesced, request order kept.
    pair = service_module.ensure_all(
        [PermissionId.SCREEN_RECORDING, PermissionId.ACCESSIBILITY],
        feature="computer_use",
        interactive=False,
    )
    assert [r.permission for r in pair] == [
        PermissionId.SCREEN_RECORDING,
        PermissionId.ACCESSIBILITY,
    ]
    # ensure_async: the interactive call asks once.
    asked = await service_module.ensure_async(PermissionId.MICROPHONE, feature="voice")
    assert asked.asked and len(world.requests("microphone")) == 1
    # check: the live state, with the target honoured.
    assert service_module.check(PermissionId.MICROPHONE) is PermissionState.NOT_DETERMINED
    assert service_module.check(PermissionId.AUTOMATION, target="com.apple.Music") in (
        PermissionState.UNAVAILABLE,
        PermissionState.NOT_REQUIRED,
        PermissionState.NOT_DETERMINED,
    )
    # outstanding: what the calls above opened, oldest first.
    features = [episode.feature for episode in service_module.outstanding()]
    assert features[0] == "window_control" and "voice" in features
    # open_settings: the pane URL of the permission asked for.
    assert service_module.open_settings(PermissionId.ACCESSIBILITY) is True
    assert world.workspace_opened_urls[-1].endswith("Privacy_Accessibility")
    # add_listener: fires when the permission turns granted, and unsubscribes.
    world.grant("accessibility")
    service_module.get_permission_service().refresh_episodes()
    assert await asyncio.to_thread(fired.wait, 5)
    unsubscribe()
    await asyncio.sleep(0.05)
    assert any(isinstance(event, PermissionNeeded) for event in seen)  # attach_bus really attached

    service_module.attach_bus(None, None)  # detaching never raises
    service_module.ensure(PermissionId.INPUT_MONITORING, feature="global_shortcuts")


def test_the_singleton_is_lazy_and_constructing_it_calls_no_framework(world: FakeTCC) -> None:
    assert service_module._SERVICE is None
    service = service_module.get_permission_service()
    assert service is service_module.get_permission_service()
    assert world.calls == () and world.loaded_modules == []

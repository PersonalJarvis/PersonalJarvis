"""``take_appshot`` takes exactly what was asked for: the front window by
default, the whole screen only on an explicit ``scope="screen"``.

A voice call once answered "take an appshot of my full screen" with a window
capture, because the tool had no way to ask for more than the front window.
"""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace

import pytest

from jarvis.appshot import service as appshot_service
from jarvis.appshot.store import get_store
from jarvis.plugins.tool.appshot import AppshotTool
from jarvis.screen_context.models import (
    CaptureTarget,
    ScreenContext,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)
from jarvis.screen_context.service import CaptureOutcome


def _context(intent: VisualIntent) -> ScreenContext:
    if intent is VisualIntent.WINDOW:
        target = CaptureTarget(
            kind=TargetKind.WINDOW,
            bbox=(0, 0, 1280, 720),
            reason=TargetReason.FOCUSED_WINDOW,
            window=WindowFacts(app_name="Editor", title="notes.md"),
        )
        size = (1280, 720)
    else:
        target = CaptureTarget(
            kind=TargetKind.MONITOR,
            bbox=(0, 0, 2560, 1440),
            reason=TargetReason.CURSOR_MONITOR,
            monitor_name="1",
        )
        size = (2560, 1440)
    return ScreenContext(
        image=b"jpeg-bytes",
        mime="image/jpeg",
        size=size,
        target=target,
        ui_text="",
        captured_at_ns=1,
    )


class FakeService:
    """Captures what the verdict scoped, like the real targeting does."""

    def __init__(self) -> None:
        self.verdicts: list = []
        self.handles: dict = {}
        self.refuse = False

    async def capture(self, *, verdict=None, trace_id=None, **_kwargs):
        self.verdicts.append(verdict)
        if self.refuse:
            return CaptureOutcome(
                status="refused",
                verdict=verdict,
                reason_kind="policy",
                message="Blocked by the privacy filter.",
            )
        self.handles["h1"] = _context(verdict.intent)
        return CaptureOutcome(
            status="captured", verdict=verdict, context=self.handles["h1"], handle_id="h1"
        )

    def consume(self, handle_id):
        return self.handles.pop(handle_id, None)


class Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)


def _config(*, enabled: bool = True):
    return SimpleNamespace(
        screen_context=SimpleNamespace(enabled=enabled, deck_preview_s=120.0, ttl_s=60.0),
        appshot=SimpleNamespace(target="auto"),
    )


@pytest.fixture
def wired(monkeypatch):
    import jarvis.plugins.tool.appshot as appshot_tool
    import jarvis.screen_context.turn as turn

    service = FakeService()
    bus = Bus()
    state = {"config": _config()}
    monkeypatch.setattr(turn, "get_service", lambda bus=None: service)
    monkeypatch.setattr(appshot_tool, "_app_bus", lambda: bus)
    monkeypatch.setattr(appshot_tool, "_load_config", lambda: state["config"])
    monkeypatch.setattr(appshot_service, "_load_config", lambda: state["config"])
    get_store().clear()
    yield service, bus, state
    get_store().clear()


def _ctx():
    return SimpleNamespace(trace_id=uuid.uuid4())


async def test_default_is_the_front_window_only(wired) -> None:
    service, bus, _state = wired

    result = await AppshotTool().execute({}, _ctx())

    assert result.success
    assert [v.intent for v in service.verdicts] == [VisualIntent.WINDOW]
    assert result.output["description"] == "Appshot of the active window, 1280x720."
    assert bus.events[-1].delivered_to == "turn"


async def test_explicit_window_scope_is_the_front_window(wired) -> None:
    service, _bus, _state = wired

    result = await AppshotTool().execute({"scope": "window"}, _ctx())

    assert result.success
    assert [v.intent for v in service.verdicts] == [VisualIntent.WINDOW]


async def test_screen_scope_takes_the_whole_monitor(wired) -> None:
    service, bus, _state = wired

    result = await AppshotTool().execute({"scope": "screen"}, _ctx())

    assert result.success
    assert [v.intent for v in service.verdicts] == [VisualIntent.SCREEN]
    assert result.output["description"] == "Appshot of the monitor 1, 2560x1440."
    assert base64.b64decode(result.output["_image"]["data"]) == b"jpeg-bytes"
    assert "whole screen" in result.output["evidence"]
    assert "front window" not in result.output["evidence"], "it was not the front window"
    assert service.handles == {}, "the capture handle is consumed at once"
    receipt = bus.events[-1]
    assert receipt.trigger == "tool" and receipt.delivered_to == "turn"
    assert get_store().latest().label == "monitor 1"


async def test_screen_scope_respects_the_off_switch(wired) -> None:
    service, bus, state = wired
    state["config"] = _config(enabled=False)

    result = await AppshotTool().execute({"scope": "screen"}, _ctx())

    assert not result.success
    assert "switched off" in result.error
    assert service.verdicts == [], "nothing is captured while appshots are off"
    assert bus.events == []


async def test_a_refused_screen_appshot_records_nothing(wired) -> None:
    service, bus, _state = wired
    service.refuse = True

    result = await AppshotTool().execute({"scope": "screen"}, _ctx())

    assert not result.success
    assert result.error == "Blocked by the privacy filter."
    assert bus.events == []
    assert get_store().latest() is None


async def test_an_unknown_scope_captures_nothing(wired) -> None:
    service, _bus, _state = wired

    result = await AppshotTool().execute({"scope": "everything"}, _ctx())

    assert not result.success
    assert service.verdicts == []


def test_schema_offers_window_and_screen_and_nothing_wider() -> None:
    schema = AppshotTool.schema
    assert schema["additionalProperties"] is False
    assert schema["properties"]["scope"]["enum"] == ["window", "screen"]
    assert "scope" not in schema.get("required", []), "the window stays the default"

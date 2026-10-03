"""The live model's ``screen_snapshot`` is an appshot to the user.

It must take its picture through the shared Screen Context service — the one
that carries the shutter flash and publishes the receipt that plays the
click-clack — and record the look in the Appshots history.
"""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace

import pytest

from jarvis.appshot import service as appshot_service
from jarvis.appshot.store import get_store
from jarvis.plugins.tool.live_screen import LiveScreenTool
from jarvis.screen_context.models import (
    CaptureTarget,
    IntentVerdict,
    ScreenContext,
    TargetKind,
    TargetReason,
    VisualIntent,
)
from jarvis.screen_context.service import CaptureOutcome


def make_context() -> ScreenContext:
    return ScreenContext(
        image=b"jpeg-bytes",
        mime="image/jpeg",
        size=(1280, 720),
        target=CaptureTarget(
            kind=TargetKind.MONITOR,
            bbox=(0, 0, 1280, 720),
            reason=TargetReason.CURSOR_MONITOR,
            monitor_name="Main",
        ),
        ui_text="OpenAI console",
        captured_at_ns=1,
    )


class SharedService:
    def __init__(self, outcome: CaptureOutcome) -> None:
        self.outcome = outcome
        self.handles = {"h1": outcome.context} if outcome.context else {}
        self.closed = False

    async def capture(self, *, verdict=None, trace_id=None):
        return self.outcome

    def consume(self, handle_id):
        return self.handles.pop(handle_id, None)

    def close(self) -> int:
        self.closed = True
        return 0


class Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)


class Config:
    class screen_context:  # noqa: N801 - mirrors the config attribute
        deck_preview_s = 120.0


@pytest.fixture
def wired(monkeypatch):
    import jarvis.plugins.tool.appshot as appshot_tool
    import jarvis.screen_context.turn as turn

    bus = Bus()
    asked: list = []
    holder: dict = {}

    def get_service(bus=None):
        asked.append(bus)
        return holder["service"]

    monkeypatch.setattr(turn, "get_service", get_service)
    monkeypatch.setattr(appshot_tool, "_app_bus", lambda: bus)
    monkeypatch.setattr(appshot_service, "_load_config", lambda: Config())
    get_store().clear()
    yield holder, bus, asked
    get_store().clear()


def _ctx():
    return SimpleNamespace(trace_id=uuid.uuid4())


async def test_snapshot_uses_the_shared_service_and_records_an_appshot(wired) -> None:
    holder, bus, asked = wired
    service = SharedService(
        CaptureOutcome(
            status="captured",
            verdict=IntentVerdict(intent=VisualIntent.SCREEN),
            context=make_context(),
            handle_id="h1",
        )
    )
    holder["service"] = service

    result = await LiveScreenTool().execute({}, _ctx())

    assert result.success
    assert base64.b64decode(result.output["_image"]["data"]) == b"jpeg-bytes"
    assert asked == [bus], "the shared service gets the app bus, so the sound plays"
    assert not service.closed, "the shared service outlives one tool call"
    assert service.handles == {}, "the capture handle is consumed at once"
    receipt = bus.events[-1]
    assert receipt.trigger == "tool" and receipt.delivered_to == "turn"
    assert get_store().latest().trigger == "tool"


async def test_a_refused_snapshot_records_nothing(wired) -> None:
    holder, bus, _asked = wired
    holder["service"] = SharedService(
        CaptureOutcome(
            status="refused",
            verdict=IntentVerdict(intent=VisualIntent.SCREEN),
            reason_kind="policy",
            message="Screen context is switched off.",
        )
    )

    result = await LiveScreenTool().execute({}, _ctx())

    assert not result.success
    assert result.error == "Screen context is switched off."
    assert bus.events == []
    assert get_store().latest() is None

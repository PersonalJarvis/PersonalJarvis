"""On macOS the bar lives in its own process — a drop there reached nobody.

The hosted surfaces deliver a drop by calling ``drop_bridge.dispatch_drop``,
but the real handler is registered by the desktop app in the PARENT process.
In the host child that bridge was empty, so a file dropped on the macOS bar was
accepted by the window (the cursor even said "copy") and then silently
discarded — it never became conversation context. Windows and Linux were
unaffected because their bar runs in-process.

These pin the round trip that closes it: child forwards the drop, parent
replays it onto its own bridge, verdict comes back down and reaches the bar.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from jarvis.overlay import drop_bridge
from jarvis.ui.jarvisbar import host
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessBarOverlay


@pytest.fixture(autouse=True)
def _clean_bridge() -> Any:
    drop_bridge.set_drop_handler(None)
    drop_bridge.set_drop_result_sink(None)
    yield
    drop_bridge.set_drop_handler(None)
    drop_bridge.set_drop_result_sink(None)


# --------------------------------------------------------------------- #
# Child side: the drop leaves the host process                          #
# --------------------------------------------------------------------- #
def test_the_host_forwards_a_drop_to_the_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        host, "emit", lambda event, **payload: emitted.append((event, payload))
    )

    host._wire_drop_forwarding()
    # This is exactly what the Qt bar / mascot call when a file lands on them.
    assert drop_bridge.dispatch_drop(["/dropped/shot.png"], "") is True

    assert emitted == [("drop", {"paths": ["/dropped/shot.png"], "text": ""})]


def test_the_forwarded_drop_survives_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """It crosses a pipe as one line — the payload must be serialisable."""
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        host, "emit", lambda event, **payload: emitted.append((event, payload))
    )

    host._wire_drop_forwarding()
    drop_bridge.dispatch_drop([], "https://example.invalid/x")

    event, payload = emitted[0]
    round_tripped = json.loads(json.dumps({"event": event, **payload}))
    assert round_tripped["paths"] == []
    assert round_tripped["text"] == "https://example.invalid/x"


# --------------------------------------------------------------------- #
# Child side: the verdict comes back down                               #
# --------------------------------------------------------------------- #
class _RecordingSurface:
    def __init__(self) -> None:
        self.results: list[bool] = []

    def notify_drop_result(self, accepted: bool) -> None:
        self.results.append(accepted)


def test_a_drop_result_command_reaches_the_hosted_bar() -> None:
    surface = _RecordingSurface()

    assert host.dispatch(surface, {"op": "drop_result", "accepted": True}) is True
    assert host.dispatch(surface, {"op": "drop_result", "accepted": False}) is True

    assert surface.results == [True, False]


def test_a_surface_without_the_confirmation_ignores_the_command() -> None:
    """The mascot host shares this protocol and has no bar renderer."""

    class _Bare:
        pass

    assert host.dispatch(_Bare(), {"op": "drop_result", "accepted": True}) is True


# --------------------------------------------------------------------- #
# Parent side: replay + answer                                          #
# --------------------------------------------------------------------- #
def _surface() -> SubprocessBarOverlay:
    """A proxy with no live child — ``_send`` is what we observe."""
    return SubprocessBarOverlay()


def test_the_parent_replays_a_forwarded_drop_onto_its_own_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled: list[tuple[list[str], str]] = []
    drop_bridge.set_drop_handler(lambda paths, text: handled.append((paths, text)))

    surface = _surface()
    sent: list[dict] = []
    monkeypatch.setattr(surface, "_send", sent.append)

    surface._dispatch_event(
        {"event": "drop", "paths": ["/dropped/a.png"], "text": ""}
    )

    assert handled == [(["/dropped/a.png"], "")]
    # The handler is async in production, so no verdict is sent synchronously —
    # it arrives later via the result sink.
    assert sent == []


def test_the_parent_registers_the_return_leg_when_a_drop_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drop_bridge.set_drop_handler(lambda _paths, _text: None)
    surface = _surface()
    sent: list[dict] = []
    monkeypatch.setattr(surface, "_send", sent.append)

    surface._dispatch_event({"event": "drop", "paths": ["/dropped/a.png"], "text": ""})
    # ...and now the backend finishes the intake.
    drop_bridge.report_drop_result(True)

    assert sent == [{"op": "drop_result", "accepted": True}]


def test_a_drop_with_no_parent_handler_is_answered_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rather than leaving the bar waiting for a verdict that never comes."""
    surface = _surface()
    sent: list[dict] = []
    monkeypatch.setattr(surface, "_send", sent.append)

    surface._dispatch_event({"event": "drop", "paths": ["/dropped/a.png"], "text": ""})

    assert sent == [{"op": "drop_result", "accepted": False}]


def test_a_malformed_drop_event_does_not_kill_the_event_pump() -> None:
    """The pump serves talk/hangup/mute too — one bad line must not end it."""
    surface = _surface()

    surface._dispatch_event({"event": "drop"})  # no paths, no text
    surface._dispatch_event({"event": "drop", "paths": None, "text": None})

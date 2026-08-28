"""OrbBusBridge wires the bar's Prompt Mode sparkle to the bus, both ways.

Down: ``DictationPromptModeChanged`` → ``surface.set_prompt_mode`` (a surface
without the method, the mascot orb, is skipped). Up: the sparkle click →
``DictationPromptModeToggleRequested(source="jarvis_bar")`` through the same
Tk→asyncio marshal the mute toggle uses. Seed: on attach and again on the
voice-ready signal the bridge reads where the user left the switch from the
pipeline's live dictation config, so the sparkle is right from the first frame
the pipeline exists — mute starts false, Prompt Mode starts wherever it was.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.events import DictationPromptModeChanged, DictationPromptModeToggleRequested
from ui.orb.bus_bridge import OrbBusBridge


class _Surface:
    def __init__(self) -> None:
        self.prompt_mode: list[bool] = []
        self.toggle_cb: Any = None

    def set_prompt_mode(self, enabled: bool) -> None:
        self.prompt_mode.append(enabled)

    def set_on_prompt_mode_toggle(self, cb: Any) -> None:
        self.toggle_cb = cb


class _Bus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_changed_event_reaches_the_surface() -> None:
    surface = _Surface()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=surface)
    await bridge._on_prompt_mode_changed(DictationPromptModeChanged(enabled=True, source="x"))
    await bridge._on_prompt_mode_changed(DictationPromptModeChanged(enabled=False, source="x"))
    assert surface.prompt_mode == [True, False]


@pytest.mark.asyncio
async def test_a_surface_without_the_method_is_skipped() -> None:
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=SimpleNamespace())
    await bridge._on_prompt_mode_changed(DictationPromptModeChanged(enabled=True))


def test_the_sparkle_click_asks_the_bus_to_flip() -> None:
    bus = _Bus()
    bridge = OrbBusBridge(bus=bus, orb=_Surface())
    # No backend loop captured: the marshal falls back to a one-shot run.
    bridge._publish_prompt_mode_toggle()
    assert len(bus.published) == 1
    event = bus.published[0]
    assert isinstance(event, DictationPromptModeToggleRequested)
    assert event.source == "jarvis_bar"


def test_seed_reads_the_switch_from_the_live_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = _Surface()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=surface)
    pipeline = SimpleNamespace(_dictation_cfg=SimpleNamespace(prompt_mode=True))
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline)
    bridge._seed_prompt_mode()
    assert surface.prompt_mode == [True]


def test_seed_without_a_pipeline_leaves_the_surface_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _Surface()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=surface)
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: None)
    bridge._seed_prompt_mode()
    assert surface.prompt_mode == []

"""OrbBusBridge wires the bar's Prompt Mode sparkle to the bus, both ways.

Down: ``DictationPromptModeChanged`` → ``surface.set_prompt_mode(enabled,
paused)`` (a surface without the method, the mascot orb, is skipped). Up: the
sparkle click → ``DictationPromptModePauseToggleRequested(source="jarvis_bar")``
through the same Tk→asyncio marshal the mute toggle uses. Seed: on attach and
again on the voice-ready signal the bridge reads BOTH levels — the setting
from the pipeline's live dictation config, the pause from the module that
holds it — so the sparkle is right from the first frame the pipeline exists.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.events import (
    DictationPromptModeChanged,
    DictationPromptModePauseToggleRequested,
)
from jarvis.dictation import prompt_mode
from ui.orb.bus_bridge import OrbBusBridge


@pytest.fixture(autouse=True)
def _no_pause() -> Any:
    """The pause is process-wide module state; never leak it between tests."""
    prompt_mode.set_prompt_mode_paused(False)
    yield
    prompt_mode.set_prompt_mode_paused(False)


class _Surface:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, bool]] = []
        self.toggle_cb: Any = None

    def set_prompt_mode(self, enabled: bool, paused: bool = False) -> None:
        self.calls.append((enabled, paused))

    def set_on_prompt_mode_toggle(self, cb: Any) -> None:
        self.toggle_cb = cb


class _Bus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_both_levels_reach_the_surface() -> None:
    surface = _Surface()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=surface)
    for enabled, paused in ((True, False), (True, True), (False, False)):
        await bridge._on_prompt_mode_changed(
            DictationPromptModeChanged(enabled=enabled, paused=paused, source="x")
        )
    assert surface.calls == [(True, False), (True, True), (False, False)]


@pytest.mark.asyncio
async def test_a_surface_without_the_method_is_skipped() -> None:
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=SimpleNamespace())
    await bridge._on_prompt_mode_changed(DictationPromptModeChanged(enabled=True))


def test_the_sparkle_click_asks_for_a_pause_not_a_settings_change() -> None:
    bus = _Bus()
    bridge = OrbBusBridge(bus=bus, orb=_Surface())
    # No backend loop captured: the marshal falls back to a one-shot run.
    bridge._publish_prompt_mode_toggle()
    assert len(bus.published) == 1
    event = bus.published[0]
    assert isinstance(event, DictationPromptModePauseToggleRequested)
    assert event.source == "jarvis_bar"


def test_seed_reads_the_setting_and_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    surface = _Surface()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=surface)
    pipeline = SimpleNamespace(_dictation_cfg=SimpleNamespace(prompt_mode=True))
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: pipeline)

    bridge._seed_prompt_mode()
    assert surface.calls == [(True, False)]

    prompt_mode.set_prompt_mode_paused(True)
    bridge._seed_prompt_mode()
    assert surface.calls[-1] == (True, True)


def test_seed_without_a_pipeline_leaves_the_surface_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _Surface()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=surface)
    monkeypatch.setattr("jarvis.core.runtime_refs.get_speech_pipeline", lambda: None)
    bridge._seed_prompt_mode()
    assert surface.calls == []

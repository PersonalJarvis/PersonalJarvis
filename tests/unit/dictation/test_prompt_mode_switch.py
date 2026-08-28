"""One writer for the Prompt Mode switch, whichever surface flips it.

Three surfaces can flip ``[dictation].prompt_mode`` (the settings card, the
front-page pill, the native bar's sparkle). If each wrote it, disk, the live
config and the surfaces would drift the moment two disagreed. So every flip
lands in ``prompt_mode_switch``: disk first, live config second, then ONE
``DictationPromptModeChanged`` that every mirror redraws from — and a save
that fails changes nothing live, because a switch that looks on until the
next restart is worse than one that refused.

No ``unittest.mock``: the writer and the bus are plain fakes.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.core import config_writer
from jarvis.core.events import DictationPromptModeChanged
from jarvis.dictation import prompt_mode
from jarvis.dictation.prompt_mode_switch import (
    announce_prompt_mode,
    apply_prompt_mode,
    toggle_prompt_mode_pause,
)


class _Bus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


class _Cfg:
    prompt_mode = False


@pytest.fixture(autouse=True)
def _no_pause() -> Any:
    """Process-wide module state; never leak it between tests."""
    prompt_mode.set_prompt_mode_paused(False)
    yield
    prompt_mode.set_prompt_mode_paused(False)


@pytest.mark.asyncio
async def test_a_flip_lands_on_disk_live_and_on_the_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        config_writer, "set_dictation_setting", lambda key, value: written.append((key, value))
    )
    bus, cfg = _Bus(), _Cfg()

    assert await apply_prompt_mode(True, dictation_cfg=cfg, bus=bus, source="jarvis_bar")

    assert written == [("prompt_mode", True)]
    assert cfg.prompt_mode is True
    assert len(bus.published) == 1
    event = bus.published[0]
    assert isinstance(event, DictationPromptModeChanged)
    assert event.enabled is True
    assert event.source == "jarvis_bar"


@pytest.mark.asyncio
async def test_a_failed_save_changes_nothing_live(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(key: str, value: Any) -> None:
        raise OSError("disk says no")

    monkeypatch.setattr(config_writer, "set_dictation_setting", _boom)
    bus, cfg = _Bus(), _Cfg()

    assert not await apply_prompt_mode(True, dictation_cfg=cfg, bus=bus, source="settings")

    assert cfg.prompt_mode is False
    assert bus.published == []


@pytest.mark.asyncio
async def test_live_only_skips_the_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    def _never(key: str, value: Any) -> None:
        raise AssertionError("persist=False must not write")

    monkeypatch.setattr(config_writer, "set_dictation_setting", _never)
    bus, cfg = _Bus(), _Cfg()
    assert await apply_prompt_mode(True, dictation_cfg=cfg, bus=bus, source="t", persist=False)
    assert cfg.prompt_mode is True
    assert bus.published[0].enabled is True


@pytest.mark.asyncio
async def test_announce_without_a_bus_is_a_quiet_no_op() -> None:
    await announce_prompt_mode(True, bus=None, source="settings")


@pytest.mark.asyncio
async def test_a_broken_bus_never_breaks_the_flip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_writer, "set_dictation_setting", lambda key, value: None)

    class _DeadBus:
        async def publish(self, event: Any) -> None:
            raise RuntimeError("bus is gone")

    cfg = _Cfg()
    assert await apply_prompt_mode(False, dictation_cfg=cfg, bus=_DeadBus(), source="t")
    assert cfg.prompt_mode is False


# --------------------------------------------------------------------------- #
# The pause: the bar's hold, which is NOT a settings change
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_pause_never_touches_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The maintainer's report in one test: the bar must hold the feature, not
    switch it off, or there is no way back to it from the bar."""

    def _never(key: str, value: Any) -> None:
        raise AssertionError("a pause must not write jarvis.toml")

    monkeypatch.setattr(config_writer, "set_dictation_setting", _never)
    bus, cfg = _Bus(), _Cfg()
    cfg.prompt_mode = True

    assert await toggle_prompt_mode_pause(cfg=cfg, bus=bus, source="jarvis_bar") is True
    assert cfg.prompt_mode is True, "the setting survives the pause"
    assert prompt_mode.prompt_mode_paused() is True
    assert (bus.published[0].enabled, bus.published[0].paused) == (True, True)

    assert await toggle_prompt_mode_pause(cfg=cfg, bus=bus, source="jarvis_bar") is False
    assert prompt_mode.prompt_mode_paused() is False
    assert (bus.published[1].enabled, bus.published[1].paused) == (True, False)


@pytest.mark.asyncio
async def test_there_is_nothing_to_pause_when_the_setting_is_off() -> None:
    """No hold on a feature that is not running — and the bar draws no control
    there either, so this is belt and braces."""
    bus, cfg = _Bus(), _Cfg()  # prompt_mode stays False
    assert await toggle_prompt_mode_pause(cfg=cfg, bus=bus, source="jarvis_bar") is False
    assert prompt_mode.prompt_mode_paused() is False
    assert bus.published == []


@pytest.mark.asyncio
async def test_writing_the_setting_clears_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """A switch the user just turned on must not come back held down by a
    click they made an hour ago."""
    monkeypatch.setattr(config_writer, "set_dictation_setting", lambda key, value: None)
    prompt_mode.set_prompt_mode_paused(True)
    bus, cfg = _Bus(), _Cfg()

    assert await apply_prompt_mode(True, dictation_cfg=cfg, bus=bus, source="settings")
    assert prompt_mode.prompt_mode_paused() is False
    assert bus.published[0].paused is False

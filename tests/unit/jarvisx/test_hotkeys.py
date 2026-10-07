"""Jarvis X shortcuts: planning, arming, re-arming, dispatch."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jarvis.jarvisx.hotkeys import JarvisXShortcuts, check_hotkeys, normalize_hotkey


def _config(**overrides) -> SimpleNamespace:
    block = {
        "enabled": True,
        "hotkey_region": "ctrl+shift+2",
        "hotkey_window": "ctrl+shift+3",
        "hotkey_fullscreen": "ctrl+shift+1",
        "hotkey_record_region": "ctrl+shift+5",
        "hotkey_record_fullscreen": "ctrl+shift+6",
        "hotkey_stop_recording": "ctrl+shift+4",
    }
    block.update(overrides)
    return SimpleNamespace(jarvisx=SimpleNamespace(**block))


class FakeTrigger:
    """Stands in for HotkeyTrigger: records bindings, replays queued presses."""

    instances: list[FakeTrigger] = []

    def __init__(self, bindings: dict[str, list[str]]) -> None:
        self.bindings = bindings
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.entered = False
        self.exited = False
        FakeTrigger.instances.append(self)

    async def __aenter__(self) -> FakeTrigger:
        self.entered = True
        return self

    async def __aexit__(self, *exc) -> None:
        self.exited = True

    async def events(self):
        while True:
            yield await self.queue.get()


def _shortcuts(config, presses, *, backend=True, owns=True) -> JarvisXShortcuts:
    FakeTrigger.instances.clear()
    return JarvisXShortcuts(
        presses.append,
        config_loader=lambda: config,
        trigger_factory=FakeTrigger,
        has_hotkey=lambda: backend,
        owns_shortcuts=lambda: owns,
    )


def test_normalize_hotkey() -> None:
    assert normalize_hotkey(" Ctrl + Shift+2 ") == "ctrl+shift+2"
    assert normalize_hotkey(None) == ""


def test_defaults_are_valid_and_distinct() -> None:
    hotkeys = {
        name: normalize_hotkey(value)
        for name, value in {
            "region": "ctrl+shift+2",
            "window": "ctrl+shift+3",
            "fullscreen": "ctrl+shift+1",
            "record_region": "ctrl+shift+5",
            "record_fullscreen": "ctrl+shift+6",
            "stop_recording": "ctrl+shift+4",
        }.items()
    }
    assert all(problem == "" for problem in check_hotkeys(hotkeys).values())


def test_duplicate_and_both_alt_shortcuts_are_flagged() -> None:
    problems = check_hotkeys(
        {"region": "ctrl+shift+2", "window": "shift+ctrl+2", "fullscreen": "alt+alt"}
    )
    assert problems["region"] == ""
    assert "same keys" in problems["window"]
    assert "appshot" in problems["fullscreen"]


async def test_all_valid_shortcuts_arm_in_one_trigger_and_dispatch() -> None:
    presses: list[str] = []
    shortcuts = _shortcuts(_config(hotkey_window=""), presses)
    statuses = await shortcuts.start()
    await asyncio.sleep(0)

    assert statuses["region"].armed and statuses["region"].hotkey == "ctrl+shift+2"
    assert not statuses["window"].armed and statuses["window"].detail == "No shortcut set."
    trigger = FakeTrigger.instances[-1]
    assert trigger.entered
    assert set(trigger.bindings) == {
        "jarvisx_region",
        "jarvisx_fullscreen",
        "jarvisx_record_region",
        "jarvisx_record_fullscreen",
        "jarvisx_stop_recording",
    }
    trigger.queue.put_nowait("jarvisx_record_region")
    trigger.queue.put_nowait("someone_else")
    for _ in range(5):
        await asyncio.sleep(0)
    assert presses == ["record_region"]
    await shortcuts.stop()
    assert trigger.exited


async def test_reload_rearms_with_the_new_keys() -> None:
    presses: list[str] = []
    config = _config()
    shortcuts = _shortcuts(config, presses)
    await shortcuts.start()
    await asyncio.sleep(0)
    first = FakeTrigger.instances[-1]

    config.jarvisx.hotkey_region = "ctrl+alt+r"
    await shortcuts.reload()
    await asyncio.sleep(0)
    second = FakeTrigger.instances[-1]
    assert first is not second and first.exited
    assert second.bindings["jarvisx_region"] == ["ctrl+alt+r"]
    await shortcuts.stop()


async def test_switched_off_or_unsupported_hosts_arm_nothing() -> None:
    off = _shortcuts(_config(enabled=False), [])
    statuses = await off.start()
    assert not any(s.armed for s in statuses.values())
    assert statuses["region"].detail == "Jarvis X is switched off."
    assert FakeTrigger.instances == []

    headless = _shortcuts(_config(), [], backend=False)
    statuses = await headless.start()
    assert "not available" in statuses["region"].detail

    second_instance = _shortcuts(_config(), [], owns=False)
    statuses = await second_instance.start()
    assert "main app" in statuses["region"].detail
    assert FakeTrigger.instances == []

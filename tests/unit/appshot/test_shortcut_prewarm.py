"""Only the ambient owner prepares Appshot surfaces, outside shortcut arming."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.appshot.hotkey import AppshotShortcut


@pytest.mark.parametrize(("enabled", "owns"), [(True, True), (False, True), (True, False)])
async def test_prewarm_is_deferred_and_scoped_to_enabled_primary_instance(
    monkeypatch, enabled, owns
):
    config = SimpleNamespace(
        screen_context=SimpleNamespace(enabled=enabled),
        appshot=SimpleNamespace(hotkey="", region_hotkey="", recording_hotkey="", effect=True),
    )
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: config)
    monkeypatch.setattr(
        "jarvis.core.instance.current_instance", lambda: SimpleNamespace(owns_ambient_duties=owns)
    )
    monkeypatch.setattr("jarvis.cu.indicator.controller.get_indicator_controller", lambda: None)
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def warm(_effect):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    owner = AppshotShortcut(object())
    monkeypatch.setattr(owner, "_warm_surfaces", warm)
    await asyncio.wait_for(owner.reload(), 1)
    if enabled and owns:
        await asyncio.wait_for(entered.wait(), 1)
        assert owner._warm_task is not None and not owner._warm_task.done()
    else:
        assert not entered.is_set() and owner._warm_task is None
    await owner.stop()
    assert stopped.is_set() == (enabled and owns)

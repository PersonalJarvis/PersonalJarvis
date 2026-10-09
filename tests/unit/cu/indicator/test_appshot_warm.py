"""Idle shutter preparation preserves other users of the shared sidecar."""

from types import SimpleNamespace

import pytest

from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.controller import CUIndicatorController


@pytest.fixture
def warm_controller(monkeypatch):
    ctl = CUIndicatorController(object())
    calls = []
    monkeypatch.setattr(ctl, "_border_capability", lambda: (True, ""))

    def spawn():
        calls.append("spawn")
        ctl._proc = SimpleNamespace(poll=lambda: None)

    async def quit_sidecar():
        calls.append("quit")
        ctl._proc = None

    monkeypatch.setattr(ctl, "_spawn_sidecar", spawn)
    monkeypatch.setattr(ctl, "_send_and_wait", lambda cmd, *_a, **_k: calls.append(cmd) or True)
    monkeypatch.setattr(ctl, "_quit_sidecar", quit_sidecar)
    return ctl, calls


async def test_warm_runtime_survives_card_close_and_is_released_on_shutdown(warm_controller):
    ctl, calls = warm_controller
    await ctl.warm_for_appshots(True)
    assert calls == ["spawn", protocol.CMD_HIDE]
    await ctl._idle_quit()
    assert "quit" not in calls
    await ctl.close()
    assert calls[-1] == "quit"


async def test_prewarm_never_hides_an_existing_mission_border(warm_controller):
    ctl, calls = warm_controller
    ctl._proc = SimpleNamespace(poll=lambda: None)
    ctl._active = 1
    await ctl.warm_for_appshots(True)
    assert calls == []
    await ctl.warm_for_appshots(False)
    await ctl._idle_quit_task
    assert calls == []
    await ctl.close()


async def test_disabling_appshots_releases_idle_runtime(warm_controller):
    ctl, calls = warm_controller
    await ctl.warm_for_appshots(True)
    await ctl.warm_for_appshots(False)
    await ctl._idle_quit_task
    assert calls[-1] == "quit"

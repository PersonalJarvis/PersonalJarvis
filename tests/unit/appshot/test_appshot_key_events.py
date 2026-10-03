"""Brief taps, modifier transitions and listener replacement must not lose shortcuts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jarvis.appshot.hotkey import AppshotShortcut
from jarvis.appshot.key_events import AppshotKeyEvents


def test_a_press_and_release_without_a_poll_interval_fires_once():
    events = []
    listener = AppshotKeyEvents()
    listener.register([["z", None, lambda: events.append("recording")]])
    key = SimpleNamespace(vk=0x5A, char="z")
    listener._on_press_key(key)
    listener._on_press_key(key)  # OS key repeat must not toggle recording twice.
    listener._on_release_key(key)
    assert events == ["recording"]


def test_control_characters_and_release_order_keep_the_same_letter():
    events = []
    listener = AppshotKeyEvents()
    listener.register([["control + z", None, lambda: events.append("recording")]])
    ctrl = SimpleNamespace(name="ctrl_l", char=None)
    listener._on_press_key(ctrl)
    listener._on_press_key(SimpleNamespace(vk=0x5A, char="\x1a"))
    listener._on_release_key(ctrl)
    listener._on_release_key(SimpleNamespace(vk=0x5A, char="z"))
    assert events == ["recording"]
    assert not listener._held


def test_numpad_keys_keep_the_shared_binding_vocabulary():
    listener = AppshotKeyEvents()
    assert listener._token_for(SimpleNamespace(vk=0x63, char="3")) == "numpad_3"
    assert listener._token_for(SimpleNamespace(vk=0x6B, char="+")) == "add_key"


async def test_cancellation_stops_and_unregisters_the_event_listener(monkeypatch):
    from jarvis.appshot import key_events

    calls = []
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    class Listener:
        ready = True

        def register(self, rows):
            calls.append("register")
            assert rows[0][0] == "control + z"

        def start(self):
            calls.append("start")
            loop.call_soon_threadsafe(started.set)

        def stop(self):
            calls.append("stop")

        def unregister(self):
            calls.append("unregister")

    monkeypatch.setattr(key_events, "AppshotKeyEvents", Listener)
    owner = AppshotShortcut(object())
    task = asyncio.create_task(owner._run_combos({"recording": "ctrl+z"}))
    # Test this Windows-only dispatch without touching a real hook.
    monkeypatch.setattr("jarvis.appshot.hotkey.sys.platform", "win32")
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert calls == ["register", "start", "stop", "unregister"]


async def test_missing_listener_is_reported_as_unarmed(monkeypatch):
    from jarvis.appshot import key_events

    class Listener:
        ready = False

        def register(self, rows):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def unregister(self):
            pass

    monkeypatch.setattr(key_events, "AppshotKeyEvents", Listener)
    owner = AppshotShortcut(object())
    await owner._run_key_events({"recording": "z"})
    assert not owner.status_for("recording").armed
    assert "could not start" in owner.status_for("recording").detail

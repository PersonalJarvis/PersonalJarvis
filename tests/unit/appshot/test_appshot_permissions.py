"""AppShot entry points preserve just-in-time consent and silent lifecycle checks.

They also say which OS permission is missing instead of arming or freezing for
nothing.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.appshot import gesture, hotkey, region
from jarvis.appshot import service as appshot_service
from jarvis.appshot.service import _pick_area
from jarvis.core.events import ConfigReloaded
from jarvis.platform import permission_service, screen_access
from jarvis.screen_context.models import IntentVerdict, VisualIntent
from jarvis.screen_context.service import ScreenContextService
from jarvis.ui.web import appshot_routes
from tests.fakes.fake_permission_service import FakePermissionService


@pytest.fixture
def gate(monkeypatch):
    fake = FakePermissionService()
    monkeypatch.setattr(screen_access, "permission_gate", lambda: fake)
    monkeypatch.setattr(permission_service, "get_permission_service", lambda: fake)
    return fake


@pytest.fixture
def desktop(monkeypatch):
    import jarvis.core.instance as instance
    import jarvis.platform as platform
    import jarvis.platform.probes as probes

    monkeypatch.setattr(platform, "detect_platform", lambda: "darwin")
    monkeypatch.setattr(probes, "display_present", lambda: True)
    monkeypatch.setattr(probes, "is_wayland", lambda: False)
    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    monkeypatch.setattr(
        instance, "current_instance", lambda: SimpleNamespace(owns_ambient_duties=True)
    )
    monkeypatch.setattr(region, "picker_capability", lambda: (True, ""))


@pytest.mark.parametrize("state", ["pending", "denied", "needs_settings", "unavailable"])
async def test_region_refuses_before_starting_the_picker(monkeypatch, gate, desktop, state):
    gate.script("screen_recording", state)
    started = []

    async def run_picker(_timeout):
        started.append(True)
        return None, 0, False

    monkeypatch.setattr(region, "_run_picker", run_picker)
    trace_id = uuid4()
    result = await _pick_area(object(), trace_id=trace_id)

    assert result.reason_code == "capture_permission"
    assert result.message
    assert started == []
    assert region._picking is False
    (call,) = gate.ensure_calls("screen_recording")
    assert (call.feature, call.interactive, call.trace_id) == ("appshot", True, trace_id)


async def test_region_only_starts_after_grant_and_cancel_stays_cancelled(
    monkeypatch, gate, desktop
):
    gate.script("screen_recording", "denied", "granted")
    started = []

    async def run_picker(_timeout):
        started.append(True)
        assert gate.ensure_calls("screen_recording")
        return None, 0, False

    monkeypatch.setattr(region, "_run_picker", run_picker)
    result = await _pick_area(object())
    assert result.reason_code == "capture_permission"
    result = await _pick_area(object())
    assert result.reason_code == "cancelled"
    assert started == [True]


async def test_existing_grant_and_unavailable_picker_never_ask(monkeypatch, gate, desktop):
    async def run_picker(_timeout):
        return None, 0, False

    monkeypatch.setattr(region, "_run_picker", run_picker)
    assert await region.pick_region() is None
    assert gate.ensure_calls("screen_recording") == []
    gate.calls.clear()
    monkeypatch.setattr(region, "picker_capability", lambda: (False, "Wayland"))
    with pytest.raises(region.RegionUnavailable, match="Wayland"):
        await region.pick_region()
    assert gate.calls == []


@pytest.mark.parametrize(
    "state, allowed",
    [
        ("granted", True),
        ("not_required", True),
        ("pending", False),
        ("denied", False),
        ("unavailable", False),
    ],
)
def test_preview_helper_checks_silently(gate, state, allowed):
    gate.script("screen_recording", state)
    assert region.preview_capture_allowed() is allowed
    assert gate.ensure_calls("screen_recording") == []
    assert {call.method for call in gate.calls} == {"check"}


@pytest.mark.parametrize("allowed", [False, True])
def test_picker_never_reads_pixels_without_its_own_grant(monkeypatch, allowed):
    grabs = []
    pixmap = SimpleNamespace(isNull=lambda: False, width=lambda: 100)

    def grab_window(_handle):
        grabs.append(True)
        return pixmap

    monkeypatch.setattr(region, "preview_capture_allowed", lambda: allowed)
    result = region.grab_preview(SimpleNamespace(grabWindow=grab_window))
    assert result is (pixmap if allowed else None)
    assert grabs == ([True] if allowed else [])


def test_failed_preview_uses_live_overlay_without_retrying_or_prompting(gate):
    def broken_capture(_handle):
        raise RuntimeError("capture unavailable")

    assert region.grab_preview(SimpleNamespace(grabWindow=broken_capture)) is None
    assert gate.ensure_calls("screen_recording") == []


@pytest.mark.parametrize(
    "evidence, feature",
    [
        ("appshot", "appshot"),
        ("appshot-region", "appshot"),
        ("appshot-screen", "appshot"),
        ("screen", "screen_context"),
        ("not-appshot", "screen_context"),
    ],
)
def test_every_appshot_scope_has_the_same_permission_feature(evidence, feature):
    verdict = IntentVerdict(intent=VisualIntent.SCREEN, evidence=(evidence,))
    assert ScreenContextService._permission_feature(verdict) == feature


@pytest.mark.parametrize("platform", ["win32", "linux"])
async def test_shortcut_save_never_requests_macos_access_elsewhere(monkeypatch, gate, platform):
    monkeypatch.setattr("jarvis.platform.detect_platform", lambda: platform)
    await hotkey.request_saved_shortcut_access(
        {"window": "alt+alt", "region": ""},
        {"window": "ctrl+alt+a", "region": ""},
        was_enabled=True,
        enabled=True,
    )
    assert gate.calls == []


@pytest.mark.parametrize(
    "old, new, was_enabled, enabled, requests",
    [
        ("alt+alt", "ctrl+alt+a", True, True, 1),
        ("ctrl+alt+a", "ctrl+alt+a", False, True, 1),
        ("ctrl+alt+a", "ctrl+alt+a", True, True, 0),
        ("ctrl+alt+a", "", True, True, 0),
        ("alt+alt", "shift+shift", True, True, 0),
        ("alt+alt", "ctrl+alt+a", True, False, 0),
    ],
)
async def test_only_changed_or_enabled_tap_shortcuts_ask(
    gate,
    desktop,
    old,
    new,
    was_enabled,
    enabled,
    requests,
):
    await hotkey.request_saved_shortcut_access(
        {"window": old, "region": ""},
        {"window": new, "region": ""},
        was_enabled=was_enabled,
        enabled=enabled,
    )
    calls = gate.ensure_calls("input_monitoring")
    assert len(calls) == requests
    if requests:
        assert calls[0].interactive is True
        assert calls[0].wait_s == 0
        assert calls[0].allow_outside_app is False
    assert gate.ensure_calls("screen_recording") == []


def _config(*, enabled=True, window="alt+alt"):
    return SimpleNamespace(
        screen_context=SimpleNamespace(enabled=enabled),
        appshot=SimpleNamespace(hotkey=window, region_hotkey="shift+shift"),
    )


async def test_disable_stops_watchers_and_config_reload_reenables_without_prompt(
    monkeypatch,
    gate,
    desktop,
):
    import jarvis.appshot.gesture as gesture

    config = _config()
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: config)
    running = []

    class Watcher:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            running.append(self)

        def stop(self):
            running.remove(self)

    callbacks = []
    bus = SimpleNamespace(subscribe=lambda event, callback: callbacks.append(callback))
    monkeypatch.setattr(gesture, "BothKeysWatcher", Watcher)
    monkeypatch.setattr(gesture, "make_probe", lambda _family: (lambda: (False, False), ""))
    shortcut = hotkey.AppshotShortcut(bus)
    try:
        await shortcut.start()
        assert len(running) == 2
        config.screen_context.enabled = False
        await callbacks[0](ConfigReloaded(changed_keys=("screen_context.enabled",)))
        assert running == []
        assert not shortcut.status.armed
        config.screen_context.enabled = True
        await callbacks[0](ConfigReloaded(changed_keys=("screen_context.enabled",)))
        assert len(running) == 2
        assert gate.calls == []
    finally:
        await shortcut.stop()
    assert running == []
    assert not shortcut.status.armed


async def test_combo_status_tracks_waiting_grant_and_listener_shutdown(monkeypatch, gate, desktop):
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: _config(window="ctrl+alt+a"))
    # Only the combo is relevant to this test.
    monkeypatch.setattr(hotkey, "configured_hotkeys", lambda _block: {"window": "ctrl+alt+a"})

    class Trigger:
        armed = False
        needs_input_monitoring = True

        def __init__(self, bindings):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            self.armed = False

        async def events(self):
            await asyncio.Event().wait()
            yield "appshot"

    monkeypatch.setattr("jarvis.trigger.hotkey.HotkeyTrigger", Trigger)
    shortcut = hotkey.AppshotShortcut(object())
    try:
        await shortcut.reload()
        assert not shortcut.status.armed
        await asyncio.sleep(0)
        assert "Input Monitoring" in shortcut.status.detail
        shortcut._trigger.armed = True
        shortcut._trigger.needs_input_monitoring = False
        assert shortcut.status.armed
        shortcut._trigger.armed = False
        assert not shortcut.status.armed
        assert gate.calls == []
    finally:
        await shortcut.stop()
    assert not shortcut.status.armed


@pytest.mark.parametrize(
    "display, wayland, supported", [(True, False, True), (False, False, False), (True, True, False)]
)
def test_readiness_is_backend_support_and_does_not_request_consent(
    monkeypatch,
    gate,
    desktop,
    display,
    wayland,
    supported,
):
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: display)
    monkeypatch.setattr("jarvis.platform.probes.is_wayland", lambda: wayland)
    monkeypatch.setattr(
        "jarvis.cu.indicator.controller.screen_indicator_capability", lambda: (True, "")
    )
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    gate.script("screen_recording", "denied")
    payload = appshot_routes._capability()
    assert payload["capture"] is supported
    assert payload["region"] is supported
    assert bool(payload["capture_detail"]) is not supported
    assert gate.calls == []


@pytest.mark.parametrize(
    "enabled, patch, requests",
    [
        (True, {"hotkey": "ctrl+alt+a"}, 1),
        (True, {"hotkey": "ctrl+alt+a", "region_hotkey": "ctrl+shift+a"}, 1),
        (False, {"hotkey": "ctrl+alt+a"}, 0),
        (True, {"enabled": False}, 0),
    ],
)
def test_settings_save_asks_after_writing_and_reloads_on_enable_change(
    monkeypatch,
    gate,
    desktop,
    enabled,
    patch,
    requests,
):
    config = _config(enabled=enabled)
    events = []
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: config)
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_appshot_settings", lambda values: events.append("write")
    )
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_screen_context_settings",
        lambda values: events.append("write"),
    )
    monkeypatch.setattr(appshot_routes, "_settings_payload", lambda: {"saved": True})

    async def reload():
        assert events == ["write"]
        assert len(gate.ensure_calls("input_monitoring")) == requests
        events.append("reload")

    monkeypatch.setattr(hotkey, "get_shortcut", lambda: SimpleNamespace(reload=reload))
    app = FastAPI()
    app.include_router(appshot_routes.router)
    with TestClient(app) as client:
        result = client.put("/api/appshot/settings", json=patch)
    assert result.status_code == 200
    assert events == ["write", "reload"]


def test_disable_is_available_even_with_duplicate_legacy_shortcuts(monkeypatch, gate, desktop):
    config = _config(window="shift+shift")
    writes = []
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: config)
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_screen_context_settings", writes.append,
    )
    monkeypatch.setattr(appshot_routes, "_settings_payload", lambda: {"enabled": False})
    monkeypatch.setattr(hotkey, "get_shortcut", lambda: None)
    app = FastAPI()
    app.include_router(appshot_routes.router)
    with TestClient(app) as client:
        assert client.put("/api/appshot/settings", json={"enabled": False}).status_code == 200
    assert writes == [{"enabled": False}]
    assert gate.calls == []


@pytest.fixture
def mac_keys(monkeypatch, gate):
    monkeypatch.setattr("jarvis.platform.detect_platform", lambda: "darwin")
    monkeypatch.setattr("jarvis.platform.probes.display_present", lambda: True)
    monkeypatch.setattr(gesture, "_macos_probe", lambda family: f"probe:{family}")
    return gate


def test_a_mac_without_input_monitoring_does_not_arm_the_gesture(mac_keys):
    mac_keys.script("input_monitoring", "denied")
    probe, reason = gesture.make_probe("alt")
    assert probe is None
    assert "Input Monitoring" in reason
    # The check is silent: the gesture never asks for the grant itself.
    assert mac_keys.ensure_calls("input_monitoring") == []


def test_a_mac_with_input_monitoring_arms_it(mac_keys):
    mac_keys.grant("input_monitoring")
    assert gesture.make_probe("shift") == ("probe:shift", "")


class _IssueService:
    def __init__(self, code: str, message: str) -> None:
        self.issue = (code, message)

    async def capture_permission_issue(self):
        return self.issue


def _region_config():
    return SimpleNamespace(
        screen_context=SimpleNamespace(enabled=True),
        ui=SimpleNamespace(language="en"),
        appshot=SimpleNamespace(),
    )


async def test_the_area_picker_never_opens_for_a_refusal_asking_cannot_fix(monkeypatch):
    opened: list[bool] = []

    async def pick_area(*_args, **_kwargs):
        opened.append(True)
        raise AssertionError("the screens must not freeze for a capture that will be refused")

    monkeypatch.setattr(appshot_service, "_load_config", _region_config)
    monkeypatch.setattr(appshot_service, "_pick_area", pick_area)
    monkeypatch.setattr(
        "jarvis.screen_context.turn.get_service",
        lambda bus=None: _IssueService("wayland_portal", "Use an X11 session."),
    )

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert result.status == "refused"
    assert result.reason_code == "wayland_portal"
    assert result.message == "Use an X11 session."
    assert opened == []


async def test_a_missing_screen_recording_grant_is_left_to_the_just_in_time_ask(monkeypatch):
    asked: list[bool] = []

    async def pick_area(*_args, **_kwargs):
        asked.append(True)
        return appshot_service.AppshotResult(
            status="refused", reason_code="capture_permission", message="macOS asked."
        )

    monkeypatch.setattr(appshot_service, "_load_config", _region_config)
    monkeypatch.setattr(appshot_service, "_pick_area", pick_area)
    monkeypatch.setattr(
        "jarvis.screen_context.turn.get_service",
        lambda bus=None: _IssueService("capture_permission", "Grant Screen Recording first."),
    )

    result = await appshot_service.take_appshot(trigger="hotkey", scope="region")

    assert asked == [True]
    assert result.message == "macOS asked."

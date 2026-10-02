"""Screen Context and the Screen Recording permission (just-in-time).

A capture a person starts (a spoken request, the bar button, an appshot gesture) is
the moment macOS is asked: ``ScreenContextService.capture`` asks through the
permission service, once per episode, and only a live GRANTED captures. The status
probes (``capture_permission_error``, ``accessibility_permission_error``) are SILENT:
they describe, they never ask. A frame that is wallpaper-only while the state claims
GRANTED is never a success.

The REAL permission service runs on a REAL ``SystemPermissionPort`` that sits on
``FakeTCC`` (a stateful model of macOS privacy); its call log is the proof of what was
(not) asked. Nothing here ran on a real Mac (see the fidelity ledger in
``tests/fakes/fake_tcc.py``).
"""

from __future__ import annotations

import pytest

from jarvis.platform.permission_service import get_permission_service
from jarvis.screen_context import ports
from jarvis.screen_context.models import WindowFacts
from jarvis.screen_context.ports import CapturePermissionIssue, WindowSnapshot
from jarvis.screen_context.service import ScreenContextService, ScreenContextSettings
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_screen_pixels import textured_pixels, wallpaper_pixels
from tests.fakes.fake_tcc import (
    DialogPolicy,
    FakeTCC,
    TccService,
    install_port,
    make_non_darwin_port,
)


@pytest.fixture(autouse=True)
def _no_real_wayland(monkeypatch):
    monkeypatch.setattr(ports, "_is_wayland", lambda: False)


_MONITORS = [
    {"left": 0, "top": 0, "width": 320, "height": 240, "name": "virtual"},
    {"left": 0, "top": 0, "width": 320, "height": 240, "name": "primary"},
]


class _Cursor:
    name = "fake-cursor"

    def position(self):
        return (100, 100)


class _Displays:
    name = "fake-displays"

    def monitors(self):
        return list(_MONITORS)


class _Window:
    name = "fake-window"

    def foreground_snapshot(self):
        facts = WindowFacts(
            app_name="editor", title="notes.md", pid=42, frame_rect=(0, 0, 320, 240)
        )
        return WindowSnapshot(facts, 7)


class _Capturer:
    """Records every grab; ``frame`` produces the pixels, ``before_grab`` runs first."""

    name = "fake-capture"

    def __init__(self, frame=None, before_grab=None) -> None:
        self.grabs: list[tuple] = []
        self._frame = frame or (lambda: textured_pixels((64, 48)))
        self._before = before_grab

    def grab(self, bbox, *, window_handle=None):
        self.grabs.append((tuple(bbox), window_handle))
        if self._before is not None:
            self._before()
        return self._frame()


class _Text:
    name = "fake-text"

    async def read(self, *, window_title_filter=None):
        return None


def _service(capturer=None, **overrides) -> ScreenContextService:
    kwargs = {
        "settings": ScreenContextSettings(),
        "cursor": _Cursor(),
        "bar": _Cursor(),
        "displays": _Displays(),
        "window_probe": _Window(),
        "capturer": capturer or _Capturer(),
        "ui_text_reader": _Text(),
    }
    kwargs.update(overrides)
    return ScreenContextService(**kwargs)


def _darwin(monkeypatch, **kwargs) -> FakeTCC:
    tcc = FakeTCC(**kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    monkeypatch.setattr(ports, "_is_wayland", lambda: False)
    return tcc


# ---------------------------------------------------------------------------
# The capture asks once; the probe never does
# ---------------------------------------------------------------------------


async def test_a_capture_while_not_determined_asks_once_and_refuses_until_answered(
    monkeypatch,
) -> None:
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    capturer = _Capturer()
    service = _service(capturer)

    first = await service.capture_for_turn("look at this", locale="en")
    second = await service.capture_for_turn("look at this", locale="en")

    for outcome in (first, second):
        assert outcome.status == "refused"
        assert outcome.reason_kind == "technical"
        assert outcome.reason_code == "capture_permission"
        assert outcome.message and not outcome.message.startswith("[permission_needed")
    assert capturer.grabs == []
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.implicit_prompts() == []


async def test_a_capture_proceeds_when_the_user_allows(monkeypatch) -> None:
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.ALLOW)
    capturer = _Capturer()

    service = _service(capturer)

    # The frozen preflight may keep the ask itself refused (the grant is only visible
    # to the window-title oracle a moment later); the next capture must go through
    # with no second request.
    outcome = await service.capture_for_turn("look at this", locale="en")
    if outcome.status != "captured":
        outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "captured"
    assert len(capturer.grabs) == 1
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1


async def test_a_denial_is_stable_and_never_asked_again(monkeypatch) -> None:
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.DENY)
    capturer = _Capturer()
    service = _service(capturer)

    await service.capture_for_turn("look at this", locale="en")
    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_code == "capture_permission"
    assert capturer.grabs == []
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.ignored_requests() == []


async def test_a_live_grant_captures_without_asking(monkeypatch) -> None:
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])

    outcome = await _service().capture_for_turn("look at this", locale="en")

    assert outcome.status == "captured"
    tcc.assert_no_prompts()


async def test_the_capture_asks_with_the_feature_that_started_it() -> None:
    gate = FakePermissionService({"screen_recording": "needs_settings"})
    service = _service(
        permission_probe=lambda: "Screen recording permission is missing.",
        permission_gate=gate,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_code == "capture_permission"
    assert "Screen Recording" in (outcome.message or "")
    (call,) = gate.ensure_calls("screen_recording")
    assert (call.method, call.feature, call.interactive, call.wait_s) == (
        "ensure_async",
        "screen_context",
        True,
        0.0,
    )

    # An appshot (take_appshot) says so in its verdict evidence; the shared service
    # carries a shutter hook for every capture, so the hook cannot tell them apart.
    from jarvis.screen_context.models import IntentVerdict, VisualIntent

    await service.capture(verdict=IntentVerdict(intent=VisualIntent.WINDOW, evidence=("appshot",)))
    assert gate.ensure_calls("screen_recording")[-1].feature == "appshot"


async def test_a_probe_that_reports_an_issue_the_gate_overrules_lets_the_capture_go() -> None:
    # The probe only DESCRIBES (it may read a stale preflight); only a live GRANTED
    # from the permission service decides.
    gate = FakePermissionService({"screen_recording": "granted"})
    capturer = _Capturer()
    service = _service(
        capturer,
        permission_probe=lambda: "Screen recording permission is missing.",
        permission_gate=gate,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "captured"
    assert len(capturer.grabs) == 1


async def test_a_wayland_issue_is_never_asked_about() -> None:
    gate = FakePermissionService()
    service = _service(
        permission_probe=lambda: CapturePermissionIssue(
            code="wayland_portal", message="A desktop portal is required."
        ),
        permission_gate=gate,
    )

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.reason_code == "wayland_portal"
    assert gate.calls == []


# ---------------------------------------------------------------------------
# Wallpaper-only frames are never a success
# ---------------------------------------------------------------------------


async def test_a_wallpaper_frame_after_a_revoke_is_refused_and_opens_an_episode(
    monkeypatch,
) -> None:
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    revoke = lambda: tcc.deny(TccService.SCREEN_RECORDING)  # noqa: E731
    capturer = _Capturer(frame=lambda: wallpaper_pixels((64, 48)), before_grab=revoke)
    service = _service(capturer)
    shutter: list = []
    service.set_shutter_hook(lambda *args: shutter.append(args))

    outcome = await service.capture_for_turn("look at this", locale="en")

    assert outcome.status == "refused"
    assert outcome.reason_code == "capture_permission"
    assert outcome.context is None and outcome.handle_id is None
    assert shutter == []  # no shutter effect for a frame that is not a screenshot
    assert [e.feature for e in get_permission_service().outstanding()] == ["screen_context"]
    assert tcc.dialogs_shown() == []


# ---------------------------------------------------------------------------
# Status probes are silent
# ---------------------------------------------------------------------------


def test_the_status_probe_describes_the_issue_and_never_asks(monkeypatch) -> None:
    tcc = _darwin(monkeypatch)

    issue = ports.capture_permission_error()

    assert issue is not None
    assert issue.code == "capture_permission"
    assert "Screen Recording" in issue.message
    assert not issue.message.startswith("[permission_needed")
    tcc.assert_no_prompts()


def test_the_status_probe_reads_a_grant_given_mid_session(monkeypatch) -> None:
    tcc = _darwin(monkeypatch)
    assert ports.capture_permission_error() is not None
    tcc.grant(TccService.SCREEN_RECORDING)  # the frozen preflight stays negative

    assert ports.capture_permission_error() is None
    tcc.assert_no_prompts()


def test_the_accessibility_probe_is_silent_and_keeps_the_image_available(monkeypatch) -> None:
    tcc = _darwin(monkeypatch)

    message = ports.accessibility_permission_error()

    assert message is not None
    assert "still available" in message
    assert "Settings > Permissions" not in message
    tcc.assert_no_prompts()
    tcc.grant(TccService.ACCESSIBILITY)
    get_permission_service().invalidate()  # the 250 ms negative cache, nothing else
    assert ports.accessibility_permission_error() is None


def test_the_probes_are_silent_and_empty_off_macos(monkeypatch) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    monkeypatch.setattr(ports, "_is_wayland", lambda: False)

    assert ports.capture_permission_error() is None
    assert ports.accessibility_permission_error() is None
    tcc.assert_silent()


async def test_a_capture_off_macos_is_unchanged_and_reads_nothing(monkeypatch) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    monkeypatch.setattr(ports, "_is_wayland", lambda: False)
    capturer = _Capturer(frame=lambda: wallpaper_pixels((64, 48)))  # flat is fine off macOS

    outcome = await _service(capturer).capture_for_turn("look at this", locale="en")

    assert outcome.status == "captured"
    tcc.assert_silent()
    assert get_permission_service().outstanding() == []

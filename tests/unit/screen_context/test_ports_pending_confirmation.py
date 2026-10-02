"""A native window capture that times out while macOS may be asking is PENDING.

The REAL permission service runs on ``FakeTCC`` (a stateful model of macOS privacy);
``grab_window`` is a hand-written stub that records the failure reason the way the
real ScreenCaptureKit path does. Nothing here ran on a real Mac.

* With a request still open (macOS may be showing its dialog) the capture is refused
  with the "may be showing a dialog" text, takes NO rect grab of the wallpaper and
  asks for nothing new.
* With a live grant the rect grab is the safe fallback and carries on.
* A plain timeout that the state does not explain refuses with the state's text.
"""

from __future__ import annotations

import sys
import types
from contextlib import nullcontext

import pytest

from jarvis.platform import window_capture
from jarvis.platform.permission_service import get_permission_service
from jarvis.platform.permissions import PermissionId
from jarvis.screen_context import ports
from tests.fakes.fake_screen_pixels import textured_pixels
from tests.fakes.fake_tcc import DialogPolicy, FakeTCC, TccService, install_port

_SR = PermissionId.SCREEN_RECORDING
_BBOX = (0, 0, 80, 60)


class _Raw:
    def __init__(self, size: tuple[int, int], rgb: bytes) -> None:
        self.size = size
        self.rgb = rgb


def _install_mss(monkeypatch: pytest.MonkeyPatch, grabs: list[dict[str, int]]) -> None:
    """A stand-in ``mss`` whose rect grab is recorded and returns real-looking pixels."""
    size, rgb = textured_pixels((80, 60))

    class _Sct:
        def __enter__(self) -> _Sct:
            return self

        def __exit__(self, *_exc: object) -> bool:
            return False

        def grab(self, bbox: dict[str, int]) -> _Raw:
            grabs.append(dict(bbox))
            return _Raw(size, rgb)

    module = types.ModuleType("mss")
    module.mss = _Sct  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", module)


def _native_fails_with(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    def native(_handle: int, _bbox: dict[str, int]) -> None:
        window_capture._failure.reason = reason
        return None

    monkeypatch.setattr(window_capture, "grab_window", native)
    monkeypatch.setattr(ports, "_is_wayland", lambda: False)
    monkeypatch.setattr(ports, "_input_space", nullcontext)
    monkeypatch.setattr("jarvis.cu.indicator.capture_guard.indicator_suppressed", nullcontext)


def _darwin(monkeypatch: pytest.MonkeyPatch, **kwargs) -> FakeTCC:
    tcc = FakeTCC(**kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    return tcc


def test_a_timeout_while_a_request_is_open_is_pending_not_denied(monkeypatch):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    get_permission_service().ensure(_SR, feature="screen_context", wait_s=0)
    assert tcc.dialog_open(TccService.SCREEN_RECORDING)
    grabs: list[dict[str, int]] = []
    _install_mss(monkeypatch, grabs)
    _native_fails_with(monkeypatch, "pending_confirmation")

    with pytest.raises(ports.CaptureUnavailable) as refused:
        ports.NativeSurfaceCapturer().grab(_BBOX, window_handle=9)

    assert "macOS may be showing a dialog" in str(refused.value)
    assert "denied" not in str(refused.value).lower()
    assert grabs == []  # no rect grab of what would be the wallpaper
    assert len(tcc.requests()) == 1  # nothing new was asked


def test_a_timeout_with_a_live_grant_falls_through_to_the_rect_grab(monkeypatch):
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])
    grabs: list[dict[str, int]] = []
    _install_mss(monkeypatch, grabs)
    _native_fails_with(monkeypatch, "pending_confirmation")

    (width, height), rgb = ports.NativeSurfaceCapturer().grab(_BBOX, window_handle=9)

    assert (width, height) == (80, 60) and rgb
    assert grabs == [{"left": 0, "top": 0, "width": 80, "height": 60}]
    tcc.assert_no_prompts()


def test_a_failure_that_is_not_a_timeout_never_looks_at_the_permission(monkeypatch):
    tcc = _darwin(monkeypatch)
    grabs: list[dict[str, int]] = []
    _install_mss(monkeypatch, grabs)
    _native_fails_with(monkeypatch, "")

    ports.NativeSurfaceCapturer().grab(_BBOX, window_handle=9)

    assert len(grabs) == 1
    tcc.assert_no_prompts()

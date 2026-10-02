"""ScreenSnapshotTool: asks for Screen Recording on the gesture, never returns wallpaper.

Audit finding 2026-07-06: without the TCC grant, mss "succeeds" but captures only the
desktop wallpaper - the tool then returned success=True with a useless image and the
model analyzed a wallpaper as if it were the screen. The tool body is the gesture
entry of a capture a person asked for, so it ASKS macOS (once per episode, through the
permission service) instead of refusing on its own preflight, only a live GRANTED
captures, and any refusal is an honest, prohibitive error naming the permission.

The REAL permission service runs on a REAL ``SystemPermissionPort`` that sits on
``FakeTCC``; its call log is the proof of what was (not) asked. Nothing here ran on
a real Mac (see the fidelity ledger in ``tests/fakes/fake_tcc.py``).
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from jarvis.platform.permission_service import get_permission_service
from jarvis.plugins.tool.screen_snapshot import ScreenSnapshotTool
from tests.fakes.fake_screen_pixels import textured_pixels, wallpaper_pixels
from tests.fakes.fake_tcc import (
    DialogPolicy,
    FakeTCC,
    TccService,
    install_port,
    make_non_darwin_port,
)

_SIZE = (8, 8)


def _fake_mss(monkeypatch: pytest.MonkeyPatch, frame: tuple[tuple[int, int], bytes]) -> None:
    class _FakeSct:
        monitors = [
            {"left": 0, "top": 0, "width": 8, "height": 8},
            {"left": 0, "top": 0, "width": 8, "height": 8},
        ]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, target):
            size, rgb = frame
            return SimpleNamespace(size=size, rgb=rgb)

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=lambda: _FakeSct()))
    monkeypatch.setattr(
        "jarvis.plugins.tool.screen_snapshot.select_capture_monitor",
        lambda monitors, strategy="foreground": monitors[1],
    )


def _darwin(monkeypatch: pytest.MonkeyPatch, **kwargs) -> FakeTCC:
    tcc = FakeTCC(**kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    return tcc


@pytest.mark.asyncio
async def test_not_determined_asks_once_and_refuses_while_the_dialog_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    _fake_mss(monkeypatch, textured_pixels(_SIZE))

    first = await ScreenSnapshotTool().execute({}, SimpleNamespace())
    second = await ScreenSnapshotTool().execute({}, SimpleNamespace())

    assert first.success is False and second.success is False
    assert (first.error or "").startswith("[permission_needed:screen_recording] ")
    assert "must not" in (first.error or "")  # prohibitive: the model never answers the dialog
    assert not getattr(first, "artifacts", ())
    # The gesture asked exactly once across two attempts; the capture never ran.
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.implicit_prompts() == []


@pytest.mark.asyncio
async def test_a_denial_is_stable_and_never_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.DENY)
    _fake_mss(monkeypatch, textured_pixels(_SIZE))

    first = await ScreenSnapshotTool().execute({}, SimpleNamespace())
    second = await ScreenSnapshotTool().execute({}, SimpleNamespace())

    assert first.success is False and second.success is False
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.ignored_requests() == []


@pytest.mark.asyncio
async def test_the_user_allowing_it_lets_the_capture_proceed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.ALLOW)
    _fake_mss(monkeypatch, textured_pixels(_SIZE))

    # The frozen preflight may keep the ask itself refused (the grant is only visible
    # to the window-title oracle a moment later); the call after it must go through
    # with no second request.
    first = await ScreenSnapshotTool().execute({"reason": "test"}, SimpleNamespace())
    res = first if first.success else await ScreenSnapshotTool().execute(
        {"reason": "test"}, SimpleNamespace()
    )

    assert res.success is True
    assert res.artifacts and res.artifacts[0]["type"] == "image"
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1


@pytest.mark.asyncio
async def test_grant_present_does_not_block_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])
    _fake_mss(monkeypatch, textured_pixels(_SIZE))

    res = await ScreenSnapshotTool().execute({"reason": "test"}, SimpleNamespace())

    assert res.success is True
    assert res.artifacts and res.artifacts[0]["type"] == "image"
    tcc.assert_no_prompts()


@pytest.mark.asyncio
async def test_a_wallpaper_frame_after_a_revoke_is_an_error_and_an_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    service = get_permission_service()
    assert service.check("screen_recording").value == "granted"  # warms the 1 s cache
    _fake_mss(monkeypatch, wallpaper_pixels(_SIZE))
    # The user revokes in System Settings between the entry read and the grab.
    real_grab = sys.modules["mss"].mss

    def revoking_mss():
        tcc.deny(TccService.SCREEN_RECORDING)
        return real_grab()

    sys.modules["mss"].mss = revoking_mss  # type: ignore[attr-defined]

    res = await ScreenSnapshotTool().execute({}, SimpleNamespace())

    assert res.success is False
    assert (res.error or "").startswith("[permission_needed:screen_recording] ")
    assert not getattr(res, "artifacts", ())
    assert [e.feature for e in service.outstanding()] == ["screen_context"]
    assert tcc.dialogs_shown() == []


@pytest.mark.asyncio
async def test_non_darwin_is_unchanged_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    _fake_mss(monkeypatch, wallpaper_pixels(_SIZE))  # a flat frame is fine off macOS

    res = await ScreenSnapshotTool().execute({"reason": "test"}, SimpleNamespace())

    assert res.success is True
    tcc.assert_silent()

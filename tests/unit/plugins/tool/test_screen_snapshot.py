"""ScreenSnapshotTool honesty on macOS without the Screen-Recording grant.

Audit finding 2026-07-06: without the TCC grant, mss "succeeds" but captures
only the desktop wallpaper — the tool then returned success=True with a
useless image and the model analyzed a wallpaper as if it were the screen.
The tool must refuse honestly with the actionable permission message instead.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.plugins.tool.screen_snapshot import ScreenSnapshotTool


@pytest.mark.asyncio
async def test_macos_screen_recording_denied_is_honest_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.plugins.tool.screen_snapshot.warn_if_screen_recording_denied",
        lambda: True,
    )

    res = await ScreenSnapshotTool().execute({}, SimpleNamespace())

    assert res.success is False
    assert "Screen Recording" in (res.error or "")
    assert not getattr(res, "artifacts", ())


@pytest.mark.asyncio
async def test_grant_present_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the grant present (probe returns False) the capture path runs.
    The mss grab itself is faked so the test works on headless CI."""
    monkeypatch.setattr(
        "jarvis.plugins.tool.screen_snapshot.warn_if_screen_recording_denied",
        lambda: False,
    )

    class _FakeShot:
        size = (4, 4)
        rgb = b"\x10\x20\x30" * 16

    class _FakeSct:
        monitors = [
            {"left": 0, "top": 0, "width": 8, "height": 8},
            {"left": 0, "top": 0, "width": 4, "height": 4},
        ]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, target):
            return _FakeShot()

    import sys
    import types

    fake_mss = types.SimpleNamespace(mss=lambda: _FakeSct())
    monkeypatch.setitem(sys.modules, "mss", fake_mss)
    monkeypatch.setattr(
        "jarvis.plugins.tool.screen_snapshot.select_capture_monitor",
        lambda monitors, strategy="foreground": monitors[1],
    )

    res = await ScreenSnapshotTool().execute({"reason": "test"}, SimpleNamespace())

    assert res.success is True
    assert res.artifacts and res.artifacts[0]["type"] == "image"

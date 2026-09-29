"""A window-only capture that comes back as one flat colour is not a picture.

Native window capture returns a uniform frame for some GPU-composited windows
(WebView2 apps, measured 2026-09-29). Handing that to a model yields a
confident description of a blank screen, so the service uses the window's
screen rectangle instead — under the same denylist rule monitor captures obey.
"""

from __future__ import annotations

from jarvis.screen_context.models import IntentVerdict, VisualIntent, WindowFacts
from jarvis.screen_context.service import ScreenContextSettings, _is_flat_frame

from .test_service import FakeCapturer, FakeWindowProbe, make_service


class VisibleWindows(FakeWindowProbe):
    def __init__(self, visible) -> None:
        super().__init__()
        self._visible = visible

    def visible_windows(self):
        return list(self._visible)


class BlankWindowCapturer(FakeCapturer):
    """Flat frame for the window-only path, real content for the rectangle."""

    def grab(self, bbox, *, window_handle=None):
        (width, height), _ = super().grab(bbox, window_handle=window_handle)
        if window_handle is not None:
            return (width, height), b"\x0a" * (width * height * 3)
        stripes = bytes(((x // 8) % 2) * 255 for x in range(width)) * height
        return (width, height), bytes(v for v in stripes for _ in range(3))


WINDOW = IntentVerdict(intent=VisualIntent.WINDOW)


def test_flat_frame_detection() -> None:
    assert _is_flat_frame((4, 4), b"\x0a" * 48)
    assert not _is_flat_frame((4, 1), b"\x00\x00\x00\xff\xff\xff" * 2)
    assert _is_flat_frame((0, 0), b"")


async def test_a_blank_window_capture_falls_back_to_its_rectangle() -> None:
    capturer = BlankWindowCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "captured"
    assert [handle for _bbox, handle in capturer.grabs] == [7, None]
    assert capturer.grabs[0][0] == capturer.grabs[1][0], "same rectangle both times"


async def test_the_fallback_obeys_the_denylist() -> None:
    capturer = BlankWindowCapturer()
    service = make_service(
        capturer=capturer,
        settings=ScreenContextSettings(denylist=("vault",)),
        window_probe=VisibleWindows(
            [WindowFacts(app_name="Vault", title="secrets", frame_rect=(10, 10, 800, 600))]
        ),
    )

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "refused"
    assert [handle for _bbox, handle in capturer.grabs] == [7], "no desktop rectangle grab"


async def test_a_window_with_content_is_used_as_captured() -> None:
    class ContentCapturer(BlankWindowCapturer):
        def grab(self, bbox, *, window_handle=None):
            return super().grab(bbox, window_handle=None)

    capturer = ContentCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "captured"
    assert len(capturer.grabs) == 1


async def test_an_appshot_service_shows_no_border_and_claims_no_missing_signal() -> None:
    from .test_service import RecordingBus

    bus = RecordingBus()
    shutters: list = []
    service = make_service(capturer=BlankWindowCapturer(), bus=bus)
    service.set_shutter_hook(lambda *args: shutters.append(args))

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "captured"
    assert len(shutters) == 1, "the flash is the visible signal"
    names = [type(event).__name__ for event in bus.events]
    assert "ScreenCaptureAnnounced" not in names, "no gold border before an appshot"
    assert "ScreenCaptureIndicatorDismissed" not in names
    assert all(d.code.value != "indicator_unavailable" for d in outcome.context.degradations)

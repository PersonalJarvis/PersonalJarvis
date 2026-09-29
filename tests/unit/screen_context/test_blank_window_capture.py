"""The front window is captured as the user sees it (BUG-220).

Native window-only capture returned near-black frames for GPU-composited
windows (WebView2 apps; live 2026-09-29: luma 0-13 over the whole frame), and
the model then described a blank screen. A window target is always the window
in front, so its rectangle of the desktop is the first choice. The window-only
path survives only as the privacy route when a denylisted window intersects
that rectangle — and a blank result there is refused, never described.
"""

from __future__ import annotations

from jarvis.screen_context.models import IntentVerdict, VisualIntent, WindowFacts
from jarvis.screen_context.service import ScreenContextSettings, _is_flat_frame

from .test_service import FakeCapturer, FakeWindowProbe, RecordingBus, make_service


class VisibleWindows(FakeWindowProbe):
    def __init__(self, visible) -> None:
        super().__init__()
        self._visible = visible

    def visible_windows(self):
        return list(self._visible)


def striped(width: int, height: int) -> bytes:
    row = bytes(((x // 8) % 2) * 255 for x in range(width))
    return bytes(v for v in row * height for _ in range(3))


class BlankWindowCapturer(FakeCapturer):
    """Near-black frame for the window-only path, real content for the rectangle."""

    def grab(self, bbox, *, window_handle=None):
        (width, height), _ = super().grab(bbox, window_handle=window_handle)
        if window_handle is not None:
            frame = bytearray(b"\x0a" * (width * height * 3))
            frame[:3] = b"\x00\x00\x00"  # a darker anti-aliased corner
            return (width, height), bytes(frame)
        return (width, height), striped(width, height)


class ContentWindowCapturer(FakeCapturer):
    def grab(self, bbox, *, window_handle=None):
        (width, height), _ = super().grab(bbox, window_handle=window_handle)
        return (width, height), striped(width, height)


WINDOW = IntentVerdict(intent=VisualIntent.WINDOW)
VAULT_ON_TOP = VisibleWindows(
    [WindowFacts(app_name="Vault", title="secrets", frame_rect=(10, 10, 800, 600))]
)


def test_flat_frame_detection_tolerates_stray_corner_pixels() -> None:
    body = bytearray(b"\x0a" * (64 * 64 * 3))
    body[:3] = b"\x00\x00\x00"
    body[-3:] = b"\x0d\x0d\x0d"
    assert _is_flat_frame((64, 64), bytes(body))
    assert not _is_flat_frame((64, 64), striped(64, 64))
    assert _is_flat_frame((0, 0), b"")


async def test_the_front_window_is_grabbed_as_its_screen_rectangle() -> None:
    capturer = BlankWindowCapturer()
    service = make_service(capturer=capturer)

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "captured"
    assert [handle for _bbox, handle in capturer.grabs] == [None]


async def test_a_denylisted_overlap_uses_the_window_alone() -> None:
    capturer = ContentWindowCapturer()
    service = make_service(
        capturer=capturer,
        settings=ScreenContextSettings(denylist=("vault",)),
        window_probe=VAULT_ON_TOP,
    )

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "captured"
    assert [handle for _bbox, handle in capturer.grabs] == [7], "no desktop rectangle"


async def test_a_blank_window_alone_is_refused_not_described() -> None:
    capturer = BlankWindowCapturer()
    service = make_service(
        capturer=capturer,
        settings=ScreenContextSettings(denylist=("vault",)),
        window_probe=VAULT_ON_TOP,
    )

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "refused"
    assert [handle for _bbox, handle in capturer.grabs] == [7]


async def test_an_appshot_service_shows_no_border_and_claims_no_missing_signal() -> None:
    bus = RecordingBus()
    shutters: list = []
    service = make_service(capturer=ContentWindowCapturer(), bus=bus)
    service.set_shutter_hook(lambda *args: shutters.append(args))

    outcome = await service.capture(verdict=WINDOW)

    assert outcome.status == "captured"
    assert len(shutters) == 1, "the flash is the visible signal"
    names = [type(event).__name__ for event in bus.events]
    assert "ScreenCaptureAnnounced" not in names, "no gold border before an appshot"
    assert "ScreenCaptureIndicatorDismissed" not in names
    assert all(d.code.value != "indicator_unavailable" for d in outcome.context.degradations)

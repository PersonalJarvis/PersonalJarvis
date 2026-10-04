"""Fakes for the direct ``computer`` tool: screen frames, input backend, event bus."""

from __future__ import annotations

import hashlib

from jarvis.cu.direct import Foreground, Probes
from jarvis.cu.geometry import CoordinateMapper

_THUMB_BYTES = 96 * 54


class FakeFrame:
    """A captured frame: ``capture`` rect in screen units, ``image`` size sent."""

    def __init__(
        self,
        *,
        shade: int = 0,
        capture: tuple[int, int, int, int] = (0, 0, 1366, 768),
        image: tuple[int, int] = (683, 384),
    ) -> None:
        left, top, width, height = capture
        self.image_width, self.image_height = image
        self.mapper = CoordinateMapper(
            capture_left=left,
            capture_top=top,
            capture_width=width,
            capture_height=height,
            image_width=self.image_width,
            image_height=self.image_height,
        )
        self.jpeg = b"\xff\xd8fake-jpeg-%d" % shade
        self.sha256 = hashlib.sha256(self.jpeg).hexdigest()
        self.thumb = bytes([shade]) * _THUMB_BYTES
        self.stable = True


class FakeScreen:
    """Hands out frames in order; the last one repeats."""

    def __init__(self, *frames: FakeFrame) -> None:
        self.frames = list(frames) or [FakeFrame()]
        self.captures = 0

    def __call__(self, max_dimension: int, monitor: str, main_monitor: str) -> FakeFrame:
        frame = self.frames[min(self.captures, len(self.frames) - 1)]
        self.captures += 1
        return frame


class FakeActuator:
    """Records input; the cursor lands exactly where it was moved."""

    name = "fake"

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._cursor = (0, 0)

    def cursor_pos(self):
        return self._cursor

    def move(self, x: int, y: int) -> None:
        self._cursor = (x, y)

    def click_at_cursor(self, *, button="left", double=False, expected=None) -> None:
        self.events.append(("click", self._cursor, button, double))

    def click(self, x, y, *, button="left", double=False) -> None:
        self.move(x, y)
        self.click_at_cursor(button=button, double=double)

    def drag_from_cursor(self, x1, y1, x2, y2, *, duration_s=0.4) -> None:
        self.events.append(("drag", (x1, y1), (x2, y2)))
        self._cursor = (x2, y2)

    def drag(self, x1, y1, x2, y2, *, duration_s=0.4) -> None:
        self.move(x1, y1)
        self.drag_from_cursor(x1, y1, x2, y2)

    def scroll(self, direction, notches, *, x=None, y=None) -> None:
        self.events.append(("scroll", direction, notches, x, y))

    def key_combo(self, keys) -> None:
        self.events.append(("key", tuple(keys)))

    def type_text(self, text, *, delay_s=0.02) -> None:
        self.events.append(("type", text))


class FakeForeground:
    """The window in front; tests swap ``signature`` to simulate a focus change."""

    def __init__(self, title: str = "Browser") -> None:
        self.signature: tuple = ("handle", 1, (0, 0, 1366, 768))
        self.title = title

    def __call__(self) -> Foreground:
        return Foreground(self.signature, self.title)


class FakeBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [type(event).__name__ for event in self.events]


def probes(
    *,
    platform: str = "win32",
    display: bool = True,
    wayland: bool = False,
    macos_missing: tuple[str, ...] = (),
    secure_desktop: bool = False,
    foreground_elevated: bool | None = False,
    process_elevated: bool | None = False,
    secure_input: bool | None = False,
) -> Probes:
    """Platform probes with fixed answers. ``macos_missing`` lists missing grant ids."""
    labels = {
        "screen_recording": "Screen Recording",
        "accessibility": "Accessibility",
        "event_posting": "Input Control",
    }

    def missing(wanted: tuple[str, ...]) -> list[tuple[str, str]]:
        return [(labels[item], "denied") for item in wanted if item in macos_missing]

    return Probes(
        platform=lambda: platform,
        display_present=lambda: display,
        is_wayland=lambda: wayland,
        macos_missing=missing,
        secure_desktop=lambda: secure_desktop,
        foreground_elevated=lambda: foreground_elevated,
        process_elevated=lambda: process_elevated,
        secure_input=lambda: secure_input,
    )

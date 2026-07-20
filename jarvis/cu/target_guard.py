"""Cross-platform foreground identity bound to the CU input coordinate space."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ForegroundTarget:
    """One atomic-enough foreground sample in platform input units."""

    window: Any
    rect: tuple[int, int, int, int] | None
    signature: tuple[Any, ...]


def window_signature(
    window: Any,
    rect: tuple[int, int, int, int] | None,
) -> tuple[Any, ...]:
    if window is None:
        return ("none",)
    pid = getattr(window, "pid", None)
    if pid:
        # App-level identity. Only the macOS foreground probe fills ``pid``:
        # there the per-window handle is the CGWindowID of the frontmost
        # layer-0 window, which churns on focus changes INSIDE the same app
        # (an address-bar suggestions dropdown is its own layer-0 window, and
        # frame rects move with it). Comparing on the owning app keeps the
        # guard's purpose — catching a cross-app focus steal between the
        # screenshot and the action — without refusing every click->type
        # batch (live incident 2026-07-20: 9 REFUSED actions in two runs).
        # Windows/Linux probes leave ``pid`` unset and keep the stable
        # per-window handle+rect identity.
        return ("app", int(pid))
    handle = getattr(window, "handle", None)
    if handle:
        return ("handle", int(handle), rect)
    return ("title", str(getattr(window, "title", "") or "").casefold(), rect)


def read_foreground_target() -> ForegroundTarget:
    """Read hwnd/title and frame under one per-thread DPI context.

    ``asyncio.to_thread`` may use a different worker for every call. Pinning
    both reads in one block prevents a mixed-DPI Windows hwnd from producing
    physical pixels in one guard and DPI-virtualized coordinates in another.
    macOS and X11 use the same helper; ``input_space`` is a no-op there.
    """
    from jarvis.cu.geometry import input_space  # noqa: PLC0415
    from jarvis.platform import window_state  # noqa: PLC0415

    with input_space():
        window = window_state.foreground_window()
        rect = (
            window_state.window_frame_rect(window)
            if window is not None
            else None
        )
    return ForegroundTarget(window, rect, window_signature(window, rect))


def foreground_signature() -> tuple[Any, ...]:
    return read_foreground_target().signature


def foreground_matches(expected: tuple[Any, ...]) -> bool:
    return bool(expected) and expected[0] != "none" and (
        foreground_signature() == expected
    )

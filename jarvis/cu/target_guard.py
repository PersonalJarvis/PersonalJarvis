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
        # Only the macOS foreground probe fills ``pid``. The signature stays
        # WINDOW-precise (CGWindowID + rect), but leads with the owning app
        # so callers can distinguish "a different window of the SAME app took
        # over" (focus churn our own click caused — an address-bar
        # suggestions dropdown is its own layer-0 window with its own rect,
        # live incident 2026-07-20) from a cross-app focus steal, via
        # :func:`signatures_same_app`. Windows/Linux probes leave ``pid``
        # unset and keep their stable per-window handle+rect identity.
        handle = getattr(window, "handle", None)
        return ("app", int(pid), int(handle) if handle else None, rect)
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


def signatures_same_app(
    a: tuple[Any, ...], b: tuple[Any, ...],
) -> bool:
    """Whether two signatures identify windows of the SAME owning app.

    Only macOS signatures carry an app identity (``("app", pid, ...)``);
    Windows/Linux signatures return ``False`` here, so every same-app
    relaxation is a structural no-op on those platforms — their per-window
    hwnd/X11-id identity is stable and needs no tolerance.
    """
    return (
        len(a) >= 2
        and len(b) >= 2
        and a[0] == "app"
        and b[0] == "app"
        and a[1] == b[1]
    )


def foreground_matches_or_same_app(expected: tuple[Any, ...]) -> bool:
    """Strict signature match, tolerating same-app window churn (macOS).

    The tolerant matcher for RE-checks that run after the engine's strict
    per-frame baseline: on macOS the frontmost layer-0 window flips whenever
    a dropdown/sheet/popover opens — usually as the direct consequence of
    the action we just executed — and a strict equality re-check
    milliseconds later would refuse the rest of a legitimate batch. A
    cross-app focus steal still mismatches.
    """
    if not expected or expected[0] == "none":
        return False
    current = foreground_signature()
    return current == expected or signatures_same_app(expected, current)

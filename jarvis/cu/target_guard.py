"""Cross-platform foreground identity bound to the CU input coordinate space."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: Max per-coordinate drift (input units) between two frame rects that still
#: count as the SAME window geometry. DWM extended-frame bounds interpolate
#: for ~200 ms during restore/maximize/snap easing, so byte-exact rect
#: equality refused clicks mid-animation ("foreground window changed" on a
#: window that merely finished settling). A real cross-window change moves
#: the rect by tens-to-hundreds of units.
RECT_TOLERANCE_PX = 3

#: One bounded re-read before a mismatch verdict. ``GetForegroundWindow``
#: returns NULL transiently during focus hand-offs (often caused by our own
#: previous action); refusing on that single bad sample lost the whole step.
_RECHECK_SETTLE_S = 0.15


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


def coerce_signature(raw: Any) -> tuple[Any, ...]:
    """Recursively normalize a signature to nested tuples.

    A signature that crossed any list-producing boundary (JSON, msgpack, a
    schema validator) arrives with its inner rect as a ``list``;
    ``("handle", h, [r]) != ("handle", h, (r))`` would then refuse every
    action silently. Tools MUST run their ``_expected_window_signature``
    through this before comparing.
    """

    def _conv(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return tuple(_conv(item) for item in value)
        return value

    return tuple(_conv(item) for item in tuple(raw))


def _rects_close(
    a: Any, b: Any, *, tolerance: int = RECT_TOLERANCE_PX,
) -> bool:
    """Whether two signature rects describe the same window geometry."""
    if a == b:
        return True
    if (
        not isinstance(a, tuple) or not isinstance(b, tuple)
        or len(a) != 4 or len(b) != 4
    ):
        return False
    try:
        return all(
            abs(int(x) - int(y)) <= tolerance
            for x, y in zip(a, b, strict=True)
        )
    except (TypeError, ValueError):
        return False


def signatures_equivalent(
    expected: tuple[Any, ...], current: tuple[Any, ...],
) -> bool:
    """Same window identity, tolerating sub-animation rect drift.

    Exact equality, or: identical signature kind and identity fields with
    the trailing rect within :data:`RECT_TOLERANCE_PX` per coordinate. A
    different handle/pid/title is NEVER equivalent — the tolerance only
    absorbs DWM frame easing on the same window.
    """
    if expected == current:
        return True
    if (
        not expected or not current
        or len(expected) != len(current)
        or expected[0] != current[0]
        or expected[0] == "none"
    ):
        return False
    # Identity fields are everything between the kind tag and the rect.
    if expected[1:-1] != current[1:-1]:
        return False
    return _rects_close(expected[-1], current[-1])


def foreground_matches(expected: tuple[Any, ...]) -> bool:
    return bool(expected) and expected[0] != "none" and (
        signatures_equivalent(expected, foreground_signature())
    )


def _hwnd_pid(handle: Any) -> int:
    """Owning process id of a Win32 hwnd, or 0 (non-Windows, dead hwnd).

    Module-level so tests can monkeypatch it; the live path is Windows-only
    and best-effort — a failure degrades to 0, which fails closed (no
    same-app relaxation).
    """
    if os.name != "nt" or not handle:
        return 0
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        pid = wintypes.DWORD(0)
        thread = ctypes.windll.user32.GetWindowThreadProcessId(
            wintypes.HWND(int(handle)), ctypes.byref(pid),
        )
        return int(pid.value) if thread else 0
    except Exception:  # noqa: BLE001 — native probe is best-effort
        log.debug("GetWindowThreadProcessId failed", exc_info=True)
        return 0


def signatures_same_app(
    a: tuple[Any, ...], b: tuple[Any, ...],
) -> bool:
    """Whether two signatures identify windows of the SAME owning app.

    macOS signatures carry the app identity directly (``("app", pid, ...)``).
    Windows ``("handle", hwnd, rect)`` signatures are related through the
    live window table instead: the same hwnd (our own action moved/resized
    the window), or two hwnds owned by the same process (a Chromium/WinUI
    context menu or dropdown is its own top-level popup that can take the
    foreground — refusing on it broke every right-click -> menu-item batch,
    the Windows twin of the macOS live incident 2026-07-20). A dead or
    foreign hwnd resolves to pid 0 and fails closed. Linux/X11 signatures
    still never match — no owning-app probe exists there yet.
    """
    if len(a) < 2 or len(b) < 2:
        return False
    if a[0] == "app" and b[0] == "app":
        return a[1] == b[1]
    if a[0] == "handle" and b[0] == "handle":
        if a[1] == b[1]:
            return True
        pid_a = _hwnd_pid(a[1])
        return pid_a != 0 and pid_a == _hwnd_pid(b[1])
    return False


def foreground_matches_or_same_app(expected: tuple[Any, ...]) -> bool:
    """Signature match, tolerating same-app window churn and one transient.

    The tolerant matcher for RE-checks that run after the engine's strict
    per-frame baseline: the frontmost window flips whenever a dropdown /
    sheet / context menu opens — usually as the direct consequence of the
    action we just executed — and a strict equality re-check milliseconds
    later would refuse the rest of a legitimate batch. A cross-app focus
    steal still mismatches.

    A first failing read is re-sampled ONCE after a short settle:
    ``GetForegroundWindow`` returns NULL (-> ``("none",)``) during focus
    hand-offs, and a single bad sample must not cost the step.
    """
    if not expected or expected[0] == "none":
        return False
    for attempt in (1, 2):
        current = foreground_signature()
        if (
            signatures_equivalent(expected, current)
            or signatures_same_app(expected, current)
        ):
            return True
        if attempt == 1:
            log.debug(
                "[cu] foreground re-check mismatch (%s vs %s) — "
                "re-sampling once after %.2fs",
                current[:1], expected[:1], _RECHECK_SETTLE_S,
            )
            time.sleep(_RECHECK_SETTLE_S)
    return False

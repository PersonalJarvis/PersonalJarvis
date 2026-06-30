"""The idle bar must stay expanded WHILE hovered and collapse only on real exit.

Forensic (2026-06-28): hovering the idle (non-active) bar made it "open briefly
and immediately snap shut". Root cause — the idle pill SIZE is bound to the
instantaneous ``_hovered`` flag, which Tk drives from ``<Enter>``/``<Leave>``
over the COLOR-KEYED opaque pill. That pill is a tiny ~48x8 px sliver inside a
107x48 window (the rest is click-through magenta), redrawn every frame with
antialiased edges, and it RESIZES on hover. So the pointer near its edge emits
rapid ``<Leave>``/``<Enter>`` pairs; each ``<Leave>`` collapsed the pill, which
shrank the opaque region inward under the pointer and reinforced the collapse.

The fix decouples the collapse from the raw ``<Leave>``: a leave starts a short
poll of the REAL pointer position and the idle pill collapses only once the
pointer has genuinely left the bar's window rect. The ACTIVE state is untouched
— its pill size comes from ``_mode``, and its hover controls keep the old,
instantaneous ``_hovered`` flag.

Headless — a fake root (after / after_cancel / winfo_pointerxy), no real Tk.
"""
from __future__ import annotations

from jarvis.ui.jarvisbar import interaction as I
from jarvis.ui.jarvisbar import renderer as R
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


class _FakeRoot:
    """Fakes the bits of Tk the collapse poll touches. The window rect is the
    LIVE on-screen geometry (winfo_rootx/rooty/width/height) — the same source
    the production code now reads — defaulting to the bar's footprint at (100,100).
    """

    def __init__(
        self, pointer: tuple[int, int] = (0, 0), rect: tuple[int, int, int, int] | None = None
    ) -> None:
        self.pointer = pointer
        self.rect = rect or (100, 100, R.WIN_W, R.WIN_H)  # (x, y, w, h)
        self.after_calls: list[tuple[int, object, int]] = []
        self.cancelled: list[int] = []
        self._next_id = 0

    def after(self, ms: int, fn: object) -> int:
        self._next_id += 1
        self.after_calls.append((ms, fn, self._next_id))
        return self._next_id

    def after_cancel(self, cid: int) -> None:
        self.cancelled.append(cid)

    def winfo_pointerxy(self) -> tuple[int, int]:
        return self.pointer

    def winfo_rootx(self) -> int:
        return self.rect[0]

    def winfo_rooty(self) -> int:
        return self.rect[1]

    def winfo_width(self) -> int:
        return self.rect[2]

    def winfo_height(self) -> int:
        return self.rect[3]


def _idle_bar(pointer: tuple[int, int]) -> tuple[JarvisBarOverlay, _FakeRoot]:
    bar = JarvisBarOverlay()
    bar._mode = "idle"  # noqa: SLF001
    bar._x, bar._y = 100, 100  # noqa: SLF001 — window top-left in screen coords
    root = _FakeRoot(pointer=pointer)
    bar._root = root  # noqa: SLF001
    return bar, root


def _center() -> tuple[int, int]:
    return (100 + R.WIN_W // 2, 100 + R.WIN_H // 2)


# --------------------------------------------------------------------------- #
# Pure geometry helper                                                        #
# --------------------------------------------------------------------------- #
def test_pointer_in_window_bounds():
    assert I.pointer_in_window(50, 50, 0, 0, 100, 100) is True
    assert I.pointer_in_window(-1, 50, 0, 0, 100, 100) is False
    assert I.pointer_in_window(50, 200, 0, 0, 100, 100) is False
    assert I.pointer_in_window(0, 0, 0, 0, 100, 100) is True       # top-left inclusive
    assert I.pointer_in_window(100, 100, 0, 0, 100, 100) is False  # bottom-right exclusive


# --------------------------------------------------------------------------- #
# Idle hover stability — the actual bug                                        #
# --------------------------------------------------------------------------- #
def test_enter_expands_idle_bar():
    bar, _root = _idle_bar(_center())
    bar._on_enter()  # noqa: SLF001
    assert bar._hover_expanded is True  # noqa: SLF001


def test_spurious_leave_keeps_expanded_while_pointer_over_bar():
    """The regression: an edge/antialiasing ``<Leave>`` fired while the pointer
    is STILL over the bar's footprint must NOT collapse the idle pill."""
    bar, root = _idle_bar(_center())
    bar._on_enter()  # noqa: SLF001
    bar._on_leave()  # noqa: SLF001 — spurious leave; pointer still inside

    # Not collapsed synchronously; a poll was scheduled instead.
    assert bar._hover_expanded is True  # noqa: SLF001
    assert root.after_calls, "collapse poll was not scheduled"

    # Running the poll keeps it open (pointer still inside) and re-arms.
    _ms, fn, _cid = root.after_calls[-1]
    fn()
    assert bar._hover_expanded is True  # noqa: SLF001
    assert len(root.after_calls) >= 2, "poll did not re-arm while still hovered"


def test_collapse_only_after_pointer_leaves_footprint():
    bar, root = _idle_bar(pointer=(0, 0))  # pointer far outside the window rect
    bar._on_enter()  # noqa: SLF001
    bar._on_leave()  # noqa: SLF001
    _ms, fn, _cid = root.after_calls[-1]
    fn()  # poll sees the pointer outside → collapse
    assert bar._hover_expanded is False  # noqa: SLF001


def test_enter_cancels_pending_collapse_poll():
    bar, root = _idle_bar(pointer=(0, 0))
    bar._on_enter()  # noqa: SLF001
    bar._on_leave()  # noqa: SLF001 — schedules a poll
    pending = bar._hover_collapse_id  # noqa: SLF001
    assert pending is not None

    bar._on_enter()  # noqa: SLF001 — re-entering must cancel the pending collapse
    assert pending in root.cancelled
    assert bar._hover_expanded is True  # noqa: SLF001
    assert bar._hover_collapse_id is None  # noqa: SLF001


def test_no_tk_root_falls_back_to_immediate_collapse():
    """Headless / pre-start: with no Tk root, a leave collapses immediately so
    the flag never sticks True without a poll to clear it."""
    bar = JarvisBarOverlay()
    bar._mode = "idle"  # noqa: SLF001
    bar._on_enter()  # noqa: SLF001 — _root is None
    assert bar._hover_expanded is True  # noqa: SLF001
    bar._on_leave()  # noqa: SLF001
    assert bar._hover_expanded is False  # noqa: SLF001


# --------------------------------------------------------------------------- #
# Active state stays exactly as before                                         #
# --------------------------------------------------------------------------- #
def test_active_hover_uses_instantaneous_flag_unchanged():
    """The active bar's controls follow the raw ``_hovered`` flag (hidden the
    instant the pointer leaves the opaque pill) — unchanged by the idle fix."""
    bar = JarvisBarOverlay()
    bar._mode = "speak"  # noqa: SLF001 — live session
    bar._x, bar._y = 100, 100  # noqa: SLF001
    bar._root = _FakeRoot(pointer=_center())  # noqa: SLF001

    bar._on_enter()  # noqa: SLF001
    assert bar._hovered is True  # noqa: SLF001
    bar._on_leave()  # noqa: SLF001
    assert bar._hovered is False  # noqa: SLF001 — instantaneous, as before

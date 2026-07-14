"""Native reveal and z-order repairs must be explicit and style-safe.

Forensic (2026-06-27): a withdrawn→deiconified ``overrideredirect`` window can
lose its topmost z-order on Windows, so later desktop windows map above it until
the next wake re-shows it. The voice-ready startup-gate release now maps the
fully configured bar and explicitly re-pins topmost. ``_do_show`` re-asserts
``-topmost`` and lifts only when it maps a withdrawn window. These tests pin
that contract without a real Tk window.

Forensic (2026-06-30): re-asserting ``-topmost`` is itself a Win32 style
mutation on this layered (color-key + alpha) window, and Windows can silently
drop the layered attributes on such a mutation (BUG-030) — the bar then briefly
renders its true opaque black backing instead of the keyed-out magenta ("black
border flashes around the bar, then disappears"). ``_do_show`` now also
re-applies ``-transparentcolor``/``-alpha`` right after the topmost re-assert.
Repeated wake updates on an already-mapped persistent bar skip all of those
native style mutations, preventing the old default-size Tk backing surface from
flashing at the top-left of the screen.
"""
from __future__ import annotations

from jarvis.ui.jarvisbar.overlay import COLOR_KEY_HEX, JarvisBarOverlay


class _FakeRoot:
    """Records the order of the visibility calls ``_do_show`` makes."""

    def __init__(self, *, mapped: bool = False) -> None:
        self.calls: list[str] = []
        self.attrs: dict[str, object] = {}
        self.mapped = mapped

    def winfo_ismapped(self) -> int:
        return int(self.mapped)

    def deiconify(self) -> None:
        self.calls.append("deiconify")
        self.mapped = True

    def lift(self) -> None:
        self.calls.append("lift")

    def update_idletasks(self) -> None:
        self.calls.append("update_idletasks")

    def wm_attributes(self, name: str, value: object) -> None:
        self.calls.append(f"wm_attributes:{name}={value}")
        self.attrs[name] = value


class _FakeCanvas:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def itemconfig(self, image_id: int, *, image: object) -> None:
        self.calls.append(f"itemconfig:{image_id}:{id(image)}")


def _bar_with_fake_root(*, mapped: bool = False) -> tuple[JarvisBarOverlay, _FakeRoot]:
    bar = JarvisBarOverlay(persistent=True)
    root = _FakeRoot(mapped=mapped)
    bar._root = root  # noqa: SLF001 — inject a fake Tk root (no real window)
    return bar, root


def test_do_show_deiconifies_then_lifts_and_repins_topmost() -> None:
    bar, root = _bar_with_fake_root()

    bar._do_show()  # noqa: SLF001

    # The zero-alpha guard may run first, but mapping must still precede the
    # post-map topmost re-assert + lift.
    assert "lift" in root.calls
    assert root.attrs.get("-topmost") is True
    # The lift happens after the deiconify (Windows remaps without topmost).
    assert root.calls.index("deiconify") < root.calls.index("lift")


def test_do_show_mapped_window_skips_all_native_style_mutations() -> None:
    bar, root = _bar_with_fake_root(mapped=True)
    bar._static_tick_key = ("idle", False, False)  # noqa: SLF001
    bar._static_tick_count = 999  # noqa: SLF001

    bar._do_show()  # noqa: SLF001

    assert root.calls == []
    assert bar._static_tick_key is None  # noqa: SLF001
    assert bar._static_tick_count == 0  # noqa: SLF001


def test_explicit_z_order_reassert_repins_an_already_mapped_window() -> None:
    bar, root = _bar_with_fake_root(mapped=True)

    bar._do_reassert_z_order()  # noqa: SLF001

    assert "deiconify" not in root.calls
    assert "lift" in root.calls
    assert root.attrs.get("-topmost") is True
    assert root.attrs.get("-transparentcolor") == COLOR_KEY_HEX


def test_do_show_reapplies_transparentcolor_and_alpha_after_topmost() -> None:
    """BUG-030 guard: the topmost re-assert must not leave the layered
    color-key/alpha attributes un-reapplied — else a Windows-side drop of
    those attributes on the style mutation shows as a black flash."""
    bar, root = _bar_with_fake_root()

    bar._do_show()  # noqa: SLF001

    assert root.attrs.get("-transparentcolor") == COLOR_KEY_HEX
    assert root.attrs.get("-alpha") == bar._opacity  # noqa: SLF001
    # Re-applied AFTER the topmost mutation, not before — the whole point is
    # to heal whatever the topmost re-assert may have just dropped.
    topmost_index = root.calls.index("wm_attributes:-topmost=True")
    transparentcolor_indices = [
        index
        for index, call in enumerate(root.calls)
        if call == f"wm_attributes:-transparentcolor={COLOR_KEY_HEX}"
    ]
    assert any(index > topmost_index for index in transparentcolor_indices)


def test_do_show_composes_at_zero_alpha_before_becoming_visible() -> None:
    """A withdrawn Tk backing surface must never be mapped while opaque."""
    bar, root = _bar_with_fake_root()

    bar._do_show()  # noqa: SLF001

    zero_alpha = root.calls.index("wm_attributes:-alpha=0.0")
    mapped = root.calls.index("deiconify")
    composed = root.calls.index("update_idletasks")
    visible_alpha = len(root.calls) - 1 - root.calls[::-1].index(
        f"wm_attributes:-alpha={bar._opacity}"  # noqa: SLF001
    )
    assert zero_alpha < mapped < composed < visible_alpha
    assert root.attrs["-alpha"] == bar._opacity  # noqa: SLF001


def test_do_show_invalidates_settled_idle_frame_for_post_map_repaint() -> None:
    bar, _root = _bar_with_fake_root()
    bar._static_tick_key = ("idle", False, False)  # noqa: SLF001
    bar._static_tick_count = 999  # noqa: SLF001

    bar._do_show()  # noqa: SLF001

    assert bar._static_tick_key is None  # noqa: SLF001
    assert bar._static_tick_count == 0  # noqa: SLF001


def test_do_show_resubmits_prepared_frame_while_alpha_is_zero() -> None:
    bar, root = _bar_with_fake_root()
    photo = object()
    bar._canvas = _FakeCanvas(root.calls)  # noqa: SLF001
    bar._image_id = 7  # noqa: SLF001
    bar._photo = photo  # noqa: SLF001

    bar._do_show()  # noqa: SLF001

    refreshed = root.calls.index(f"itemconfig:7:{id(photo)}")
    composed = root.calls.index("update_idletasks")
    visible_alpha = len(root.calls) - 1 - root.calls[::-1].index(
        f"wm_attributes:-alpha={bar._opacity}"  # noqa: SLF001
    )
    assert refreshed < composed < visible_alpha


def test_do_show_transparentcolor_reassert_failure_does_not_raise() -> None:
    bar, root = _bar_with_fake_root()

    def _boom(name: str, value: object) -> None:
        if name == "-transparentcolor":
            raise RuntimeError("transparentcolor exploded")
        root.calls.append(f"wm_attributes:{name}={value}")
        root.attrs[name] = value

    root.wm_attributes = _boom  # type: ignore[method-assign]

    # Must not raise even when the color-key re-assert fails.
    bar._do_show()  # noqa: SLF001


def test_do_show_lift_failure_does_not_swallow_deiconify() -> None:
    bar, root = _bar_with_fake_root()

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("lift exploded")

    root.lift = _boom  # type: ignore[method-assign]

    # Must not raise even when the lift/topmost re-assert fails — the deiconify
    # already ran and the bar is visible; the lift is best-effort.
    bar._do_show()  # noqa: SLF001

    assert "deiconify" in root.calls


def test_do_show_safe_without_root() -> None:
    bar = JarvisBarOverlay(persistent=True)
    # No Tk root yet (boot race) → silent no-op, never raises.
    bar._do_show()  # noqa: SLF001

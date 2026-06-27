"""The reveal path must LIFT + re-pin topmost, not just deiconify.

Forensic (2026-06-27): on the fast-boot path the persistent bar's boot reveal
(``VoiceBootStatus(ready=True)`` → ``show("idle")``) fires within ~200 ms of
window creation, before the desktop main window + tray finish mapping. A
withdrawn→deiconified ``overrideredirect`` window loses its topmost z-order on
Windows, so the later boot windows mapped on top of the bar and it stayed hidden
until the first wake-word re-showed it ("bar does not appear when ready, only
after the wake-word"). ``_do_show`` now re-asserts ``-topmost`` and lifts, matching
the mascot orb. These tests pin that contract without a real Tk window.
"""
from __future__ import annotations

from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay


class _FakeRoot:
    """Records the order of the visibility calls ``_do_show`` makes."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.attrs: dict[str, object] = {}

    def deiconify(self) -> None:
        self.calls.append("deiconify")

    def lift(self) -> None:
        self.calls.append("lift")

    def wm_attributes(self, name: str, value: object) -> None:
        self.calls.append(f"wm_attributes:{name}={value}")
        self.attrs[name] = value


def _bar_with_fake_root() -> tuple[JarvisBarOverlay, _FakeRoot]:
    bar = JarvisBarOverlay(persistent=True)
    root = _FakeRoot()
    bar._root = root  # noqa: SLF001 — inject a fake Tk root (no real window)
    return bar, root


def test_do_show_deiconifies_then_lifts_and_repins_topmost() -> None:
    bar, root = _bar_with_fake_root()

    bar._do_show()  # noqa: SLF001

    # deiconify must come first, then the topmost re-assert + lift.
    assert root.calls[0] == "deiconify"
    assert "lift" in root.calls
    assert root.attrs.get("-topmost") is True
    # The lift happens after the deiconify (Windows remaps without topmost).
    assert root.calls.index("deiconify") < root.calls.index("lift")


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

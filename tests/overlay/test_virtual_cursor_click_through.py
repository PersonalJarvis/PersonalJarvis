"""Regression test for the virtual-cursor black-screen bug (incident 2026-05-26).

The previous ``_make_click_through`` set ``WS_EX_LAYERED | WS_EX_TRANSPARENT``
via ``SetWindowLongPtrW`` but never re-applied ``SetLayeredWindowAttributes``
afterwards. Per the Win32 docs, writing ``GWL_EXSTYLE`` on a layered window
silently invalidates the cached chroma-key / alpha set by Tk's
``-transparentcolor`` attribute. The result was a fullscreen layered overlay
spanning the whole virtual desktop that painted opaque, blacking out every
monitor — and because the Tk thread is a daemon under a long-lived
``pythonw.exe``, the HWND survived until a full PC reboot.

This test pins the call ordering and the chroma-key value so the bug cannot
return unnoticed. The production module is patched at the ``ctypes.windll``
seam: no real Win32 call is made, the test runs headless.
"""
from __future__ import annotations

import types


def _install_fake_windll(monkeypatch) -> list[tuple[str, tuple]]:
    """Replace ``ctypes.windll`` with a recording fake; return the call log.

    ``_make_click_through`` resolves ``ctypes.windll.user32`` inside its body,
    so the patch only needs to land before the call — not before the import.
    """
    calls: list[tuple[str, tuple]] = []

    class FakeUser32:
        # Production code uses ``getattr(user32, "GetWindowLongPtrW",
        # user32.GetWindowLongW)`` which evaluates the fallback eagerly, so
        # both names must exist on the fake even though the PtrW variant wins.
        def GetWindowLongPtrW(self, hwnd, idx):
            calls.append(("GetWindowLongPtrW", (hwnd, idx)))
            return 0  # caller ORs flags in; the starting value does not matter

        def GetWindowLongW(self, hwnd, idx):  # pragma: no cover — fallback only
            calls.append(("GetWindowLongW", (hwnd, idx)))
            return 0

        def SetWindowLongPtrW(self, hwnd, idx, val):
            calls.append(("SetWindowLongPtrW", (hwnd, idx, val)))
            return 0

        def SetWindowLongW(self, hwnd, idx, val):  # pragma: no cover — fallback only
            calls.append(("SetWindowLongW", (hwnd, idx, val)))
            return 0

        def SetLayeredWindowAttributes(self, hwnd, color, alpha, flags):
            calls.append(("SetLayeredWindowAttributes", (hwnd, color, alpha, flags)))
            return 1

        def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
            calls.append(("SetWindowPos", (hwnd, after, x, y, w, h, flags)))
            return 1

    import ctypes
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=FakeUser32()))
    return calls


def test_click_through_reapplies_layered_attributes_after_style_change(monkeypatch):
    """The chroma-key must be re-applied after ``SetWindowLongPtrW``.

    Otherwise Windows drops the cached LWA attributes and the fullscreen
    overlay paints solid — see the 2026-05-26 black-screen incident.
    """
    calls = _install_fake_windll(monkeypatch)

    from ui.orb.virtual_cursor_window import _make_click_through

    _make_click_through(0xCAFEBEEF)

    names = [c[0] for c in calls]
    assert "SetWindowLongPtrW" in names, (
        "_make_click_through must call SetWindowLongPtrW to add WS_EX_TRANSPARENT"
    )
    assert "SetLayeredWindowAttributes" in names, (
        "_make_click_through must call SetLayeredWindowAttributes AFTER "
        "SetWindowLongPtrW to re-apply the chroma-key; otherwise the fullscreen "
        "overlay paints opaque on every monitor (incident 2026-05-26)."
    )

    swl_idx = names.index("SetWindowLongPtrW")
    lwa_idx = names.index("SetLayeredWindowAttributes")
    assert lwa_idx > swl_idx, (
        "SetLayeredWindowAttributes must be called AFTER SetWindowLongPtrW "
        f"(observed order: {names}). SetWindowLong invalidates the cached "
        "chroma-key; the re-apply restores it."
    )


def test_click_through_uses_magenta_color_key(monkeypatch):
    """The chroma-key passed to LWA must match the Tk window background.

    The bg in :data:`COLOR_KEY_HEX` is ``#FF00FF`` (magenta). Windows
    expects a ``COLORREF`` packed as ``0x00BBGGRR``, so magenta is
    ``0x00FF00FF``. Any mismatch would key out the wrong colour and the
    overlay would still paint solid.
    """
    calls = _install_fake_windll(monkeypatch)

    from ui.orb.virtual_cursor_window import _make_click_through

    _make_click_through(0xCAFEBEEF)

    lwa_call = next(c for c in calls if c[0] == "SetLayeredWindowAttributes")[1]
    _, color, _, flags = lwa_call

    _LWA_COLORKEY = 0x00000001
    _MAGENTA_COLORREF = 0x00FF00FF  # COLORREF = 0x00BBGGRR with B=FF G=00 R=FF

    assert flags & _LWA_COLORKEY, (
        f"LWA flags must include LWA_COLORKEY (0x1), got 0x{flags:x}"
    )
    assert color == _MAGENTA_COLORREF, (
        f"Chroma-key colour must be magenta COLORREF 0x{_MAGENTA_COLORREF:x} "
        f"(matches COLOR_KEY_HEX='#FF00FF'); got 0x{color:x}."
    )


def test_click_through_commits_style_change_with_framechanged(monkeypatch):
    """``SetWindowPos`` with ``SWP_FRAMECHANGED`` is required so Windows
    actually recalculates the non-client area after the ex-style flip.

    Without this commit the new ``WS_EX_TRANSPARENT`` is honoured for input
    but the layered surface may render stale until the next move/resize.
    """
    calls = _install_fake_windll(monkeypatch)

    from ui.orb.virtual_cursor_window import _make_click_through

    _make_click_through(0xCAFEBEEF)

    names = [c[0] for c in calls]
    assert "SetWindowPos" in names, (
        "_make_click_through must call SetWindowPos(...SWP_FRAMECHANGED) after "
        "SetWindowLongPtrW so the ex-style change takes effect immediately."
    )

    swp_call = next(c for c in calls if c[0] == "SetWindowPos")[1]
    flags = swp_call[6]
    _SWP_FRAMECHANGED = 0x0020
    _SWP_NOMOVE = 0x0002
    _SWP_NOSIZE = 0x0001
    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010
    assert flags & _SWP_FRAMECHANGED, (
        f"SetWindowPos must include SWP_FRAMECHANGED (0x20), got 0x{flags:x}"
    )
    # Defensive: don't accidentally move, resize, raise or focus the overlay.
    for required, name in (
        (_SWP_NOMOVE, "SWP_NOMOVE"),
        (_SWP_NOSIZE, "SWP_NOSIZE"),
        (_SWP_NOZORDER, "SWP_NOZORDER"),
        (_SWP_NOACTIVATE, "SWP_NOACTIVATE"),
    ):
        assert flags & required, (
            f"SetWindowPos must include {name} (0x{required:x}) to avoid moving / "
            f"resizing / raising / focusing the overlay; got flags 0x{flags:x}"
        )

"""Virtual-cursor overlay must be click-through on the window Windows hit-tests.

Tk's ``winfo_id()`` returns the INNER ``TkChild`` client window; hit-testing
(``WM_NCHITTEST``) resolves against its ``TkTopLevel`` wrapper parent. Styling
the child left the wrapper opaque to input, so the overlay ATE the very clicks
it visualizes — its gold center dot sits exactly on the click hotspot
(Windows click-failure class, 2026-07-21). These tests pin the wrapper
resolution and the style bits ``_make_click_through`` applies.
"""
from __future__ import annotations

from ui.orb.virtual_cursor_window import (
    _WS_EX_LAYERED,
    _WS_EX_NOACTIVATE,
    _WS_EX_TOOLWINDOW,
    _WS_EX_TRANSPARENT,
    _resolve_toplevel_hwnd,
)


class TestResolveToplevelHwnd:
    def test_prefers_the_wrapper_parent(self):
        assert _resolve_toplevel_hwnd(1001, get_parent=lambda h: 2002) == 2002

    def test_falls_back_to_inner_when_no_parent(self):
        assert _resolve_toplevel_hwnd(1001, get_parent=lambda h: 0) == 1001

    def test_falls_back_to_inner_when_probe_raises(self):
        def boom(_hwnd: int) -> int:
            raise OSError("no user32 on this host")

        assert _resolve_toplevel_hwnd(1001, get_parent=boom) == 1001

    def test_queries_the_inner_hwnd(self):
        seen: list[int] = []

        def record(hwnd: int) -> int:
            seen.append(hwnd)
            return 0

        _resolve_toplevel_hwnd(1001, get_parent=record)
        assert seen == [1001]


def test_click_through_style_bits_cover_input_and_activation():
    # The four extended styles are what makes the overlay input-invisible:
    # LAYERED+TRANSPARENT pass clicks through, NOACTIVATE never steals focus,
    # TOOLWINDOW keeps it out of alt-tab. Pin the winuser.h values so a typo
    # cannot silently disarm the overlay.
    assert _WS_EX_LAYERED == 0x00080000
    assert _WS_EX_TRANSPARENT == 0x00000020
    assert _WS_EX_TOOLWINDOW == 0x00000080
    assert _WS_EX_NOACTIVATE == 0x08000000

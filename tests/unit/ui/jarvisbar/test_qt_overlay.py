"""Focused regression tests for the Darwin Qt Jarvis Bar surface."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw

from jarvis.ui.jarvisbar import qt_overlay, renderer


class _Rect:
    """Small QRect-compatible test double."""

    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._values = (x, y, width, height)

    def x(self) -> int:
        return self._values[0]

    def y(self) -> int:
        return self._values[1]

    def width(self) -> int:
        return self._values[2]

    def height(self) -> int:
        return self._values[3]

    def contains(self, point: _Point) -> bool:
        x, y, width, height = self._values
        return x <= point.x() < x + width and y <= point.y() < y + height


class _Point:
    def __init__(self, x: int, y: int) -> None:
        self._x = x
        self._y = y

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y


def test_surface_state_is_safe_before_qt_is_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construction and pre-start bus updates must not import or touch Qt."""

    def fail_qt_import() -> Any:
        raise AssertionError("Qt was loaded before the surface started")

    monkeypatch.setattr(qt_overlay, "_qt", fail_qt_import)
    surface = qt_overlay.QtJarvisBarOverlay(
        persistent=True,
        startup_gated=True,
    )

    surface.show("think")
    surface.set_level(2.0)
    surface.set_muted(True)

    assert surface._mode == "think"  # noqa: SLF001
    assert surface._ext_level == 1.0  # noqa: SLF001
    assert surface._muted is True  # noqa: SLF001
    assert surface._desired_visible is False  # noqa: SLF001
    assert surface.release_startup_gate() is True
    assert surface._desired_visible is True  # noqa: SLF001
    assert surface.release_startup_gate() is False


def test_macos_qt_process_disables_foreground_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    monkeypatch.delenv("QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM", raising=False)

    qt_overlay._prepare_macos_qt_process()  # noqa: SLF001

    assert qt_overlay.os.environ["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] == "1"


def test_hidden_bottom_dock_restores_only_the_dock_strip() -> None:
    full = (0, 0, 1440, 900)
    available = (0, 25, 1440, 818)

    assert qt_overlay._dock_reserved_edge(full, available) == "bottom"  # noqa: SLF001
    assert qt_overlay._expand_geometry_for_hidden_dock(  # noqa: SLF001
        full,
        available,
    ) == (0, 25, 1440, 875)


@pytest.mark.parametrize(
    ("available", "edge", "expanded"),
    [
        ((60, 25, 1380, 875), "left", (0, 25, 1440, 875)),
        ((0, 25, 1380, 875), "right", (0, 25, 1440, 875)),
    ],
)
def test_hidden_side_dock_restores_only_its_reserved_edge(
    available: tuple[int, int, int, int],
    edge: str,
    expanded: tuple[int, int, int, int],
) -> None:
    full = (0, 0, 1440, 900)

    assert qt_overlay._dock_reserved_edge(full, available) == edge  # noqa: SLF001
    assert qt_overlay._expand_geometry_for_hidden_dock(  # noqa: SLF001
        full,
        available,
    ) == expanded


def test_macos_dock_visibility_uses_onscreen_windows_on_the_target_display(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dock_window = {
        "kCGWindowOwnerName": "Dock",
        "kCGWindowAlpha": 1.0,
        "kCGWindowBounds": {"X": 1440, "Y": 0, "Width": 1920, "Height": 1080},
    }
    quartz = SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListExcludeDesktopElements=2,
        kCGNullWindowID=0,
        CGWindowListCopyWindowInfo=lambda _options, _window_id: [dock_window],
    )
    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Quartz", quartz)

    assert qt_overlay._macos_dock_is_visible_on_screen((0, 0, 1440, 900)) is False  # noqa: SLF001
    assert qt_overlay._macos_dock_is_visible_on_screen((1440, 0, 1920, 1080)) is True  # noqa: SLF001


def test_macos_geometry_expands_only_while_dock_is_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Screen:
        @staticmethod
        def geometry() -> _Rect:
            return _Rect(0, 0, 1440, 900)

        @staticmethod
        def availableGeometry() -> _Rect:  # noqa: N802 - Qt-compatible double
            return _Rect(0, 25, 1440, 818)

    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    monkeypatch.setattr(
        qt_overlay,
        "_qt",
        lambda: SimpleNamespace(QtCore=SimpleNamespace(QRect=_Rect)),
    )
    surface = qt_overlay.QtJarvisBarOverlay()

    for probe_result in (True, None):
        monkeypatch.setattr(
            qt_overlay,
            "_macos_dock_is_visible_on_screen",
            lambda _screen, result=probe_result: result,
        )
        visible_or_unknown = surface._geometry_for_screen_ui(_Screen())  # noqa: SLF001
        assert qt_overlay._geometry_bounds(visible_or_unknown) == (  # noqa: SLF001
            0,
            25,
            1440,
            818,
        )

    monkeypatch.setattr(qt_overlay, "_macos_dock_is_visible_on_screen", lambda _screen: False)
    hidden = surface._geometry_for_screen_ui(_Screen())  # noqa: SLF001
    assert qt_overlay._geometry_bounds(hidden) == (0, 25, 1440, 875)  # noqa: SLF001


def test_non_macos_geometry_keeps_the_existing_available_area(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Screen:
        @staticmethod
        def geometry() -> _Rect:
            raise AssertionError("non-macOS placement must not inspect full geometry")

        @staticmethod
        def availableGeometry() -> _Rect:  # noqa: N802 - Qt-compatible double
            return _Rect(10, 20, 1280, 700)

    monkeypatch.setattr(qt_overlay.sys, "platform", "linux")
    monkeypatch.setattr(
        qt_overlay,
        "_macos_dock_is_visible_on_screen",
        lambda _screen: (_ for _ in ()).throw(
            AssertionError("non-macOS placement must not probe Quartz")
        ),
    )
    surface = qt_overlay.QtJarvisBarOverlay()

    geometry = surface._geometry_for_screen_ui(_Screen())  # noqa: SLF001

    assert qt_overlay._geometry_bounds(geometry) == (10, 20, 1280, 700)  # noqa: SLF001


def test_dynamic_position_retreats_for_dock_and_restores_user_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Window:
        def __init__(self) -> None:
            self.moves: list[tuple[int, int]] = []

        def move(self, x: int, y: int) -> None:
            self.moves.append((x, y))

    safe = _Rect(0, 25, 1440, 818)
    expanded = _Rect(0, 25, 1440, 875)
    preferred = (600, expanded.y() + expanded.height() - renderer.WIN_H - qt_overlay.MARGIN_PX)
    safe_position = (
        preferred[0],
        safe.y() + safe.height() - renderer.WIN_H - qt_overlay.MARGIN_PX,
    )
    active_geometry = [safe]
    window = _Window()
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._window = window  # type: ignore[assignment] # noqa: SLF001
    surface._preferred_position = preferred  # noqa: SLF001
    surface._x, surface._y = preferred  # noqa: SLF001
    monkeypatch.setattr(
        surface,
        "_screen_geometry_for_point_ui",
        lambda _x, _y, **_kwargs: active_geometry[0],
    )

    assert surface._reconcile_dynamic_position_ui() is True  # noqa: SLF001
    assert (surface._x, surface._y) == safe_position  # noqa: SLF001
    assert surface._preferred_position == preferred  # noqa: SLF001

    active_geometry[0] = expanded
    assert surface._reconcile_dynamic_position_ui() is True  # noqa: SLF001
    assert (surface._x, surface._y) == preferred  # noqa: SLF001
    assert window.moves == [safe_position, preferred]


def test_dynamic_position_does_not_move_during_drag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._preferred_position = (600, 850)  # noqa: SLF001
    surface._x, surface._y = 600, 794  # noqa: SLF001
    surface._drag = {"moved": True}  # noqa: SLF001
    monkeypatch.setattr(
        surface,
        "_screen_geometry_for_point_ui",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("screen geometry must not change an active drag")
        ),
    )

    assert surface._reconcile_dynamic_position_ui() is False  # noqa: SLF001


def test_position_persistence_keeps_preferred_location_when_dock_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[tuple[int, int]] = []
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._preferred_position = (600, 851)  # noqa: SLF001
    surface._x, surface._y = 600, 794  # noqa: SLF001
    monkeypatch.setattr(
        qt_overlay.interaction,
        "save_jarvisbar_position",
        lambda _path, x, y, rel=None: saved.append((x, y)),
    )

    surface._persist_position_ui()  # noqa: SLF001

    assert saved == [(600, 851)]


def test_macos_z_order_uses_native_nonactivating_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Window:
        def __init__(self) -> None:
            self.qt_raise_calls = 0

        def isVisible(self) -> bool:  # noqa: N802 - Qt-compatible double
            return True

        def raise_(self) -> None:
            self.qt_raise_calls += 1

    class _NativeWindow:
        def __init__(self) -> None:
            self.order_calls = 0

        def orderFrontRegardless(self) -> None:  # noqa: N802 - AppKit-compatible double
            self.order_calls += 1

    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    surface = qt_overlay.QtJarvisBarOverlay(startup_gated=False)
    window = _Window()
    native_window = _NativeWindow()
    surface._window = window  # type: ignore[assignment] # noqa: SLF001
    surface._native_window = native_window  # noqa: SLF001

    surface._raise_ui()  # noqa: SLF001

    assert native_window.order_calls == 1
    assert window.qt_raise_calls == 0


def test_macos_z_order_never_falls_back_to_focus_stealing_qt_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Window:
        def __init__(self) -> None:
            self.qt_raise_calls = 0

        def isVisible(self) -> bool:  # noqa: N802 - Qt-compatible double
            return True

        def raise_(self) -> None:
            self.qt_raise_calls += 1

    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    surface = qt_overlay.QtJarvisBarOverlay(startup_gated=False)
    window = _Window()
    surface._window = window  # type: ignore[assignment] # noqa: SLF001
    surface._native_window = None  # noqa: SLF001

    surface._raise_ui()  # noqa: SLF001

    assert window.qt_raise_calls == 0


def test_non_cocoa_qt_backend_never_enters_the_appkit_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _App:
        @staticmethod
        def platformName() -> str:  # noqa: N802 - Qt-compatible double
            return "offscreen"

    class _Window:
        @staticmethod
        def winId() -> int:  # noqa: N802 - Qt-compatible double
            raise AssertionError("offscreen winId must not be treated as an NSView")

    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._app = _App()  # type: ignore[assignment] # noqa: SLF001
    surface._window = _Window()  # type: ignore[assignment] # noqa: SLF001

    surface._configure_macos_nonactivating_window_ui()  # noqa: SLF001

    assert surface._native_window is None  # noqa: SLF001


def test_macos_native_panel_accepts_mouse_moves_without_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _App:
        @staticmethod
        def platformName() -> str:  # noqa: N802 - Qt-compatible double
            return "cocoa"

    class _Window:
        @staticmethod
        def winId() -> int:  # noqa: N802 - Qt-compatible double
            return 42

    class _NativeWindow:
        def __init__(self) -> None:
            self.accepts_mouse_moves = False

        @staticmethod
        def styleMask() -> int:  # noqa: N802 - AppKit-compatible double
            return 0

        def setStyleMask_(self, _mask: int) -> None: ...

        def setBecomesKeyOnlyIfNeeded_(self, _enabled: bool) -> None: ...

        def setHidesOnDeactivate_(self, _enabled: bool) -> None: ...

        def setAcceptsMouseMovedEvents_(self, enabled: bool) -> None:
            self.accepts_mouse_moves = enabled

    native_window = _NativeWindow()

    class _NativeView:
        @staticmethod
        def window() -> _NativeWindow:
            return native_window

    native_view = _NativeView()
    installed: dict[str, Any] = {}

    def _selector(callback: Any, **metadata: Any) -> Any:
        installed["callback"] = callback
        installed["metadata"] = metadata
        return SimpleNamespace(callback=callback, metadata=metadata)

    def _class_add_method(cls: type, name: bytes, method: Any) -> None:
        installed["class"] = cls
        installed["name"] = name
        installed["method"] = method

    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules,
        "objc",
        SimpleNamespace(
            objc_object=lambda **_kwargs: native_view,
            selector=_selector,
            classAddMethod=_class_add_method,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(NSWindowStyleMaskNonactivatingPanel=128),
    )
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._app = _App()  # type: ignore[assignment] # noqa: SLF001
    surface._window = _Window()  # type: ignore[assignment] # noqa: SLF001

    surface._configure_macos_nonactivating_window_ui()  # noqa: SLF001

    assert surface._native_window is native_window  # noqa: SLF001
    assert native_window.accepts_mouse_moves is True
    assert installed["class"] is _NativeView
    assert installed["name"] == b"acceptsFirstMouse:"
    assert installed["metadata"] == {
        "selector": b"acceptsFirstMouse:",
        "signature": b"c@:@",
    }
    assert installed["callback"](native_view, None) is True


def test_first_mouse_hook_failure_keeps_native_panel_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing PyObjC mutation seam must not disable the overlay panel."""

    class _App:
        @staticmethod
        def platformName() -> str:  # noqa: N802 - Qt-compatible double
            return "cocoa"

    class _Window:
        @staticmethod
        def winId() -> int:  # noqa: N802 - Qt-compatible double
            return 42

    class _NativeWindow:
        configured = False

        @staticmethod
        def styleMask() -> int:  # noqa: N802 - AppKit-compatible double
            return 0

        def setStyleMask_(self, _mask: int) -> None:
            self.configured = True

        def setBecomesKeyOnlyIfNeeded_(self, _enabled: bool) -> None: ...

        def setHidesOnDeactivate_(self, _enabled: bool) -> None: ...

        def setAcceptsMouseMovedEvents_(self, _enabled: bool) -> None: ...

    native_window = _NativeWindow()
    native_view = SimpleNamespace(window=lambda: native_window)
    monkeypatch.setattr(qt_overlay.sys, "platform", "darwin")
    monkeypatch.setitem(
        sys.modules,
        "objc",
        SimpleNamespace(
            objc_object=lambda **_kwargs: native_view,
            selector=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("method injection unavailable")
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(NSWindowStyleMaskNonactivatingPanel=128),
    )
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._app = _App()  # type: ignore[assignment] # noqa: SLF001
    surface._window = _Window()  # type: ignore[assignment] # noqa: SLF001

    surface._configure_macos_nonactivating_window_ui()  # noqa: SLF001

    assert surface._native_window is native_window  # noqa: SLF001
    assert native_window.configured is True


@pytest.mark.parametrize("mode", ["idle", "listen", "think", "speak"])
def test_hover_poll_tracks_distinct_mouse_out_and_mouse_over_states(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    class _Mask:
        @staticmethod
        def contains(point: _Point) -> bool:
            center_x = renderer.WIN_W // 2
            center_y = round(renderer.pill_center_y(renderer.COLLAPSED_H))
            return _Rect(center_x - 5, center_y - 3, 10, 6).contains(point)

    class _Window:
        def __init__(self) -> None:
            self.updates = 0

        @staticmethod
        def isVisible() -> bool:  # noqa: N802 - Qt-compatible double
            return True

        @staticmethod
        def mapFromGlobal(point: _Point) -> _Point:  # noqa: N802 - Qt-compatible double
            return point

        @staticmethod
        def rect() -> _Rect:
            return _Rect(0, 0, renderer.WIN_W, renderer.WIN_H)

        def update(self) -> None:
            self.updates += 1

    center_x = renderer.WIN_W // 2
    center_y = round(renderer.pill_center_y(renderer.COLLAPSED_H))
    cursor = [_Point(center_x, center_y)]
    monkeypatch.setattr(
        qt_overlay,
        "_qt",
        lambda: SimpleNamespace(
            QtGui=SimpleNamespace(
                QCursor=SimpleNamespace(pos=lambda: cursor[0]),
            )
        ),
    )
    window = _Window()
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._mode = mode  # noqa: SLF001
    surface._window = window  # type: ignore[assignment] # noqa: SLF001
    surface._input_mask = _Mask()  # type: ignore[assignment] # noqa: SLF001

    surface._poll_hover_ui()  # noqa: SLF001
    assert surface._hovered is True  # noqa: SLF001

    # A point outside the collapsed acquisition mask but inside the stable
    # hovered pill must survive Cocoa's spurious Leave during setMask().
    hovered_w, hovered_h = renderer.target_pill_size(mode, True)
    cursor[0] = _Point(
        round(center_x - hovered_w / 2.0 + 3),
        round(renderer.pill_center_y(hovered_h)),
    )
    surface._hover_leave_ui()  # noqa: SLF001
    assert surface._hovered is True  # noqa: SLF001

    # Transparent fixed-window padding is not the bar: leaving the hovered
    # pill must restore the non-hover state in every voice mode.
    cursor[0] = _Point(0, 0)
    surface._poll_hover_ui()  # noqa: SLF001
    assert surface._hovered is False  # noqa: SLF001
    assert window.updates == 2

    # Clear padding cannot acquire hover from the collapsed state.
    cursor[0] = _Point(0, 0)
    surface._poll_hover_ui()  # noqa: SLF001
    assert surface._hovered is False  # noqa: SLF001


def test_hover_poll_clears_stale_state_when_surface_is_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._hovered = True  # noqa: SLF001
    surface._desired_visible = False  # noqa: SLF001
    monkeypatch.setattr(
        surface,
        "_pointer_over_bar_ui",
        lambda: (_ for _ in ()).throw(
            AssertionError("hidden surface must not inspect the cursor")
        ),
    )

    surface._poll_hover_ui()  # noqa: SLF001

    assert surface._hovered is False  # noqa: SLF001


def test_clicks_are_forwarded_to_parent_owned_callbacks() -> None:
    """The companion surface must not look for a child-local speech pipeline."""
    surface = qt_overlay.QtJarvisBarOverlay()
    voice_actions: list[str] = []
    mute_toggles: list[bool] = []
    surface.set_on_voice_action(voice_actions.append)
    surface.set_on_mute_toggle(lambda: mute_toggles.append(True))

    surface._mode = "idle"  # noqa: SLF001
    assert surface._dispatch_click_ui(renderer.WIN_W / 2) == "talk"  # noqa: SLF001

    surface._mode = "listen"  # noqa: SLF001
    assert surface._dispatch_click_ui(renderer.WIN_W * 0.8) == "mute"  # noqa: SLF001

    close_x = renderer.WIN_W / 2 - 0.42 * renderer.ACTIVE_W
    assert surface._dispatch_click_ui(close_x, hovered=True) == "hangup"  # noqa: SLF001

    assert voice_actions == ["talk", "hangup"]
    assert mute_toggles == [True]
    assert surface._muted is True  # noqa: SLF001
    assert surface._mode == "idle"  # noqa: SLF001


def test_full_frame_paint_clears_black_backing_and_previous_pixels() -> None:
    """Transparent pixels must replace, rather than blend over, old content."""
    pytest.importorskip("PySide6")
    q = qt_overlay._qt()  # noqa: SLF001
    size = 16
    target = q.QtGui.QImage(
        size,
        size,
        q.QtGui.QImage.Format.Format_RGBA8888,
    )
    target.fill(q.QtGui.QColor(0, 0, 0, 255))

    first = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(first).rectangle((5, 5, 10, 10), fill=(231, 196, 110, 255))
    painter = q.QtGui.QPainter(target)
    qt_overlay._paint_transparent_frame(  # noqa: SLF001
        painter,
        target.rect(),
        qt_overlay._qimage_from_pil(first),  # noqa: SLF001
    )
    painter.end()

    assert target.pixelColor(0, 0).alpha() == 0
    assert target.pixelColor(7, 7).alpha() == 255

    second = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    painter = q.QtGui.QPainter(target)
    qt_overlay._paint_transparent_frame(  # noqa: SLF001
        painter,
        target.rect(),
        qt_overlay._qimage_from_pil(second),  # noqa: SLF001
    )
    painter.end()

    assert target.pixelColor(0, 0).alpha() == 0
    assert target.pixelColor(7, 7).alpha() == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin Qt input-mask regression")
def test_transparent_frame_pixels_are_excluded_from_the_input_mask() -> None:
    """Invisible window padding must pass clicks to the app underneath."""
    pytest.importorskip("PySide6")
    q = qt_overlay._qt()  # noqa: SLF001
    app = q.QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = q.QtWidgets.QApplication(["jarvisbar-input-mask-test"])

    frame = Image.new("RGBA", (16, 12), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle((5, 4, 10, 8), fill=(231, 196, 110, 255))
    mask = qt_overlay._input_mask_from_frame(  # noqa: SLF001
        qt_overlay._qimage_from_pil(frame),  # noqa: SLF001
    )

    assert mask.contains(q.QtCore.QPoint(5, 4))
    assert mask.contains(q.QtCore.QPoint(10, 8))
    assert not mask.contains(q.QtCore.QPoint(0, 0))
    assert not mask.contains(q.QtCore.QPoint(15, 11))
    if owns_app:
        app.quit()


def test_render_applies_the_frame_input_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every eased geometry frame must update the native hit-test shape."""
    surface = qt_overlay.QtJarvisBarOverlay()
    surface._renderer = renderer.JarvisBarRenderer()  # noqa: SLF001
    surface._t0 = 0.0  # noqa: SLF001
    qimage = object()
    input_mask = object()

    class _Window:
        def __init__(self) -> None:
            self.masks: list[object] = []
            self.updates = 0

        def setMask(self, mask: object) -> None:  # noqa: N802 - Qt-compatible double
            self.masks.append(mask)

        def update(self) -> None:
            self.updates += 1

    window = _Window()
    surface._window = window  # type: ignore[assignment] # noqa: SLF001
    monkeypatch.setattr(qt_overlay, "_qimage_from_pil", lambda _image: qimage)
    monkeypatch.setattr(
        qt_overlay,
        "_input_mask_from_frame",
        lambda image: input_mask if image is qimage else None,
    )

    surface._render_frame_ui()  # noqa: SLF001
    surface._render_frame_ui()  # noqa: SLF001

    assert window.masks == [input_mask]
    assert window.updates == 2


def test_qt_surface_preserves_the_overlay_contract() -> None:
    surface = qt_overlay.QtJarvisBarOverlay()
    methods = {
        "hide",
        "hide_comment",
        "play_animation",
        "reassert_z_order",
        "release_startup_gate",
        "set_feedback_publisher",
        "set_level",
        "set_muted",
        "set_on_mute_toggle",
        "set_on_show_window",
        "set_on_voice_action",
        "show",
        "show_listening_transcript",
        "start",
        "start_in_thread",
        "start_mouth_animation",
        "stop",
        "stop_animation",
        "stop_mouth_animation",
    }

    assert all(callable(getattr(surface, name, None)) for name in methods)

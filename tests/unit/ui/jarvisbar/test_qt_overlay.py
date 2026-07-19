"""Focused regression tests for the Darwin Qt Jarvis Bar surface."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from PIL import Image, ImageDraw

from jarvis.ui.jarvisbar import qt_overlay, renderer


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

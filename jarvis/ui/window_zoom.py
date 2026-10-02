"""Zoom a desktop window's whole page, the way Ctrl + `+` zooms a browser.

The page asks for a zoom factor; this module hands it to the WebView engine
that draws the window. That is real page zoom, not a CSS trick: the engine
shrinks the CSS viewport, so `100vh` layouts, popovers and pointer coordinates
all stay correct at every level.

pywebview does not expose zoom, so each engine is reached through the native
view pywebview keeps per window (``BrowserView.instances[uid]``). Only the
platform module that is already running is looked at — nothing here imports a
GUI toolkit, so a headless server never pays for one:

* Windows (WebView2): ``ZoomFactor`` on the WinForms control, set on the UI
  thread through ``Form.Invoke``.
* macOS (WKWebView): ``setPageZoom:`` (macOS 11+), queued on the main thread.
* Linux (WebKitGTK): ``set_zoom_level``, queued on the GLib main loop.

Anything else (the Qt backend, a browser tab, a headless host) answers
``ok: false`` with a reason; the page then leaves zoom to the browser.
"""

from __future__ import annotations

import math
import sys
from typing import Any

from loguru import logger

ZOOM_MIN = 0.5
ZOOM_MAX = 3.0


def clamp_zoom(factor: float) -> float:
    """Keep a requested factor inside the range every engine accepts."""
    if not math.isfinite(factor):
        return 1.0
    return round(min(ZOOM_MAX, max(ZOOM_MIN, factor)), 3)


def _browser_view(module_name: str, window: Any) -> Any | None:
    """The native view pywebview keeps for ``window`` in a loaded platform module."""
    module = sys.modules.get(module_name)
    if module is None:
        return None
    view_cls = getattr(module, "BrowserView", None)
    instances = getattr(view_cls, "instances", None)
    if not isinstance(instances, dict):
        return None
    return instances.get(getattr(window, "uid", None))


def _zoom_winforms(form: Any, factor: float) -> bool:
    webview = getattr(form, "webview", None)
    # The legacy MSHTML control has no ZoomFactor; only WebView2 does. Asked of
    # the CLASS: reading the property on the instance is a COM call, and off the
    # UI thread it fails with E_NOINTERFACE.
    if webview is None or not hasattr(type(webview), "ZoomFactor"):
        return False
    from System import (  # type: ignore[import-not-found]  # pythonnet, loaded by pywebview
        Func,
        Type,
    )

    def _apply() -> None:
        webview.ZoomFactor = factor

    if getattr(form, "InvokeRequired", False):
        form.Invoke(Func[Type](_apply))
    else:
        _apply()
    return True


def _zoom_cocoa(view: Any, factor: float) -> bool:
    webview = getattr(view, "webview", None)
    # pageZoom arrived with macOS 11; older systems only have pinch magnify,
    # which does not reflow the page and so is not offered.
    if webview is None or not webview.respondsToSelector_("setPageZoom:"):
        return False
    from PyObjCTools import AppHelper  # type: ignore[import-not-found]

    AppHelper.callAfter(webview.setPageZoom_, factor)
    return True


def _zoom_gtk(view: Any, factor: float) -> bool:
    webview = getattr(view, "webview", None)
    if webview is None or not hasattr(webview, "set_zoom_level"):
        return False
    from gi.repository import GLib  # type: ignore[import-not-found]

    def _apply() -> bool:
        webview.set_zoom_level(factor)
        return False  # run once

    GLib.idle_add(_apply)
    return True


_ENGINES = (
    ("webview.platforms.winforms", _zoom_winforms),
    ("webview.platforms.cocoa", _zoom_cocoa),
    ("webview.platforms.gtk", _zoom_gtk),
)


def set_window_zoom(window: Any, factor: float) -> dict[str, Any]:
    """Zoom ``window``'s page to ``factor`` (1.0 = 100 %). Worker-thread safe."""
    if window is None:
        return {"ok": False, "reason": "no_window"}
    zoom = clamp_zoom(factor)
    for module_name, apply in _ENGINES:
        view = _browser_view(module_name, window)
        if view is None:
            continue
        try:
            if apply(view, zoom):
                return {"ok": True, "zoom": zoom}
        except Exception:  # noqa: BLE001 — reported to the page, which falls back
            logger.exception("Window zoom failed on {}", module_name)
            return {"ok": False, "reason": "zoom_failed"}
        return {"ok": False, "reason": "zoom_unsupported"}
    return {"ok": False, "reason": "zoom_unsupported"}

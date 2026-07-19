"""Native OS file drag-source — drag a REAL file out of the desktop WebView.

The desktop UI runs inside a WebView (pywebview: WebView2 on Windows, WKWebView
on macOS, WebKitGTK on Linux). A WebView cannot drag a real file OUT of the
window via HTML5 drag-and-drop, and pywebview ships no drag-out API
(r0x0r/pywebview#877, #1192). So we start the native OS drag ourselves.

**The one thing that makes this work (and the reason a naive attempt fails):**
the native drag must begin on the WebView host's UI thread while the physical
mouse button is still down. pywebview's ``js_api`` bridge runs exposed methods
on a worker thread, so it cannot take over the in-progress press. The frontend
therefore uses each engine's raw script-message bridge on ``mousedown``:
WebView2's ``window.chrome.webview`` on Windows and WKWebView's
``window.webkit.messageHandlers.jarvisFileDrag`` on macOS.

Cross-platform, capability-gated, fail-closed to a logged no-op:
* Windows: monkeypatch ``EdgeChrome.on_script_notify`` → WinForms ``DataObject``
  (which packs CF_HDROP for us) → ``webview.DoDragDrop(..., Copy)``.
* macOS: register a dedicated WKScriptMessage handler → ``NSDraggingItem``
  carrying a real file URL → ``beginDraggingSessionWithItems``.
* Linux: GTK drag-source seam — honest no-op until wired.

Path safety (AP-2 spirit): a path posted by the renderer is dragged only if it
resolves to an existing regular file inside an explicit allow-list of base dirs
(the caller passes ``~/Downloads`` etc.), so a compromised page cannot exfiltrate
arbitrary files.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Discriminator the frontend puts as the first element of its postMessage array.
# Kept in sync with the frontend helper (src/lib/nativeDrag.ts).
MESSAGE_TAG = "jarvis-file-drag"

# Windows DROPEFFECT: COPY (never MOVE — the source download must stay put).
_DROPEFFECT_COPY = 1

# A dedicated WKWebView handler appears in JavaScript only when the AppKit bridge
# registered successfully. That keeps frontend capability detection truthful;
# the normal pywebview js_api bridge deliberately hops to a worker thread.
MACOS_MESSAGE_HANDLER = "jarvisFileDrag"

# A WK script message normally arrives synchronously from the DOM mousedown.
# Keep a small allowance for WebKit dispatch without ever reusing an old click.
_MACOS_MOUSE_EVENT_MAX_AGE_S = 1.0
_MACOS_DRAG_ICON_SIZE = 32.0


def _resolve_bases(allowed_base_dirs: Sequence[Path] | None) -> list[Path] | None:
    if allowed_base_dirs is None:
        return None
    bases: list[Path] = []
    for b in allowed_base_dirs:
        try:
            bases.append(Path(b).resolve())
        except OSError:
            continue
    return bases


def _validate_paths(paths: Sequence[str], bases: list[Path] | None) -> list[str]:
    """Return the absolute existing regular files that pass the allow-list.

    A path survives only if it resolves to an existing regular file and — when
    ``bases`` is given — sits inside one of them. Returned as native OS path
    strings (back-slashes on Windows) for the drag data object.
    """
    out: list[str] = []
    for raw in paths:
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if not p.is_file():
            log.debug("native_drag: not a regular file: %r", raw)
            continue
        if bases is not None and not any(
            p == base or base in p.parents for base in bases
        ):
            log.debug("native_drag: %s outside allow-list — skipped", p)
            continue
        out.append(str(p))
    return out


def _drag_files_from_message(message: object) -> list[str] | None:
    """Extract our file list, or return ``None`` when a message is not ours."""
    data = message
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return None

    if (
        not isinstance(data, Sequence)
        or isinstance(data, (str, bytes, bytearray))
        or len(data) < 2
        or data[0] != MESSAGE_TAG
        or not isinstance(data[1], Mapping)
    ):
        return None

    raw_files = data[1].get("files", [])
    if not isinstance(raw_files, Sequence) or isinstance(
        raw_files, (str, bytes, bytearray)
    ):
        return []
    return [str(path) for path in raw_files]


def install_native_drag(allowed_base_dirs: Sequence[Path] | None = None) -> bool:
    """Wire native file drag-out into the desktop WebView. Call once before
    ``webview.start()``. Returns True if a drag source was installed for this OS.

    Windows and macOS have native sources. Linux returns False (honest no-op)
    until its GTK seam lands. Never raises — a missing dependency or unexpected
    pywebview internal just disables the feature and logs.
    """
    if sys.platform == "win32":
        return _install_windows_drag(allowed_base_dirs)
    if sys.platform == "darwin":
        return _install_macos_drag(allowed_base_dirs)
    # Phase 2: _install_linux_drag.
    log.info("native_drag: no drag source for platform %s yet", sys.platform)
    return False


def _install_windows_drag(allowed_base_dirs: Sequence[Path] | None) -> bool:
    """Monkeypatch pywebview's WebView2 message handler to start an OLE file drag
    on the UI thread when the frontend posts a ``[MESSAGE_TAG, {files}]`` message.
    """
    try:
        import clr  # type: ignore[import-not-found]

        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Collections")
        import System.Windows.Forms as WinForms  # type: ignore[import-not-found] # noqa: N813
        import webview.platforms.edgechromium as ec
        from System import Array  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — .NET/pywebview internals absent
        log.warning("native_drag: WebView2 drag bridge unavailable (%s)", exc)
        return False

    if getattr(ec.EdgeChrome, "_jarvis_drag_installed", False):
        return True  # idempotent across restarts / duplicate calls

    bases = _resolve_bases(allowed_base_dirs)
    original = ec.EdgeChrome.on_script_notify

    def _patched_on_script_notify(self, sender, args):  # noqa: ANN001, ANN202
        # Decide FIRST whether this is our drag message; anything else (including a
        # parse error) falls through UNTOUCHED to pywebview's original handler so
        # the js_api bridge / drop-in feature keep working.
        try:
            data = json.loads(args.get_WebMessageAsJson())
            raw_files = _drag_files_from_message(data)
        except Exception:  # noqa: BLE001 — not JSON / not ours
            raw_files = None

        if raw_files is None:
            return original(self, sender, args)

        # Our drag message — handled on the UI thread (this callback). Any failure
        # is contained here; we never re-dispatch to the original for a drag msg.
        try:
            paths = _validate_paths(raw_files, bases)
            if paths:
                obj = WinForms.DataObject(
                    WinForms.DataFormats.FileDrop, Array[str](paths)
                )
                # THE fix: DoDragDrop on the WebView2 UI thread, mouse still down.
                self.webview.DoDragDrop(obj, WinForms.DragDropEffects.Copy)
        except Exception:  # noqa: BLE001 — a drag must never crash the UI thread
            log.exception("native_drag: DoDragDrop failed")
        return None

    ec.EdgeChrome.on_script_notify = _patched_on_script_notify  # type: ignore[method-assign]
    ec.EdgeChrome._jarvis_drag_installed = True  # type: ignore[attr-defined]
    log.info("native_drag: WebView2 file drag-out installed")
    return True


def _begin_macos_drag(
    webview: Any,
    paths: Sequence[str],
    event: Any,
    source: Any,
    *,
    appkit: Any,
    foundation: Any,
) -> bool:
    """Start one AppKit copy drag containing existing file URLs."""
    try:
        location = webview.convertPoint_fromView_(event.locationInWindow(), None)
        workspace = appkit.NSWorkspace.sharedWorkspace()
        items = []
        for index, path in enumerate(paths):
            url = foundation.NSURL.fileURLWithPath_(path)
            item = appkit.NSDraggingItem.alloc().initWithPasteboardWriter_(url)
            icon = workspace.iconForFile_(path)
            offset = min(index, 4) * 3.0
            frame = appkit.NSMakeRect(
                float(location.x) - (_MACOS_DRAG_ICON_SIZE / 2.0) + offset,
                float(location.y) - (_MACOS_DRAG_ICON_SIZE / 2.0) - offset,
                _MACOS_DRAG_ICON_SIZE,
                _MACOS_DRAG_ICON_SIZE,
            )
            item.setDraggingFrame_contents_(frame, icon)
            items.append(item)

        if not items:
            return False
        session = webview.beginDraggingSessionWithItems_event_source_(
            items,
            event,
            source,
        )
        if session is not None:
            session.setAnimatesToStartingPositionsOnCancelOrFail_(True)
        return session is not None
    except Exception:  # noqa: BLE001 — a drag must never crash AppKit's main thread
        log.exception("native_drag: AppKit dragging session failed")
        return False


def _install_macos_drag(allowed_base_dirs: Sequence[Path] | None) -> bool:
    """Patch pywebview's WKWebView bridge to start a native AppKit file drag."""
    try:
        import AppKit  # type: ignore[import-not-found, import-untyped] # noqa: N813, PLC0415
        import Foundation  # type: ignore[import-not-found, import-untyped] # noqa: N813, PLC0415
        import objc  # type: ignore[import-not-found, import-untyped] # noqa: PLC0415
        import webview.platforms.cocoa as cocoa  # type: ignore[import-untyped] # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional desktop internals absent
        log.warning("native_drag: WKWebView drag bridge unavailable (%s)", exc)
        return False

    browser_class = cocoa.BrowserView
    host_class = browser_class.WebKitHost
    if getattr(host_class, "_jarvis_drag_installed", False):
        return True

    try:
        dragging_source_protocol = objc.protocolNamed("NSDraggingSource")

        class JarvisFileDragSource(  # type: ignore[call-arg] # noqa: D101
            AppKit.NSObject,
            protocols=[dragging_source_protocol],
        ):
            def draggingSession_sourceOperationMaskForDraggingContext_(
                self,
                _session: Any,
                _context: int,
            ) -> int:
                return int(AppKit.NSDragOperationCopy)

        bases = _resolve_bases(allowed_base_dirs)
        source = JarvisFileDragSource.alloc().init()
        original_mouse_down = host_class.mouseDown_
        original_browser_init = browser_class.__init__

        def _patched_mouse_down(self: Any, event: Any) -> Any:
            self._jarvis_drag_mouse_down = (event, time.monotonic())
            return original_mouse_down(self, event)

        script_message_protocol = objc.protocolNamed("WKScriptMessageHandler")

        class JarvisFileDragMessageHandler(  # type: ignore[call-arg] # noqa: D101
            AppKit.NSObject,
            protocols=[script_message_protocol],
        ):
            def userContentController_didReceiveScriptMessage_(
                self,
                _controller: Any,
                message: Any,
            ) -> None:
                raw_files = _drag_files_from_message(message.body())
                if raw_files is None:
                    return

                try:
                    webview = message.webView()
                    mouse_state = getattr(webview, "_jarvis_drag_mouse_down", None)
                    webview._jarvis_drag_mouse_down = None
                    if not mouse_state:
                        return
                    event, started_at = mouse_state
                    if (
                        time.monotonic() - float(started_at)
                        > _MACOS_MOUSE_EVENT_MAX_AGE_S
                    ):
                        return
                    if not (int(AppKit.NSEvent.pressedMouseButtons()) & 1):
                        return

                    paths = _validate_paths(raw_files, bases)
                    if paths:
                        _begin_macos_drag(
                            webview,
                            paths,
                            event,
                            source,
                            appkit=AppKit,
                            foundation=Foundation,
                        )
                except Exception:  # noqa: BLE001 — contain failures on AppKit's UI thread
                    log.exception("native_drag: WKWebView drag message failed")

        def _patched_browser_init(self: Any, window: Any) -> None:
            original_browser_init(self, window)
            try:
                handler = JarvisFileDragMessageHandler.alloc().init()
                self.webview.configuration().userContentController().addScriptMessageHandler_name_(
                    handler,
                    MACOS_MESSAGE_HANDLER,
                )
                # WKUserContentController retains the handler; this explicit
                # reference also keeps its lifetime obvious across pywebview versions.
                self._jarvis_drag_message_handler = handler
            except Exception:  # noqa: BLE001 — window creation must still succeed
                log.exception("native_drag: WKWebView handler registration failed")

        host_class.mouseDown_ = _patched_mouse_down  # type: ignore[method-assign]
        browser_class.__init__ = _patched_browser_init  # type: ignore[method-assign]
        # Retain both the source and original methods for the full process and
        # make duplicate installer calls idempotent.
        host_class._jarvis_drag_source = source
        host_class._jarvis_drag_original_mouse_down = original_mouse_down
        browser_class._jarvis_drag_original_init = original_browser_init  # type: ignore[attr-defined]
        host_class._jarvis_drag_installed = True
    except Exception as exc:  # noqa: BLE001 — pywebview/AppKit internals changed
        log.warning("native_drag: WKWebView drag bridge unavailable (%s)", exc)
        return False

    log.info("native_drag: WKWebView file drag-out installed")
    return True

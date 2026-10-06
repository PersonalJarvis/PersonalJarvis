"""Let the page see Cmd+W on macOS, so it closes an editor tab, not the app.

pywebview's WKWebView host (``webview.platforms.cocoa.BrowserView.WebKitHost``)
answers a handful of Command shortcuts itself before the page sees the key:
Cmd+W calls ``performClose_`` on the window. Inside the Agentic IDE's code
editor, Cmd+W means "close this tab" (as Ctrl+W does on Windows and Linux), and
closing the whole Jarvis window instead is the opposite of what was asked.

This module wraps the host's ``keyDown_`` so Cmd+W goes to the web content
like any other key; every other shortcut keeps pywebview's handling. When no
editor tab takes the shortcut, the page posts ``"close-window"`` to the
``jarvisWindow`` script-message handler registered here, and the window
closes as before. Cmd+Z is left alone on purpose: pywebview sends it to the
native undo manager, which WebKit turns into an ``historyUndo`` input event
the editor already honours.

macOS only. On every other platform, or when the pywebview/AppKit internals
are not what this expects, installing is a logged no-op — the shortcut then
simply keeps its old meaning. Not verifiable on a non-Mac machine; see
``docs/code-editor.md``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["MESSAGE_HANDLER", "install_macos_editor_keys", "page_owns_shortcut"]

#: Command shortcuts the page handles itself instead of the window.
_PAGE_KEYS = frozenset({"w"})
#: Script-message handler the page uses to hand Cmd+W back to the window.
MESSAGE_HANDLER = "jarvisWindow"
_CLOSE_MESSAGE = "close-window"


def page_owns_shortcut(command: bool, characters: str) -> bool:
    """True when a Command shortcut must reach the page, not the window."""
    return command and characters.lower() in _PAGE_KEYS


def install_macos_editor_keys() -> bool:
    """Patch the WKWebView host once; returns whether the patch is active."""
    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # type: ignore[import-not-found, import-untyped] # noqa: N813, PLC0415
        import objc  # type: ignore[import-not-found, import-untyped] # noqa: PLC0415
        import webview.platforms.cocoa as cocoa  # type: ignore[import-untyped] # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 — optional desktop internals absent
        log.warning("macos_editor_keys: WKWebView host unavailable (%s)", exc)
        return False

    browser_class = cocoa.BrowserView
    host_class = browser_class.WebKitHost
    if getattr(host_class, "_jarvis_editor_keys_installed", False):
        return True
    try:
        original_key_down = host_class.keyDown_
        original_browser_init = browser_class.__init__
        script_message_protocol = objc.protocolNamed("WKScriptMessageHandler")

        class JarvisWindowMessageHandler(  # type: ignore[call-arg] # noqa: D101
            AppKit.NSObject,
            protocols=[script_message_protocol],
        ):
            def userContentController_didReceiveScriptMessage_(
                self, _controller: Any, message: Any
            ) -> None:
                try:
                    if str(message.body()) != _CLOSE_MESSAGE:
                        return
                    window = message.webView().window()
                    if window is not None:
                        window.performClose_(None)
                except Exception:  # noqa: BLE001 — never raise on AppKit's thread
                    log.exception("macos_editor_keys: close request failed")

        def _patched_browser_init(self: Any, window: Any) -> None:
            original_browser_init(self, window)
            try:
                handler = JarvisWindowMessageHandler.alloc().init()
                controller = self.webview.configuration().userContentController()
                controller.addScriptMessageHandler_name_(handler, MESSAGE_HANDLER)
                self._jarvis_window_message_handler = handler
            except Exception:  # noqa: BLE001 — window creation must still succeed
                log.exception("macos_editor_keys: close handler registration failed")

        def _patched_key_down(self: Any, event: Any) -> Any:
            try:
                command = bool(event.modifierFlags() & AppKit.NSCommandKeyMask)
                characters = str(event.charactersIgnoringModifiers() or "")
                if page_owns_shortcut(command, characters):
                    # WKWebView's own keyDown_ hands the event to the page.
                    return super(host_class, self).keyDown_(event)
            except Exception:  # noqa: BLE001 — never break typing on AppKit's thread
                log.exception("macos_editor_keys: key routing failed; using the default")
            return original_key_down(self, event)

        host_class.keyDown_ = _patched_key_down  # type: ignore[method-assign]
        browser_class.__init__ = _patched_browser_init  # type: ignore[method-assign]
        host_class._jarvis_editor_keys_original = original_key_down
        browser_class._jarvis_editor_keys_original_init = original_browser_init  # type: ignore[attr-defined]
        host_class._jarvis_editor_keys_installed = True
    except Exception as exc:  # noqa: BLE001 — pywebview internals changed
        log.warning("macos_editor_keys: could not route Cmd+W to the page (%s)", exc)
        return False
    log.info("macos_editor_keys: Cmd+W now reaches the page")
    return True

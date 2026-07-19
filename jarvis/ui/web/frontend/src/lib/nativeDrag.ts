/**
 * Native OS file drag-out from the desktop shell.
 *
 * A WebView cannot drag a real file out via HTML5 drag-and-drop, so we hand the
 * drag to the native side: on `mousedown` (button still down) we post through
 * WebView2 on Windows or WKWebView on macOS. pywebview delivers either message
 * on the UI thread, where `jarvis/ui/native_drag.py` starts the platform drag
 * session and hands a real file URL to Explorer, Finder, browser upload zones,
 * chats, and other native targets.
 *
 * Only available inside a supported desktop shell; `canNativeDrag()` is false
 * in a plain browser, on Linux until its GTK seam lands, and on a headless VPS,
 * where the UI falls back to the "Show in folder" action instead.
 */

/** Discriminator — MUST match `MESSAGE_TAG` in jarvis/ui/native_drag.py. */
const DRAG_MESSAGE_TAG = "jarvis-file-drag";
/** Handler name — MUST match `MACOS_MESSAGE_HANDLER` in native_drag.py. */
const MACOS_MESSAGE_HANDLER = "jarvisFileDrag";

interface NativeMessageBridge {
  postMessage: (message: unknown) => void;
}

function bridge(): NativeMessageBridge | undefined {
  const host = window as unknown as {
    chrome?: { webview?: NativeMessageBridge };
    webkit?: {
      messageHandlers?: Record<string, NativeMessageBridge | undefined>;
    };
  };
  const webView2 = host.chrome?.webview;
  if (webView2 && typeof webView2.postMessage === "function") return webView2;

  const wkWebView = host.webkit?.messageHandlers?.[MACOS_MESSAGE_HANDLER];
  return wkWebView && typeof wkWebView.postMessage === "function"
    ? wkWebView
    : undefined;
}

/** True in a desktop shell that exposes a native file-drag message bridge. */
export function canNativeDrag(): boolean {
  return bridge() !== undefined;
}

/**
 * Begin a native OS file drag of `path`. Call from a `mousedown` handler while
 * the button is held. No-op (returns false) outside the desktop shell.
 */
export function startNativeFileDrag(path: string): boolean {
  const nativeBridge = bridge();
  if (!nativeBridge || !path) return false;
  nativeBridge.postMessage([DRAG_MESSAGE_TAG, { files: [path] }]);
  return true;
}

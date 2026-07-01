/**
 * Native OS file drag-out from the desktop WebView2 shell.
 *
 * A WebView cannot drag a real file out via HTML5 drag-and-drop, so we hand the
 * drag to the native side: on `mousedown` (button still down) we post a raw
 * WebView2 message. pywebview delivers it to `EdgeChrome.on_script_notify` ON THE
 * UI THREAD, where `jarvis/ui/native_drag.py` starts an OLE `DoDragDrop` that
 * takes over the in-progress mouse press. From there the OS carries a real file
 * to any target — Explorer, a browser upload zone, a chat. See native_drag.py.
 *
 * Only available inside the desktop WebView2 shell; `canNativeDrag()` is false in
 * a plain browser / on the VPS, where the UI falls back to the "Show in folder"
 * action instead.
 */

/** Discriminator — MUST match `MESSAGE_TAG` in jarvis/ui/native_drag.py. */
const DRAG_MESSAGE_TAG = "jarvis-file-drag";

interface WebView2Bridge {
  postMessage: (message: unknown) => void;
}

function bridge(): WebView2Bridge | undefined {
  const chrome = (window as { chrome?: { webview?: WebView2Bridge } }).chrome;
  const wv = chrome?.webview;
  return wv && typeof wv.postMessage === "function" ? wv : undefined;
}

/** True in the desktop WebView2 shell, where a native file drag is possible. */
export function canNativeDrag(): boolean {
  return bridge() !== undefined;
}

/**
 * Begin a native OS file drag of `path`. Call from a `mousedown` handler while
 * the button is held. No-op (returns false) outside the desktop shell.
 */
export function startNativeFileDrag(path: string): boolean {
  const wv = bridge();
  if (!wv || !path) return false;
  wv.postMessage([DRAG_MESSAGE_TAG, { files: [path] }]);
  return true;
}

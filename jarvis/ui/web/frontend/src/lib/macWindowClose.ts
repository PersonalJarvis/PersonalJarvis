import { useEffect } from "react";

/**
 * macOS desktop app: Cmd+W reaches the page (jarvis/ui/macos_editor_keys.py)
 * so the code editor can close a tab with it. When nothing in the page took
 * the shortcut, hand it back to the native window, which closes as before.
 *
 * A no-op everywhere else: the message handler only exists in the macOS
 * WKWebView host.
 */
interface WindowMessageHandler {
  postMessage: (message: string) => void;
}

function nativeWindowHandler(): WindowMessageHandler | undefined {
  const webkit = (window as unknown as { webkit?: { messageHandlers?: Record<string, WindowMessageHandler> } }).webkit;
  return webkit?.messageHandlers?.jarvisWindow;
}

export function useMacWindowCloseFallback(): void {
  useEffect(() => {
    if (!nativeWindowHandler()) return;
    const onKey = (event: KeyboardEvent) => {
      if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (event.key.toLowerCase() !== "w" || event.defaultPrevented) return;
      nativeWindowHandler()?.postMessage("close-window");
    };
    // Bubble phase: the editor's own capture-phase handler runs first and
    // prevents the default when it closed a tab.
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

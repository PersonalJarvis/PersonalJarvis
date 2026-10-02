/** True only inside a pywebview host, never from the backend's machine flag.
 *
 * A normal Chrome window can connect to the same local desktop backend, where
 * `native_file_actions` is also true. Checking the client bridge keeps browser
 * microphone control visible there without enabling a second microphone in the
 * embedded desktop window.
 */
export function hasEmbeddedDesktopBridge(): boolean {
  const host = window as unknown as {
    __JARVIS_EMBEDDED_DESKTOP?: boolean;
    pywebview?: { api?: unknown };
    chrome?: { webview?: { postMessage?: unknown } };
    webkit?: { messageHandlers?: Record<string, unknown> };
  };
  return Boolean(
    host.__JARVIS_EMBEDDED_DESKTOP ||
      host.pywebview?.api ||
      typeof host.chrome?.webview?.postMessage === "function" ||
      host.webkit?.messageHandlers?.jarvisFileDrag,
  );
}

/** Clients whose user agent says macOS (the embedded WebView and Safari/Chrome on a Mac). */
export function isMacClient(userAgent: string): boolean {
  return /Macintosh|Mac OS X/i.test(userAgent);
}

/**
 * True in the embedded desktop window of a Mac: the one place where a macOS
 * permission is the host's own, so asking for it or opening System Settings acts
 * on the computer in front of the person. A remote browser (another computer) and
 * Windows or Linux never qualify. Read at call time: the desktop shell injects
 * the bridge flag AFTER the page loads.
 */
export function isEmbeddedMacWindow(): boolean {
  return (
    typeof navigator !== "undefined" && isMacClient(navigator.userAgent) && hasEmbeddedDesktopBridge()
  );
}

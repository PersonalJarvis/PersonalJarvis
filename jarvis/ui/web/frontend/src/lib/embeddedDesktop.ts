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

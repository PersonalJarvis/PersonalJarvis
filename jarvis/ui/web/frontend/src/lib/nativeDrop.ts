/**
 * Real paths for files and folders dropped onto the desktop shell.
 *
 * A browser hands a page only the NAME of a dropped folder — never its path.
 * Inside the desktop shell the host process sees the native drop and knows the
 * path; `jarvis/ui/native_drop.py` dispatches it to the page as a
 * `jarvis-native-drop` event right after the DOM `drop`. A drop handler that
 * wants the exact path calls `waitForNativeDrop()` synchronously from its drop
 * handler and gets the paths a moment later — or `null`, promptly, in a plain
 * browser tab or when the shell could not resolve them, and then keeps its own
 * fallback (searching by name).
 */

/** Event name — MUST match `EVENT_NAME` in jarvis/ui/native_drop.py. */
export const NATIVE_DROP_EVENT = "jarvis-native-drop";

/** How long a page waits for the shell before falling back. The round trip is
 * usually well under 200 ms; the ceiling only matters when the bridge is not
 * there at all. */
const NATIVE_DROP_TIMEOUT_MS = 2000;

export interface NativeDropDetail {
  /** Full native paths, one per dropped file or folder that could be resolved. */
  paths: string[];
  /** The browser-side names the paths belong to, in the same order. */
  names: string[];
}

/** True inside the pywebview desktop shell (any OS), where the host can answer. */
export function inDesktopShell(): boolean {
  const host = window as unknown as {
    __JARVIS_EMBEDDED_DESKTOP?: boolean;
    pywebview?: unknown;
  };
  return Boolean(host.__JARVIS_EMBEDDED_DESKTOP || host.pywebview);
}

/**
 * Wait for the shell to report the paths of the drop that just happened.
 *
 * Call it synchronously inside the `drop` handler, so the listener is in place
 * before the shell answers. Resolves `null` immediately outside the shell, and
 * after the timeout when no matching announcement arrives. When `name` is
 * given, an announcement is accepted only if it carries that name — a stray
 * event from another drop is never mistaken for this one.
 */
export function waitForNativeDrop(opts: {
  name?: string;
  timeoutMs?: number;
} = {}): Promise<NativeDropDetail | null> {
  if (!inDesktopShell()) return Promise.resolve(null);
  const timeoutMs = opts.timeoutMs ?? NATIVE_DROP_TIMEOUT_MS;
  return new Promise((resolve) => {
    let timer: number | undefined;
    const onEvent = (event: Event) => {
      const detail = (event as CustomEvent<Partial<NativeDropDetail>>).detail;
      const paths = Array.isArray(detail?.paths) ? detail.paths : [];
      const names = Array.isArray(detail?.names) ? detail.names : [];
      if (paths.length === 0) return;
      if (opts.name && names.length > 0 && !names.includes(opts.name)) return;
      window.removeEventListener(NATIVE_DROP_EVENT, onEvent);
      if (timer !== undefined) window.clearTimeout(timer);
      resolve({ paths, names });
    };
    window.addEventListener(NATIVE_DROP_EVENT, onEvent);
    timer = window.setTimeout(() => {
      window.removeEventListener(NATIVE_DROP_EVENT, onEvent);
      resolve(null);
    }, timeoutMs);
  });
}

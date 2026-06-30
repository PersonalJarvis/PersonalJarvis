/**
 * useCapabilities — reports the desktop-shell capabilities the frontend needs
 * to decide *how* to perform an action.
 *
 * `native_file_actions` is true only on a local desktop run (pywebview); false
 * on a headless VPS reached through a browser. Download buttons use it to choose
 * between a backend save to the local ~/Downloads (desktop) and a normal browser
 * download (VPS/browser).
 *
 * Implemented without react-query on purpose: this is a cross-cutting capability
 * consumed by leaf components (TurnCard, ShareDialog) that are rendered — and
 * unit-tested — outside any QueryClientProvider. A module-level cache + a shared
 * in-flight promise dedupe the request so many components trigger only one fetch
 * (the same dedup react-query would give, without the provider requirement).
 */
import { useEffect, useState } from "react";

export interface Capabilities {
  native_file_actions: boolean;
  platform: "win32" | "darwin" | "linux";
}

let _cache: Capabilities | null = null;
let _inflight: Promise<Capabilities> | null = null;

function loadCapabilities(): Promise<Capabilities> {
  if (_cache) return Promise.resolve(_cache);
  if (_inflight) return _inflight;
  // Promise.resolve().then(...) so a missing/relative-URL fetch (e.g. in a unit
  // test with no server) becomes a rejected promise, never a synchronous throw.
  _inflight = Promise.resolve()
    .then(() => fetch("/api/downloads/capabilities"))
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<Capabilities>;
    })
    .then((c) => {
      _cache = c;
      return c;
    })
    .catch((e) => {
      _inflight = null; // allow a retry on the next mount
      throw e;
    });
  return _inflight;
}

/** Returns `{ data }` (undefined until loaded) — mirrors the react-query hooks
 *  used elsewhere so call sites read `caps.data?.native_file_actions`. */
export function useCapabilities(): { data: Capabilities | undefined } {
  const [data, setData] = useState<Capabilities | undefined>(_cache ?? undefined);
  useEffect(() => {
    let alive = true;
    loadCapabilities()
      .then((c) => {
        if (alive) setData(c);
      })
      .catch(() => {
        // Treated as "not native" by callers — they fall back to the browser
        // download, which is the correct behavior outside the desktop shell.
      });
    return () => {
      alive = false;
    };
  }, []);
  return { data };
}

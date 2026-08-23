import { useEffect, useState } from "react";

/**
 * Which *instance* of the desktop app this window belongs to.
 *
 * One checkout can run two desktop apps side by side (``jarvis.core.instance``):
 * the default app and a "dev" app that is restarted freely to look at the
 * current working tree. They share the same frontend bundle, so the only thing
 * that can tell a dev window apart from the inside is the backend saying so —
 * ``/api/health`` carries ``instance`` from the first (warming) poll on.
 *
 * Resolved once per page load and shared by every caller; the instance of a
 * process never changes while it runs. ``null`` until known, and a window whose
 * backend cannot be reached stays ``null`` rather than guessing "default".
 */
export type AppInstance = { name: string; isDev: boolean };

let cached: AppInstance | null = null;
let inflight: Promise<AppInstance | null> | null = null;

export function resetAppInstanceCache(): void {
  cached = null;
  inflight = null;
}

export async function fetchAppInstance(): Promise<AppInstance | null> {
  if (cached) return cached;
  if (!inflight) {
    inflight = fetch("/api/health", { cache: "no-store", credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok) return null;
        const body = (await res.json()) as { instance?: unknown };
        const name = typeof body?.instance === "string" && body.instance ? body.instance : "default";
        cached = { name, isDev: name !== "default" };
        return cached;
      })
      .catch(() => null)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function useAppInstance(): AppInstance | null {
  const [instance, setInstance] = useState<AppInstance | null>(cached);
  useEffect(() => {
    if (cached) return;
    let cancelled = false;
    void fetchAppInstance().then((value) => {
      if (!cancelled && value) setInstance(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return instance;
}

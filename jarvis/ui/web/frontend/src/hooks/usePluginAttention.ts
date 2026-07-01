import { useEffect, useState } from "react";

/**
 * Polls the marketplace plugin list and reports whether ANY connected plugin
 * needs attention — a revoked/expired token (`needs_reauth`) or an `error`.
 *
 * Standalone (no React-Query) so it works in the Sidebar, which renders without
 * a QueryClientProvider. Fully fault-tolerant: with no backend / no `fetch`, it
 * stays calm (returns false) rather than throwing — a sidebar dot must never
 * crash the shell.
 */
export function usePluginAttention(): boolean {
  const [needsReconnect, setNeedsReconnect] = useState(false);

  useEffect(() => {
    let alive = true;

    const check = async () => {
      try {
        const res = await fetch("/api/marketplace/plugins", {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as {
          plugins?: { status?: string }[];
        };
        if (!alive) return;
        const flag = (data.plugins ?? []).some(
          (p) => p.status === "needs_reauth" || p.status === "error",
        );
        setNeedsReconnect(flag);
      } catch {
        // Offline / no backend / no fetch — stay calm, surface nothing.
      }
    };

    void check();
    const id = setInterval(check, 60_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return needsReconnect;
}

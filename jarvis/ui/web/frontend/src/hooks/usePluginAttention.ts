import { useEffect, useState } from "react";

/** What the sidebar needs to surface a plugin problem: how many connected
 *  plugins need attention and their display names (for a concrete tooltip). */
export interface PluginAttention {
  count: number;
  names: string[];
}

const CALM: PluginAttention = { count: 0, names: [] };

/**
 * Polls the marketplace plugin list and reports which connected plugins need
 * attention — a revoked/expired token (`needs_reauth`) or an `error`. Returns
 * the count plus the display names so the sidebar can name the culprit instead
 * of showing a bare, cryptic dot.
 *
 * Standalone (no React-Query) so it works in the Sidebar, which renders without
 * a QueryClientProvider. Fully fault-tolerant: with no backend / no `fetch`, it
 * stays calm (count 0) rather than throwing — a sidebar dot must never crash the
 * shell.
 */
export function usePluginAttention(): PluginAttention {
  const [attention, setAttention] = useState<PluginAttention>(CALM);

  useEffect(() => {
    let alive = true;

    const check = async () => {
      try {
        const res = await fetch("/api/marketplace/plugins", {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as {
          plugins?: { status?: string; display_name?: string }[];
        };
        if (!alive) return;
        const flagged = (data.plugins ?? []).filter(
          (p) => p.status === "needs_reauth" || p.status === "error",
        );
        setAttention({
          count: flagged.length,
          names: flagged.map((p) => p.display_name ?? "").filter(Boolean),
        });
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

  return attention;
}

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { z } from "zod";

export const BACKGROUND_MODES = ["foreground", "background", "stopping", "stopped"] as const;
const backgroundSchema = z.object({
  available: z.boolean(), reason: z.string().nullable(), enabled: z.boolean(),
  mode: z.enum(BACKGROUND_MODES), window_open: z.boolean(),
});
const KEY = ["desktop", "background"];

export function MarsBackgroundControl() {
  const t = useT(), client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const state = useQuery({
    queryKey: KEY,
    queryFn: async ({ signal }) => {
      const response = await fetch("/api/window/background", { signal });
      if (!response.ok) throw new Error("background_status_unavailable");
      return backgroundSchema.parse(await response.json());
    },
    refetchInterval: 5000, retry: false,
  });
  const toggle = async () => {
    if (busy || !state.data?.available) return;
    setBusy(true); setFailed(false);
    try {
      const response = await fetch("/api/window/background", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !state.data.enabled }),
      });
      if (!response.ok) throw new Error("background_change_failed");
      const next = backgroundSchema.parse(await response.json());
      client.setQueryData(KEY, next);
      if (next.enabled === state.data.enabled) setFailed(true);
    } catch { setFailed(true); }
    finally { setBusy(false); }
  };
  const headless = state.data?.reason === "headless_host";
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-mars-ui>
      <button type="button" className="mars-use-station disabled:opacity-50" aria-pressed={state.data?.enabled ?? false} disabled={busy || !state.data?.available} onClick={() => void toggle()}>
        {t(headless ? "society.mars.headless_background" : state.data?.enabled ? "society.mars.background_on" : "society.mars.background_off")}
      </button>
      <span role="status" className="text-muted-foreground">
        {t(failed ? "society.mars.background_failed" : headless ? "society.mars.headless_hint" : state.isError || !state.data?.available ? "society.mars.background_unavailable" : state.data?.enabled ? "society.mars.background_hint" : "society.mars.background_enable_hint")}
      </span>
    </div>
  );
}

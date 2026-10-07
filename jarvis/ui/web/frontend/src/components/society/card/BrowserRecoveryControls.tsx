import { useEffect, useRef, useState } from "react";
import { Power, RotateCw } from "lucide-react";
import { useT } from "@/i18n";

interface Props {
  agentId: string;
  canReload: boolean;
  canRestart: boolean;
  reload: () => void;
}

/** Recovery stays available even when the browser stream stops responding. */
export function BrowserRecoveryControls({ agentId, canReload, canRestart, reload }: Props) {
  const t = useT();
  const request = useRef<AbortController | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => request.current?.abort(), []);

  const restart = async () => {
    if (request.current) return;
    const pending = new AbortController();
    request.current = pending;
    setRestarting(true);
    setError("");
    try {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/browser/restart`, {
        method: "POST", signal: pending.signal,
      });
      if (!response.ok) throw new Error("Browser restart failed");
    } catch {
      if (!pending.signal.aborted) setError(t("society.browser_live.restart_failed"));
    } finally {
      if (!pending.signal.aborted) setRestarting(false);
      request.current = null;
    }
  };
  const buttonClass = "inline-flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-secondary disabled:opacity-40";
  return <>
    <button type="button" className={buttonClass} disabled={!canReload || restarting}
      onClick={reload}>
      <RotateCw size={13} aria-hidden="true" />{t("society.browser_live.reload")}
    </button>
    {canRestart && <button type="button" className={buttonClass} disabled={restarting}
      title={t("society.browser_live.restart_hint")} onClick={() => void restart()}>
      <Power size={13} aria-hidden="true" />
      {t(restarting ? "society.browser_live.restarting" : "society.browser_live.restart")}
    </button>}
    {restarting && <span role="status" className="w-full text-center text-xs text-muted-foreground">
      {t("society.browser_live.restart_hint")}
    </span>}
    {error && <span role="alert" className="w-full text-center text-xs text-destructive">{error}</span>}
  </>;
}

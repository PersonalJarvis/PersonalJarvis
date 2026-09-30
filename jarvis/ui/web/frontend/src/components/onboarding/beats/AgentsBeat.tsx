import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { BeatProps } from "../WelcomeFlow";
import { PrimaryAction, QuietAction, Rise, Status } from "../ui";

/** One row of `GET /api/jarvis-agent/status` → `mapping`. */
interface AgentRow {
  jarvis: string;
  label?: string | null;
  dedicated_key_set?: boolean;
  oauth_connected?: boolean;
  is_active_brain?: boolean;
  keyless?: boolean;
}

/** The agent that currently takes the big jobs, if one can actually run. */
export function readyAgent(rows: AgentRow[] | null): AgentRow | null {
  return (
    rows?.find(
      (row) => row.is_active_brain && (row.dedicated_key_set || row.oauth_connected || row.keyless),
    ) ?? null
  );
}

/**
 * Detect instead of ask: the coding agents this computer is already signed
 * in to (subscriptions), each one a card that connects in the browser.
 *
 * Entirely optional — the assistant talks, answers and uses its tools
 * without one — so Continue is always live. The status is read once, then
 * again whenever a connection changes (the cards announce it) or the user
 * presses "Check again"; there is no timer (AP-33).
 */
export function AgentsBeat({ next, report, cheer }: BeatProps) {
  const t = useT();
  const [rows, setRows] = useState<AgentRow[] | null>(null);
  const [checking, setChecking] = useState(false);

  const refresh = useCallback(async () => {
    setChecking(true);
    try {
      const res = await fetch("/api/jarvis-agent/status", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const status = (await res.json()) as { mapping?: AgentRow[] };
      setRows(Array.isArray(status.mapping) ? status.mapping : []);
    } catch {
      setRows([]);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onChange = () => void refresh();
    window.addEventListener("jarvis:agent-switched", onChange);
    window.addEventListener("jarvis:secret-configured", onChange);
    return () => {
      window.removeEventListener("jarvis:agent-switched", onChange);
      window.removeEventListener("jarvis:secret-configured", onChange);
    };
  }, [refresh]);

  const agent = readyAgent(rows);
  const agentLabel = agent ? agent.label || agent.jarvis : null;

  useEffect(() => {
    if (agentLabel) {
      report({ summary: agentLabel, gap: null });
      cheer("jump");
    } else {
      report({ summary: null, gap: t("first_run.agents.gap") });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentLabel, t]);

  return (
    <div className="space-y-5">
      <Rise index={0}>
        <div className="max-h-[46vh] overflow-y-auto rounded-xl border border-border bg-background p-4 scrollbar-jarvis" data-testid="onboarding-agent-subscriptions">
          <JarvisAgentSection hideHeader subscriptionsOnly />
        </div>
      </Rise>

      <Rise index={1} className="flex flex-wrap items-center justify-between gap-3">
        {rows === null ? (
          <Status tone="muted">{t("first_run.agents.checking")}</Status>
        ) : agentLabel ? (
          <Status tone="ok" testId="onboarding-agent-ready">
            {fill(t("first_run.agents.ready"), { agent: agentLabel })}
          </Status>
        ) : (
          <Status tone="muted" testId="onboarding-agent-optional">
            {t("first_run.agents.optional")}
          </Status>
        )}
        <QuietAction
          onClick={() => void refresh()}
          disabled={checking}
          className="inline-flex items-center gap-1.5"
          testId="onboarding-agent-recheck"
        >
          <RefreshCw aria-hidden className={cn("h-3.5 w-3.5", checking && "animate-spin")} />
          {t("first_run.agents.check_again")}
        </QuietAction>
      </Rise>

      <Rise index={2}>
        <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.agents.api_key_hint")}</p>
      </Rise>

      <Rise index={3}>
        <PrimaryAction onClick={next}>{t("first_run.continue")}</PrimaryAction>
      </Rise>
    </div>
  );
}

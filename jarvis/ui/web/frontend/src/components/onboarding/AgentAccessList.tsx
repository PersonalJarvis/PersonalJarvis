import { useState } from "react";
import { KeyRound } from "lucide-react";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { Button, FOCUS_RING } from "@/components/agentic/controls";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import { switchSubagentProvider } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { StatusLine } from "./primitives";

export interface AgentAccessRow {
  jarvis: string;
  label?: string | null;
  billing?: string;
  secret_key?: string | null;
  dashboard_url?: string | null;
  credential_help?: string | null;
  dedicated_key_set?: boolean;
  oauth_connected?: boolean;
  is_active_brain?: boolean;
  keyless?: boolean;
}

/** Keep the API key choices visible during onboarding; only the selected form expands. */
export function AgentAccessList({
  rows,
  onChanged,
}: {
  rows: AgentAccessRow[] | null;
  onChanged: () => void | Promise<void>;
}) {
  const t = useT();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyRows = (rows ?? []).filter((row) =>
    row.secret_key && (row.billing === "api" || row.billing === "subscription_or_api"),
  );
  const selected = keyRows.find((row) => row.jarvis === selectedId) ??
    keyRows.find((row) => row.is_active_brain) ?? keyRows[0];

  async function activate(row: AgentAccessRow) {
    if (switching) return;
    setSwitching(true);
    setError(null);
    try {
      await switchSubagentProvider(row.jarvis);
      window.dispatchEvent(new CustomEvent("jarvis:agent-switched"));
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSwitching(false);
    }
  }

  return (
    <div className="space-y-6" data-testid="onboarding-agent-access">
      <div data-testid="onboarding-agent-subscriptions">
        <JarvisAgentSection hideHeader subscriptionsOnly />
      </div>
      <div className="space-y-4 border-t border-border pt-5">
      <div>
        <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <KeyRound className="h-4 w-4 text-primary" />
          {t("onboarding.api_keys.agents_api_title")}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">{t("onboarding.api_keys.agents_api_hint")}</p>
      </div>
      {rows === null ? (
        <p className="text-sm text-muted-foreground">{t("onboarding.api_keys.agents_loading")}</p>
      ) : keyRows.length === 0 ? (
        <StatusLine tone="warning">{t("onboarding.api_keys.agents_unavailable")}</StatusLine>
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card" data-testid="onboarding-agent-key-list">
          {keyRows.map((row) => (
            <button
              key={row.jarvis}
              type="button"
              aria-pressed={selected?.jarvis === row.jarvis}
              data-testid={`onboarding-agent-key-${row.jarvis}`}
              onClick={() => setSelectedId(row.jarvis)}
              className={cn(
                "flex w-full items-center justify-between gap-3 border-b border-border/60 px-4 py-3 text-left last:border-b-0 hover:bg-secondary/50",
                selected?.jarvis === row.jarvis && "bg-secondary/40",
                FOCUS_RING,
              )}
            >
              <span className="text-sm font-medium text-foreground">{row.label || row.jarvis}</span>
              <span className="text-xs text-muted-foreground">
                {row.dedicated_key_set
                  ? row.is_active_brain
                    ? t("onboarding.api_keys.agents_active")
                    : t("onboarding.api_keys.configured")
                  : t("onboarding.api_keys.not_configured")}
              </span>
            </button>
          ))}
        </div>
      )}
      {selected?.secret_key && (
        <div className="rounded-xl border border-border bg-card p-4" data-testid="onboarding-agent-key-form">
          <p className="mb-3 text-sm font-semibold text-foreground">{selected.label || selected.jarvis}</p>
          <ApiKeyForm
            key={selected.secret_key}
            secretKey={selected.secret_key}
            dashboardUrl={selected.dashboard_url ?? null}
            configured={Boolean(selected.dedicated_key_set)}
            credentialHelp={selected.credential_help}
            onChanged={onChanged}
            onSavedActivate={() => void activate(selected)}
          />
          {selected.dedicated_key_set && !selected.is_active_brain && (
            <Button variant="quiet" disabled={switching} onClick={() => void activate(selected)}>
              {t("onboarding.api_keys.agents_use_key")}
            </Button>
          )}
        </div>
      )}
      {error && <StatusLine tone="error">{error}</StatusLine>}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { Bot, KeyRound, Loader2, Phone, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { AgentAccountsPanel } from "@/components/AgentAccountsPanel";
import { SubagentModelCard, type SubagentStatus } from "@/components/SubagentModelCard";
import { PromptWriterCard } from "@/components/PromptWriterCard";
import { useTierHealth } from "@/components/providers/ProviderTierSection";
import { Button } from "@/components/ui/button";
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
import { JarvisApiGroup } from "@/views/settings/JarvisApiGroup";
import { TeamProxyGroup } from "@/views/settings/TeamProxyGroup";
import { ProvidersPanel } from "@/views/apikeys/ProvidersPanel";
import { useProviders, useSectionHealth } from "@/hooks/useProviders";
import { useProviderFamilies } from "@/lib/providerFamilies";
import { useLocaleChunk, useT } from "@/i18n";

/**
 * API Keys — every AI company once, each with one way to sign in and one key.
 *
 * The page used to be split by feature (live voice, agents, the install key,
 * advanced), so one company appeared on several tabs with several keys. Now
 * a company is connected once — its subscription login or a single key that
 * every feature reads — and its settings say what the assistant uses it for,
 * with each job switchable where it is shown. Below the providers sit the
 * settings that belong to no single company: the agents' model and accounts,
 * the install's own key, and the optional integrations.
 */
export function ApiKeysView() {
  const t = useT();
  // The page's own strings load with it; nothing paints raw keys meanwhile.
  const stringsReady = useLocaleChunk("providers");
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  const families = useProviderFamilies();
  const { health } = useSectionHealth();
  const tierHealth = useTierHealth(providers);
  const refreshing = families.loading;

  if (!stringsReady) {
    return (
      <div role="status" aria-busy="true" className="flex h-full items-center justify-center">
        <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-tour="apikeys-page">
      <div className="shrink-0 px-8">
        <PageHeader
          icon={<KeyRound />}
          title={t("apikeys_view.title")}
          description={t("providers_page.subtitle")}
          actions={
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              data-testid="providers-refresh"
              disabled={refreshing}
              onClick={() => {
                refetch();
                void families.reload();
              }}
            >
              {refreshing ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              <CheckedAgo at={families.checkedAt} />
            </Button>
          }
        />
      </div>

      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 pb-10 pt-2"
      >
        <div className="profile-rise flex w-full flex-col gap-12">
          <ProvidersPanel
            data={families}
            providers={providers}
            providersLoading={loading}
            providersError={error}
            health={health}
            realtimeHealth={tierHealth.realtime}
            onProvidersChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
          />
          <AgentSettings />
          <section className="flex flex-col gap-3">
            <SectionTitle title={t("apikeys_view.jarvis_key_title")} description={t("apikeys_view.jarvis_key_desc")} />
            <JarvisApiGroup />
          </section>
          <section className="flex flex-col gap-3">
            <SectionTitle title={t("apikeys_view.advanced_title")} description={t("apikeys_view.advanced_desc")} />
            <div className="space-y-4">
              <TeamProxyGroup />
              <TelephonySection />
              <WikiProviderCard />
            </div>
            {/* Nominative-use trademark notice: provider and integration names
                and logos belong to their owners and only identify what you
                connect to (see TRADEMARK.md). */}
            <p className="px-1 pt-2 text-sm text-muted-foreground">{t("apikeys_view.trademark_notice")}</p>
          </section>
        </div>
      </div>
    </div>
  );
}

/** "Checked 2 min ago", re-rendered every half minute. */
function CheckedAgo({ at }: { at: number | null }) {
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  if (at === null) return <>{t("providers_page.checking")}</>;
  const minutes = Math.floor(Math.max(0, now - at) / 60_000);
  return (
    <>
      {minutes < 1
        ? t("providers_page.checked_now")
        : t("providers_page.checked_minutes").replace("{0}", String(minutes))}
    </>
  );
}

function SectionTitle({ title, description }: { title: string; description: string }) {
  return (
    <div className="px-1">
      <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
      <p className="mt-0.5 text-base text-muted-foreground">{description}</p>
    </div>
  );
}

/**
 * Settings that belong to the agents rather than to one company: the model a
 * worker runs, who writes the task briefs, and the several logins a person
 * can hold per command-line tool.
 */
function AgentSettings() {
  const t = useT();
  const [status, setStatus] = useState<SubagentStatus | null>(null);
  const reload = async () => {
    try {
      const res = await fetch("/api/jarvis-agent/status", { cache: "no-store" });
      if (res.ok) setStatus((await res.json()) as SubagentStatus);
    } catch (cause) {
      // The model card simply stays hidden; the provider list above reports
      // the same endpoint's failure.
      console.debug("agent status unavailable", cause);
    }
  };
  useEffect(() => {
    void reload();
    const onChange = () => void reload();
    window.addEventListener("jarvis:agent-switched", onChange);
    return () => window.removeEventListener("jarvis:agent-switched", onChange);
  }, []);

  return (
    <section data-testid="provider-agent-settings" className="flex flex-col gap-3">
      <SectionTitle title={t("providers_page.agent_settings_title")} description={t("providers_page.agent_settings_desc")} />
      <div className="grid gap-4 xl:grid-cols-2">
        {status && <SubagentModelCard status={status} onSaved={() => void reload()} />}
        <PromptWriterCard />
      </div>
      <div className="flex items-center gap-2 px-1 pt-2 text-sm font-medium text-muted-foreground">
        <Bot aria-hidden="true" className="h-4 w-4" />
        {t("providers_page.accounts_title")}
      </div>
      <AgentAccountsPanel />
    </section>
  );
}

/**
 * Telephony: a labelled header above the embedded `TelephonyPanel`, which owns
 * its data source (`/api/telephony/*`); the setup scripts and guide live on the
 * dedicated TelephonySetupView, reached through the panel's "Setup script".
 */
function TelephonySection() {
  const t = useT();
  return (
    <section>
      <h3 className="mb-3 inline-flex items-center gap-2 text-sm font-medium text-muted-foreground">
        <Phone aria-hidden="true" className="h-4 w-4" /> {t("apikeys_view.tier_telephony")}
      </h3>
      <TelephonyPanel />
    </section>
  );
}

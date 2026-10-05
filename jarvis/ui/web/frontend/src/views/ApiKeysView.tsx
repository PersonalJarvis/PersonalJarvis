import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Loader2, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { LiveProfile, liveProfileQuery, liveProfileReady } from "@/components/providers/LiveProfile";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Button } from "@/components/ui/button";
import { AgentsTab } from "@/views/apikeys/AgentsTab";
import { KeyField } from "@/views/apikeys/KeyField";
import { DetailHeader, ListRow, MasterDetail } from "@/views/apikeys/settingsUi";
import { useProviders, useSectionHealth } from "@/hooks/useProviders";
import { useProviderFamilies } from "@/lib/providerFamilies";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

const TABS = ["voice", "agents"] as const;
type Tab = (typeof TABS)[number];
const TAB_STORAGE_KEY = "jarvis.apikeys.tab";

function initialTab(): Tab {
  try {
    const saved = localStorage.getItem(TAB_STORAGE_KEY);
    if (saved && (TABS as readonly string[]).includes(saved)) return saved as Tab;
  } catch {
    // Private mode / no storage: open on the first tab.
  }
  return "voice";
}

/**
 * API Keys, in two parts behind one centred switch, both as a provider list
 * beside the selected provider's settings:
 *
 * - Live calls: the live voice runs on OpenAI GPT-Live — paid with the
 *   ChatGPT subscription or one OpenAI key — with its voice and the thinking
 *   model that answers with tools;
 * - Agents: every company the assistant's agents can run on, any number of
 *   them on at once, each reached by its subscription or its API key.
 */
export function ApiKeysView() {
  const t = useT();
  // The page's own strings load with it; nothing paints raw keys meanwhile.
  const stringsReady = useLocaleChunk("providers");
  const { providers, refetch } = useProviders();
  const families = useProviderFamilies();
  const { health } = useSectionHealth();
  const [tab, setTab] = useState<Tab>(initialTab);
  const choose = (next: Tab) => {
    setTab(next);
    try {
      localStorage.setItem(TAB_STORAGE_KEY, next);
    } catch {
      // Remembering the tab is a convenience; without storage it resets.
    }
  };

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
          title={t("apikeys_view.title")}
          description={t("providers_page.subtitle")}
          actions={
            <Button
              size="sm"
              variant="ghost"
              className="text-muted-foreground"
              data-testid="providers-refresh"
              disabled={families.loading}
              onClick={() => {
                refetch();
                void families.reload();
              }}
            >
              {families.loading ? <Loader2 className="animate-spin" /> : <RefreshCw />}
              <CheckedAgo at={families.checkedAt} />
            </Button>
          }
        />
        <div className="flex justify-center">
          <TabSwitch value={tab} onChange={choose} />
        </div>
      </div>

      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 pb-10 pt-6"
      >
        <div key={tab} role="tabpanel" id="apikeys-panel" aria-labelledby={`apikeys-tab-${tab}`} className="profile-rise w-full">
          {tab === "voice" && <VoiceTab data={families} onSaved={refetch} />}
          {tab === "agents" &&
            (families.error === "restart_required" ? (
              <RestartNeeded />
            ) : (
              <AgentsTab data={families} providers={providers} health={health} onProvidersChanged={refetch} />
            ))}
        </div>
      </div>
    </div>
  );
}

/** A segmented switch between the page's parts. */
function TabSwitch({ value, onChange }: { value: Tab; onChange: (tab: Tab) => void }) {
  const t = useT();
  return (
    <div
      role="tablist"
      aria-label={t("apikeys_view.title")}
      data-testid="api-keys-category-tabs"
      className="inline-flex rounded-lg border border-border/60 bg-card/40 p-0.5"
    >
      {TABS.map((tab) => (
        <button
          key={tab}
          type="button"
          role="tab"
          id={`apikeys-tab-${tab}`}
          aria-selected={value === tab}
          aria-controls="apikeys-panel"
          onClick={() => onChange(tab)}
          className={cn(
            "h-7 rounded-md px-3 text-sm font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === tab ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`providers_page.tab_${tab}`)}
        </button>
      ))}
    </div>
  );
}

/**
 * Live calls run on OpenAI GPT-Live — the one live speech-to-speech model the
 * assistant runs — so the list holds that one provider, in the same list and
 * detail layout as the Agents tab: how it is paid for, its voice and the
 * thinking model that answers with tools.
 */
function VoiceTab({ data, onSaved }: { data: ReturnType<typeof useProviderFamilies>; onSaved: () => void }) {
  const t = useT();
  const openai = data.families?.find((family) => family.id === "openai");
  const slot = openai?.key_slot ?? "openai_api_key";
  const live = useQuery(liveProfileQuery);
  const subscription = live.data?.profile.auth_mode === "chatgpt_subscription";
  const ready = liveProfileReady(live.data);
  const status = !live.data
    ? t("live.loading")
    : `${t(subscription ? "providers_page.access_subscription" : "providers_page.access_api_key")} · ${t(
        ready ? "providers_page.voice_ready" : "providers_page.voice_not_ready",
      )}`;
  return (
    <div data-testid="apikeys-voice">
      <MasterDetail
        testId="voice-provider-list"
        list={
          <ListRow
            testId="voice-provider-gpt-live"
            icon={<ProviderLogo providerId={openai?.logo_id ?? "openai"} label="OpenAI" size="sm" className="h-5 w-5" />}
            name="GPT-Live"
            status={status}
            selected
            dimmed={false}
            onSelect={() => undefined}
            trailing={ready ? <Check aria-label={t("providers_page.voice_ready")} className="h-4 w-4 text-accent" /> : null}
          />
        }
        detail={
          <div className="space-y-2.5">
            <DetailHeader
              icon={<ProviderLogo providerId={openai?.logo_id ?? "openai"} label="OpenAI" size="sm" className="h-5 w-5" />}
              name="GPT-Live · OpenAI"
            />
            <LiveProfile
              onSaved={onSaved}
              keyField={
                <KeyField
                  slot={slot}
                  present={openai?.key_present ?? false}
                  providerLabel="OpenAI"
                  dashboardUrl={openai?.dashboard_url}
                  onChanged={() => void data.reload()}
                  testId="voice-key"
                />
              }
            />
          </div>
        }
      />
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

/**
 * The window runs a newer page than its backend: the bundle reloads on its
 * own after an update, the server does not. One sentence and one button,
 * instead of an error that suggests something is broken.
 */
function RestartNeeded() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [restarting, setRestarting] = useState(false);

  async function restart() {
    setRestarting(true);
    try {
      const response = await fetch("/api/settings/restart-app", { method: "POST" });
      if (response.status === 409) {
        pushToast("warning", t("topbar.restart_missions_running"));
        setRestarting(false);
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      // The window closes and relaunches; the button stays busy until then.
    } catch (cause) {
      pushToast("error", (cause as Error).message);
      setRestarting(false);
    }
  }

  return (
    <div
      role="status"
      data-testid="providers-restart-needed"
      className="flex items-center gap-4 rounded-xl border border-border/60 bg-card/40 px-4 py-3"
    >
      <RefreshCw aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{t("providers_page.restart_title")}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{t("providers_page.restart_desc")}</p>
      </div>
      <Button size="sm" disabled={restarting} onClick={() => void restart()}>
        {restarting && <Loader2 className="animate-spin" />}
        {t("providers_page.restart_action")}
      </Button>
    </div>
  );
}

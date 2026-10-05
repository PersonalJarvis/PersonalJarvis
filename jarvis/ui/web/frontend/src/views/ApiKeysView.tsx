import { useEffect, useState } from "react";
import { Bot, KeyRound, Phone, Radio, SlidersHorizontal } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
import { JarvisApiGroup } from "@/views/settings/JarvisApiGroup";
import { TeamProxyGroup } from "@/views/settings/TeamProxyGroup";
import { RealtimeTab } from "@/views/apikeys/RealtimeTab";
import { useTierHealth, type LucideIcon } from "@/components/providers/ProviderTierSection";
import { type SectionHealth, useProviders } from "@/hooks/useProviders";
import {
  APIKEYS_TAB_EVENT,
  API_KEYS_TABS,
  type ApiKeysTab,
  clearApiKeysTabRequest,
  requestedApiKeysTab,
} from "@/lib/apiKeysTab";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** A tab another part of the app asked for, or the page's first tab. */
function resolveTab(wanted: string | null): ApiKeysTab {
  return API_KEYS_TABS.includes(wanted as ApiKeysTab) ? (wanted as ApiKeysTab) : "realtime";
}

/**
 * API Keys — the realtime voice, the agents, the install's own key and the
 * optional integrations, one tab each.
 *
 * Realtime is the only voice engine this page sets up. The Pipeline|Realtime
 * switch, the Brain / Voice Output / Voice Input / Wording tabs and the Local
 * Mode toggle are gone: speech-to-text and the dictation wording pass are
 * configured in the voice section, where dictation lives.
 */
export function ApiKeysView() {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  // Per-tab health (amber = still to set up, red = set up but failing a live
  // check), re-bound to the provider that is actually active.
  const health = useTierHealth(providers);
  const [active, setActive] = useState<ApiKeysTab>(() => resolveTab(requestedApiKeysTab()));

  // The first-run guide may ask for a tab after the page mounted (it loads
  // lazily); a request stays in force until the user picks a tab themselves.
  useEffect(() => {
    const onRequest = (event: Event) => {
      setActive(resolveTab((event as CustomEvent<string | null>).detail));
    };
    window.addEventListener(APIKEYS_TAB_EVENT, onRequest);
    return () => window.removeEventListener(APIKEYS_TAB_EVENT, onRequest);
  }, []);

  const selectTab = (key: ApiKeysTab) => {
    clearApiKeysTabRequest();
    setActive(key);
  };

  return (
    <div className="flex h-full min-h-0 flex-col" data-tour="apikeys-page">
      <div className="shrink-0 px-8">
        <PageHeader
          icon={<KeyRound />}
          title={t("apikeys_view.title")}
          description={t("apikeys_view.subtitle")}
          tabs={<CategoryTabs active={active} onSelect={selectTab} health={health} />}
        />
      </div>

      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-8 pb-10 pt-6"
      >
        {/* The key re-runs the rise animation on a tab change. */}
        <div
          key={active}
          role="tabpanel"
          id="apikeys-panel"
          aria-labelledby={`apikeys-tab-${active}`}
          className="profile-rise w-full max-w-page"
        >
          {active === "realtime" && (
            <RealtimeTab
              providers={providers}
              loading={loading}
              error={error}
              onChanged={refetch}
              onActivateOptimistic={setActiveOptimistic}
              health={health.realtime}
            />
          )}
          {active === "subagents" && <SubagentCategory />}
          {active === "jarvis-key" && <JarvisKeyCategory />}
          {active === "advanced" && <AdvancedCategory />}
        </div>
      </div>
    </div>
  );
}

/**
 * Underline tabs on the header's rule, in the shared tab-bar look. A tab whose
 * section needs attention swaps its icon for a status dot: red for "set up but
 * not working", amber for "still to set up". Healthy tabs stay silent, so a
 * dot always means "look here".
 */
function CategoryTabs({
  active,
  onSelect,
  health,
}: {
  active: ApiKeysTab;
  onSelect: (key: ApiKeysTab) => void;
  /** Per-tab health rollup keyed by tab; absent keys render no dot. */
  health: Record<string, SectionHealth>;
}) {
  const t = useT();
  const meta: Record<ApiKeysTab, { label: string; icon: LucideIcon }> = {
    realtime: { label: t("apikeys_view.tab_realtime"), icon: Radio },
    subagents: { label: t("apikeys_view.tab_subagents"), icon: Bot },
    "jarvis-key": { label: t("apikeys_view.tab_jarvis_key"), icon: KeyRound },
    advanced: { label: t("apikeys_view.tab_advanced"), icon: SlidersHorizontal },
  };
  return (
    <div
      role="tablist"
      data-testid="api-keys-category-tabs"
      className="flex items-center gap-6 overflow-x-auto border-b border-border scrollbar-jarvis"
    >
      {API_KEYS_TABS.map((key) => (
        <TabButton
          key={key}
          id={`apikeys-tab-${key}`}
          icon={meta[key].icon}
          label={meta[key].label}
          selected={active === key}
          onClick={() => onSelect(key)}
          health={health[key]}
        />
      ))}
    </div>
  );
}

function TabButton({
  id,
  icon: Icon,
  label,
  selected,
  onClick,
  health,
}: {
  id: string;
  icon: LucideIcon;
  label: string;
  selected: boolean;
  onClick: () => void;
  health?: SectionHealth;
}) {
  const t = useT();
  const indicator =
    health?.status === "error"
      ? "error"
      : health?.status === "needs_setup"
        ? "needs_setup"
        : null;
  const statusLabel =
    indicator === "error"
      ? t("apikeys_view.health_error")
      : indicator === "needs_setup"
        ? t("apikeys_view.health_needs_setup")
        : "";
  // Tooltip: the plain-language status plus the backend's one-line detail
  // (e.g. "OpenAI GPT-Live: out of credit"), so hovering says what is wrong.
  const title = indicator ? [statusLabel, health?.detail].filter(Boolean).join(" — ") : undefined;
  return (
    <button
      type="button"
      role="tab"
      id={id}
      aria-selected={selected}
      aria-controls="apikeys-panel"
      onClick={onClick}
      title={title}
      className={cn(
        "relative -mb-px inline-flex h-10 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 text-base font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        selected
          ? "border-accent text-foreground-strong"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {indicator ? (
        <span
          aria-hidden="true"
          className={cn(
            "h-2 w-2 shrink-0 rounded-full",
            indicator === "error" ? "bg-destructive" : "bg-warning",
          )}
        />
      ) : (
        <Icon aria-hidden="true" className="h-4 w-4 opacity-80" />
      )}
      {label}
      {indicator && <span className="sr-only">{` (${statusLabel})`}</span>}
    </button>
  );
}

/** The heading every non-voice tab opens with, in the settings-group grammar. */
function TabIntro({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-4 px-1">
      <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
      <p className="mt-0.5 text-base text-muted-foreground">{description}</p>
    </div>
  );
}

/**
 * The agents tab — the heavy-task worker selection. `JarvisAgentSection` owns
 * its own data source (/api/jarvis-agent/status) and rows.
 */
function SubagentCategory() {
  const t = useT();
  return (
    <div>
      <TabIntro
        title={t("apikeys_view.cat_subagents_title")}
        description={t("apikeys_view.cat_subagents_desc")}
      />
      <JarvisAgentSection hideHeader />
    </div>
  );
}

/**
 * The "<Name> Key" tab — the per-install Control Key that unlocks the browser
 * UI and authenticates local agents, named after the configured wake word via
 * the i18n `{name}` token, so the tab the lock screen points at carries the
 * name the user knows their assistant by.
 */
function JarvisKeyCategory() {
  const t = useT();
  return (
    <div>
      <TabIntro title={t("apikeys_view.jarvis_key_title")} description={t("apikeys_view.jarvis_key_desc")} />
      <JarvisApiGroup />
    </div>
  );
}

/**
 * Optional integrations: the team key proxy, telephony and the knowledge-Wiki
 * provider. Each block keeps its own labelled header.
 */
function AdvancedCategory() {
  const t = useT();
  return (
    <div>
      <TabIntro title={t("apikeys_view.advanced_title")} description={t("apikeys_view.advanced_desc")} />
      <div className="space-y-4">
        <TeamProxyGroup />
        <TelephonySection />
        <WikiProviderCard />
        {/* Nominative-use trademark notice: provider and integration names and
            logos belong to their owners and only identify what you connect to
            (see TRADEMARK.md). */}
        <p className="pt-2 text-sm text-muted-foreground">
          {t("apikeys_view.trademark_notice")}
        </p>
      </div>
    </div>
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

import { useEffect, useRef, useState } from "react";
import { Bot, Brain, KeyRound, Mic, Phone, Radio, SlidersHorizontal, Terminal, Volume2, Wand2 } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
import { JarvisApiGroup } from "@/views/settings/JarvisApiGroup";
import { TeamProxyGroup } from "@/views/settings/TeamProxyGroup";
// The provider-card machinery lives in its own module so the voice section's
// "API Keys" tab renders the very same subtree (scoped to the `stt` tier)
// instead of forking a second implementation.
import {
  CategoryHero,
  EngineModeSwitch,
  GuidancePanel,
  LocalModeSwitch,
  makeProviderCategories,
  ProviderCategory,
  useTierHealth,
  VoiceEngineContext,
  type CategoryMeta,
  type LucideIcon,
  type RecommendationTab,
  type VoiceEngineMode,
} from "@/components/providers/ProviderTierSection";
import {
  type ProviderDescriptor,
  type ProviderTier,
  type SectionHealth,
  useProviders,
} from "@/hooks/useProviders";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useLocalMode } from "@/lib/localMode";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

// The view is organised around exactly five primary categories — Brain, Voice
// Output (TTS), Voice Input (STT), Realtime and Subagents — surfaced as a
// segmented tab bar. The per-install Control Key gets its OWN de-emphasized
// tab ("jarvis-key", labelled "<Name> Key" after the configured wake word) so
// a user hunting for the key the lock screen asks about finds a section named
// like their assistant. Everything else (team key proxy, telephony, Wiki)
// lives in the "Advanced" tab so it never competes with the core categories.
type CategoryKey = ProviderTier | "subagents" | "jarvis-key" | "advanced";

// Realtime replaces STT+Brain+TTS with one full-duplex model, so those three
// tiers don't apply in Realtime mode — that's the whole reason for the split.
// "computer-use" is GLOBAL (not mode-specific — Computer-Use is one engine for
// the whole app), so it appears right after the main chat-model tab in BOTH
// tab sets.
// "dictation" sits right behind "stt" because that is the order the text
// travels in: speech becomes a transcript, then the optional wording pass
// tidies it. It rides with the Pipeline tab set for the same reason "stt"
// does — it only ever works on a transcript that tier produced.
const PIPELINE_TABS: CategoryKey[] = [
  "brain",
  "computer-use",
  "tts",
  "stt",
  "dictation",
  "subagents",
  "jarvis-key",
  "advanced",
];
const REALTIME_TABS: CategoryKey[] = [
  "realtime",
  "computer-use",
  "subagents",
  "jarvis-key",
  "advanced",
];

export function ApiKeysView() {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  // Per-tab health (amber = the active provider isn't set up, red = it's set up
  // but failing a live check). Best-effort and off the render-blocking path.
  const health = useTierHealth(providers);
  const categories = makeProviderCategories(t);
  const [active, setActive] = useState<CategoryKey>("brain");
  const [engineMode, setEngineMode] = useState<VoiceEngineMode>("pipeline");
  // The LIVE `[voice].mode` (+ cross-family availability) for the "Active"
  // badge AND for gating the segment's own persistence (Feature A). See
  // VoiceEngineMode / EngineModeSwitch in the provider module.
  const {
    mode: liveMode,
    realtimeAvailable,
    statusKnown,
    connecting,
    requiresWebRtcOffer,
    transportOfferReady,
    transportOfferDetail,
    transportIssue,
    sessionActive,
    activeSessionMode,
    activeSessionProvider,
    activeSessionModel,
    transitioning,
    lastStartError,
    setMode: setVoiceMode,
    isLoading: liveModeLoading,
  } = useVoiceMode();
  // Show only providers that run on the user's own hardware. A per-machine view
  // preference (localStorage), deliberately NOT a config switch: it hides cards,
  // it never changes what the app runs on. See lib/localMode.ts.
  const { localMode, setLocalMode } = useLocalMode();

  // Reset the selected tab to the mode's first tab whenever the mode changes,
  // so switching Pipeline→Realtime never leaves `active` pointing at a tab
  // that no longer exists in the new mode (e.g. "tts").
  useEffect(() => {
    setActive(engineMode === "realtime" ? "realtime" : "brain");
  }, [engineMode]);

  // Open the view on the engine that is actually LIVE (once, when the mode
  // query resolves) — a user whose voice runs on Realtime should not land on
  // the Pipeline tab set. Later live-mode changes never yank the view.
  const viewSyncedToLive = useRef(false);
  useEffect(() => {
    if (viewSyncedToLive.current || liveModeLoading) return;
    viewSyncedToLive.current = true;
    setEngineMode(liveMode === "realtime" ? "realtime" : "pipeline");
  }, [liveMode, liveModeLoading]);

  const modeTabs = engineMode === "realtime" ? REALTIME_TABS : PIPELINE_TABS;

  // A recommendation row navigates to the tab it talks about. This is VIEW
  // navigation only: opening the Realtime tab set here never persists
  // `[voice].mode` (only the segmented switch does that, key-gated).
  function openRecommendedTab(tab: RecommendationTab) {
    if (tab === "realtime") setEngineMode("realtime");
    setActive(tab);
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<KeyRound className="h-4 w-4 text-primary" />}
        title={t("apikeys_view.title")}
        subtitle={t("apikeys_view.subtitle")}
        right={
          <div className="flex items-start gap-2">
            <LocalModeSwitch enabled={localMode} onToggle={setLocalMode} />
            <EngineModeSwitch
              mode={engineMode}
              liveMode={liveMode}
              realtimeAvailable={realtimeAvailable}
              onSelect={setEngineMode}
              onSetVoiceMode={setVoiceMode}
            />
          </div>
        }
      />

      <CategoryTabs active={active} onSelect={setActive} health={health} tabs={modeTabs} />

      <div
        data-testid="api-keys-provider-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis px-4 py-4"
      >
        <VoiceEngineContext
          mode={engineMode}
          realtimeAvailable={realtimeAvailable}
          statusKnown={statusKnown}
          connecting={connecting}
          requiresWebRtcOffer={requiresWebRtcOffer}
          transportOfferReady={transportOfferReady}
          transportOfferDetail={transportOfferDetail}
          transportIssue={transportIssue}
          sessionActive={sessionActive}
          activeSessionMode={activeSessionMode}
          activeSessionProvider={activeSessionProvider}
          activeSessionModel={activeSessionModel}
          transitioning={transitioning}
          lastStartError={lastStartError}
          liveMode={liveMode}
          onOpenRecommendedTab={openRecommendedTab}
        />
        {/* Readability: the provider cards used to stretch across the full
            window width (2000px+ on wide screens). One centered measure keeps
            every card scannable; the key prop re-runs the rise animation on
            each tab/mode change (respects prefers-reduced-motion). */}
        <div key={`${engineMode}-${active}`} className="profile-rise mx-auto w-full max-w-4xl">
        {(active === "brain" ||
          active === "tts" ||
          active === "stt" ||
          active === "dictation") && (
          <ProviderCategory
            meta={categories[active]}
            tier={active}
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
            health={health[active]}
            localMode={localMode}
            onDisableLocalMode={() => setLocalMode(false)}
          />
        )}
        {active === "realtime" && (
          <RealtimeCategory
            meta={categories.realtime}
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
            health={health.realtime}
            localMode={localMode}
            onDisableLocalMode={() => setLocalMode(false)}
          />
        )}
        {active === "computer-use" && (
          <ComputerUseCategory
            meta={categories["computer-use"]}
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
            health={health["computer-use"]}
            localMode={localMode}
            onDisableLocalMode={() => setLocalMode(false)}
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
 * The segmented category navigation. The four core categories are grouped in one
 * pill container; the de-emphasized "Advanced" tab is set apart by a divider and
 * neutral (non-gold) styling so it reads as secondary, never competing with the
 * four primary categories.
 */
function CategoryTabs({
  active,
  onSelect,
  health,
  tabs,
}: {
  active: CategoryKey;
  onSelect: (key: CategoryKey) => void;
  /** Per-tab health rollup keyed by category; absent keys render no dot. */
  health: Record<string, SectionHealth>;
  /** The mode-derived tab list (PIPELINE_TABS / REALTIME_TABS) — "advanced",
   *  if present, is rendered separately (de-emphasized, past a divider). */
  tabs: CategoryKey[];
}) {
  const t = useT();
  type CoreTab = Exclude<CategoryKey, "advanced" | "jarvis-key">;
  const tabMeta: Record<CoreTab, { label: string; icon: LucideIcon }> = {
    brain: { label: t("apikeys_view.tab_brain"), icon: Brain },
    tts: { label: t("apikeys_view.tab_tts"), icon: Volume2 },
    stt: { label: t("apikeys_view.tab_stt"), icon: Mic },
    realtime: { label: t("apikeys_view.tab_realtime"), icon: Radio },
    "computer-use": { label: t("apikeys_view.tab_computer_use"), icon: Terminal },
    dictation: { label: t("apikeys_view.tab_dictation"), icon: Wand2 },
    subagents: { label: t("apikeys_view.tab_subagents"), icon: Bot },
  };
  const coreTabs = tabs.filter(
    (key): key is CoreTab => key !== "advanced" && key !== "jarvis-key",
  );
  const showJarvisKey = tabs.includes("jarvis-key");
  const showAdvanced = tabs.includes("advanced");
  return (
    <div
      data-testid="api-keys-category-tabs"
      className="shrink-0 overflow-x-auto border-b border-border px-4 py-2 scrollbar-jarvis"
    >
      <div role="tablist" className="flex min-w-max flex-nowrap items-center gap-1.5">
        <div className="flex flex-nowrap items-center gap-1 rounded-xl border border-border bg-card/40 p-1">
          {coreTabs.map((key) => (
            <TabButton
              key={key}
              icon={tabMeta[key].icon}
              label={tabMeta[key].label}
              selected={active === key}
              onClick={() => onSelect(key)}
              health={health[key]}
            />
          ))}
        </div>
        {(showJarvisKey || showAdvanced) && (
          <span
            className="mx-1 hidden h-6 w-px bg-border/70 sm:block"
            aria-hidden="true"
          />
        )}
        {showJarvisKey && (
          <TabButton
            icon={KeyRound}
            label={t("apikeys_view.tab_jarvis_key")}
            selected={active === "jarvis-key"}
            onClick={() => onSelect("jarvis-key")}
            health={health["jarvis-key"]}
            muted
          />
        )}
        {showAdvanced && (
          <TabButton
            icon={SlidersHorizontal}
            label={t("apikeys_view.tab_advanced")}
            selected={active === "advanced"}
            onClick={() => onSelect("advanced")}
            health={health.advanced}
            muted
          />
        )}
      </div>
    </div>
  );
}

function TabButton({
  icon: Icon,
  label,
  selected,
  onClick,
  muted = false,
  health,
}: {
  icon: LucideIcon;
  label: string;
  selected: boolean;
  onClick: () => void;
  /** The de-emphasized "Advanced" tab: neutral fill instead of the gold accent. */
  muted?: boolean;
  /** Optional health rollup driving the corner status dot. */
  health?: SectionHealth;
}) {
  const t = useT();
  // Only the two "needs attention" states draw a dot — amber for "still has to be
  // set up", red for "set up but not working". `ok` / `unknown` stay silent so the
  // tab bar is calm and a dot always means "look here".
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
  // (e.g. "Groq STT: key invalid"), so hovering explains exactly what's wrong.
  const title = indicator
    ? [statusLabel, health?.detail].filter(Boolean).join(" — ")
    : undefined;

  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      onClick={onClick}
      title={title}
      className={cn(
        "relative inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
        muted
          ? selected
            ? "bg-secondary text-foreground ring-1 ring-border"
            : "text-muted-foreground/70 hover:bg-secondary/50 hover:text-foreground"
          : selected
            ? "bg-primary/10 text-primary ring-1 ring-primary/30"
            : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
        // A faint red outline echoes the "rot umrandet" cue for a broken section,
        // independent of the selection ring so the two never fight.
        indicator === "error" && "outline outline-1 outline-destructive/70",
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
      {indicator && (
        <>
          <span
            aria-hidden="true"
            className={cn(
              "absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-background",
              indicator === "error" ? "bg-destructive" : "bg-amber-500",
            )}
          />
          <span className="sr-only">{` (${statusLabel})`}</span>
        </>
      )}
    </button>
  );
}

/**
 * The Realtime category (Feature B): the two realtime provider cards, via the
 * SAME `ProviderCategory` used for brain/tts/stt (unchanged). Realtime
 * speech-to-speech models can't see the screen, so Computer-Use during a
 * realtime turn runs on the dedicated Computer-Use provider (or the active
 * Brain provider, until one is picked) — now its own "Computer-Use" tab
 * (see `ComputerUseCategory` below) rather than a panel embedded here. This
 * wrapper mirrors `SubagentCategory` below: it owns nothing itself, it just
 * composes the existing tier section.
 */
function RealtimeCategory({
  meta,
  providers,
  loading,
  error,
  onChanged,
  onActivateOptimistic,
  health,
  localMode = false,
  onDisableLocalMode,
}: {
  meta: CategoryMeta;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
  localMode?: boolean;
  onDisableLocalMode?: () => void;
}) {
  const t = useT();
  return (
    <ProviderCategory
      meta={meta}
      tier="realtime"
      providers={providers}
      loading={loading}
      error={error}
      onChanged={onChanged}
      onActivateOptimistic={onActivateOptimistic}
      health={health}
      localMode={localMode}
      onDisableLocalMode={onDisableLocalMode}
      intro={
        <GuidancePanel
          title={t("apikeys_view.guide_realtime_title")}
          body={t("apikeys_view.guide_realtime_body")}
        />
      }
    />
  );
}

/**
 * The Computer-Use tab: an OVERLAY over the brain-tier provider cards
 * (Claude/OpenAI/OpenRouter/Gemini), NOT a new provider tier. Reuses the SAME
 * `ProviderCategory`/`TierSection`/`ProviderCard` machinery as Brain/TTS/STT
 * by mapping every brain-switchable provider to a synthetic `"computer-use"`
 * tier descriptor whose `active` mirrors `computer_use_active` — a SEPARATE
 * selection from the Brain tab's `active`/`brain.primary`. The synthetic
 * `tier` value forks the shared machinery cleanly: the radio group's
 * `name="active-computer-use"` never collides with `name="active-brain"`,
 * and `ProviderCard.activate()` routes to `switchComputerUseProvider` instead
 * of `switchBrainProvider`. The CU provider is GLOBAL (one engine for the
 * whole app), so this tab renders identically in Pipeline and Realtime mode —
 * it replaces the old `RealtimeComputerUsePanel`, which only displayed the
 * delegation without letting the user pick a provider.
 */
function ComputerUseCategory({
  meta,
  providers,
  loading,
  error,
  onChanged,
  onActivateOptimistic,
  health,
  localMode = false,
  onDisableLocalMode,
}: {
  meta: CategoryMeta;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
  localMode?: boolean;
  onDisableLocalMode?: () => void;
}) {
  const t = useT();
  const cuProviders: ProviderDescriptor[] = providers
    .filter((p) => p.tier === "brain" && p.brain_switchable !== false)
    .map((p) => ({ ...p, tier: "computer-use", active: !!p.computer_use_active }));

  return (
    <ProviderCategory
      meta={meta}
      tier="computer-use"
      providers={cuProviders}
      loading={loading}
      error={error}
      onChanged={onChanged}
      onActivateOptimistic={onActivateOptimistic}
      health={health}
      localMode={localMode}
      onDisableLocalMode={onDisableLocalMode}
      intro={
        <GuidancePanel
          title={t("apikeys_view.guide_computer_use_title")}
          body={t("apikeys_view.guide_computer_use_body")}
        />
      }
    />
  );
}

/**
 * The Subagents category — the heavy-task worker selection. `SubagentSection`
 * owns its own data source (/api/jarvis-agent/status) and card system; the hero band
 * just frames it consistently with the provider tiers.
 */
function SubagentCategory() {
  const t = useT();
  return (
    <div role="tabpanel">
      <CategoryHero
        icon={Bot}
        title={t("apikeys_view.cat_subagents_title")}
        description={t("apikeys_view.cat_subagents_desc")}
      />
      <JarvisAgentSection hideHeader />
    </div>
  );
}

/**
 * The dedicated "<Name> Key" category — the per-install Control Key that
 * unlocks the browser UI and authenticates local agents. The tab and hero are
 * named after the configured wake word via the i18n `{name}` token ("Hannes"
 * -> "Hannes Key"), so the section the lock screen points at carries the name
 * the user actually knows their assistant by.
 */
function JarvisKeyCategory() {
  const t = useT();
  return (
    <div role="tabpanel">
      <CategoryHero
        icon={KeyRound}
        title={t("apikeys_view.jarvis_key_title")}
        description={t("apikeys_view.jarvis_key_desc")}
      />
      <div className="space-y-4">
        <JarvisApiGroup />
      </div>
    </div>
  );
}

/**
 * The de-emphasized "Advanced" category — everything that is NOT one of the four
 * core provider categories: the team key proxy, telephony, and the
 * knowledge-Wiki provider. Each block keeps its own labelled sub-section
 * header, so the zone reads as a clearly separated list of optional
 * integrations rather than competing with the four primary categories.
 */
function AdvancedCategory() {
  const t = useT();
  return (
    <div role="tabpanel">
      <CategoryHero
        icon={SlidersHorizontal}
        title={t("apikeys_view.advanced_title")}
        description={t("apikeys_view.advanced_desc")}
      />
      <div className="space-y-4">
        {/* Team key proxy — credential / key-routing management, so it lives
            with the provider keys rather than in the behaviour-focused
            Settings view. */}
        <TeamProxyGroup />
        {/* Telephony — the former standalone screen, embedded as a section (own
            data source /api/telephony/*). */}
        <TelephonySection />
        {/* Wiki — dedicated long-term-memory curator provider/model. Own data
            source (/api/settings/wiki-provider). */}
        <WikiProviderCard />
        {/* Nominative-use trademark notice: provider/integration names and logos
            belong to their owners and are shown only to identify what you connect
            to. Backs the third-party logos used on plugin cards (see
            TRADEMARK.md). */}
        <p className="pt-2 text-[11px] leading-relaxed text-muted-foreground">
          {t("apikeys_view.trademark_notice")}
        </p>
      </div>
    </div>
  );
}

/**
 * Telephony tier section. Visually a sibling of the brain/tts/stt/subagent
 * tiers: the same uppercase tier header (Phone icon + label) above the embedded
 * `TelephonyPanel`, which carries the status / credentials / calls cards in the
 * shared `card-outline` style. The heavier setup scripts + guide moved to the
 * dedicated TelephonySetupView (reached via the panel's "Setup script" button)
 * to keep this section compact. Its own data source (`/api/telephony/*`) is
 * owned by the panel, so this stays a thin wrapper.
 */
function TelephonySection() {
  const t = useT();
  return (
    <section>
      <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <Phone className="h-3.5 w-3.5" /> {t("apikeys_view.tier_telephony")}
      </h3>
      <TelephonyPanel />
    </section>
  );
}

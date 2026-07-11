import { useEffect, useRef, useState } from "react";
import { useMemo } from "react";
import { AlertCircle, Bot, Brain, Check, Copy, KeyRound, Loader2, LogIn, LogOut, Mic, Phone, PlugZap, Radio, SlidersHorizontal, Sparkles, Terminal, Volume2, Waypoints, XCircle } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { AltCredentialNote } from "@/components/AltCredentialNote";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { OpenRouterTtsControls } from "@/components/OpenRouterTtsVoicePicker";
import { CuModelSelector } from "@/components/CuModelSelector";
import { RealtimeOptionsControl } from "@/components/RealtimeOptionsControl";
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";
import { JarvisAgentSection } from "@/components/JarvisAgentSection";
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
import { JarvisApiGroup } from "@/views/settings/JarvisApiGroup";
import { TeamProxyGroup } from "@/views/settings/TeamProxyGroup";
import { Button } from "@/components/ui/button";
import {
  codexLogout,
  loginAntigravity,
  logoutAntigravity,
  type ProviderDescriptor,
  type ProviderTestResult,
  type ProviderTestStatus,
  type ProviderTier,
  type SectionHealth,
  sectionHealthForSubject,
  startCodexLogin,
  switchBrainProvider,
  switchComputerUseProvider,
  switchRealtimeProvider,
  switchSttProvider,
  switchTtsProvider,
  testProvider,
  useProviders,
  useSectionHealth,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

// The view is organised around exactly five primary categories — Brain, Voice
// Output (TTS), Voice Input (STT), Realtime and Subagents — surfaced as a
// segmented tab bar. Everything else (Control-API key, team key proxy,
// telephony, Wiki) lives in a clearly separated, de-emphasized "Advanced" tab
// so it never competes with the five core categories.
type CategoryKey = ProviderTier | "subagents" | "advanced";

// The top-level engine mode. Feature A supersedes design D1 ("view-only"):
// the switch now decides which tab set is shown AND persists `[voice].mode`
// via `useVoiceMode().setMode` — Pipeline always (it's always reachable),
// Realtime only when a realtime provider actually has a key
// (`realtimeAvailable`), so the switch can never pin the boot default to an
// unreachable engine. See `EngineModeSwitch` below for the exact rule.
type VoiceEngineMode = "pipeline" | "realtime";

// Realtime replaces STT+Brain+TTS with one full-duplex model, so those three
// tiers don't apply in Realtime mode — that's the whole reason for the split.
// "computer-use" is GLOBAL (not mode-specific — Computer-Use is one engine for
// the whole app), so it appears right after the main chat-model tab in BOTH
// tab sets.
const PIPELINE_TABS: CategoryKey[] = [
  "brain",
  "computer-use",
  "tts",
  "stt",
  "subagents",
  "advanced",
];
const REALTIME_TABS: CategoryKey[] = ["realtime", "computer-use", "subagents", "advanced"];

type LucideIcon = typeof Brain;

interface CategoryMeta {
  /** Short label for the segmented tab. */
  tab: string;
  /** Full heading shown in the category hero band. */
  title: string;
  /** One-line plain-language description under the heading. */
  description: string;
  icon: LucideIcon;
}

// Meta for the three provider tiers (brain/tts/stt). Subagents and Advanced are
// composed separately because they own their own data sources / sub-sections.
function makeProviderCategories(
  t: (k: string) => string,
): Record<ProviderTier, CategoryMeta> {
  return {
    brain: {
      tab: t("apikeys_view.tab_brain"),
      title: t("apikeys_view.tier_brain"),
      description: t("apikeys_view.cat_brain_desc"),
      icon: Brain,
    },
    tts: {
      tab: t("apikeys_view.tab_tts"),
      title: t("apikeys_view.tier_tts"),
      description: t("apikeys_view.cat_tts_desc"),
      icon: Volume2,
    },
    stt: {
      tab: t("apikeys_view.tab_stt"),
      title: t("apikeys_view.tier_stt"),
      description: t("apikeys_view.cat_stt_desc"),
      icon: Mic,
    },
    realtime: {
      tab: t("apikeys_view.tab_realtime"),
      title: t("apikeys_view.tier_realtime"),
      description: t("apikeys_view.cat_realtime_desc"),
      icon: Radio,
    },
    "computer-use": {
      tab: t("apikeys_view.tab_computer_use"),
      title: t("apikeys_view.tier_computer_use"),
      description: t("apikeys_view.cat_computer_use_desc"),
      icon: Terminal,
    },
  };
}

export function ApiKeysView() {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  // Per-tab health (amber = the active provider isn't set up, red = it's set up
  // but failing a live check). Best-effort and off the render-blocking path.
  const { health: rawHealth } = useSectionHealth();
  const health = useMemo(() => {
    const visible = { ...rawHealth };
    const activeSubjects: Record<string, string | undefined> = {
      brain: providers.find((provider) => provider.tier === "brain" && provider.active)?.id,
      tts: providers.find((provider) => provider.tier === "tts" && provider.active)?.id,
      stt: providers.find((provider) => provider.tier === "stt" && provider.active)?.id,
      realtime: providers.find(
        (provider) => provider.tier === "realtime" && provider.active,
      )?.id,
      "computer-use": providers.find(
        (provider) => provider.tier === "brain" && provider.computer_use_active,
      )?.id,
    };
    for (const [section, subjectId] of Object.entries(activeSubjects)) {
      const matching = sectionHealthForSubject(rawHealth[section], subjectId);
      if (matching) visible[section] = matching;
      else delete visible[section];
    }
    return visible;
  }, [providers, rawHealth]);
  const categories = makeProviderCategories(t);
  const [active, setActive] = useState<CategoryKey>("brain");
  const [engineMode, setEngineMode] = useState<VoiceEngineMode>("pipeline");
  // The LIVE `[voice].mode` (+ cross-family availability) for the "Active"
  // badge AND for gating the segment's own persistence (Feature A). See
  // VoiceEngineMode / EngineModeSwitch above.
  const {
    mode: liveMode,
    realtimeAvailable,
    sessionActive,
    activeSessionMode,
    activeSessionProvider,
    activeSessionModel,
    transitioning,
    setMode: setVoiceMode,
    isLoading: liveModeLoading,
  } = useVoiceMode();

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

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<KeyRound className="h-4 w-4 text-primary" />}
        title={t("apikeys_view.title")}
        subtitle={t("apikeys_view.subtitle")}
      />

      <EngineModeSwitch
        mode={engineMode}
        liveMode={liveMode}
        realtimeAvailable={realtimeAvailable}
        sessionActive={sessionActive}
        activeSessionMode={activeSessionMode}
        activeSessionProvider={activeSessionProvider}
        activeSessionModel={activeSessionModel}
        transitioning={transitioning}
        onSelect={setEngineMode}
        onSetVoiceMode={setVoiceMode}
      />

      <CategoryTabs active={active} onSelect={setActive} health={health} tabs={modeTabs} />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        {/* Readability: the provider cards used to stretch across the full
            window width (2000px+ on wide screens). One centered measure keeps
            every card scannable; the key prop re-runs the rise animation on
            each tab/mode change (respects prefers-reduced-motion). */}
        <div key={`${engineMode}-${active}`} className="profile-rise mx-auto w-full max-w-4xl">
        {(active === "brain" || active === "tts" || active === "stt") && (
          <ProviderCategory
            meta={categories[active]}
            tier={active}
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
            health={health[active]}
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
          />
        )}
        {active === "subagents" && <SubagentCategory />}
        {active === "advanced" && <AdvancedCategory />}
        </div>
      </div>
    </div>
  );
}

/**
 * The Pipeline|Realtime segmented switch. Feature A (supersedes D1): clicking
 * a segment still switches the local view (`onSelect`), and ALSO persists
 * `[voice].mode` — Pipeline unconditionally (always reachable), Realtime only
 * when `realtimeAvailable` (a key is present for some realtime family);
 * otherwise the click just switches the view so the user can add a key from
 * the Realtime tab without silently pinning the boot default to a dead
 * engine.
 *
 * Visual system (one system, two legible states):
 * - A sliding gold thumb sits under the segment matching the LIVE
 *   `[voice].mode` — unmistakably "this engine is on". `useVoiceMode` updates
 *   its cache optimistically, so the thumb follows the click INSTANTLY and
 *   only rolls back if the persist fails.
 * - The segment currently being VIEWED but not live gets a subtle outline
 *   only (no fill) — it's a transient look, not "on".
 * - Realtime with no key configured anywhere (`!realtimeAvailable`) reads
 *   muted with an "add a key" caption below; it stays clickable (opens the
 *   Realtime tab so the user can add one) but never gets the fill.
 * - One caption line under the control describes the VIEWED engine in plain
 *   words, so "Pipeline vs Realtime" needs no prior knowledge.
 */
function EngineModeSwitch({
  mode,
  liveMode,
  realtimeAvailable,
  sessionActive,
  activeSessionMode,
  activeSessionProvider,
  activeSessionModel,
  transitioning,
  onSelect,
  onSetVoiceMode,
}: {
  mode: VoiceEngineMode;
  /** The live `[voice].mode` value — determines the filled/active segment. */
  liveMode: string;
  /** Whether SOME realtime family (OpenAI/Gemini) has a key configured. */
  realtimeAvailable: boolean;
  /** What the currently open voice session actually uses. */
  sessionActive: boolean;
  activeSessionMode: "pipeline" | "realtime" | null;
  activeSessionProvider: string;
  activeSessionModel: string;
  transitioning: boolean;
  onSelect: (mode: VoiceEngineMode) => void;
  /** Persists `[voice].mode` — gated per the rule above. */
  onSetVoiceMode: (mode: string) => void;
}) {
  const t = useT();
  // Realtime leads: it is the recommended default, so it takes the first
  // (left) segment with the Recommended badge fully visible.
  const segments: { key: VoiceEngineMode; label: string; icon: LucideIcon }[] = [
    { key: "realtime", label: t("apikeys_view.mode_realtime"), icon: Radio },
    { key: "pipeline", label: t("apikeys_view.mode_pipeline"), icon: Waypoints },
  ];
  const liveIndex = liveMode === "realtime" ? 0 : 1;
  const runtimeDetail = [activeSessionProvider, activeSessionModel]
    .filter(Boolean)
    .join(" · ");
  const runtimeText = transitioning
    ? t("apikeys_view.runtime_switching")
    : sessionActive && activeSessionMode === "realtime"
      ? `${t("apikeys_view.runtime_realtime")}${runtimeDetail ? ` · ${runtimeDetail}` : ""}`
      : sessionActive && activeSessionMode === "pipeline" && liveMode === "realtime"
        ? t("apikeys_view.runtime_fallback_pipeline")
        : sessionActive && activeSessionMode === "pipeline"
          ? t("apikeys_view.runtime_pipeline")
          : t("apikeys_view.runtime_idle");
  const runtimeMatchesSelection =
    !sessionActive || activeSessionMode === null || activeSessionMode === liveMode;

  function handleSelect(seg: VoiceEngineMode) {
    onSelect(seg);
    if (seg === "pipeline" || realtimeAvailable) {
      onSetVoiceMode(seg);
    }
  }

  return (
    <div className="border-b border-border px-6 pt-4 pb-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 className="font-display text-sm font-semibold text-foreground">
            {t("apikeys_view.voice_engine_label")}
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("apikeys_view.voice_engine_desc")}
          </p>
        </div>

        <div className="shrink-0">
          {/* Two equal segments over one sliding thumb: the thumb tracks the
              LIVE engine, so its glide IS the switch feedback. */}
          {/* w-auto + equal columns: the container grows with the widest
              segment (Realtime + Recommended badge) instead of clipping it;
              the sliding thumb stays correct at any width. */}
          <div className="relative grid w-auto min-w-64 grid-cols-2 rounded-xl border border-border bg-card/40 p-1">
            <span
              aria-hidden="true"
              className="absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-lg bg-primary shadow-[0_0_18px_rgba(255,214,10,0.25)] transition-transform duration-200 ease-out"
              style={{ transform: `translateX(${liveIndex * 100}%)` }}
            />
            {segments.map((seg) => {
              const isLive = liveMode === seg.key;
              const isViewedOnly = mode === seg.key && !isLive;
              const needsKey = seg.key === "realtime" && !realtimeAvailable;
              const Icon = seg.icon;
              return (
                <button
                  key={seg.key}
                  type="button"
                  onClick={() => handleSelect(seg.key)}
                  aria-pressed={mode === seg.key}
                  className={cn(
                    "relative z-10 inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    isLive
                      ? "text-primary-foreground"
                      : isViewedOnly
                        ? "text-foreground ring-1 ring-border"
                        : needsKey
                          ? "text-muted-foreground/60 hover:text-muted-foreground"
                          : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Icon aria-hidden="true" className="h-3.5 w-3.5" />
                  {seg.label}
                  {seg.key === "realtime" && (
                    <span
                      className={cn(
                        "whitespace-nowrap rounded-full px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide",
                        isLive
                          ? "bg-primary-foreground/20 text-primary-foreground"
                          : "bg-primary/15 text-primary",
                      )}
                    >
                      {t("apikeys_view.mode_recommended")}
                    </span>
                  )}
                  {isLive && (
                    <span className="sr-only">{t("apikeys_view.mode_active_badge")}</span>
                  )}
                </button>
              );
            })}
          </div>
          <p className="mt-1.5 max-w-64 text-right text-[11px] leading-snug text-muted-foreground">
            {mode === "realtime" && !realtimeAvailable
              ? t("apikeys_view.mode_needs_key")
              : mode === "realtime"
                ? t("apikeys_view.mode_desc_realtime")
                : t("apikeys_view.mode_desc_pipeline")}
          </p>
          <div
            className="mt-2 flex max-w-64 items-start justify-end gap-1.5 text-right text-[11px] leading-snug"
            aria-live="polite"
            data-testid="voice-engine-runtime-status"
          >
            <span
              aria-hidden="true"
              className={cn(
                "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                transitioning
                  ? "animate-pulse bg-amber-400 motion-reduce:animate-none"
                  : runtimeMatchesSelection
                    ? "bg-emerald-400"
                    : "bg-amber-400",
              )}
            />
            <span
              className={cn(
                runtimeMatchesSelection && !transitioning
                  ? "text-muted-foreground"
                  : "text-amber-300",
              )}
            >
              {runtimeText}
            </span>
          </div>
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
  const tabMeta: Record<Exclude<CategoryKey, "advanced">, { label: string; icon: LucideIcon }> = {
    brain: { label: t("apikeys_view.tab_brain"), icon: Brain },
    tts: { label: t("apikeys_view.tab_tts"), icon: Volume2 },
    stt: { label: t("apikeys_view.tab_stt"), icon: Mic },
    realtime: { label: t("apikeys_view.tab_realtime"), icon: Radio },
    "computer-use": { label: t("apikeys_view.tab_computer_use"), icon: Terminal },
    subagents: { label: t("apikeys_view.tab_subagents"), icon: Bot },
  };
  const coreTabs = tabs.filter(
    (key): key is Exclude<CategoryKey, "advanced"> => key !== "advanced",
  );
  const showAdvanced = tabs.includes("advanced");
  return (
    <div className="border-b border-border px-6 py-3">
      <div role="tablist" className="flex flex-wrap items-center gap-1.5">
        <div className="flex flex-wrap items-center gap-1 rounded-xl border border-border bg-card/40 p-1">
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
        {showAdvanced && (
          <>
            <span
              className="mx-1 hidden h-6 w-px bg-border/70 sm:block"
              aria-hidden="true"
            />
            <TabButton
              icon={SlidersHorizontal}
              label={t("apikeys_view.tab_advanced")}
              selected={active === "advanced"}
              onClick={() => onSelect("advanced")}
              health={health.advanced}
              muted
            />
          </>
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
        "relative inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors",
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
 * The header band atop each category panel: an icon chip, the full category
 * title (display font), and a one-line plain-language description. Mirrors the
 * app's `ViewHeader` icon treatment so the screen reads as one system.
 */
function CategoryHero({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="mb-6 flex items-start gap-3">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-secondary/40 text-primary">
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0 pt-0.5">
        <h3 className="font-display text-base font-semibold tracking-tight">
          {title}
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
      </div>
    </div>
  );
}

/**
 * A soft, gold-tinted "which one should I pick?" band — the one place a
 * category speaks up with an opinion. Used by the Realtime and Computer-Use
 * tabs, where the model choice genuinely confuses people; the calmer tiers
 * carry their guidance on the cards themselves (Recommended badges).
 */
function GuidancePanel({ title, body }: { title: string; body: string }) {
  return (
    <div className="mb-5 flex items-start gap-3 rounded-xl border border-primary/25 bg-primary/[0.05] px-4 py-3">
      <Sparkles aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 text-xs leading-relaxed">
        <p className="font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-muted-foreground">{body}</p>
      </div>
    </div>
  );
}

/**
 * One of the three provider tiers (brain/tts/stt): the hero band plus the
 * loading / error / empty / card states. The card list itself is `TierSection`,
 * reused unchanged from the original layout.
 */
function ProviderCategory({
  meta,
  tier,
  providers,
  loading,
  error,
  onChanged,
  onActivateOptimistic,
  health,
  intro,
}: {
  meta: CategoryMeta;
  tier: ProviderTier;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  /** Live health of this tier's ACTIVE provider — drills the tab's red dot down
   *  onto the exact card that is failing so the user sees WHICH provider broke. */
  health?: SectionHealth;
  /** Optional guidance band rendered between the hero and the card list. */
  intro?: React.ReactNode;
}) {
  const t = useT();
  const tierProviders = providers.filter(
    (p) => p.tier === tier && p.brain_switchable !== false,
  );

  return (
    <div role="tabpanel">
      <CategoryHero icon={meta.icon} title={meta.title} description={meta.description} />

      {intro}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> {t("apikeys_view.loading_providers")}
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertCircle className="mt-0.5 h-4 w-4" />
          <div>
            {t("apikeys_view.load_error")} ({error}).
            <button onClick={() => onChanged()} className="ml-2 underline">
              {t("apikeys_view.retry")}
            </button>
          </div>
        </div>
      )}

      {!loading && !error && tierProviders.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t("apikeys_view.no_providers_in_tier")}
        </p>
      )}

      {!loading && !error && tierProviders.length > 0 && (
        <TierSection
          providers={tierProviders}
          onChanged={onChanged}
          onActivateOptimistic={onActivateOptimistic}
          health={health}
        />
      )}
    </div>
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
}: {
  meta: CategoryMeta;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
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
}: {
  meta: CategoryMeta;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  health?: SectionHealth;
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
 * owns its own data source (/api/openclaw/status) and card system; the hero band
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
 * The de-emphasized "Advanced" category — everything that is NOT one of the four
 * core provider categories: the local Control-API key, the team key proxy,
 * telephony, and the knowledge-Wiki provider. Each block keeps its own labelled
 * sub-section header, so the zone reads as a clearly separated list of optional
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
        {/* Jarvis access — the local Control-API key (lets local coding agents
            drive Jarvis over HTTP) and the optional Team key proxy. Both are
            credential / key-routing management, so they live with the provider
            keys rather than in the behaviour-focused Settings view. */}
        <JarvisApiGroup />
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

// The card list for a single provider tier. The category label now lives in the
// tab bar + the `CategoryHero` above, so this renders only the cards. If nobody
// in the tier is active yet, a freshly saved key auto-activates itself — the
// first configured provider wins automatically (`autoActivateOnSave`).
function TierSection({
  providers,
  onChanged,
  onActivateOptimistic,
  health,
}: {
  providers: ProviderDescriptor[];
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  /** Tier health — handed only to the ACTIVE card, since section-health tests
   *  exactly the one provider powering this tier. */
  health?: SectionHealth;
}) {
  const tierHasActive = providers.some((p) => p.active);
  // Configured (or active) providers first — the wall of empty key forms used
  // to bury the one or two cards the user actually set up. `active` counts so
  // an active-but-keyless anomaly (e.g. a free-tier provider) can never hide
  // below untouched cards; among configured cards nothing reorders on a switch
  // (both stay rank 0), so a card never jumps under the pointer mid-click.
  // Stable within each group (Array.sort is stable).
  const sorted = [...providers].sort(
    (a, b) =>
      Number(b.configured || b.active) - Number(a.configured || a.active),
  );
  return (
    <ul className="space-y-3">
      {sorted.map((p) => (
        <li key={p.id}>
          <ProviderCard
            descriptor={p}
            onChanged={onChanged}
            onActivateOptimistic={onActivateOptimistic}
            autoActivateOnSave={!tierHasActive}
            health={p.active ? sectionHealthForSubject(health, p.id) : undefined}
          />
        </li>
      ))}
    </ul>
  );
}

function ProviderCard({
  descriptor,
  onChanged,
  onActivateOptimistic,
  autoActivateOnSave,
  health,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  autoActivateOnSave: boolean;
  /** Live section-health for THIS card (set only on the active provider). A
   *  status of "error" turns the card red and surfaces the cause inline — the
   *  tab dot says "something here is broken", this says exactly WHAT/WHERE. */
  health?: SectionHealth;
}) {
  const t = useT();
  const [activating, setActivating] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);
  // The card only escalates to red for a real "set up but failing" error — the
  // amber "needs setup" case stays on the tab + the open/ready badge so a fresh,
  // half-configured screen doesn't paint cards red.
  const cardError = descriptor.active && health?.status === "error";
  // The backend one-liner (e.g. "OpenRouter: rate limited") already names the
  // provider + cause, so it answers "what is wrong" without a second lookup.
  const cardErrorDetail = health?.detail?.trim() || "";

  // Codex is filtered out of the Brain tier (`brain_switchable=false`) and is
  // selected from the Subagent section. This branch stays for older payloads or
  // tests that still mount a Codex descriptor directly.
  const isCodex = descriptor.auth_mode === "codex";
  const isBrainSwitchable =
    descriptor.tier !== "brain" || descriptor.brain_switchable !== false;

  async function activate(assumeConfigured = false) {
    if (descriptor.active) return;
    if (!isBrainSwitchable) {
      pushToast("warning", `${descriptor.label} is only available for Jarvis-Agents.`);
      return;
    }
    if (isCodex && !descriptor.codex_brain_ready) {
      // The card is "connected" via OAuth, but a chat brain needs an OpenAI key.
      // Guide honestly instead of switching and failing on the first turn.
      pushToast("warning", t("apikeys_codex.brain_needs_openai_key"));
      return;
    }
    if (!assumeConfigured && !descriptor.configured) {
      pushToast(
        "warning",
        descriptor.auth_mode === "codex"
          ? t("apikeys_codex.needs_codex_full").replace("{0}", descriptor.label)
          : descriptor.auth_mode === "antigravity"
            ? t("apikeys_antigravity.needs_login_full").replace("{0}", descriptor.label)
            : t("apikeys_codex.needs_key_full").replace("{0}", descriptor.label),
      );
      return;
    }
    // Flip the highlight immediately so the switch feels instant — the backend
    // call below can take a few seconds (a TTS switch rebuilds the provider and
    // injects it into the live pipeline). The refetch on success / failure then
    // reconciles with server truth.
    onActivateOptimistic(descriptor.tier, descriptor.id);
    setActivating(true);
    try {
      if (descriptor.tier === "brain") {
        await switchBrainProvider(descriptor.id);
        pushToast("success", `Brain → ${descriptor.label}`);
        window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      } else if (descriptor.tier === "tts") {
        const result = await switchTtsProvider(descriptor.id);
        const note = result.restart_required
          ? " (active from next voice start)"
          : "";
        pushToast("success", `Voice output → ${descriptor.label}${note}`);
        window.dispatchEvent(new CustomEvent("jarvis:tts-switched"));
      } else if (descriptor.tier === "stt") {
        const result = await switchSttProvider(descriptor.id);
        const note = result.restart_required
          ? " (active from next voice start)"
          : "";
        pushToast("success", `Voice input → ${descriptor.label}${note}`);
        window.dispatchEvent(new CustomEvent("jarvis:stt-switched"));
      } else if (descriptor.tier === "computer-use") {
        await switchComputerUseProvider(descriptor.id);
        pushToast("success", `Computer-Use → ${descriptor.label}`);
        window.dispatchEvent(new CustomEvent("jarvis:computer-use-switched"));
      } else {
        const result = await switchRealtimeProvider(descriptor.id);
        const note = result.restart_required
          ? " (active from next voice start)"
          : "";
        pushToast("success", `Realtime → ${descriptor.label}${note}`);
        window.dispatchEvent(new CustomEvent("jarvis:realtime-switched"));
      }
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
      // Roll the optimistic highlight back to the true active provider.
      onChanged();
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-switch-failed", {
          detail: { section: descriptor.tier, provider: descriptor.id },
        }),
      );
    } finally {
      setActivating(false);
    }
  }

  // A single click AND a double click on the card both activate the provider.
  // We explicitly filter clicks on interactive sub-elements (inputs, buttons,
  // links) so that a click into the password field or on the
  // "Replace"/trash icon does NOT accidentally trigger a switch. The radio
  // also has its own stopPropagation for historical reasons
  // (belt and suspenders).
  function handleCardActivate(e: React.MouseEvent<HTMLDivElement>) {
    // Codex is connection-only — a card click must never trigger a brain switch.
    if (isCodex) return;
    if (!isBrainSwitchable) return;
    const target = e.target as HTMLElement | null;
    if (
      target &&
      (target.closest("input") ||
        target.closest("button") ||
        target.closest("a") ||
        target.closest("label"))
    ) {
      return;
    }
    void activate();
  }

  // Called by ApiKeyForm as soon as a key has been saved for a previously
  // unconfigured provider. If no one else is active in this tier, the
  // freshly configured provider takes over automatically — otherwise the
  // user has to click the now-visible "Activate" button.
  async function handleSavedActivate() {
    if (!autoActivateOnSave) return;
    if (descriptor.active) return;
    await activate(true);
  }

  return (
    <div
      onClick={handleCardActivate}
      onDoubleClick={handleCardActivate}
      title={
        descriptor.active
          ? t("apikeys_view.active_tooltip")
          : !isBrainSwitchable
            ? "Available for Jarvis-Agents only"
          : descriptor.configured
            ? t("apikeys_view.click_to_activate")
            : descriptor.auth_mode === "codex"
              ? t("apikeys_view.needs_codex")
              : descriptor.auth_mode === "antigravity"
                ? t("apikeys_view.needs_login")
                : t("apikeys_view.needs_key")
      }
      className={cn(
        "card-outline space-y-3 p-4 transition-colors",
        // A broken active provider wins the card's frame — red outline + faint
        // red wash — so the eye lands on the exact card behind the tab's red dot.
        cardError
          ? "border-destructive/70 bg-destructive/[0.05] ring-1 ring-destructive/30"
          : descriptor.active
            ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
            : descriptor.configured
              ? isBrainSwitchable
                ? "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]"
                : ""
              : "opacity-95",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-display text-sm font-semibold tracking-tight">
              {descriptor.label}
            </span>
            <StatusBadge descriptor={descriptor} />
            {descriptor.recommended && (
              <span
                className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary"
                title={
                  descriptor.recommended_model
                    ? t("apikeys_view.recommended_tooltip").replace(
                        "{0}",
                        descriptor.recommended_model,
                      )
                    : undefined
                }
              >
                <Sparkles aria-hidden="true" className="h-2.5 w-2.5" />
                {t("apikeys_view.recommended")}
              </span>
            )}
            {descriptor.caution && (
              <span
                className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
                title={descriptor.caution}
              >
                {t("apikeys_view.not_recommended")}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <code className="font-mono">{descriptor.id}</code>
            {" · "}
            <span>
              {descriptor.auth_mode === "api_key" && "API key auth"}
              {descriptor.auth_mode === "codex" && "ChatGPT / Codex login"}
              {descriptor.auth_mode === "antigravity" && "Google subscription login"}
              {descriptor.auth_mode === "none" && "Local — no auth"}
            </span>
          </p>
        </div>

        <ActiveControl
          descriptor={
            isCodex
              ? { ...descriptor, configured: Boolean(descriptor.codex_brain_ready) }
              : descriptor
          }
          activating={activating}
          onActivate={activate}
          disabled={!isBrainSwitchable || (isCodex && !descriptor.codex_brain_ready)}
          disabledReason={
            !isBrainSwitchable
              ? "Available for Jarvis-Agents only"
              : isCodex && !descriptor.codex_brain_ready
                ? t("apikeys_codex.brain_needs_openai_key")
                : undefined
          }
        />
      </div>

      {/* The precise "this card is the problem" banner: only on the active card,
          only when the live check actually failed. Names the cause in plain
          words instead of leaving the user to guess behind the tab's red dot. */}
      {cardError && (
        <div
          data-testid={`provider-health-error-${descriptor.id}`}
          role="status"
          className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/[0.07] px-3 py-2 text-[11px] leading-relaxed text-destructive"
        >
          <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            <span className="font-medium">{t("apikeys_view.health_error")}</span>
            {cardErrorDetail ? ` — ${cardErrorDetail}` : ""}
          </span>
        </div>
      )}

      <AuthWidget
        descriptor={descriptor}
        onChanged={onChanged}
        onSavedActivate={handleSavedActivate}
      />

      {!isBrainSwitchable && (
        <p className="rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-700">
          Jarvis-Agent only. This provider cannot be used as the main Brain or
          Computer-Use planner because it does not receive screenshots.
        </p>
      )}

      {/* Model / voice picker. Every switchable brain provider shows its model
          picker — even before a key is set — because the catalog falls back to
          the provider's curated family without a key (no network, no error), so
          a model can be pre-picked. TTS/STT share a single global [tts]/[stt]
          block, so the picker only WRITES from the ACTIVE provider and sets the
          voice (Grok/Gemini/OpenAI/Google) or model (Cartesia/STT). */}
      {((descriptor.tier === "brain" && isBrainSwitchable) ||
        ((descriptor.tier === "tts" || descriptor.tier === "stt") &&
          descriptor.active &&
          descriptor.configured)) &&
        // OpenRouter TTS is the one provider where the user also picks a VOICE
        // (per model, language-tagged, with an audio preview) — render the
        // combined model + voice controls; every other tier keeps the plain
        // model/voice picker.
        (descriptor.id === "openrouter-tts" ? (
          <OpenRouterTtsControls
            providerId={descriptor.id}
            recommendedModel={descriptor.recommended_model}
            healthActive={descriptor.active}
          />
        ) : (
          <BrainModelSelector
            providerId={descriptor.id}
            recommendedModel={descriptor.recommended_model}
            healthSection={descriptor.tier}
            healthActive={descriptor.active}
          />
        ))}

      {/* TTS/STT model/voice is a single global value, so a configured-but-
          inactive provider can't own it — make the capability discoverable with
          a hint instead of silently hiding the picker. */}
      {(descriptor.tier === "tts" || descriptor.tier === "stt") &&
        descriptor.configured &&
        !descriptor.active && (
          <p className="text-[11px] text-muted-foreground">
            {t("apikeys_view.model_picker_activate_hint")}
          </p>
        )}

      {/* Phase 3: a dedicated Computer-Use model, selectable per brain provider
          (defaults to the provider's main model — no automatic escalation).
          Also shown under the Computer-Use tab (synthetic "computer-use"
          tier, same underlying brain id) — but never the plain
          BrainModelSelector above, which stays Brain-tab-only. */}
      {(descriptor.tier === "brain" || descriptor.tier === "computer-use") &&
        descriptor.configured &&
        isBrainSwitchable && (
          <CuModelSelector
            providerId={descriptor.id}
            recommendedModel={descriptor.recommended_model}
            healthActive={
              descriptor.tier === "computer-use"
                ? descriptor.active
                : Boolean(descriptor.computer_use_active)
            }
          />
        )}

      {/* Realtime needs BOTH a model AND a voice pinned per provider — a
          dedicated compact control (two dropdowns), gated on the card
          already having a stored credential like the other tiers' pickers
          above. */}
      {descriptor.tier === "realtime" && descriptor.configured && (
        <RealtimeOptionsControl
          providerId={descriptor.id}
          healthActive={descriptor.active}
        />
      )}

      {/* Footer: the live connectivity test, visually separated from the
          configuration body so "set up" and "verify" read as two steps. */}
      <div className="border-t border-border/60 pt-2.5">
        <ProviderTestControl
          providerId={descriptor.id}
          providerLabel={descriptor.label}
          section={descriptor.tier}
          active={descriptor.active}
        />
      </div>
    </div>
  );
}

// Tone per status: green = works; amber = reached but key/account/model blocks
// (integration is fine); red = couldn't reach / integration bug.
const TEST_STATUS_TONE: Record<ProviderTestStatus, string> = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
  not_configured: "border-border bg-muted text-muted-foreground",
  bad_key: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  no_credits: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  rate_limited: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  model_unavailable: "border-amber-500/30 bg-amber-500/10 text-amber-600",
  unreachable: "border-destructive/30 bg-destructive/10 text-destructive",
  error: "border-destructive/30 bg-destructive/10 text-destructive",
};

/**
 * "Test" button + honest result chip. Calls POST /api/providers/{id}/test which
 * makes a REAL minimal call — distinguishing a working provider from an invalid
 * key, an out-of-credits account, or an unreachable endpoint. This is the piece
 * the API-Keys view was missing: the green "configured" badge only ever meant a
 * key STRING was stored, never that the provider answers.
 */
function ProviderTestControl({
  providerId,
  providerLabel,
  section,
  active,
}: {
  providerId: string;
  providerLabel: string;
  section: ProviderTier;
  active: boolean;
}) {
  const t = useT();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const activeRef = useRef(active);
  activeRef.current = active;

  function publish(next: ProviderTestResult) {
    window.dispatchEvent(
      new CustomEvent("jarvis:provider-tested", {
        detail: {
          section,
          provider: providerId,
          provider_label: providerLabel,
          active: activeRef.current,
          result: next,
        },
      }),
    );
  }

  async function run() {
    setRunning(true);
    setResult(null);
    try {
      const next = await testProvider(providerId);
      setResult(next);
      publish(next);
    } catch (e) {
      const next: ProviderTestResult = {
        provider: providerId,
        status: "error",
        detail: (e as Error).message,
        latency_ms: 0,
        integration_ok: false,
      };
      setResult(next);
      publish(next);
    } finally {
      setRunning(false);
    }
  }

  const tone = result ? TEST_STATUS_TONE[result.status] : "";
  const note = result
    ? (result.integration_ok
        ? t("apikeys_test.integration_ok_note")
        : t("apikeys_test.integration_bad_note"))
    : "";

  return (
    <div className="flex flex-wrap items-center gap-2 pt-0.5">
      <Button
        size="sm"
        variant="outline"
        onClick={(e) => {
          e.stopPropagation();
          void run();
        }}
        disabled={running}
        className="h-7 gap-1.5 text-xs"
      >
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <PlugZap className="h-3.5 w-3.5" />
        )}
        {running ? t("apikeys_test.running") : t("apikeys_test.button")}
      </Button>

      {result && (
        <span
          data-testid={`provider-test-result-${providerId}`}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
            tone,
          )}
          title={[note, result.detail, result.latency_ms ? `${Math.round(result.latency_ms)} ms` : ""]
            .filter(Boolean)
            .join("\n")}
        >
          {result.status === "ok" ? (
            <Check className="h-3 w-3" />
          ) : result.integration_ok ? (
            <AlertCircle className="h-3 w-3" />
          ) : (
            <XCircle className="h-3 w-3" />
          )}
          {t(`apikeys_test.status_${result.status}`)}
          {result.status === "ok" && result.latency_ms
            ? ` · ${Math.round(result.latency_ms)} ms`
            : ""}
        </span>
      )}
    </div>
  );
}

/**
 * Radio-button-based active toggle. The source of truth stays
 * `descriptor.active` from `/api/providers` — the radio mirrors server
 * state, it is not held locally. `name="active-{tier}"` gives us browser-
 * native exclusivity per tier (Brain/TTS/STT).
 *
 * `disabled` would suppress onChange; that's why the radio is NOT disabled
 * when a key is missing — instead it routes through `activate()` to raise a
 * warning toast. This way the user gets a reaction to every click, instead
 * of bumping into a silent element.
 */
function ActiveControl({
  descriptor,
  activating,
  onActivate,
  disabled = false,
  disabledReason,
}: {
  descriptor: ProviderDescriptor;
  activating: boolean;
  onActivate: () => void;
  /**
   * Truly disable the radio (no click, no toast). Used for Codex when it cannot
   * be a brain yet (no OpenAI key) — a click there would be a dead end, so we
   * disable instead of firing a warning toast. Other providers stay clickable
   * (warn-on-click) because their key field is right on the card.
   */
  disabled?: boolean;
  disabledReason?: string;
}) {
  const labelTitle = descriptor.active
    ? "This provider is active"
    : disabled
      ? disabledReason ?? "Provider cannot be activated"
      : descriptor.configured
        ? "Activate this provider"
        : "Set an API key first";

  return (
    <label
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => {
        // A double click on the radio label must NOT also trigger the
        // card's onDoubleClick — otherwise activate() would fire twice
        // (idempotent, but sends two API calls).
        e.stopPropagation();
      }}
      className={cn(
        "inline-flex shrink-0 select-none items-center gap-1.5 text-xs",
        disabled ? "cursor-not-allowed" : "cursor-pointer",
        descriptor.active
          ? "font-medium text-primary"
          : descriptor.configured
            ? "text-muted-foreground hover:text-foreground"
            : "text-muted-foreground/70",
      )}
      title={labelTitle}
    >
      <input
        type="radio"
        name={`active-${descriptor.tier}`}
        checked={descriptor.active}
        onChange={() => onActivate()}
        disabled={activating || disabled}
        className="accent-primary"
      />
      {activating ? "Activating…" : descriptor.active ? "Active" : "Set active"}
    </label>
  );
}

function AuthWidget({
  descriptor,
  onChanged,
  onSavedActivate,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
  onSavedActivate?: () => void;
}) {
  return (
    <div className="space-y-2">
      <ProviderBillingBadge billing={descriptor.billing} />
      {descriptor.auth_mode === "none" && (
        <p className="text-xs text-muted-foreground">
          Local provider — no credentials needed.
        </p>
      )}
      {descriptor.auth_mode === "codex" && (
        <CodexAuthWidget descriptor={descriptor} onChanged={onChanged} />
      )}
      {descriptor.auth_mode === "antigravity" && (
        <AntigravityAuthWidget descriptor={descriptor} onChanged={onChanged} />
      )}
      {descriptor.auth_mode === "api_key" && (
        <>
          {descriptor.secret_keys.map((k) => (
            <ApiKeyForm
              key={k}
              secretKey={k}
              dashboardUrl={descriptor.dashboard_url}
              configured={Boolean(descriptor.secrets_set[k])}
              credentialHelp={descriptor.credential_help}
              onChanged={onChanged}
              onSavedActivate={onSavedActivate}
            />
          ))}
          {descriptor.alt_credential && (
            <AltCredentialNote alt={descriptor.alt_credential} />
          )}
        </>
      )}
    </div>
  );
}

function CodexAuthWidget({
  descriptor,
  onChanged,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
}) {
  const t = useT();
  const [pending, setPending] = useState<"login" | "logout" | "copy" | null>(null);
  const pushToast = useEventStore((s) => s.pushToast);
  const status = descriptor.codex_status;
  const installCommand = descriptor.install_hint ?? "npm i -g @openai/codex";

  async function handleCopy() {
    setPending("copy");
    try {
      await navigator.clipboard.writeText(installCommand);
      pushToast("success", "Install command copied");
    } catch {
      pushToast("warning", installCommand);
    } finally {
      setPending(null);
    }
  }

  async function handleLogin() {
    setPending("login");
    try {
      await startCodexLogin();
      pushToast("info", t("apikeys_codex.login_started"));
      // `codex login` opens the browser OAuth flow; it only completes once the
      // user clicks through (seconds later). Poll a few times so the card flips
      // to the compact "connected" state on its own once auth.json appears —
      // no manual refresh needed.
      [1500, 4000, 8000, 15000, 25000].forEach((ms) =>
        window.setTimeout(onChanged, ms),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function handleLogout() {
    setPending("logout");
    try {
      await codexLogout();
      pushToast("info", t("apikeys_codex.disconnected"));
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(null);
    }
  }

  // Connected: collapse to a small "logged in" badge instead of the full card.
  // No connect button (no second invitation), no API-key field (the key lives on
  // the separate "OpenAI" provider). Activation as the worker happens in the
  // Subagent list below.
  if (status?.connected) {
    return (
      <div className="space-y-3">
        <div
          data-testid="codex-connected"
          className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/[0.06] px-3 py-2 text-xs"
        >
          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span className="min-w-0 break-words text-foreground">
            {status.message ?? "Connected via ChatGPT."}
          </span>
          {status.version && (
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{status.version}</code>
          )}
          {status.mode === "chatgpt" && <span className="chip-yellow">CHATGPT-LOGIN</span>}
          <Button
            size="sm"
            variant="ghost"
            onClick={handleLogout}
            disabled={pending !== null}
            className="ml-auto"
          >
            <LogOut className="h-3.5 w-3.5" />
            Disconnect
          </Button>
        </div>
      </div>
    );
  }

  // Not connected: status + (install hint) + the single "connect" action.
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-border bg-background/40 p-3 text-xs text-muted-foreground">
        <div className="flex flex-wrap items-center gap-2">
          <span>{status?.message ?? t("apikeys_codex.status_loading")}</span>
          {status?.version && (
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{status.version}</code>
          )}
        </div>
      </div>

      {!status?.installed && (
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-[220px] flex-1 rounded-md border border-border bg-muted/30 px-3 py-1.5 font-mono text-xs">
            {installCommand}
          </code>
          <Button size="sm" variant="outline" onClick={handleCopy} disabled={pending === "copy"}>
            {pending === "copy" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            Copy command
          </Button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={handleLogin} disabled={pending !== null || !status?.installed}>
          <LogIn className="h-3.5 w-3.5" />
          Connect with ChatGPT
        </Button>
        <Button size="sm" variant="outline" asChild>
          <a href="https://help.openai.com/en/articles/11381614" target="_blank" rel="noreferrer">
            <Terminal className="h-3.5 w-3.5" />
            Install Codex
          </a>
        </Button>
      </div>

    </div>
  );
}

function AntigravityAuthWidget({
  descriptor,
  onChanged,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
}) {
  const t = useT();
  const [pending, setPending] = useState<"login" | "logout" | "copy" | null>(null);
  const pushToast = useEventStore((s) => s.pushToast);
  const status = descriptor.antigravity_status;
  const installCommand =
    descriptor.install_hint ?? "curl -fsSL https://antigravity.google/cli/install.sh | bash";

  async function handleCopy() {
    setPending("copy");
    try {
      await navigator.clipboard.writeText(installCommand);
      pushToast("success", "Install command copied");
    } catch {
      pushToast("warning", installCommand);
    } finally {
      setPending(null);
    }
  }

  async function handleLogin() {
    setPending("login");
    try {
      await loginAntigravity();
      pushToast("info", t("apikeys_antigravity.login_started"));
      // The Google CLI opens the browser "Sign in with Google" flow; it only
      // completes once the user clicks through (seconds later). Poll a few times
      // so the card flips to the compact "connected" state on its own once the
      // on-disk creds appear — no manual refresh needed (mirror of Codex).
      [1500, 4000, 8000, 15000, 25000].forEach((ms) =>
        window.setTimeout(onChanged, ms),
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function handleLogout() {
    setPending("logout");
    try {
      await logoutAntigravity();
      pushToast("info", t("apikeys_antigravity.disconnected"));
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(null);
    }
  }

  // Connected: collapse to a small "logged in" badge instead of the full card.
  // The Google subscription bills the brain/subagent; no key field (OAuth-only).
  if (status?.connected) {
    return (
      <div className="space-y-3">
        <div
          data-testid="antigravity-connected"
          className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/[0.06] px-3 py-2 text-xs"
        >
          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span className="min-w-0 break-words text-foreground">
            {status.user_email
              ? t("apikeys_antigravity.connected_as").replace("{0}", status.user_email)
              : status.message || t("apikeys_antigravity.connected")}
          </span>
          {status.version && (
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{status.version}</code>
          )}
          <span className="chip-yellow">GOOGLE-LOGIN</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleLogout}
            disabled={pending !== null}
            className="ml-auto"
          >
            <LogOut className="h-3.5 w-3.5" />
            Disconnect
          </Button>
        </div>
      </div>
    );
  }

  // Not connected: status + (install hint) + the single "connect" action.
  return (
    <div className="space-y-3">
      <div className="rounded-md border border-border bg-background/40 p-3 text-xs text-muted-foreground">
        <div className="flex flex-wrap items-center gap-2">
          <span>{status?.message ?? t("apikeys_antigravity.status_loading")}</span>
          {status?.version && (
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{status.version}</code>
          )}
        </div>
      </div>

      {!status?.installed && (
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-[220px] flex-1 rounded-md border border-border bg-muted/30 px-3 py-1.5 font-mono text-xs">
            {installCommand}
          </code>
          <Button size="sm" variant="outline" onClick={handleCopy} disabled={pending === "copy"}>
            {pending === "copy" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            Copy command
          </Button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={handleLogin} disabled={pending !== null || !status?.installed}>
          <LogIn className="h-3.5 w-3.5" />
          Connect with Google
        </Button>
        <Button size="sm" variant="outline" asChild>
          <a href="https://antigravity.google" target="_blank" rel="noreferrer">
            <Terminal className="h-3.5 w-3.5" />
            Install Antigravity
          </a>
        </Button>
      </div>
    </div>
  );
}

// One calm chip vocabulary for every card state. Sentence-case, small, and
// tonally consistent (gold = on, green = ready, neutral = untouched, red =
// broken) — replaces the earlier mix of shouting uppercase badges.
const STATE_CHIP_TONE = {
  active: "border-primary/40 bg-primary/15 text-primary font-semibold",
  ready: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
  missing: "border-destructive/30 bg-destructive/10 text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
} as const;

function StateChip({
  tone,
  children,
}: {
  tone: keyof typeof STATE_CHIP_TONE;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium",
        STATE_CHIP_TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

function StatusBadge({ descriptor }: { descriptor: ProviderDescriptor }) {
  if (descriptor.active) return <StateChip tone="active">active</StateChip>;
  if (descriptor.auth_mode === "codex") {
    const status = descriptor.codex_status;
    if (!status?.installed) return <StateChip tone="missing">missing</StateChip>;
    if (descriptor.configured) return <StateChip tone="ready">ready</StateChip>;
    return <StateChip tone="neutral">not connected</StateChip>;
  }
  if (descriptor.auth_mode === "antigravity") {
    const status = descriptor.antigravity_status;
    if (!status?.installed) return <StateChip tone="missing">missing</StateChip>;
    if (status.connected) return <StateChip tone="ready">ready</StateChip>;
    return <StateChip tone="neutral">not connected</StateChip>;
  }
  if (descriptor.configured) return <StateChip tone="ready">ready</StateChip>;
  return <StateChip tone="neutral">open</StateChip>;
}

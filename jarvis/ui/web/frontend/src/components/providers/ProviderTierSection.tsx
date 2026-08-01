import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Bot, Brain, Check, Copy, Download, Loader2, LogIn, LogOut, Mic, PlugZap, Radio, Sparkles, Terminal, Volume2, Wand2, Waypoints, XCircle } from "lucide-react";
import { AltCredentialNote } from "@/components/AltCredentialNote";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { OpenRouterTtsControls } from "@/components/OpenRouterTtsVoicePicker";
import { CuModelSelector } from "@/components/CuModelSelector";
import { RealtimeOptionsControl } from "@/components/RealtimeOptionsControl";
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";
import { Button } from "@/components/ui/button";
import {
  codexLogout,
  loginAntigravity,
  localInstallStatus,
  logoutAntigravity,
  startLocalInstall,
  type LocalInstallProgress,
  type ProviderDescriptor,
  type ProviderTestResult,
  type ProviderTestStatus,
  type ProviderTier,
  type SectionHealth,
  saveProviderBaseUrl,
  sectionHealthForSubject,
  startCodexLogin,
  switchBrainProvider,
  switchComputerUseProvider,
  switchDictationPolishProvider,
  switchRealtimeProvider,
  switchSttProvider,
  switchTtsProvider,
  testProvider,
  useSectionHealth,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { agentBrand, agentsBrand } from "@/lib/agentBrand";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The provider-tier building blocks shared by every screen that lets a user
 * configure a provider: the API-Keys view (all tiers) and the voice section's
 * "API Keys" tab (the `stt` tier only).
 *
 * This module is a VERBATIM extraction out of `views/ApiKeysView.tsx` — the
 * pieces below were module-private there and are unchanged apart from being
 * exported. `ApiKeysView` imports them back, so its pinned test suites keep
 * describing the exact same rendered output.
 */

export type { ProviderTier } from "@/hooks/useProviders";

export type LucideIcon = typeof Brain;

export interface CategoryMeta {
  /** Short label for the segmented tab. */
  tab: string;
  /** Full heading shown in the category hero band. */
  title: string;
  /** One-line plain-language description under the heading. */
  description: string;
  icon: LucideIcon;
}

// The top-level engine mode. Feature A supersedes design D1 ("view-only"):
// the switch now decides which tab set is shown AND persists `[voice].mode`
// via `useVoiceMode().setMode` — Pipeline always (it's always reachable),
// Realtime only when a realtime provider actually has a key
// (`realtimeAvailable`), so the switch can never pin the boot default to an
// unreachable engine. See `EngineModeSwitch` below for the exact rule.
export type VoiceEngineMode = "pipeline" | "realtime";

// The three provider slots the maintainer's setup recommendation speaks about
// (RecommendedSetupPanel). All three are ordinary CategoryKeys; "realtime"
// additionally requires the Realtime tab set, so opening it switches the VIEW
// mode (never the persisted `[voice].mode`).
export type RecommendationTab = "realtime" | "computer-use" | "subagents";

// Meta for the three provider tiers (brain/tts/stt). Subagents and Advanced are
// composed separately because they own their own data sources / sub-sections.
export function makeProviderCategories(
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
    // The one OPTIONAL tier. Its description has to carry that, because an
    // empty section in a screen full of required keys reads as "you still have
    // work to do" — here it only means the dictation arrives exactly as it was
    // recognized, which is what every install did before this tier existed.
    dictation: {
      tab: t("apikeys_view.tab_dictation"),
      title: t("apikeys_view.tier_dictation"),
      description: t("apikeys_view.cat_dictation_desc"),
      icon: Wand2,
    },
  };
}

/**
 * Per-tab health (amber = the active provider isn't set up, red = it's set up
 * but failing a live check), re-bound to the provider that is ACTUALLY active
 * right now. Best-effort and off the render-blocking path.
 *
 * The re-bind is load-bearing, not cosmetic: the backend rollup carries the
 * `subject_id` it tested, and a stale entry (e.g. a NVIDIA timeout recorded
 * before the user switched to OpenRouter) must be DROPPED rather than
 * attributed to whoever is active now. Handing raw rollup entries to the tabs
 * re-opens exactly that bug.
 */
export function useTierHealth(
  providers: ProviderDescriptor[],
): Record<string, SectionHealth> {
  const { health: rawHealth } = useSectionHealth();
  return useMemo(() => {
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
      // "dictation" is deliberately absent. Its default pin is "auto", where no
      // single card is chosen and the family that answers is decided per call by
      // the key-aware chain — so there is no local "active id" to re-bind
      // against, and dropping the entry for want of one would hide a real
      // failure. The backend rollup names the family it tested in `subject_id`,
      // and `TierSection` only ever hands health to a card that matches it, so
      // the card-level drill-down still cannot blame the wrong provider.
    };
    for (const [section, subjectId] of Object.entries(activeSubjects)) {
      const matching = sectionHealthForSubject(rawHealth[section], subjectId);
      if (matching) visible[section] = matching;
      else delete visible[section];
    }
    return visible;
  }, [providers, rawHealth]);
}

/**
 * The Pipeline|Realtime segmented switch. Feature A (supersedes D1): clicking
 * a segment still switches the local view (`onSelect`), and ALSO persists
 * `[voice].mode` — Pipeline unconditionally (always reachable), Realtime only
 * when `realtimeAvailable` (subscription login or API access is ready);
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
 * - Realtime with no provider access (`!realtimeAvailable`) reads
 *   muted; it stays clickable (opens the Realtime tab so the user can add one)
 *   but never gets the fill.
 * - The explanatory copy lives in `VoiceEngineContext` inside the provider
 *   scroller, keeping this always-visible header control one compact row.
 */
export function EngineModeSwitch({
  mode,
  liveMode,
  realtimeAvailable,
  onSelect,
  onSetVoiceMode,
}: {
  mode: VoiceEngineMode;
  /** The live `[voice].mode` value — determines the filled/active segment. */
  liveMode: string;
  /** Whether some realtime provider has usable subscription or API access. */
  realtimeAvailable: boolean;
  onSelect: (mode: VoiceEngineMode) => void;
  /** Persists `[voice].mode` — gated per the rule above. */
  onSetVoiceMode: (mode: string) => void;
}) {
  const t = useT();
  // Realtime leads as the recommended default. Pipeline follows with an
  // explicit Not recommended badge so the product guidance is unambiguous.
  const segments: { key: VoiceEngineMode; label: string; icon: LucideIcon }[] = [
    { key: "realtime", label: t("apikeys_view.mode_realtime"), icon: Radio },
    { key: "pipeline", label: t("apikeys_view.mode_pipeline"), icon: Waypoints },
  ];
  const liveIndex = liveMode === "realtime" ? 0 : 1;
  const liveModeAvailable = liveMode !== "realtime" || realtimeAvailable;

  function handleSelect(seg: VoiceEngineMode) {
    onSelect(seg);
    if (seg === "pipeline" || realtimeAvailable) {
      onSetVoiceMode(seg);
    }
  }

  return (
    <div
      data-testid="voice-engine-header-control"
      role="group"
      aria-label={t("apikeys_view.voice_engine_label")}
      className="shrink-0"
    >
      <div className="relative grid min-w-56 grid-cols-2 rounded-lg border border-border bg-card/40 p-0.5">
        {liveModeAvailable && (
          <span
            data-testid="voice-engine-live-thumb"
            aria-hidden="true"
            className="absolute inset-y-0.5 left-0.5 w-[calc(50%-0.125rem)] rounded-md bg-primary shadow-[0_0_14px_rgba(255,214,10,0.22)] transition-transform duration-200 ease-out"
            style={{ transform: `translateX(${liveIndex * 100}%)` }}
          />
        )}
        {segments.map((seg) => {
          const isLive = liveModeAvailable && liveMode === seg.key;
          const isViewedOnly = mode === seg.key && !isLive;
          const needsKey = seg.key === "realtime" && !realtimeAvailable;
          const isRecommended = seg.key === "realtime";
          const Icon = seg.icon;
          return (
            <button
              key={seg.key}
              type="button"
              onClick={() => handleSelect(seg.key)}
              aria-pressed={mode === seg.key}
              className={cn(
                "relative z-10 inline-flex items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
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
              <Icon aria-hidden="true" className="h-3 w-3" />
              <span className="whitespace-nowrap">{seg.label}</span>
              <span
                className={cn(
                  "whitespace-nowrap rounded-full px-1 py-px text-[8px] font-semibold uppercase tracking-wide",
                  isRecommended
                    ? isLive
                      ? "bg-primary-foreground/20 text-primary-foreground"
                      : "bg-primary/15 text-primary"
                    : isLive
                      ? "border border-primary-foreground/30 bg-primary-foreground/10 text-primary-foreground"
                      : "border border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
                )}
              >
                {t(
                  isRecommended
                    ? "apikeys_view.mode_recommended"
                    : "apikeys_view.not_recommended",
                )}
              </span>
              {isLive && (
                <span className="sr-only">{t("apikeys_view.mode_active_badge")}</span>
              )}
            </button>
          );
        })}
      </div>
      {/* One discreet reminder right where the choice is made: you configure
          exactly one engine, never both. The per-mode key details live in the
          VoiceEngineContext copy below, so this stays a single short line. */}
      <p
        data-testid="voice-engine-pick-one-hint"
        className="mt-1 text-right text-[10px] leading-tight text-muted-foreground/70"
      >
        {t("apikeys_view.mode_pick_one_hint")}
      </p>
    </div>
  );
}

/**
 * Compact, scrollable context for the header-level engine control. Keeping
 * explanatory copy and recommendations inside the provider scroller gives the
 * fixed viewport back to provider cards on laptop-height windows, while the
 * switch itself remains immediately reachable in the main header.
 */
export function VoiceEngineContext({
  mode,
  realtimeAvailable,
  statusKnown,
  sessionActive,
  activeSessionMode,
  activeSessionProvider,
  activeSessionModel,
  transitioning,
  liveMode,
  onOpenRecommendedTab,
}: {
  mode: VoiceEngineMode;
  realtimeAvailable: boolean;
  statusKnown: boolean;
  sessionActive: boolean;
  activeSessionMode: "pipeline" | "realtime" | null;
  activeSessionProvider: string;
  activeSessionModel: string;
  transitioning: boolean;
  liveMode: string;
  onOpenRecommendedTab: (tab: RecommendationTab) => void;
}) {
  const t = useT();
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
  const modeDescription =
    mode === "realtime" && !realtimeAvailable
      ? t(
          statusKnown
            ? "apikeys_view.mode_needs_credentials"
            : "apikeys_view.mode_status_unknown",
        )
      : mode === "realtime"
        ? t("apikeys_view.mode_desc_realtime")
        : t("apikeys_view.mode_desc_pipeline");

  return (
    <section
      data-testid="voice-engine-context"
      className="mx-auto mb-4 w-full max-w-4xl rounded-xl border border-border bg-card/25 px-3 py-2"
      aria-label={t("apikeys_view.voice_engine_label")}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1.5">
        <div
          className="flex min-w-64 flex-1 items-center gap-2 overflow-hidden text-xs"
          title={`${t("apikeys_view.voice_engine_desc")} ${modeDescription}`}
        >
          <Volume2 aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="shrink-0 font-medium text-foreground">
            {t("apikeys_view.voice_engine_label")}
          </span>
          <span aria-hidden="true" className="text-border">·</span>
          <span className="min-w-0 truncate text-muted-foreground">
            {modeDescription}
            {mode === "realtime" && (
              <span className="text-amber-500/90">
                {` · ${t("apikeys_view.mode_realtime_preview")}`}
              </span>
            )}
          </span>
        </div>
        <div
          className="flex shrink-0 items-center gap-1.5 text-[11px] leading-snug"
          aria-live="polite"
          data-testid="voice-engine-runtime-status"
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
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

      <p
        data-testid="voice-engine-keys-hint"
        className="mt-1 text-[11px] leading-snug text-muted-foreground/80"
      >
        {t("apikeys_view.mode_keys_hint")}
      </p>

      {mode === "realtime" && (
        <RecommendedSetupPanel onOpenTab={onOpenRecommendedTab} />
      )}
    </section>
  );
}

/**
 * The maintainer's personal pick for the three provider slots of the REALTIME
 * tab set, shown in the scrollable engine context only while that tab set is
 * being viewed — the picks name Realtime-mode tabs, so surfacing them next to
 * the Pipeline tabs would point at tabs that are not even on screen
 * (maintainer feedback 2026-07-17). Same contract as the per-card
 * "Recommended" badges fed by provider_spec.py: a presentation hint only — it
 * never gates behavior and never branches a code path on a provider name
 * (AP-21). Each row is a button that jumps straight to the tab it names, so
 * the guidance sits one click from the place it applies. The rows carry an
 * explicit aria-label starting with the panel title so their accessible names
 * never collide with the Pipeline|Realtime segment buttons (tests match those
 * via /^realtime/i).
 */
function RecommendedSetupPanel({
  onOpenTab,
}: {
  onOpenTab: (tab: RecommendationTab) => void;
}) {
  const t = useT();
  const rows: {
    tab: RecommendationTab;
    icon: LucideIcon;
    label: string;
    pick: string;
    why: string;
  }[] = [
    {
      tab: "realtime",
      icon: Radio,
      label: t("apikeys_view.tab_realtime"),
      pick: t("apikeys_view.reco_realtime_pick"),
      why: t("apikeys_view.reco_realtime_why"),
    },
    {
      tab: "computer-use",
      icon: Terminal,
      label: t("apikeys_view.tab_computer_use"),
      pick: t("apikeys_view.reco_computer_use_pick"),
      why: t("apikeys_view.reco_computer_use_why"),
    },
    {
      tab: "subagents",
      icon: Bot,
      label: t("apikeys_view.tab_subagents"),
      pick: t("apikeys_view.reco_subagents_pick"),
      why: t("apikeys_view.reco_subagents_why"),
    },
  ];
  return (
    <div
      data-testid="recommended-setup-panel"
      className="mt-2 flex min-w-0 items-center gap-2 border-t border-primary/20 pt-2"
    >
      <p className="flex shrink-0 items-center gap-1.5 text-[11px] font-medium text-foreground">
        <Sparkles aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-primary" />
        {t("apikeys_view.reco_title")}
      </p>
      <ul className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto scrollbar-jarvis">
        {rows.map((row) => {
          const Icon = row.icon;
          return (
            <li key={row.tab} className="shrink-0">
              <button
                type="button"
                onClick={() => onOpenTab(row.tab)}
                data-testid={`reco-row-${row.tab}`}
                aria-label={`${t("apikeys_view.reco_title")}: ${row.label} — ${row.pick}. ${row.why}`}
                title={row.why}
                className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-primary/15 bg-primary/[0.04] px-2 py-1 text-[11px] leading-none transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Icon
                  aria-hidden="true"
                  className="h-3 w-3 shrink-0 text-primary/80"
                />
                <span className="text-muted-foreground">{row.label}:</span>
                <span className="font-medium text-foreground">{row.pick}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The header band atop each category panel: an icon chip, the full category
 * title (display font), and a one-line plain-language description. Mirrors the
 * app's `ViewHeader` icon treatment so the screen reads as one system.
 */
export function CategoryHero({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="mb-4 flex items-start gap-2.5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary/40 text-primary">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
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
export function GuidancePanel({ title, body }: { title: string; body: string }) {
  return (
    <div className="mb-3 flex items-start gap-2.5 rounded-xl border border-primary/25 bg-primary/[0.05] px-3 py-2">
      <Sparkles aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <p className="min-w-0 text-xs leading-relaxed">
        <span className="font-medium text-foreground">{title}</span>
        <span className="text-muted-foreground"> · {body}</span>
      </p>
    </div>
  );
}

/**
 * One of the three provider tiers (brain/tts/stt): the hero band plus the
 * loading / error / empty / card states. The card list itself is `TierSection`,
 * reused unchanged from the original layout.
 */
export function ProviderCategory({
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

// The card list for a single provider tier. The category label now lives in the
// tab bar + the `CategoryHero` above, so this renders only the cards. If nobody
// in the tier is active yet, a freshly saved key auto-activates itself — the
// first configured provider wins automatically (`autoActivateOnSave`).
export function TierSection({
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

export function ProviderCard({
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
  const assistantName = useEventStore((s) => s.assistantName);
  // The card only escalates to red for a real "set up but failing" error — the
  // amber "needs setup" case stays on the tab + the open/ready badge so a fresh,
  // half-configured screen doesn't paint cards red.
  const cardError = descriptor.active && health?.status === "error";
  // The backend one-liner (e.g. "OpenRouter: rate limited") already names the
  // provider + cause, so it answers "what is wrong" without a second lookup.
  const cardErrorDetail = health?.detail?.trim() || "";

  // The legacy Codex Brain card has stricter readiness rules than other
  // Codex-auth surfaces. Subscription Realtime uses the same login widget but
  // must remain activatable without an API key.
  const isCodexAuth = descriptor.auth_mode === "codex";
  const isCodexBrain = isCodexAuth && descriptor.tier === "brain";
  const isSubscriptionLoginOnly =
    isCodexAuth &&
    descriptor.billing === "subscription" &&
    descriptor.secret_keys.length === 0;
  const isBrainSwitchable =
    descriptor.tier !== "brain" || descriptor.brain_switchable !== false;

  /**
   * Prove the polish provider the user just picked can actually answer, and
   * say so when it cannot.
   *
   * Runs AFTER the switch, never as a gate: a probe that fails for its own
   * reasons must not be able to block a preference the user is entitled to
   * set. Never throws — a verification that breaks has to stay quieter than
   * the switch it is checking.
   */
  async function verifyPolishProvider() {
    let result: ProviderTestResult;
    try {
      result = await testProvider(descriptor.id);
    } catch (e) {
      // Not the user's problem and not evidence about the provider: say
      // nothing rather than raise a false alarm about a working switch.
      console.debug("polish provider verification failed to run", e);
      return;
    }
    if (result.status === "ok") return;
    // The backend's sentence already names the cause AND the fix ("no credits",
    // "model not pulled", "slower than the 1200 ms limit — raise it or pick a
    // faster provider"), so it is shown verbatim instead of being flattened
    // into a generic "does not work".
    pushToast(
      "warning",
      `${descriptor.label} is selected, but it did not answer: ${
        result.detail || result.status
      }`,
    );
    // Let the card repaint with the health this test just produced.
    onChanged();
  }

  async function activate(assumeConfigured = false) {
    if (descriptor.active) return;
    if (!isBrainSwitchable) {
      pushToast(
        "warning",
        `${descriptor.label} is only available for ${agentsBrand(assistantName)}.`,
      );
      return;
    }
    if (isCodexBrain && !descriptor.codex_brain_ready) {
      // The card is "connected" via OAuth, but a chat brain needs an OpenAI key.
      // Guide honestly instead of switching and failing on the first turn.
      pushToast("warning", t("apikeys_codex.brain_needs_openai_key"));
      return;
    }
    if (!assumeConfigured && !descriptor.configured) {
      pushToast(
        "warning",
        descriptor.auth_mode === "codex"
          ? isSubscriptionLoginOnly
            ? t("apikeys_codex.subscription_login_required")
            : t("apikeys_codex.needs_codex_full").replace("{0}", descriptor.label)
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
      } else if (descriptor.tier === "dictation") {
        // Its own branch on purpose: this tier has no `/switch` route (the pin
        // is a plain `[dictation]` setting), and without the branch it would
        // fall through to the realtime switch below and reconfigure the user's
        // voice engine from a dictation card.
        //
        // `polish_family`, NOT `id`: the config stores "openai" while this card
        // is "openai-polish" (a bare "openai" is already the brain card). The
        // chain ignores a family id it does not know and quietly falls back to
        // the auto order, so sending `id` wrote a pin that returned HTTP 200,
        // showed a success toast, and left the previous provider active — the
        // exact "it says it did it but nothing changed" failure. Falling back to
        // `id` keeps an older payload without the field working.
        await switchDictationPolishProvider(descriptor.polish_family || descriptor.id);
        pushToast("success", `Dictation wording → ${descriptor.label}`);
        window.dispatchEvent(new CustomEvent("jarvis:dictation-polish-switched"));
        // A stored key is not a working provider, and this tier is where the
        // gap bites hardest: the pass is INVISIBLE when it fails (it delivers
        // the raw transcript), so an out-of-credits account, a model the host
        // never pulled, or a family too slow for the latency budget all look
        // exactly like "active and fine" on the card. Measured on a real
        // install, four of five cards that read "ready" could not polish a
        // sentence. So the switch verifies itself and says what it found —
        // cheap here (a handful of tokens) and the only honest signal.
        void verifyPolishProvider();
      } else {
        if (
          descriptor.experimental &&
          !window.confirm(t("apikeys_view.experimental_subscription_consent"))
        ) {
          return;
        }
        const result = await switchRealtimeProvider(
          descriptor.id,
          descriptor.experimental === true,
        );
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
    if (isCodexBrain) return;
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
            ? `Available for ${agentsBrand(assistantName)} only`
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
            {descriptor.experimental && (
              <span
                data-testid={`provider-experimental-${descriptor.id}`}
                className="rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-600 dark:text-violet-300"
              >
                {t("apikeys_view.experimental")}
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
            {/* Neutral, never amber: an unset optional key is not a warning.
                The chip is what makes the whole section read as "recommended",
                so a user scanning for what still needs doing can skip it. */}
            {descriptor.optional && (
              <span
                data-testid={`provider-optional-${descriptor.id}`}
                className="rounded-full border border-border bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground"
                title={t("apikeys_view.optional_tooltip")}
              >
                {t("apikeys_view.optional")}
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
            isCodexBrain
              ? { ...descriptor, configured: Boolean(descriptor.codex_brain_ready) }
              : descriptor
          }
          activating={activating}
          onActivate={activate}
          disabled={
            !isBrainSwitchable || (isCodexBrain && !descriptor.codex_brain_ready)
          }
          disabledReason={
            !isBrainSwitchable
              ? `Available for ${agentsBrand(assistantName)} only`
              : isCodexBrain && !descriptor.codex_brain_ready
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
          {agentBrand(assistantName)} only. This provider cannot be used as the main Brain or
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
            // An on-device provider's list is what is installed here, so there
            // is nothing to type — and with a single entry, nothing to pick.
            fixedCatalog={Boolean(descriptor.local_runtime)}
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
export function ProviderTestControl({
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
export function ActiveControl({
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
  const t = useT();
  const labelTitle = descriptor.active
    ? "This provider is active"
    : disabled
      ? disabledReason ?? "Provider cannot be activated"
      : descriptor.configured
        ? "Activate this provider"
        : t("apikeys_view.needs_credentials");

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

export function BaseUrlField({
  descriptor,
  onChanged,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [value, setValue] = useState(descriptor.base_url ?? "");
  const [saving, setSaving] = useState(false);

  // Follow refetches (e.g. after another card action) without clobbering an
  // in-flight edit: only resync while the user is not mid-save.
  useEffect(() => {
    setValue(descriptor.base_url ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [descriptor.base_url]);

  const dirty = value.trim() !== (descriptor.base_url ?? "");

  async function save() {
    setSaving(true);
    try {
      const stored = await saveProviderBaseUrl(descriptor.id, value.trim() || null);
      setValue(stored ?? "");
      pushToast(
        "success",
        stored ? t("apikeys_base_url.saved") : t("apikeys_base_url.cleared"),
      );
      onChanged();
    } catch (e) {
      pushToast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-1">
      <label className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {t("apikeys_base_url.label")}
      </label>
      <div className="flex gap-2">
        <input
          type="url"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={
            descriptor.default_base_url ?? t("apikeys_base_url.placeholder_required")
          }
          className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
        />
        <Button
          size="sm"
          variant="secondary"
          onClick={save}
          disabled={saving || !dirty}
        >
          {saving ? t("apikeys_base_url.saving") : t("apikeys_base_url.save")}
        </Button>
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {descriptor.default_base_url
          ? t("apikeys_base_url.hint_default")
          : t("apikeys_base_url.hint_required")}
      </p>
    </div>
  );
}

/**
 * The on-device half of a local provider's card: is the engine here, are the
 * weights here, and — when they are not — the one button that fixes it.
 *
 * This panel is the reason the local Whisper card could come back. Its
 * predecessor rendered as ready on installs where nothing was installed, so the
 * rule here is that NOTHING is inferred client-side: `ready` and `detail` come
 * from the server's on-disk probe and are rendered verbatim. While an install
 * runs we poll, because a 3 GB download finishes minutes after the click and a
 * card frozen on "Installing…" would be its own kind of lie.
 */
function LocalRuntimePanel({
  descriptor,
  onChanged,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
}) {
  const t = useT();
  const status = descriptor.local_runtime;
  const [progress, setProgress] = useState<LocalInstallProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = progress?.state === "running";

  // Poll only while an install is actually in flight, and stop on the terminal
  // state. A finished run refreshes the provider list so the card's own
  // readiness (and the "Set active" control that depends on it) update too.
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await localInstallStatus(descriptor.id);
        if (cancelled) return;
        setProgress(next);
        if (next.state === "done" || next.state === "error") {
          window.clearInterval(timer);
          onChanged();
        }
      } catch (err) {
        if (cancelled) return;
        window.clearInterval(timer);
        setError(err instanceof Error ? err.message : String(err));
        setProgress(null);
      }
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [running, descriptor.id, onChanged]);

  if (!status) return null;

  const start = async () => {
    setError(null);
    try {
      setProgress(await startLocalInstall(descriptor.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const failed = progress?.state === "error";
  return (
    <div className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-3">
      <div className="flex items-start gap-2 text-xs">
        {status.ready ? (
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
        ) : (
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
        )}
        <span className="text-muted-foreground">
          {progress?.message ?? status.detail}
        </span>
      </div>
      {!status.ready && (
        <Button
          size="sm"
          variant="secondary"
          onClick={start}
          disabled={running}
          className="gap-2"
        >
          {running ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          {running
            ? t("apikeys_view.local_installing")
            : failed
              ? t("apikeys_view.local_install_retry")
              : t("apikeys_view.local_install_cta")}
        </Button>
      )}
      {running && (
        <p className="text-[11px] text-muted-foreground">
          {t("apikeys_view.local_install_hint")}
        </p>
      )}
      {error && (
        <p className="text-[11px] text-destructive">{error}</p>
      )}
    </div>
  );
}

export function AuthWidget({
  descriptor,
  onChanged,
  onSavedActivate,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
  onSavedActivate?: () => void;
}) {
  const t = useT();
  return (
    <div className="space-y-2">
      <ProviderBillingBadge billing={descriptor.billing} />
      {descriptor.experimental && (
        <div
          data-testid={`provider-experimental-note-${descriptor.id}`}
          className="rounded-md border border-violet-500/30 bg-violet-500/[0.06] px-3 py-2 text-xs leading-relaxed text-muted-foreground"
        >
          <p>{t("apikeys_view.subscription_realtime_description")}</p>
          <p className="mt-1">
            {t("apikeys_view.experimental_subscription_fallback")}
          </p>
        </div>
      )}
      <LocalRuntimePanel descriptor={descriptor} onChanged={onChanged} />
      {descriptor.supports_base_url && (
        <BaseUrlField descriptor={descriptor} onChanged={onChanged} />
      )}
      {descriptor.auth_mode === "none" && (
        <>
          <p className="text-xs text-muted-foreground">
            {descriptor.secret_keys.length > 0
              ? "Local provider — runs without credentials. The key below is optional, only for servers that enforce one (e.g. vLLM --api-key)."
              : "Local provider — no credentials needed."}
          </p>
          {descriptor.secret_keys.map((k) => (
            <ApiKeyForm
              key={k}
              secretKey={k}
              dashboardUrl={descriptor.dashboard_url}
              configured={Boolean(descriptor.secrets_set[k])}
              effectiveConfigured={Boolean(descriptor.secrets_effective?.[k])}
              sharedWith={descriptor.secret_shared_with?.[k] ?? []}
              credentialHelp={descriptor.credential_help}
              onChanged={onChanged}
              onSavedActivate={onSavedActivate}
            />
          ))}
        </>
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
              effectiveConfigured={Boolean(descriptor.secrets_effective?.[k])}
              sharedWith={descriptor.secret_shared_with?.[k] ?? []}
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
  const [loginPolling, setLoginPolling] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);
  const status = descriptor.codex_status;
  const installCommand = descriptor.install_hint ?? "npm i -g @openai/codex";
  const subscriptionOnly = descriptor.secret_keys.length === 0;
  const loginReady = Boolean(
    status?.connected && (!subscriptionOnly || status.mode === "chatgpt"),
  );
  const subscriptionStatusKey = !status
    ? "apikeys_codex.status_loading"
    : status.reason_code === "ready"
      ? "apikeys_codex.status_ready"
      : status.reason_code === "lifecycle_unavailable"
        ? "apikeys_codex.status_lifecycle_unavailable"
      : status.reason_code === "setup_invalid"
        ? "apikeys_codex.status_setup_invalid"
        : status.reason_code === "busy"
          ? "apikeys_codex.status_busy"
          : status.reason_code === "not_installed" || !status.installed
            ? "apikeys_codex.status_not_installed"
            : "apikeys_codex.status_login_required";

  useEffect(() => {
    if (!loginPolling || loginReady) return;
    let stopped = false;
    const deadline = Date.now() + 5 * 60_000;
    let timer: number | null = null;

    const poll = () => {
      if (stopped) return;
      onChanged();
      if (Date.now() < deadline) {
        timer = window.setTimeout(poll, 5_000);
      } else {
        setLoginPolling(false);
      }
    };
    timer = window.setTimeout(poll, 1_000);
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [loginPolling, loginReady, onChanged]);

  async function handleCopy() {
    setPending("copy");
    try {
      const copied = await robustCopy(installCommand);
      pushToast(
        copied ? "success" : "warning",
        copied ? t("apikeys_codex.install_command_copied") : installCommand,
      );
    } finally {
      setPending(null);
    }
  }

  async function handleLogin() {
    setPending("login");
    try {
      await startCodexLogin(subscriptionOnly);
      pushToast("info", t("apikeys_codex.login_started"));
      // OAuth has no fixed completion time. Keep polling until the parent sees
      // connected truth instead of leaving a successful slow login invisible.
      onChanged();
      setLoginPolling(true);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function handleLogout() {
    setPending("logout");
    setLoginPolling(false);
    try {
      await codexLogout(subscriptionOnly);
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
  if (loginReady && status) {
    return (
      <div className="space-y-3">
        <div
          data-testid="codex-connected"
          className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-500/30 bg-emerald-500/[0.06] px-3 py-2 text-xs"
        >
          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
          <span className="min-w-0 break-words text-foreground">
            {subscriptionOnly
              ? t("apikeys_codex.connected_chatgpt")
              : status.message ?? t("apikeys_codex.connected_chatgpt")}
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
            {t("apikeys_codex.disconnect")}
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
          <span>
            {subscriptionOnly
              ? t(subscriptionStatusKey)
              : status?.message ?? t("apikeys_codex.status_loading")}
          </span>
          {status?.version && (
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{status.version}</code>
          )}
        </div>
        {/* A real setup problem carries a precise backend diagnosis (for
            example the exact required Codex version). Hiding it behind the
            generic sentence left users guessing what to fix. */}
        {subscriptionOnly &&
          status?.reason_code === "setup_invalid" &&
          status.message && (
            <div
              data-testid="codex-setup-detail"
              className="mt-2 break-words border-t border-border/60 pt-2 font-mono text-[11px]"
            >
              {status.message}
            </div>
          )}
      </div>

      {!status?.installed && status?.reason_code !== "busy" && (
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-[220px] flex-1 rounded-md border border-border bg-muted/30 px-3 py-1.5 font-mono text-xs">
            {installCommand}
          </code>
          <Button size="sm" variant="outline" onClick={handleCopy} disabled={pending === "copy"}>
            {pending === "copy" ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            {t("apikeys_codex.copy_command")}
          </Button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={handleLogin}
          disabled={pending !== null || loginPolling || !status?.installed}
        >
          <LogIn className="h-3.5 w-3.5" />
          {t("apikeys_codex.connect_chatgpt")}
        </Button>
        <Button size="sm" variant="outline" asChild>
          <a href="https://help.openai.com/en/articles/11381614" target="_blank" rel="noreferrer">
            <Terminal className="h-3.5 w-3.5" />
            {t("apikeys_codex.install_codex")}
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
      const copied = await robustCopy(installCommand);
      pushToast(copied ? "success" : "warning", copied ? "Install command copied" : installCommand);
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

export function StateChip({
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

export function StatusBadge({ descriptor }: { descriptor: ProviderDescriptor }) {
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

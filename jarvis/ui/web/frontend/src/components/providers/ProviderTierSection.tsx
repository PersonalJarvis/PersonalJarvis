import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Bot, Brain, Check, ChevronDown, Copy, Download, HardDrive, Loader2, LogIn, LogOut, Mic, Play, PlugZap, Radio, Search, Sparkles, Square, Terminal, Volume2, Wand2, Waypoints, XCircle } from "lucide-react";
import { AltCredentialNote } from "@/components/AltCredentialNote";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { OpenRouterTtsControls } from "@/components/OpenRouterTtsVoicePicker";
import { CuModelSelector } from "@/components/CuModelSelector";
import { RealtimeOptionsControl } from "@/components/RealtimeOptionsControl";
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import {
  codexLogout,
  loginAntigravity,
  localInstallStatus,
  logoutAntigravity,
  managedServerInstall,
  managedServerModelCatalog,
  managedServerPreflight,
  managedServerSetup,
  managedServerStart,
  managedServerStatus,
  managedServerStop,
  managedServerUninstall,
  modelLibraryTags,
  modelPullStatus,
  ollamaRuntime,
  ollamaRuntimeInstall,
  ollamaRuntimeStart,
  startModelPull,
  type ManagedModelCatalog,
  type ManagedServerRuntime,
  type OllamaRuntimeInstallProgress,
  type OllamaRuntimeStatus,
  pullableModels,
  searchModelLibrary,
  startLocalInstall,
  type LibraryModel,
  type LibraryTag,
  type LocalInstallProgress,
  type ManagedInstallProgress,
  type ManagedPreflight,
  type ModelPullProgress,
  type ProviderDescriptor,
  type PullableModel,
  type PullableRole,
  type PullableModels,
  type ProviderTestResult,
  type ProviderTestStatus,
  type ProviderTier,
  type SectionHealth,
  PROVIDER_BACKEND_UNREACHABLE,
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
import { filterForLocalMode } from "@/lib/localMode";
import {
  realtimeTransportIssueKey,
  requestRealtimeTransportOffer,
  type RealtimeTransportIssue,
} from "@/lib/realtimeTransportIssue";
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
/** Remembered acknowledgement of an experimental provider route.
 *
 * The notice is worth showing once — it explains whose plan pays and that the
 * route can change without notice. Showing it on EVERY switch is the
 * confirmation fatigue this project rejects, and it taught the user to click
 * it away unread, which defeats the point of having it. */
function experimentalConsentKey(providerId: string): string {
  return `jarvis.experimentalConsent.${providerId}`;
}

function hasExperimentalConsent(providerId: string): boolean {
  try {
    return window.localStorage.getItem(experimentalConsentKey(providerId)) === "1";
  } catch {
    // A WebView with storage disabled simply asks again next time: annoying,
    // never broken, and never silently skipping the notice.
    return false;
  }
}

function rememberExperimentalConsent(providerId: string): void {
  try {
    window.localStorage.setItem(experimentalConsentKey(providerId), "1");
  } catch {
    // Same trade-off as above — the dialog reappears, nothing else breaks.
  }
}

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
 * - A sliding gold thumb sits under the segment currently selected in this
 *   view. The header and the provider content therefore always describe the
 *   same mode; runtime truth remains in the dedicated status row below.
 * - Realtime remains selectable when no provider is available, so its setup
 *   cards stay reachable; the context below explains that live activation is
 *   still unavailable.
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
  /** Server/runtime mode, used only for honest live-status metadata. */
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
  const selectedIndex = mode === "realtime" ? 0 : 1;

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
        <span
          data-testid="voice-engine-selection-thumb"
          aria-hidden="true"
          className="absolute inset-y-0.5 left-0.5 w-[calc(50%-0.125rem)] rounded-md bg-primary shadow-[0_0_14px_rgba(255,214,10,0.22)] transition-transform duration-200 ease-out"
          style={{ transform: `translateX(${selectedIndex * 100}%)` }}
        />
        {segments.map((seg) => {
          const isSelected = mode === seg.key;
          const isLive =
            liveMode === seg.key && (seg.key !== "realtime" || realtimeAvailable);
          const needsKey = seg.key === "realtime" && !realtimeAvailable;
          const isRecommended = seg.key === "realtime";
          const Icon = seg.icon;
          return (
            <button
              key={seg.key}
              type="button"
              onClick={() => handleSelect(seg.key)}
              aria-pressed={isSelected}
              data-live={isLive ? "true" : "false"}
              className={cn(
                "relative z-10 inline-flex items-center justify-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isSelected
                  ? "text-primary-foreground"
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
                    ? isSelected
                      ? "bg-primary-foreground/20 text-primary-foreground"
                      : "bg-primary/15 text-primary"
                    : isSelected
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
 * The Local Mode toggle that sits next to the engine switch in the header.
 *
 * One button, two legible states, and an explicit on/off word rather than a
 * lit-versus-unlit icon: the whole complaint this answers is that the previous
 * local path felt like something that happened TO the user and could not be
 * clearly turned back off. A control you can read the state of, and click once
 * to reverse, is the fix.
 *
 * Presentation only — see `lib/localMode.ts`. It never switches a provider and
 * never writes config, so leaving it on can't break an install.
 */
export function LocalModeSwitch({
  enabled,
  onToggle,
}: {
  enabled: boolean;
  onToggle: (next: boolean) => void;
}) {
  const t = useT();
  return (
    <div className="shrink-0">
      <button
        type="button"
        data-testid="local-mode-switch"
        aria-pressed={enabled}
        onClick={() => onToggle(!enabled)}
        title={t(
          enabled ? "apikeys_view.local_mode_title_on" : "apikeys_view.local_mode_title_off",
        )}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-[0.4375rem] text-xs font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          enabled
            ? "border-primary/40 bg-primary/15 text-primary"
            : "border-border bg-card/40 text-muted-foreground hover:text-foreground",
        )}
      >
        <HardDrive aria-hidden="true" className="h-3.5 w-3.5" />
        <span className="whitespace-nowrap">{t("apikeys_view.local_mode_label")}</span>
        <span
          className={cn(
            "whitespace-nowrap rounded-full px-1 py-px text-[8px] font-semibold uppercase tracking-wide",
            enabled
              ? "bg-primary/25 text-primary"
              : "border border-border bg-background/60 text-muted-foreground/80",
          )}
        >
          {t(enabled ? "apikeys_view.local_mode_on" : "apikeys_view.local_mode_off")}
        </span>
      </button>
      {/* Mirrors the engine switch's one-line hint so the two controls sit on
          the same baseline and the header stays a single tidy row. */}
      <p
        data-testid="local-mode-hint"
        className="mt-1 text-right text-[10px] leading-tight text-muted-foreground/70"
      >
        {t("apikeys_view.local_mode_hint")}
      </p>
    </div>
  );
}

/**
 * The one line that explains a shorter card list. Without it, a user who
 * forgot the switch is on reads the missing hosted cards as a broken install —
 * so the notice states the count, and carries the switch-off action itself.
 */
export function LocalModeNotice({
  hiddenCount,
  keptActiveHosted,
  onDisable,
}: {
  hiddenCount: number;
  keptActiveHosted: boolean;
  onDisable: () => void;
}) {
  const t = useT();
  return (
    <div
      data-testid="local-mode-notice"
      // The count as a plain attribute as well as in the sentence: the sentence
      // is a translated template, so this is the one place a test (or a support
      // screenshot) can read the number without depending on wording.
      data-hidden-count={hiddenCount}
      className="mb-3 flex items-start gap-2.5 rounded-xl border border-primary/25 bg-primary/[0.05] px-3 py-2"
    >
      <HardDrive aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <p className="min-w-0 text-xs leading-relaxed text-muted-foreground">
        <span className="font-medium text-foreground">
          {t("apikeys_view.local_mode_label")}
        </span>{" "}
        · {t("apikeys_view.local_mode_notice").replace("{0}", String(hiddenCount))}
        {keptActiveHosted && ` ${t("apikeys_view.local_mode_notice_active")}`}{" "}
        <button
          type="button"
          onClick={onDisable}
          className="font-medium text-primary underline-offset-2 hover:underline"
        >
          {t("apikeys_view.local_mode_show_all")}
        </button>
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
  connecting = false,
  requiresWebRtcOffer = false,
  transportOfferReady = null,
  transportOfferDetail = "",
  transportIssue = null,
  sessionActive,
  activeSessionMode,
  activeSessionProvider,
  activeSessionModel,
  transitioning,
  lastStartError = null,
  liveMode,
  onOpenRecommendedTab,
}: {
  mode: VoiceEngineMode;
  realtimeAvailable: boolean;
  statusKnown: boolean;
  /** A realtime call is negotiating right now — neither idle nor running. */
  connecting?: boolean;
  /** The resolved realtime transport needs a browser WebRTC offer. */
  requiresWebRtcOffer?: boolean;
  /** Whether an offer is registered; null on a backend that does not report it. */
  transportOfferReady?: boolean | null;
  /** Backend one-liner naming WHY no offer is available. Rendered verbatim. */
  transportOfferDetail?: string;
  /** Client-side broker blocker; outranks the backend line when set. */
  transportIssue?: RealtimeTransportIssue | null;
  sessionActive: boolean;
  activeSessionMode: "pipeline" | "realtime" | null;
  activeSessionProvider: string;
  activeSessionModel: string;
  transitioning: boolean;
  /** Why the LAST realtime start attempt failed; null while connecting/live. */
  lastStartError?: { provider: string; message: string; at: number } | null;
  liveMode: string;
  onOpenRecommendedTab: (tab: RecommendationTab) => void;
}) {
  const t = useT();
  const runtimeDetail = [activeSessionProvider, activeSessionModel]
    .filter(Boolean)
    .join(" · ");
  // "Connecting" comes FIRST. A subscription transport spends 15-45 s spawning
  // its app-server, verifying the account and negotiating WebRTC; reporting
  // that window as "no voice session is active" is what made a working call
  // look frozen.
  const runtimeText = connecting
    ? t("apikeys_view.runtime_connecting")
    : transitioning
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
  // The one honest explanation for a call that never starts on an
  // offer-requiring transport (only the subscription route requires one).
  // Rendered verbatim: the backend one-liner names the actual blocker.
  const offerBlocked =
    liveMode === "realtime" &&
    requiresWebRtcOffer &&
    transportOfferReady === false;
  const offerDetail = !offerBlocked
    ? ""
    : transportIssue
      ? t(realtimeTransportIssueKey(transportIssue))
      : transportOfferDetail;
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
              transitioning || connecting
                ? "animate-pulse bg-amber-400 motion-reduce:animate-none"
                : runtimeMatchesSelection
                  ? "bg-emerald-400"
                  : "bg-amber-400",
            )}
          />
          <span
            className={cn(
              runtimeMatchesSelection && !transitioning && !connecting
                ? "text-muted-foreground"
                : "text-amber-300",
            )}
          >
            {runtimeText}
          </span>
        </div>
      </div>

      {offerDetail && (
        <p
          data-testid="voice-engine-transport-offer-detail"
          className="mt-1 text-[11px] leading-snug text-amber-600 dark:text-amber-400"
          aria-live="polite"
        >
          {offerDetail}
        </p>
      )}

      {liveMode === "realtime" && lastStartError && (
        <p
          data-testid="voice-engine-last-start-error"
          className="mt-1 text-[11px] leading-snug text-amber-600 dark:text-amber-400"
          aria-live="polite"
        >
          {t("voice_state.connect_failed")
            .replace("{0}", lastStartError.provider || "?")
            .replace("{1}", lastStartError.message)}
        </p>
      )}

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
  localMode = false,
  onDisableLocalMode,
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
  /** Show only cards that run on the user's own hardware. Presentation only —
   *  defaults off so every existing caller renders the full catalog. */
  localMode?: boolean;
  /** Turns Local Mode back off from the notice above the list. */
  onDisableLocalMode?: () => void;
}) {
  const t = useT();
  const allTierProviders = providers.filter(
    (p) => p.tier === tier && p.brain_switchable !== false,
  );
  const {
    visible: tierProviders,
    hiddenCount,
    keptActiveHosted,
  } = filterForLocalMode(allTierProviders, localMode);

  return (
    <div role="tabpanel">
      <CategoryHero icon={meta.icon} title={meta.title} description={meta.description} />

      {/* Above the guidance band: the shorter list has to be explained before
          the user starts scanning it, not after. */}
      {localMode && hiddenCount > 0 && (
        <LocalModeNotice
          hiddenCount={hiddenCount}
          keptActiveHosted={keptActiveHosted}
          onDisable={() => onDisableLocalMode?.()}
        />
      )}

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
            {error === PROVIDER_BACKEND_UNREACHABLE
              ? t("apikeys_view.backend_unavailable")
              : `${t("apikeys_view.load_error")} (${error}).`}
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
  // The provider this tier actually RUNS on leads the list. Somebody who just
  // picked one — onboarding's local path being the sharpest case — has to find
  // it at the top, not somewhere inside the catalog order below cards they
  // never touched. Capability-driven: whatever is active leads, no provider id
  // is named here (AP-21).
  //
  // Anchored on the id that was active when this list FIRST rendered, never on
  // the live one: re-sorting on every change would yank a card to the top
  // under the pointer the moment it is activated. The anchor re-arms when the
  // tier is re-entered (the view remounts this list per tab/mode change), so
  // the next visit leads with the new choice.
  const [leadId] = useState<string | null>(
    () => providers.find((p) => p.active)?.id ?? null,
  );
  // Configured (or active) providers next — the wall of empty key forms used
  // to bury the one or two cards the user actually set up. `active` counts so
  // an active-but-keyless anomaly (e.g. a free-tier provider) can never hide
  // below untouched cards; among configured cards nothing reorders on a switch
  // (both stay rank 0), so a card never jumps under the pointer mid-click.
  // Stable within each group (Array.sort is stable).
  const rank = (p: ProviderDescriptor) =>
    p.id === leadId ? 2 : Number(p.configured || p.active);
  const sorted = [...providers].sort((a, b) => rank(b) - rank(a));
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
  // The experimental-route acknowledgement. It used to be a window.confirm,
  // which the desktop WebView renders as a raw "127.0.0.1 says" box that also
  // blocks the whole window — and it reappeared on EVERY switch, which is the
  // confirmation fatigue this project explicitly rejects. Now it is an in-app
  // dialog shown once per provider.
  const [consentPending, setConsentPending] = useState(false);
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
      t("apikeys_view.polish_verify_failed")
        .replace("{0}", descriptor.label)
        .replace("{1}", result.detail || result.status),
    );
    // Let the card repaint with the health this test just produced.
    onChanged();
  }

  /** "<tier> → <provider>", plus the "from the next voice start" note. */
  function switchToast(key: string, restartRequired = false): string {
    const line = t(key).replace("{0}", descriptor.label);
    return restartRequired
      ? `${line}${t("apikeys_view.switch_note_next_start")}`
      : line;
  }

  async function activate(assumeConfigured = false) {
    if (descriptor.active) return;
    if (!isBrainSwitchable) {
      pushToast(
        "warning",
        t("apikeys_view.agents_only_toast")
          .replace("{0}", descriptor.label)
          .replace("{1}", agentsBrand(assistantName)),
      );
      return;
    }
    if (isCodexBrain && !descriptor.codex_brain_ready) {
      // The card is "connected" via OAuth, but a chat brain needs an OpenAI key.
      // Guide honestly instead of switching and failing on the first turn.
      pushToast("warning", t("apikeys_codex.brain_needs_openai_key"));
      return;
    }
    if (
      !assumeConfigured &&
      !descriptor.configured &&
      // plan_unsupported deliberately proceeds to the backend switch: its
      // live account gate is the ONLY judge that can clear the sticky block
      // when the plan changed back; a re-refusal answers with the precise
      // 409 detail as an error toast.
      descriptor.codex_status?.reason_code !== "plan_unsupported"
    ) {
      const codexReason = descriptor.codex_status?.reason_code;
      // Transient/in-flight states are notes, not faults — and telling the
      // user to redo a working ChatGPT login would contradict the card's own
      // "checking" / "finish the login" line (and the backend's 409s).
      if (codexReason === "busy" || codexReason === "login_in_progress") {
        pushToast("info", t(CODEX_STATUS_KEY_BY_REASON[codexReason]));
        return;
      }
      pushToast(
        "warning",
        descriptor.auth_mode === "codex"
          ? isSubscriptionLoginOnly
            ? // The reason-specific line names the actual remedy (install the
              // pinned release, unsupported OS, unsupported plan…) — a blank
              // "connect first" would be wrong for most of those states.
              t(
                (codexReason &&
                  (CODEX_STATUS_KEY_BY_REASON as Record<string, string>)[
                    codexReason
                  ]) ||
                  "apikeys_codex.subscription_login_required",
              )
            : t("apikeys_codex.needs_codex_full").replace("{0}", descriptor.label)
          : descriptor.auth_mode === "antigravity"
            ? t("apikeys_antigravity.needs_login_full").replace("{0}", descriptor.label)
            : t("apikeys_codex.needs_key_full").replace("{0}", descriptor.label),
      );
      return;
    }
    // The experimental acknowledgement must come BEFORE the optimistic flip:
    // a declined dialog used to return early with the radio stuck on the new
    // card and no refetch to roll it back.
    const isRealtimeSwitch = !["brain", "tts", "stt", "computer-use", "dictation"].includes(
      descriptor.tier,
    );
    if (
      isRealtimeSwitch &&
      descriptor.experimental &&
      !hasExperimentalConsent(descriptor.id)
    ) {
      setConsentPending(true);
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
        pushToast("success", switchToast("apikeys_view.switch_done_brain"));
        window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      } else if (descriptor.tier === "tts") {
        const result = await switchTtsProvider(descriptor.id);
        pushToast(
          "success",
          switchToast("apikeys_view.switch_done_tts", result.restart_required),
        );
        window.dispatchEvent(new CustomEvent("jarvis:tts-switched"));
      } else if (descriptor.tier === "stt") {
        const result = await switchSttProvider(descriptor.id);
        pushToast(
          "success",
          switchToast("apikeys_view.switch_done_stt", result.restart_required),
        );
        window.dispatchEvent(new CustomEvent("jarvis:stt-switched"));
      } else if (descriptor.tier === "computer-use") {
        await switchComputerUseProvider(descriptor.id);
        pushToast("success", switchToast("apikeys_view.switch_done_computer_use"));
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
        pushToast("success", switchToast("apikeys_view.switch_done_dictation"));
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
        // The switch reconnects an OPEN call synchronously, and an
        // offer-requiring transport cannot start without a registered browser
        // offer. Ask for one before the POST rather than after the reconnect
        // already failed. Dispatched for every realtime switch, never keyed on
        // a provider name (AP-21) — a transport that needs none ignores it.
        requestRealtimeTransportOffer();
        const result = await switchRealtimeProvider(
          descriptor.id,
          descriptor.experimental === true,
        );
        pushToast(
          "success",
          switchToast("apikeys_view.switch_done_realtime", result.restart_required),
        );
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

  // A click anywhere on the card activates the provider. There is deliberately
  // NO separate onDoubleClick: a double click already delivers two `click`
  // events, and binding both handlers made it fire activate() THREE times —
  // on a card that only answers with "connect this provider first", that meant
  // three identical warnings per double click (six after two), stacked into a
  // wall over the connect button itself.
  //
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

  const consentDialog = consentPending ? (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 p-4"
      onClick={(event) => {
        event.stopPropagation();
        setConsentPending(false);
      }}
    >
      <div
        className="card-outline max-w-lg space-y-4 bg-background p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="text-sm font-semibold">{descriptor.label}</div>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t(
            isSubscriptionLoginOnly
              ? "apikeys_view.experimental_subscription_consent"
              : "apikeys_view.experimental_consent",
          )}
        </p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            className="btn-outline px-3 py-1.5 text-sm"
            onClick={(event) => {
              event.stopPropagation();
              setConsentPending(false);
            }}
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary px-3 py-1.5 text-sm"
            onClick={(event) => {
              event.stopPropagation();
              rememberExperimentalConsent(descriptor.id);
              setConsentPending(false);
              void activate();
            }}
          >
            {t("common.yes")}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <>
    {consentDialog}
    <div
      onClick={handleCardActivate}
      title={
        descriptor.active
          ? t("apikeys_view.active_tooltip")
          : !isBrainSwitchable
            ? t("apikeys_view.agents_only_short").replace(
                "{0}",
                agentsBrand(assistantName),
              )
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
              {t(`apikeys_view.auth_mode_${descriptor.auth_mode}`)}
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
              ? t("apikeys_view.agents_only_short").replace(
                  "{0}",
                  agentsBrand(assistantName),
                )
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
          {t("apikeys_view.agents_only_note").replace(
            "{0}",
            agentBrand(assistantName),
          )}
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
      {descriptor.tier === "realtime" &&
        (descriptor.configured ||
          // Keep the model/voice pickers mounted through a transient busy
          // probe so the card does not visibly flicker while saying
          // "one moment".
          descriptor.codex_status?.reason_code === "busy") && (
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
    </>
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
    ? t("apikeys_view.activate_tooltip_active")
    : disabled
      ? disabledReason ?? t("apikeys_view.activate_tooltip_blocked")
      : descriptor.configured
        ? t("apikeys_view.activate_tooltip_activate")
        : descriptor.codex_status?.reason_code
          ? // The reason-specific line names the actual remedy; a generic
            // "connect first" is wrong for most non-ready codex states.
            t(
              (CODEX_STATUS_KEY_BY_REASON as Record<string, string>)[
                descriptor.codex_status.reason_code
              ] ?? "apikeys_view.needs_credentials",
            )
          : t("apikeys_view.needs_credentials");

  return (
    <label
      // The radio owns its own activation; letting the click bubble would run
      // the card handler for the same gesture and send a second API call.
      // (The card no longer binds onDoubleClick, so there is nothing else to
      // stop here.)
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex shrink-0 select-none items-center gap-1.5 rounded-full border px-2.5 py-1.5 text-xs transition-colors focus-within:ring-2 focus-within:ring-primary focus-within:ring-offset-2 focus-within:ring-offset-background",
        disabled ? "cursor-not-allowed" : "cursor-pointer",
        descriptor.active
          ? "border-primary/40 bg-primary/10 font-semibold text-primary"
          : descriptor.configured
            ? "border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground"
            : "border-border/60 text-muted-foreground/70",
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
      {activating
        ? t("apikeys_view.provider_activating")
        : descriptor.active
          ? t("apikeys_view.provider_active")
          : t("apikeys_view.provider_set_active")}
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

export type ManagedRuntimeBadge = {
  key: string;
  tone: "ok" | "warn" | "muted";
};

/**
 * The live server verdict, in one place because it is a RULE, not decoration.
 *
 * Honest about ownership: reachable-and-not-ours is a port conflict (or an
 * orphan of a removed install), never claimed as ours. Honest about
 * readiness: "running" means the server could take a call, not merely that a
 * socket answers. The old port-only badge stayed green through the whole
 * model load, so the card said "Server running" while every call died on a
 * cold pool (live 2026-08-09 11:50:47).
 *
 * A pool with every slot in use is still healthy — that is a busy server, not
 * a broken one. Backends older than this rule omit `ready`/`available`;
 * treating only an explicit `false` as bad keeps the badge exactly as honest
 * as its input instead of inventing a warning from a missing field.
 */
export function managedRuntimeBadge(
  runtime: ManagedServerRuntime | null,
  installReady: boolean,
): ManagedRuntimeBadge | null {
  if (runtime === null) return null;
  const ours = runtime.owned || installReady;
  const serving = (runtime.pool?.in_use ?? 0) > 0;
  const canTakeACall =
    runtime.ready !== false && (runtime.available !== false || serving);
  if (runtime.reachable && ours) {
    if (canTakeACall) {
      return { key: "apikeys_view.managed_server_running", tone: "ok" };
    }
    return runtime.ready === false
      ? { key: "apikeys_view.managed_server_starting", tone: "warn" }
      : { key: "apikeys_view.managed_server_no_capacity", tone: "warn" };
  }
  if (runtime.reachable) {
    return { key: "apikeys_view.managed_server_port_conflict", tone: "warn" };
  }
  if (runtime.owned) {
    return { key: "apikeys_view.managed_server_starting", tone: "warn" };
  }
  // Two consecutive readiness timeouts mean whole five-minute boot budgets
  // burned with nothing to show — "Server stopped" would hide a crash loop
  // behind the same muted badge as a deliberate stop (live 2026-08-09, four
  // reaped generations in forty minutes read as an endless quiet "starting").
  if ((runtime.boot?.failed_streak ?? 0) >= 2) {
    return { key: "apikeys_view.managed_server_crash_loop", tone: "warn" };
  }
  return { key: "apikeys_view.managed_server_stopped", tone: "muted" };
}

/**
 * One-click managed install for the self-hosted realtime server (the card
 * that used to require a terminal, a venv, and a hand-written launch
 * command). Flow: preflight (honest hardware/tier/brain verdict or the
 * 12 GB-floor blocker) → one confirm with download size and expected
 * latency in view → poll-shaped progress → the card's fail-closed readiness
 * flips server-side. Every sentence of substance comes from the server and
 * renders verbatim; only the chrome is localized.
 */
function ManagedServerPanel({
  descriptor,
  onChanged,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
}) {
  const t = useT();
  const status = descriptor.managed_server;
  const [preflight, setPreflight] = useState<ManagedPreflight | null>(null);
  const [progress, setProgress] = useState<ManagedInstallProgress | null>(null);
  const [runtime, setRuntime] = useState<ManagedServerRuntime | null>(null);
  const [catalog, setCatalog] = useState<ManagedModelCatalog | null>(null);
  const [selectedBrain, setSelectedBrain] = useState("");
  const [selectedVoice, setSelectedVoice] = useState("");
  const [voiceQuery, setVoiceQuery] = useState("");
  const [setupBusy, setSetupBusy] = useState(false);
  const [setupNote, setSetupNote] = useState<string | null>(null);
  const [libraryBusy, setLibraryBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const running = Boolean(progress?.running);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const next = await managedServerStatus();
        if (cancelled) return;
        setProgress(next.progress);
        setRuntime(next.runtime ?? null);
        if (!next.progress.running) {
          window.clearInterval(timer);
          if (next.progress.phase === "error") {
            setError(next.progress.error || "install failed");
          }
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
  }, [running, onChanged]);

  // Idle runtime poll: the panel used to only look at the server DURING an
  // install, so a crashed/stopped/foreign server rendered as whatever the
  // last install left behind. One fetch on mount, then a slow heartbeat.
  const installed = Boolean(status?.ready);
  useEffect(() => {
    if (running || !installed) return;
    let cancelled = false;
    const read = async () => {
      try {
        const next = await managedServerStatus();
        if (!cancelled) setRuntime(next.runtime ?? null);
      } catch {
        if (!cancelled) setRuntime(null);
      }
    };
    void read();
    const timer = window.setInterval(read, 20_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [running, installed]);

  if (!status) return null;

  // The Remove control must survive a PARTIAL state: after a failed
  // uninstall (or interrupted install) `ready` is false but gigabytes are
  // still on disk — gating removal on `ready` would strand them forever.
  const anythingOnDisk = Object.values(status.components ?? {}).some(Boolean);

  const check = async () => {
    setError(null);
    setChecking(true);
    try {
      setPreflight(await managedServerPreflight());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setChecking(false);
    }
  };

  const startInstall = async () => {
    setError(null);
    try {
      // A fixable blocked brain is CONFIRMED as "ollama": the engine sets it
      // up first and its post-setup re-check must match this confirmation.
      const first = await managedServerInstall(
        preflight?.brain_fixable ? "ollama" : preflight?.brain?.kind,
        selectedBrain,
        selectedVoice,
      );
      setProgress(first);
      // A failure BEFORE the first poll tick (fast preflight error inside
      // the engine thread) would otherwise never render: the polling
      // effect only runs while `running` is true.
      if (!first.running && first.phase === "error" && first.error) {
        setError(first.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const remove = async () => {
    if (!confirmRemove) {
      setConfirmRemove(true);
      return;
    }
    setConfirmRemove(false);
    setError(null);
    try {
      await managedServerUninstall();
      setPreflight(null);
      setProgress(null);
      setRuntime(null);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const startServer = async () => {
    setError(null);
    setLifecycleBusy(true);
    try {
      setRuntime(await managedServerStart());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLifecycleBusy(false);
    }
  };

  const loadCatalog = useCallback(async () => {
    try {
      const next = await managedServerModelCatalog();
      setCatalog(next);
      setSelectedBrain((current) =>
        current ||
        next.brain.current ||
        next.brain.models.find((choice) => choice.recommended && choice.fits)?.id ||
        next.brain.models.find((choice) => choice.fits)?.id ||
        "",
      );
      setSelectedVoice((current) =>
        current ||
        next.current ||
        next.models.find((choice) => choice.recommended && choice.selectable)?.id ||
        next.models.find((choice) => choice.selectable)?.id ||
        "",
      );
    } catch (err) {
      setCatalog(null);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (running) return;
    void loadCatalog();
  }, [running, loadCatalog]);

  const downloadBrain = async (model: string) => {
    await startModelPull("ollama", model);
    for (;;) {
      const pull = await modelPullStatus("ollama", model);
      if (pull.state === "done") return;
      if (pull.state === "error") {
        throw new Error(pull.message || "model download failed");
      }
      setSetupNote(`${model}: ${Math.round(pull.percent ?? 0)}%`);
      await new Promise((resolve) => window.setTimeout(resolve, 2500));
    }
  };

  const applySetup = async (
    brainModel = selectedBrain,
    downloadFirst = false,
    voiceModel = selectedVoice,
  ) => {
    if (!brainModel || !voiceModel) return;
    setError(null);
    setSetupNote(null);
    setSetupBusy(true);
    try {
      const choice = catalog?.brain.models.find((item) => item.id === brainModel);
      if (downloadFirst || (choice && !choice.installed)) {
        // Download through the SAME pull machinery the Ollama card uses,
        // then adopt — never adopt a model the server cannot serve yet.
        await downloadBrain(brainModel);
      }
      const result = await managedServerSetup(brainModel, voiceModel);
      const latency = result.smoke.first_audio_ms;
      setSelectedBrain(brainModel);
      setSetupNote(
        latency === null
          ? t("apikeys_view.managed_setup_complete")
          : t("apikeys_view.managed_setup_complete_ms").replace(
              "{0}",
              String(latency),
            ),
      );
      await loadCatalog();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSetupBusy(false);
      setLibraryBusy(null);
    }
  };

  const applyCatalogModel = async (model: string, installedModel: boolean) => {
    setLibraryBusy(model);
    setSelectedBrain(model);
    if (!installed) {
      try {
        if (!installedModel) await downloadBrain(model);
        setSetupNote(t("apikeys_view.managed_catalog_selected"));
        await loadCatalog();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLibraryBusy(null);
      }
      return;
    }
    await applySetup(model, !installedModel);
  };

  const applyVoiceChoice = async (choice: ManagedModelCatalog["models"][number]) => {
    if (!choice.selectable || !selectedBrain) return;
    setSelectedVoice(choice.id);
    setError(null);
    setSetupNote(null);
    if (!installed) {
      setSetupNote(t("apikeys_view.managed_catalog_selected"));
      return;
    }
    if (choice.runtime_ready === false) {
      setSetupBusy(true);
      try {
        // A runtime-compatible model whose optional package is absent uses the
        // existing resumable managed installer. It stops the owned server,
        // installs the pinned adapter dependency, applies compatibility
        // patches, downloads this checkpoint, and only commits after smoke.
        const first = await managedServerInstall(undefined, selectedBrain, choice.id);
        setProgress(first);
        if (!first.running && first.phase === "error" && first.error) {
          setError(first.error);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSetupBusy(false);
      }
      return;
    }
    await applySetup(selectedBrain, false, choice.id);
  };

  const stopServer = async () => {
    setError(null);
    setLifecycleBusy(true);
    try {
      setRuntime(await managedServerStop());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLifecycleBusy(false);
    }
  };

  const runtimeBadge = managedRuntimeBadge(runtime, status.ready);

  const failed = progress?.phase === "error";
  const normalizedVoiceQuery = voiceQuery.trim().toLocaleLowerCase();
  const voiceResults = (catalog?.models ?? []).filter((choice) => {
    if (!normalizedVoiceQuery) return true;
    return [
      choice.label,
      choice.backend,
      choice.model,
      choice.note,
      choice.license ?? "",
      ...(choice.languages ?? []),
    ]
      .join(" ")
      .toLocaleLowerCase()
      .includes(normalizedVoiceQuery);
  });
  return (
    <div className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-3">
      <div className="flex items-start gap-2 text-xs">
        {status.ready ? (
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
        ) : (
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
        )}
        <span className="text-muted-foreground">
          {running ? (progress?.detail ?? status.sentence) : status.sentence}
        </span>
      </div>

      {!running && installed && runtimeBadge && (
        <div
          className="flex items-center gap-2 text-[11px]"
          data-testid="managed-server-runtime"
          aria-live="polite"
        >
          <span
            aria-hidden="true"
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              runtimeBadge.tone === "ok"
                ? "bg-emerald-400"
                : runtimeBadge.tone === "warn"
                  ? "animate-pulse bg-amber-400 motion-reduce:animate-none"
                  : "bg-muted-foreground/40",
            )}
          />
          <span
            className={cn(
              runtimeBadge.tone === "warn" ? "text-amber-400" : "text-muted-foreground",
            )}
          >
            {t(runtimeBadge.key)}
          </span>
          {runtimeBadge.key === "apikeys_view.managed_server_starting" &&
            runtime?.boot?.starting &&
            runtime.boot.stage_label && (
              <span className="text-muted-foreground">
                {typeof runtime.boot.remaining_s === "number" &&
                runtime.boot.remaining_s > 0
                  ? `${runtime.boot.stage_label} · ${t(
                      "apikeys_view.managed_server_starting_eta",
                    ).replace(
                      "{0}",
                      String(
                        Math.max(
                          10,
                          Math.round(runtime.boot.remaining_s / 10) * 10,
                        ),
                      ),
                    )}`
                  : runtime.boot.stage_label}
              </span>
            )}
          {runtime && !runtime.reachable && !runtime.owned && (
            <Button
              size="sm"
              variant="secondary"
              onClick={startServer}
              disabled={lifecycleBusy}
              className="h-6 gap-1.5 px-2 text-[11px]"
            >
              {lifecycleBusy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Play className="h-3 w-3" />
              )}
              {t("apikeys_view.managed_start_cta")}
            </Button>
          )}
          {runtime?.owned && (
            <Button
              size="sm"
              variant="ghost"
              onClick={stopServer}
              disabled={lifecycleBusy}
              className="h-6 gap-1.5 px-2 text-[11px]"
            >
              {lifecycleBusy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Square className="h-3 w-3" />
              )}
              {t("apikeys_view.managed_stop_cta")}
            </Button>
          )}
        </div>
      )}

      {setupNote && (
        <p className="text-[11px] text-muted-foreground" aria-live="polite">
          {setupNote}
        </p>
      )}

      {!running && catalog && (
        <div className="space-y-3" data-testid="managed-model-picker">
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="space-y-1 rounded-md border border-border/60 bg-background/50 p-2">
              <span className="flex items-center gap-1.5 text-[11px] font-medium">
                <Mic className="h-3.5 w-3.5 text-muted-foreground" />
                {t("apikeys_view.managed_hearing_title")}
              </span>
              <span className="block text-xs">{catalog.hearing.label}</span>
              <span className="block text-[10px] leading-snug text-muted-foreground">
                {t("apikeys_view.managed_hearing_note")}
              </span>
            </label>

            <label className="space-y-1 rounded-md border border-border/60 bg-background/50 p-2">
              <span className="flex items-center gap-1.5 text-[11px] font-medium">
                <Brain className="h-3.5 w-3.5 text-muted-foreground" />
                {t("apikeys_view.managed_thinking_title")}
              </span>
              <BrandedSelect
                ariaLabel={t("apikeys_view.managed_thinking_title")}
                value={selectedBrain}
                onValueChange={setSelectedBrain}
                disabled={setupBusy}
                className="h-8 px-2 text-xs"
                options={catalog.brain.models.map((choice) => ({
                  value: choice.id,
                  label: `${choice.label} · ~${choice.size_gb} GB${
                    choice.recommended
                      ? ` · ${t("apikeys_view.managed_brain_recommended")}`
                      : ""
                  }${
                    choice.fits
                      ? ""
                      : ` · ${t("apikeys_view.managed_brain_no_fit")}`
                  }`,
                  disabled: !choice.fits,
                }))}
              />
            </label>

            <label className="space-y-1 rounded-md border border-border/60 bg-background/50 p-2">
              <span className="flex items-center gap-1.5 text-[11px] font-medium">
                <Volume2 className="h-3.5 w-3.5 text-muted-foreground" />
                {t("apikeys_view.managed_speaking_title")}
              </span>
              <BrandedSelect
                ariaLabel={t("apikeys_view.managed_speaking_title")}
                value={selectedVoice}
                onValueChange={setSelectedVoice}
                disabled={setupBusy}
                className="h-8 px-2 text-xs"
                options={catalog.models.map((choice) => ({
                  value: choice.id,
                  label: `${choice.label}${
                    choice.recommended
                      ? ` · ${t("apikeys_view.managed_brain_recommended")}`
                      : ""
                  }${
                    !choice.selectable
                      ? ` · ${t("apikeys_view.managed_voice_unavailable")}`
                      : choice.runtime_ready === false
                        ? ` · ${t("apikeys_view.managed_voice_install_required")}`
                        : ""
                  }`,
                  disabled: !choice.selectable,
                }))}
              />
            </label>
          </div>

          {installed && (
            <Button
              size="sm"
              onClick={() => void applySetup()}
              disabled={setupBusy || !selectedBrain || !selectedVoice}
              className="gap-2"
            >
              {setupBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Volume2 className="h-3.5 w-3.5" />
              )}
              {t("apikeys_view.managed_apply_test")}
            </Button>
          )}

          <details className="rounded-md border border-border/50 bg-background/30 p-2">
              <summary className="cursor-pointer text-[11px] font-medium">
                {t("apikeys_view.managed_full_catalog")}
              </summary>
              <LibraryBrowser
                providerId="ollama"
                onPull={(model) => void applyCatalogModel(model, false)}
                onInstalledSelect={(model) => void applyCatalogModel(model, true)}
                disabled={setupBusy}
                pullingModel={libraryBusy}
                actionLabel={t("apikeys_view.managed_download_test")}
                installedActionLabel={t("apikeys_view.managed_use_test")}
              />
          </details>

          <details
            data-testid="managed-voice-catalog"
            className="rounded-md border border-border/50 bg-background/30 p-2"
          >
            <summary className="cursor-pointer text-[11px] font-medium">
              {t("apikeys_view.managed_voice_catalog_title")}
            </summary>
            <div className="mt-2 space-y-2">
              <label className="relative block">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground"
                />
                <input
                  type="search"
                  value={voiceQuery}
                  onChange={(event) => setVoiceQuery(event.target.value)}
                  placeholder={t("apikeys_view.managed_voice_catalog_search")}
                  aria-label={t("apikeys_view.managed_voice_catalog_search")}
                  className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-2 text-xs"
                />
              </label>

              {voiceResults.length === 0 ? (
                <p className="px-1 py-2 text-[11px] text-muted-foreground">
                  {t("apikeys_view.managed_voice_catalog_empty")}
                </p>
              ) : (
                <div className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
                  {voiceResults.map((choice) => (
                    <div
                      key={choice.id}
                      data-testid={`managed-voice-row-${choice.id}`}
                      className={cn(
                        "flex items-start justify-between gap-3 rounded-md border px-2.5 py-2",
                        choice.id === selectedVoice
                          ? "border-primary/45 bg-primary/[0.05]"
                          : "border-border/50 bg-background/50",
                      )}
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-xs font-medium">{choice.label}</span>
                          {choice.frontier && (
                            <span className="rounded-full bg-primary/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-primary">
                              {t("apikeys_view.managed_voice_frontier")}
                            </span>
                          )}
                          {choice.streaming && (
                            <span className="rounded-full border border-border px-1.5 py-px text-[9px] text-muted-foreground">
                              {t("apikeys_view.managed_voice_streaming")}
                            </span>
                          )}
                          <span
                            className={cn(
                              "rounded-full border px-1.5 py-px text-[9px]",
                              choice.selectable
                                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
                                : "border-amber-500/30 bg-amber-500/10 text-amber-500",
                            )}
                          >
                            {t(
                              choice.selectable
                                ? "apikeys_view.managed_voice_compatible"
                                : "apikeys_view.managed_voice_integration_pending",
                            )}
                          </span>
                        </div>
                        <p className="break-all font-mono text-[10px] text-muted-foreground">
                          {choice.model}
                        </p>
                        <p className="text-[10px] leading-snug text-muted-foreground">
                          {choice.note}
                        </p>
                        <div className="flex flex-wrap items-center gap-1 text-[9px] text-muted-foreground">
                          {(choice.languages ?? []).slice(0, 12).map((language) => (
                            <span key={language} className="rounded bg-muted px-1 py-px uppercase">
                              {language}
                            </span>
                          ))}
                          {(choice.languages?.length ?? 0) > 12 && (
                            <span>+{(choice.languages?.length ?? 0) - 12}</span>
                          )}
                          {choice.release_date && <span>· {choice.release_date}</span>}
                          {choice.license && <span>· {choice.license}</span>}
                          {(choice.size_gb ?? 0) > 0 && <span>· ~{choice.size_gb} GB</span>}
                          {choice.source_url && (
                            <a
                              href={choice.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-medium text-primary hover:underline"
                            >
                              {t("apikeys_view.managed_voice_source")}
                            </a>
                          )}
                        </div>
                      </div>
                      {choice.selectable && (
                        <Button
                          size="sm"
                          variant={choice.id === selectedVoice ? "secondary" : "outline"}
                          disabled={setupBusy || !selectedBrain}
                          onClick={() => void applyVoiceChoice(choice)}
                          className="h-7 shrink-0 gap-1 px-2 text-[10px]"
                        >
                          {setupBusy && choice.id === selectedVoice ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Volume2 className="h-3 w-3" />
                          )}
                          {t(
                            choice.runtime_ready === false
                              ? "apikeys_view.managed_voice_install_test"
                              : "apikeys_view.managed_use_test",
                          )}
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </details>
        </div>
      )}

      {running && (
        <div className="space-y-1">
          <div className="h-1.5 w-full overflow-hidden rounded bg-muted">
            <div
              className="h-full rounded bg-primary transition-all"
              style={{ width: `${Math.max(2, progress?.percent ?? 0)}%` }}
            />
          </div>
          <p className="text-[11px] text-muted-foreground">
            {t("apikeys_view.managed_hint")}
          </p>
        </div>
      )}

      {!status.ready && !running && !preflight && (
        <Button
          size="sm"
          variant="secondary"
          onClick={check}
          disabled={checking}
          className="gap-2"
        >
          {checking ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          {failed
            ? t("apikeys_view.managed_retry")
            : t("apikeys_view.managed_check_cta")}
        </Button>
      )}

      {!status.ready && !running && preflight && !preflight.ok && !preflight.brain_fixable && (
        <div className="space-y-2 text-xs">
          <p className="text-amber-500">{preflight.blocker}</p>
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
            {preflight.actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </div>
      )}

      {!status.ready &&
        !running &&
        preflight &&
        (preflight.ok || preflight.brain_fixable) &&
        preflight.tier && (
          <div className="space-y-2 text-xs">
            <div className="space-y-0.5 text-muted-foreground">
              <p>
                {preflight.tier.label} · {t("apikeys_view.managed_download_size")}{" "}
                ~{preflight.tier.download_gb} GB ·{" "}
                {t("apikeys_view.managed_latency")}{" "}
                {preflight.tier.expected_latency}
              </p>
              <p>{preflight.stack_sentence}</p>
              {preflight.brain && <p>{preflight.brain.note}</p>}
              {/* Blocked brain, but the install fixes it itself: say so
                  honestly BEFORE the click — Ollama plus the brain model are
                  extra downloads the tier size above does not include. */}
              {!preflight.ok && preflight.brain_fixable && (
                <p className="text-amber-500">
                  {t("apikeys_view.managed_brain_setup_note")}
                </p>
              )}
            </div>
            <Button size="sm" onClick={startInstall} className="gap-2">
              <Download className="h-3.5 w-3.5" />
              {t("apikeys_view.managed_install_cta")}
            </Button>
          </div>
        )}

      {anythingOnDisk && !running && (
        <Button
          size="sm"
          variant="ghost"
          onClick={remove}
          className="gap-2 text-destructive hover:text-destructive"
        >
          <XCircle className="h-3.5 w-3.5" />
          {confirmRemove
            ? t("apikeys_view.managed_uninstall_confirm")
            : t("apikeys_view.managed_uninstall")}
        </Button>
      )}

      {error && <p className="text-[11px] text-destructive">{error}</p>}
    </div>
  );
}

/**
 * Install or start the Ollama runtime itself — the last terminal step on the
 * local-model path. Invisible while Ollama runs (nothing to do); when it is
 * stopped it offers Start, when it is absent it offers a confirmed Install
 * (official ollama.com artifacts only, backend-authored status sentences
 * rendered verbatim). Mounted on pull-capable cards and inside the managed
 * realtime card's blocked-brain state.
 */
function OllamaRuntimePanel({
  providerId,
  onChanged,
}: {
  /** The pull-capable card whose runtime routes this panel drives. */
  providerId: string;
  onChanged: () => void;
}) {
  const t = useT();
  const [status, setStatus] = useState<OllamaRuntimeStatus | null>(null);
  const [progress, setProgress] = useState<OllamaRuntimeInstallProgress | null>(
    null,
  );
  const [confirmInstall, setConfirmInstall] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const installing = Boolean(progress?.running);

  const refresh = useCallback(async () => {
    try {
      const next = await ollamaRuntime(providerId);
      // A mocked/older backend may answer with a partial body — treat any
      // missing half as "no data" instead of crashing the whole card.
      if (!next.status || !next.install) return null;
      setStatus(next.status);
      setProgress(next.install);
      return next;
    } catch {
      return null;
    }
  }, [providerId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!installing) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      const next = await refresh();
      if (cancelled || next === null) return;
      if (!next.install.running) {
        window.clearInterval(timer);
        if (next.install.phase === "error") {
          setError(next.install.error || "install failed");
        }
        onChanged();
      }
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [installing, refresh, onChanged]);

  const install = async () => {
    if (!confirmInstall) {
      setConfirmInstall(true);
      return;
    }
    setConfirmInstall(false);
    setError(null);
    try {
      const first = await ollamaRuntimeInstall(providerId);
      setProgress(first);
      if (!first.running && first.phase === "error" && first.error) {
        setError(first.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const start = async () => {
    setError(null);
    setBusy(true);
    try {
      const next = await ollamaRuntimeStart(providerId);
      if (next) setStatus(next);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (status === null) return null;
  if (status.running && !installing) return null; // healthy — stay out of the way

  return (
    <div
      className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-3"
      data-testid="ollama-runtime-panel"
    >
      <div className="flex items-start gap-2 text-xs">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
        <span className="text-muted-foreground">
          {installing ? (progress?.detail ?? status.detail) : status.detail}
        </span>
      </div>

      {installing && (
        <div className="h-1.5 w-full overflow-hidden rounded bg-muted">
          <div
            className="h-full rounded bg-primary transition-all"
            style={{ width: `${Math.max(2, progress?.percent ?? 0)}%` }}
          />
        </div>
      )}

      {!installing && !status.installed && (
        <div className="space-y-1">
          <Button size="sm" onClick={install} className="gap-2">
            <Download className="h-3.5 w-3.5" />
            {confirmInstall
              ? t("apikeys_view.ollama_install_confirm")
              : t("apikeys_view.ollama_install_cta")}
          </Button>
          <p className="text-[11px] text-muted-foreground">
            {t("apikeys_view.ollama_install_hint")}
          </p>
        </div>
      )}

      {!installing && status.installed && !status.running && (
        <Button
          size="sm"
          variant="secondary"
          onClick={start}
          disabled={busy}
          className="gap-2"
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          {t("apikeys_view.ollama_start_cta")}
        </Button>
      )}

      {error && <p className="text-[11px] text-destructive">{error}</p>}
    </div>
  );
}

/**
 * Browse the provider's WHOLE public library, not just the curated shortlist.
 *
 * The shortlist answers "which model should I get?" for someone who has no
 * opinion. Someone who does had exactly one way through before this: type an
 * exact tag into a blind text field and hope. That meant leaving the app for a
 * browser, and it silently punished the common mistake of pulling a bare name
 * (`qwen3.5` is 6.6 GB; `qwen3.5:122b` is not).
 *
 * So the same field now searches. Results carry the catalog's own blurb and
 * badges, and opening one lists every published version with its real download
 * size and the SAME fit verdict the shortlist uses. Nothing here is required:
 * the field still pulls whatever is typed, so a machine that cannot reach the
 * public catalog keeps the exact-name path it always had.
 */
function LibraryBrowser({
  providerId,
  onPull,
  onInstalledSelect,
  disabled,
  pullingModel,
  actionLabel,
  installedActionLabel,
}: {
  providerId: string;
  onPull: (model: string) => void;
  onInstalledSelect?: (model: string) => void;
  /** The local server is unreachable — nothing can be downloaded into it. */
  disabled: boolean;
  /** Id of the download in flight, so its row shows the spinner. */
  pullingModel: string | null;
  actionLabel?: string;
  installedActionLabel?: string;
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LibraryModel[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [openModel, setOpenModel] = useState<string | null>(null);
  const [tags, setTags] = useState<Record<string, LibraryTag[]>>({});
  const [tagsError, setTagsError] = useState<string | null>(null);
  const [loadingTags, setLoadingTags] = useState(false);

  // Nothing is fetched until the user asks for it. The panel renders inside a
  // provider LIST, and a search-on-mount would hit the public catalog once per
  // card on every render of the settings view.
  const [browsing, setBrowsing] = useState(false);

  const runSearch = useCallback(
    async (q: string) => {
      setSearching(true);
      try {
        const answer = await searchModelLibrary(providerId, q);
        setResults(answer.models);
        setLibraryError(answer.error);
      } catch (err) {
        setResults([]);
        setLibraryError(err instanceof Error ? err.message : String(err));
      } finally {
        setSearching(false);
      }
    },
    [providerId],
  );

  // Debounced: a search per keystroke would hammer the catalog and make the
  // list flicker between partial-word answers.
  useEffect(() => {
    if (!browsing) return;
    const timer = window.setTimeout(() => void runSearch(query.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [browsing, query, runSearch]);

  const toggleModel = async (name: string) => {
    if (openModel === name) {
      setOpenModel(null);
      return;
    }
    setOpenModel(name);
    setTagsError(null);
    if (tags[name]) return;
    setLoadingTags(true);
    try {
      const answer = await modelLibraryTags(providerId, name);
      if (answer.error) setTagsError(answer.error);
      setTags((prev) => ({ ...prev, [name]: answer.tags }));
    } catch (err) {
      setTagsError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingTags(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-border/50 pt-2" data-testid="model-library">
      {/* The first version of this shipped as a bare input with a changed
          placeholder, in the exact spot the old blind text field had occupied.
          It worked and nobody could see it: a feature that looks identical to
          what it replaced has not been delivered. Hence the heading — the
          panel has to SAY that the whole catalog is searchable here. */}
      <p className="flex items-center gap-1.5 text-xs font-medium">
        <Search className="h-3.5 w-3.5 text-muted-foreground" />
        {t("apikeys_model_pull.library_title")}
      </p>
      <div className="flex items-center gap-2">
        <div className="relative w-full">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            value={query}
            onFocus={() => setBrowsing(true)}
            onChange={(e) => {
              setQuery(e.target.value);
              setBrowsing(true);
            }}
            placeholder={t("apikeys_model_pull.custom_placeholder")}
            className="h-8 w-full rounded-md border border-border bg-background pl-7 pr-2 text-xs"
          />
        </div>
        {/* The exact-name path the panel has always had: whatever is typed can
            be pulled directly, whether or not the catalog was reachable. */}
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || pullingModel !== null || !query.trim()}
          onClick={() => onPull(query)}
        >
          {actionLabel ?? t("apikeys_model_pull.download")}
        </Button>
      </div>

      {browsing && (
        <div className="space-y-1.5">
          {searching && !results && (
            <p className="text-[11px] text-muted-foreground">
              {t("apikeys_model_pull.library_searching")}
            </p>
          )}
          {/* An unreachable catalog is a note, never a blocker — the field
              above still downloads by exact name. */}
          {libraryError && (
            <p className="text-[11px] text-amber-500">{libraryError}</p>
          )}
          {results && results.length === 0 && !libraryError && (
            <p className="text-[11px] text-muted-foreground">
              {t("apikeys_model_pull.library_no_results")}
            </p>
          )}

          {(results ?? []).map((model) => (
            <div
              key={model.name}
              data-testid={`library-row-${model.name}`}
              className="rounded border border-border/50 bg-background/50"
            >
              <button
                type="button"
                className="flex w-full items-start justify-between gap-2 px-2 py-1.5 text-left"
                onClick={() => void toggleModel(model.name)}
              >
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium">
                    {model.name}
                    {model.installed && (
                      <span className="ml-1.5 text-[10px] font-normal text-emerald-500">
                        {t("apikeys_model_pull.installed")}
                      </span>
                    )}
                  </p>
                  {model.description && (
                    <p className="text-[11px] leading-snug text-muted-foreground">
                      {model.description}
                    </p>
                  )}
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {model.capabilities.map((cap) => (
                      <span
                        key={cap}
                        className="rounded bg-primary/10 px-1 py-px text-[9px] font-medium uppercase tracking-wide text-primary"
                      >
                        {cap}
                      </span>
                    ))}
                    {model.sizes.slice(0, 6).map((size) => (
                      <span
                        key={size}
                        className="rounded bg-muted px-1 py-px text-[9px] font-medium text-muted-foreground"
                      >
                        {size}
                      </span>
                    ))}
                  </div>
                </div>
                <ChevronDown
                  className={cn(
                    "mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                    openModel === model.name && "rotate-180",
                  )}
                />
              </button>

              {openModel === model.name && (
                // A popular family publishes well over a hundred tags
                // (qwen2.5: 133). Un-capped, one opened model would push the
                // whole settings page down by several screens.
                <div className="max-h-56 space-y-1 overflow-y-auto border-t border-border/40 px-2 py-1.5">
                  {loadingTags && !tags[model.name] && (
                    <p className="text-[11px] text-muted-foreground">
                      {t("apikeys_model_pull.library_loading_versions")}
                    </p>
                  )}
                  {tagsError && (
                    <p className="text-[11px] text-amber-500">{tagsError}</p>
                  )}
                  {(tags[model.name] ?? []).map((tag) => (
                    <div
                      key={tag.id}
                      data-testid={`library-tag-${tag.id}`}
                      className="flex items-center justify-between gap-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-[11px]">
                          <span className="font-mono">{tag.id}</span>{" "}
                          <span className="text-muted-foreground">
                            {tag.size_gb
                              ? t("apikeys_model_pull.size").replace(
                                  "{0}",
                                  String(tag.size_gb),
                                )
                              : t("apikeys_model_pull.library_size_unknown")}
                            {tag.context
                              ? ` · ${t("apikeys_model_pull.library_context").replace("{0}", tag.context)}`
                              : ""}
                          </span>
                        </p>
                        {tag.fit === "tight" && tag.fit_note && (
                          <p className="text-[10px] leading-snug text-amber-500">
                            {tag.fit_note}
                          </p>
                        )}
                      </div>
                      {/* A hosted tag has no weights to fetch. Offering
                          "Download" for it would fail at the server. */}
                      {tag.cloud ? (
                        <span className="shrink-0 text-[10px] text-muted-foreground">
                          {t("apikeys_model_pull.library_hosted")}
                        </span>
                      ) : tag.installed && onInstalledSelect ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          className="h-6 shrink-0 gap-1 px-2 text-[10px]"
                          disabled={disabled || pullingModel !== null}
                          onClick={() => onInstalledSelect(tag.id)}
                        >
                          {pullingModel === tag.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Check className="h-3 w-3" />
                          )}
                          {installedActionLabel ?? t("apikeys_model_pull.installed")}
                        </Button>
                      ) : tag.installed ? (
                        <span className="flex shrink-0 items-center gap-1 text-[10px] text-emerald-500">
                          <Check className="h-3 w-3" />
                          {t("apikeys_model_pull.installed")}
                        </span>
                      ) : (
                        <Button
                          size="sm"
                          variant="secondary"
                          className="h-6 shrink-0 gap-1 px-2 text-[10px]"
                          disabled={disabled || pullingModel !== null}
                          onClick={() => onPull(tag.id)}
                        >
                          {pullingModel === tag.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <Download className="h-3 w-3" />
                          )}
                          {actionLabel ?? t("apikeys_model_pull.download")}
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * The download half of a keyless local brain card: which models this machine
 * could run, which it already has, and the one button that fetches one.
 *
 * Without it a keyless install dead-ended at "run: ollama pull <model>" — a
 * terminal instruction in an app that has no terminal, and the exact point
 * where §3's "recoverable in-app" contract broke. Nothing is inferred client
 * side: installed state, fit verdict and progress all come from the server,
 * because only it can see the user's inventory and memory. The fit verdict is
 * advisory — a GPU runs models the RAM rule calls tight, so it never disables
 * the button.
 */
export function LocalModelDownloadPanel({
  descriptor,
  onChanged,
}: {
  /**
   * Only the two fields the panel actually uses. Widened from
   * `ProviderDescriptor` so the Subagents tab — whose rows come from a
   * different endpoint with a different shape — can render the very same panel
   * instead of dead-ending a local-only user at an empty model dropdown.
   */
  descriptor: { id: string; supports_model_pull?: boolean };
  onChanged: () => void;
}) {
  const t = useT();
  const [catalog, setCatalog] = useState<PullableModels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ModelPullProgress | null>(null);
  const running = progress?.state === "running";

  // Hooks run before the capability check below, so the fetch itself is gated:
  // a cloud card that mounted this panel would otherwise fire a request the
  // route answers with 400 on every render of the provider list.
  const pullable = Boolean(descriptor.supports_model_pull);
  const load = useMemo(
    () => async () => {
      if (!pullable) return;
      try {
        setCatalog(await pullableModels(descriptor.id));
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [descriptor.id, pullable],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while a pull is in flight. A finished download refreshes both the
  // shortlist (so the row flips to "installed") and the provider list (so the
  // model picker sees the new model without a restart).
  useEffect(() => {
    if (!running || !progress) return;
    let cancelled = false;
    const model = progress.model;
    const timer = window.setInterval(async () => {
      try {
        const next = await modelPullStatus(descriptor.id, model);
        if (cancelled) return;
        setProgress(next);
        if (next.state === "done" || next.state === "error") {
          window.clearInterval(timer);
          void load();
          onChanged();
        }
      } catch (err) {
        if (cancelled) return;
        window.clearInterval(timer);
        setError(err instanceof Error ? err.message : String(err));
        setProgress(null);
      }
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [running, progress, descriptor.id, load, onChanged]);

  if (!pullable) return null;

  const pull = async (model: string) => {
    const name = model.trim();
    if (!name) return;
    setError(null);
    try {
      const started = await startModelPull(descriptor.id, name);
      setProgress(started);
      if (started.state === "done") {
        void load();
        onChanged();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const percent = progress?.percent ?? 0;
  const rows: PullableModel[] = catalog?.models ?? [];
  // Roles come from the backend, in its order. A payload without them (an older
  // server) collapses into one unlabelled group, which is exactly how this
  // panel looked before roles existed — no empty headings, no crash.
  const roles: PullableRole[] = catalog?.roles ?? [];
  const groups: { role: PullableRole | null; rows: PullableModel[] }[] = roles.length
    ? roles
        .map((role) => ({
          role,
          rows: rows.filter((r) => (r.role ?? "chat") === role),
        }))
        .filter((g) => g.rows.length > 0)
    : [{ role: null, rows }];
  // What the fit verdicts were judged against, in one short phrase. Naming the
  // GPU matters: "18 GB is tight" is confusing on a box with 64 GB of RAM until
  // you know the number being compared is the graphics card's.
  const hardwareNote = catalog
    ? (catalog.accelerator_gb ?? 0) > 0
      ? t("apikeys_model_pull.hardware_gpu").replace(
          "{0}",
          String(Math.round(catalog.accelerator_gb ?? 0)),
        )
      : catalog.memory_gb
        ? t("apikeys_model_pull.memory").replace("{0}", String(catalog.memory_gb))
        : ""
    : "";

  return (
    <div
      data-testid={`provider-model-pull-${descriptor.id}`}
      className="space-y-2 rounded-md border border-border/60 bg-muted/30 p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium">{t("apikeys_model_pull.title")}</p>
        {hardwareNote ? (
          <span
            data-testid="model-pull-hardware"
            className="text-[11px] text-muted-foreground"
          >
            {hardwareNote}
          </span>
        ) : null}
      </div>

      {catalog && !catalog.server_reachable && (
        <p className="text-[11px] text-amber-500">{catalog.message}</p>
      )}

      {groups.map((group) => (
        <div key={group.role ?? "all"} className="space-y-1.5">
          {group.role && (
            <p className="pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/80">
              {t(`apikeys_model_pull.role_${group.role}`)}
            </p>
          )}
          {group.rows.map((row) => (
          <div
            key={row.id}
            data-testid={`model-pull-row-${row.id}`}
            data-recommended={row.recommended ? "true" : "false"}
            className={cn(
              "flex items-start justify-between gap-2 rounded border bg-background/50 px-2 py-1.5",
              row.recommended
                ? "border-primary/40 bg-primary/[0.04]"
                : "border-border/50",
            )}
          >
            <div className="min-w-0">
              <p className="truncate text-xs font-medium">
                {row.label}{" "}
                <span className="font-normal text-muted-foreground">
                  {t("apikeys_model_pull.size").replace(
                    "{0}",
                    String(row.size_gb),
                  )}
                </span>
                {row.recommended && (
                  <span className="ml-1.5 whitespace-nowrap rounded-full bg-primary/15 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-primary">
                    {t("apikeys_model_pull.best_for_machine")}
                  </span>
                )}
              </p>
              <p className="text-[11px] leading-snug text-muted-foreground">
                {row.purpose}
              </p>
              {/* The honest half of a recommendation: the comfortable note
                  explains why THIS one was picked, the tight note explains what
                  a bigger one would cost. Both are the server's wording. */}
              {(row.fit === "tight" || row.recommended) && (
                <p
                  className={cn(
                    "text-[11px] leading-snug",
                    row.fit === "tight" ? "text-amber-500" : "text-muted-foreground/80",
                  )}
                >
                  {row.fit_note}
                </p>
              )}
            </div>
            {row.installed ? (
              <span className="flex shrink-0 items-center gap-1 text-[11px] text-emerald-500">
                <Check className="h-3.5 w-3.5" />
                {t("apikeys_model_pull.installed")}
              </span>
            ) : (
              <Button
                size="sm"
                variant={row.recommended ? "default" : "secondary"}
                className="shrink-0 gap-1.5"
                disabled={running || !catalog?.server_reachable}
                onClick={() => void pull(row.id)}
              >
                {running && progress?.model === row.id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Download className="h-3.5 w-3.5" />
                )}
                {t("apikeys_model_pull.download")}
              </Button>
            )}
          </div>
          ))}
        </div>
      ))}

      {/* Any name the library knows — the shortlist is a starting point, not a
          gate, and a user who wants a specific model should not have to leave
          the app for it. */}
      <LibraryBrowser
        providerId={descriptor.id}
        onPull={(model) => void pull(model)}
        disabled={running || !catalog?.server_reachable}
        pullingModel={running ? (progress?.model ?? null) : null}
      />

      {progress && progress.state !== "idle" && (
        <div className="space-y-1">
          <p className="text-[11px] text-muted-foreground">
            {progress.model}: {progress.message}
          </p>
          {running && (
            <div className="h-1 w-full overflow-hidden rounded bg-border">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}
        </div>
      )}
      {error && <p className="text-[11px] text-destructive">{error}</p>}
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
  // "Billed through a login, not a key" — the SHAPE that makes the
  // subscription wording true, never a provider id (AP-21).
  const isSubscriptionBilled =
    descriptor.billing === "subscription" && descriptor.secret_keys.length === 0;
  return (
    <div className="space-y-2">
      <ProviderBillingBadge billing={descriptor.billing} />
      {descriptor.experimental && (
        <div
          data-testid={`provider-experimental-note-${descriptor.id}`}
          className="rounded-md border border-violet-500/30 bg-violet-500/[0.06] px-2.5 py-1.5 text-[11px] leading-snug text-muted-foreground"
        >
          <p>
            {/* The subscription wording ("uses the ChatGPT plan…") belongs to
                a card that IS billed through a subscription login — the shape,
                never a provider name (AP-21). Bound to `experimental` alone it
                would tell the next experimental provider's users that their
                ChatGPT plan pays for it. */}
            {isSubscriptionBilled ? (
              <>
                <span>{t("apikeys_view.subscription_realtime_description")}</span>{" "}
                <span>{t("apikeys_view.experimental_subscription_fallback")}</span>
              </>
            ) : (
              <span>{t("apikeys_view.experimental_note")}</span>
            )}
          </p>
        </div>
      )}
      <LocalRuntimePanel descriptor={descriptor} onChanged={onChanged} />
      <ManagedServerPanel descriptor={descriptor} onChanged={onChanged} />
      {descriptor.supports_base_url && descriptor.managed_server && (
        <details className="rounded-md border border-border/50 bg-muted/20 px-3 py-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {t("apikeys_view.managed_advanced_connection")}
          </summary>
          <div className="pt-2">
            <BaseUrlField descriptor={descriptor} onChanged={onChanged} />
          </div>
        </details>
      )}
      {descriptor.supports_base_url && !descriptor.managed_server && (
        <BaseUrlField descriptor={descriptor} onChanged={onChanged} />
      )}
      {/* The runtime itself comes before the model list: pull buttons are
          dead while Ollama is absent or stopped, and this panel is the fix. */}
      {descriptor.supports_model_pull && (
        <OllamaRuntimePanel providerId={descriptor.id} onChanged={onChanged} />
      )}
      <LocalModelDownloadPanel descriptor={descriptor} onChanged={onChanged} />
      {/* Ordered after the server URL on purpose: a download can only be
          offered once the card points at the right server. */}
      {descriptor.auth_mode === "none" && (
        <>
          <p className="text-xs text-muted-foreground">
            {descriptor.secret_keys.length > 0
              ? t("apikeys_view.local_optional_key_hint")
              : t("apikeys_view.local_no_credentials_hint")}
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

// Exhaustive by construction: adding a value to CodexStatus["reason_code"]
// without a card mapping fails the TypeScript build here instead of silently
// falling through to a wrong status line (the BUG-008 multi-layer enum class).
export const CODEX_STATUS_KEY_BY_REASON: Record<
  NonNullable<NonNullable<ProviderDescriptor["codex_status"]>["reason_code"]>,
  string
> = {
  ready: "apikeys_codex.status_ready",
  login_required: "apikeys_codex.status_login_required",
  login_in_progress: "apikeys_codex.status_login_in_progress",
  lifecycle_unavailable: "apikeys_codex.status_lifecycle_unavailable",
  not_installed: "apikeys_codex.status_not_installed",
  setup_invalid: "apikeys_codex.status_setup_invalid",
  plan_unsupported: "apikeys_codex.status_plan_unsupported",
  busy: "apikeys_codex.status_busy",
};

// States in which the install row and an active Connect button would be
// wrong: transient windows, a login the user must FINISH (not restart), and
// an OS the feature does not support at all.
const CODEX_NO_INSTALL_PROMPT_REASONS = new Set<string>([
  "busy",
  "login_in_progress",
  "lifecycle_unavailable",
]);

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
    : status.reason_code && status.reason_code !== "login_required"
      ? // Runtime fallback for a backend value the union does not know yet —
        // the Record enforces exhaustiveness at compile time, but a newer
        // backend must not render t(undefined).
        ((CODEX_STATUS_KEY_BY_REASON as Record<string, string>)[
          status.reason_code
        ] ?? "apikeys_codex.status_loading")
      : !status.installed
        ? "apikeys_codex.status_not_installed"
        : "apikeys_codex.status_login_required";

  useEffect(() => {
    if (!loginPolling) return;
    if (loginReady) {
      // The login just landed. `/api/providers` alone does not unlock the
      // engine switch: realtime AVAILABILITY lives on the voice-mode snapshot,
      // which listens for this event. Without it the card went green while the
      // Pipeline|Realtime segment stayed disabled — for up to five minutes, and
      // on a long-open window indefinitely.
      window.dispatchEvent(new CustomEvent("jarvis:realtime-switched"));
      return;
    }
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
      // Losing the login removes a realtime provider: the engine switch has to
      // re-read availability, or it keeps offering a mode that cannot start.
      window.dispatchEvent(new CustomEvent("jarvis:realtime-switched"));
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
          (status?.reason_code === "setup_invalid" ||
            status?.reason_code === "not_installed" ||
            status?.reason_code === "plan_unsupported" ||
            status?.reason_code === "lifecycle_unavailable") &&
          status.message && (
            <div
              data-testid="codex-setup-detail"
              className="mt-2 break-words border-t border-border/60 pt-2 font-mono text-[11px]"
            >
              <span className="mr-1 font-sans">
                {t("apikeys_codex.setup_detail_label")}
              </span>
              {status.message}
            </div>
          )}
      </div>

      {/* Deliberately `codex_status.installed`, NOT the payload's
          `cli_installed`. The backend's carve-out for an ownership window
          (answer "is it installed" from PATH instead of the owned profile)
          protects exactly the states this row already suppresses below, so
          reading both would add a second source of truth that can disagree —
          and the two DO disagree in practice. See useProviders.ts. */}
      {!status?.installed &&
        !CODEX_NO_INSTALL_PROMPT_REASONS.has(status?.reason_code ?? "") && (
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
          disabled={
            pending !== null ||
            loginPolling ||
            // Finish the RUNNING login instead of starting a second one; an
            // unsupported OS can never connect at all.
            status?.reason_code === "login_in_progress" ||
            status?.reason_code === "lifecycle_unavailable" ||
            (!status?.installed && status?.reason_code !== "busy")
          }
          title={
            status?.reason_code === "login_in_progress"
              ? t("apikeys_codex.status_login_in_progress")
              : status?.reason_code === "lifecycle_unavailable"
                ? t("apikeys_codex.status_lifecycle_unavailable")
                : undefined
          }
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
        {/* A plan-refused or invalid profile is stored but unusable — offer
            the in-app exit the backend implements, even though the connected
            panel (with its Disconnect) never renders in these states. */}
        {(status?.reason_code === "plan_unsupported" ||
          status?.reason_code === "setup_invalid") && (
          <Button
            size="sm"
            variant="ghost"
            onClick={handleLogout}
            disabled={pending !== null}
          >
            <LogOut className="h-3.5 w-3.5" />
            {t("apikeys_codex.disconnect")}
          </Button>
        )}
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

/**
 * The card's state chip vocabulary — the summary a user actually scans.
 *
 * Every member maps to one `apikeys_view.state_*` key present in en/de/es
 * (pinned by `provider-state-chip-parity.test.ts`). These used to be bare
 * English literals, so a translated card carried an untranslated chip.
 */
export const PROVIDER_STATE_CHIPS = {
  active: { tone: "active", key: "apikeys_view.state_active" },
  ready: { tone: "ready", key: "apikeys_view.state_ready" },
  checking: { tone: "neutral", key: "apikeys_view.state_checking" },
  unavailable: { tone: "neutral", key: "apikeys_view.state_unavailable" },
  blocked: { tone: "missing", key: "apikeys_view.state_blocked" },
  missing: { tone: "missing", key: "apikeys_view.state_missing" },
  not_connected: { tone: "neutral", key: "apikeys_view.state_not_connected" },
  open: { tone: "neutral", key: "apikeys_view.state_open" },
} as const satisfies Record<
  string,
  { tone: keyof typeof STATE_CHIP_TONE; key: string }
>;

export type ProviderStateChip = keyof typeof PROVIDER_STATE_CHIPS;

/** Which chip a descriptor deserves. Pure, so the mapping stays testable. */
export function providerStateChip(
  descriptor: ProviderDescriptor,
): ProviderStateChip {
  if (descriptor.active) return "active";
  if (descriptor.auth_mode === "codex") {
    const status = descriptor.codex_status;
    // Transient: the status is being probed right now. Neither "missing" nor
    // "not connected" is known yet, and the red chip on a healthy install was
    // exactly the bug this state exists to prevent.
    if (status?.reason_code === "busy") return "checking";
    // Nothing is "missing" on an OS the feature does not support at all.
    if (status?.reason_code === "lifecycle_unavailable") return "unavailable";
    // Terminal until the account changes — "not connected" would undersell it.
    if (status?.reason_code === "plan_unsupported") return "blocked";
    if (!status?.installed) return "missing";
    return descriptor.configured ? "ready" : "not_connected";
  }
  if (descriptor.auth_mode === "antigravity") {
    const status = descriptor.antigravity_status;
    if (!status?.installed) return "missing";
    return status.connected ? "ready" : "not_connected";
  }
  return descriptor.configured ? "ready" : "open";
}

export function StatusBadge({ descriptor }: { descriptor: ProviderDescriptor }) {
  const t = useT();
  const chip = PROVIDER_STATE_CHIPS[providerStateChip(descriptor)];
  return <StateChip tone={chip.tone}>{t(chip.key)}</StateChip>;
}

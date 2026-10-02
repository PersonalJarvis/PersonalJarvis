import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2, Radio, XCircle } from "lucide-react";
import {
  LiveProfile,
  liveProfileQuery,
  liveProfileReady,
  saveLiveProfile,
  type LiveAuthMode,
} from "@/components/providers/LiveProfile";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import {
  AuthWidget,
  ProviderTestControl,
  StatusBadge,
  Tag,
} from "@/components/providers/ProviderTierSection";
import { RealtimeOptionsControl } from "@/components/RealtimeOptionsControl";
import { Button } from "@/components/ui/button";
import {
  PROVIDER_BACKEND_UNREACHABLE,
  type ProviderDescriptor,
  type ProviderTier,
  type SectionHealth,
  sectionHealthForSubject,
  switchRealtimeProvider,
} from "@/hooks/useProviders";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";
import {
  hasExperimentalConsent,
  rememberExperimentalConsent,
} from "@/lib/experimentalConsent";
import {
  realtimeTransportIssueKey,
  requestRealtimeTransportOffer,
} from "@/lib/realtimeTransportIssue";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { ProfileGroup, SettingRow } from "@/views/profile/ProfileGroup";

type VoiceRuntime = ReturnType<typeof useVoiceMode>;

function liveMode(provider: ProviderDescriptor): LiveAuthMode | undefined {
  if (provider.configuration_surface !== "live") return undefined;
  return provider.id === "openai-live-subscription" ? "chatgpt_subscription" : "api_key";
}

/**
 * The shared Realtime provider setup. Its parent owns the available voice
 * modes, including the separate classic pipeline settings.
 *
 * Top to bottom: what the voice runs on right now, the providers as one list
 * where a click on a provider with a saved key makes it the voice, then the
 * selected provider's own settings as plain groups (connection, model and
 * voice or the GPT-Live profile, and a connection test).
 *
 * The provider list and the settings are separate on purpose: the previous
 * layout named the provider twice (a chip, then a card header) and nested its
 * settings four frames deep inside a 600 px dialog.
 */
export function RealtimeTab({
  providers,
  loading,
  error,
  onChanged,
  onActivateOptimistic,
  health,
}: {
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  /** Live health of the realtime tier's ACTIVE provider. */
  health?: SectionHealth;
}) {
  const t = useT();
  const voice = useVoiceMode();
  const queryClient = useQueryClient();
  const pushToast = useEventStore((s) => s.pushToast);
  const rawChoices = providers.filter((provider) => provider.tier === "realtime");
  const hasLiveSurface = rawChoices.some(
    (provider) => provider.configuration_surface === "live",
  );
  const live = useQuery({ ...liveProfileQuery, enabled: hasLiveSurface });
  const apiLive = rawChoices.find((provider) => provider.id === "openai-live" && provider.configuration_surface === "live");
  const savedLiveId = live.data?.profile.auth_mode === "chatgpt_subscription" ? "openai-live-subscription" : "openai-live";
  const runtimeLiveId = voice.activeProvider === "openai-live" || voice.activeProvider === "openai-live-subscription"
    ? voice.activeProvider : undefined;
  // Provider selection updates before the profile/runtime queries catch up.
  // An active non-Live descriptor must clear both Live rows immediately.
  const activeOtherId = rawChoices.find((provider) => provider.active && provider.configuration_surface !== "live")?.id
    ?? rawChoices.find((provider) => provider.id === voice.activeProvider && provider.configuration_surface !== "live")?.id;
  const activeLiveId = activeOtherId ? undefined
    : live.data?.active ? savedLiveId : runtimeLiveId ?? (apiLive?.active ? "openai-live" : undefined);
  // Two visible choices share one profile, but retain independent credentials,
  // billing labels and saved model selections. The subscription comes first.
  const choices = apiLive ? rawChoices.flatMap((provider): ProviderDescriptor[] => {
    if (provider.id === "openai-live-subscription") return [];
    if (provider.id !== apiLive.id) return [{ ...provider, active: provider.id === activeOtherId }];
    return [{
      ...provider, id: "openai-live-subscription", label: "GPT Subscription", billing: "subscription",
      auth_mode: "codex", secret_keys: [], secrets_set: {}, secrets_effective: {},
      active: activeLiveId === "openai-live-subscription", configured: liveProfileReady(live.data, "chatgpt_subscription"),
    }, {
      ...provider, billing: "api", active: activeLiveId === "openai-live",
      configured: live.data ? liveProfileReady(live.data, "api_key") : provider.configured,
    }];
  }) : rawChoices;
  const [inspectedId, setInspectedId] = useState<string | null>(null);
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const setupRef = useRef<HTMLDivElement>(null);
  const [setupFocusRequest, setSetupFocusRequest] = useState(0);
  useEffect(() => {
    if (!setupFocusRequest) return;
    setupRef.current?.scrollIntoView?.({ block: "start" });
    setupRef.current?.focus({ preventScroll: true });
  }, [setupFocusRequest]);
  // This transport uses an existing account, not the Codex CLI lifecycle.
  // Never infer a missing installation from an absent CLI status object.
  const subscriptionStatus = live.isError ? t("apikeys_view.load_error")
    : !live.data ? t("live.loading")
      : live.data.profile.auth_mode === undefined || !live.data.subscription
        ? t("settings_view.wake_word.restart_required")
        : !live.data.subscription.account_connected ? t("live.subscription_sign_in")
          : !live.data.profile.subscription_backend_model?.trim()
            ? t("live.subscription_model_required") : t("live.subscription_connected");
  // The provider waiting for the experimental acknowledgement, with the flag
  // its activation started with: a key saved a moment ago is still
  // "unconfigured" in this descriptor, and dropping the flag here would let
  // the confirmed dialog end in a silent no-op.
  const [consentFor, setConsentFor] = useState<{
    provider: ProviderDescriptor;
    assumeConfigured: boolean;
  } | null>(null);

  const active = choices.find((provider) => provider.active) ?? null;
  const currentProviderId = voice.sessionActive ? voice.activeSessionProvider : activeOtherId ?? voice.activeProvider;
  const currentProvider = choices.find((provider) => provider.id === currentProviderId) ?? active;
  const selected =
    choices.find((provider) => provider.id === inspectedId) ?? active ?? choices[0] ?? null;
  const activeHealth = active ? sectionHealthForSubject(health, active.id) : undefined;

  /** Whether a click can switch to this provider without asking for anything.
   *  `assumeConfigured` covers the moment a key was just saved, before the
   *  provider list has refetched and still calls the provider unconfigured. */
  function canSwitchTo(provider: ProviderDescriptor, assumeConfigured = false): boolean {
    if (provider.active || activatingId) return false;
    // GPT-Live saves its provider together with the thinking model, so it can
    // only take over once that profile is complete.
    if (provider.configuration_surface === "live") return liveProfileReady(live.data, liveMode(provider));
    if (!provider.configured && !assumeConfigured) return false;
    return true;
  }

  async function activate(
    provider: ProviderDescriptor,
    { consented = false, assumeConfigured = false } = {},
  ) {
    if (!canSwitchTo(provider, assumeConfigured)) return;
    // The experimental acknowledgement comes BEFORE the optimistic flip, so a
    // declined dialog leaves nothing to roll back.
    if (provider.experimental && !consented && !hasExperimentalConsent(provider.id)) {
      setConsentFor({ provider, assumeConfigured });
      return;
    }
    if (provider.configuration_surface !== "live") onActivateOptimistic("realtime", provider.id);
    setActivatingId(provider.id);
    try {
      if (provider.configuration_surface === "live" && live.data) {
        // Re-saving the stored profile is what selects GPT-Live; the backend
        // writes the provider and `[voice].mode = "realtime"` together.
        const mode = liveMode(provider)!;
        const chosen = mode === "api_key" && live.data.profile.auth_mode === undefined
          ? live.data.profile : { ...live.data.profile, auth_mode: mode };
        await saveLiveProfile(chosen);
        await queryClient.invalidateQueries({ queryKey: ["live-profile"] });
      } else {
        // The switch reconnects an OPEN call synchronously, and an
        // offer-requiring transport cannot start without a browser offer, so
        // ask for one before the POST (a transport that needs none ignores it).
        requestRealtimeTransportOffer();
        await switchRealtimeProvider(provider.id, provider.experimental === true);
        // The provider switch never touches `[voice].mode`. This page offers
        // no other engine, so picking a provider here also means "use it".
        if (voice.mode !== "realtime") voice.setMode("realtime");
      }
      pushToast(
        "success",
        t("apikeys_view.switch_done_realtime").replace("{0}", provider.label),
      );
      window.dispatchEvent(new CustomEvent("jarvis:realtime-switched"));
      onChanged();
    } catch (cause) {
      console.warn("realtime provider switch failed", provider.id, cause);
      pushToast("error", (cause as Error).message);
      // Roll the optimistic highlight back to the true active provider.
      onChanged();
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-switch-failed", {
          detail: { section: "realtime", provider: provider.id },
        }),
      );
    } finally {
      setActivatingId(null);
    }
  }

  function select(provider: ProviderDescriptor) {
    if (activatingId) return;
    setInspectedId(provider.id);
    if (liveMode(provider) === "chatgpt_subscription") setSetupFocusRequest((count) => count + 1);
    // One click selects: the settings open below AND a provider whose key is
    // already saved becomes the voice. Without a key it only opens, so the
    // key field is on screen to paste into.
    if (provider.active) {
      // Already the provider; on an install still pinned to the pipeline
      // engine the click means "use it", which only the engine switch can do.
      if (voice.mode !== "realtime" && voice.realtimeAvailable) voice.setMode("realtime");
      return;
    }
    void activate(provider);
  }

  return (
    <section
      aria-label={t("apikeys_realtime.aria")}
      data-testid="realtime-tab"
      className="flex flex-col gap-group"
    >
      <VoiceNow active={currentProvider} activeHealth={currentProvider
        ? sectionHealthForSubject(health, currentProvider.id) : undefined} voice={voice} />

      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-border bg-card px-5 py-4 text-sm text-destructive"
        >
          <AlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="min-w-0 flex-1">
            {error === PROVIDER_BACKEND_UNREACHABLE
              ? t("apikeys_view.backend_unavailable")
              : `${t("apikeys_view.load_error")} (${error}).`}
          </p>
          <Button variant="outline" size="sm" onClick={onChanged}>
            {t("common.retry")}
          </Button>
        </div>
      )}

      <ProfileGroup
        title={t("apikeys_realtime.providers_title")}
        description={t("apikeys_realtime.providers_desc")}
        testId="realtime-provider-list"
      >
        {loading && !choices.length ? (
          <div role="status" className="flex items-center gap-2 px-5 py-4 text-sm text-muted-foreground">
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            {t("apikeys_realtime.loading")}
          </div>
        ) : (
          choices.map((provider) => (
            <ProviderRow
              key={provider.id}
              provider={provider}
              statusLabel={liveMode(provider) === "chatgpt_subscription" ? subscriptionStatus : undefined}
              selected={selected?.id === provider.id}
              activating={activatingId === provider.id}
              health={provider.active ? activeHealth : undefined}
              onSelect={() => select(provider)}
            />
          ))
        )}
      </ProfileGroup>

      {selected && (
        <div ref={setupRef} tabIndex={-1} data-testid="realtime-provider-setup" className="scroll-mt-4">
          <ProviderSettings
            key={selected.id}
            provider={selected}
            health={selected.active ? activeHealth : undefined}
            activating={activatingId === selected.id}
            canSwitch={canSwitchTo(selected)}
            onActivate={() => void activate(selected)}
            // The first provider set up becomes the voice by itself; once one
            // holds the voice, a new key never takes it over unasked.
            onKeySaved={
              active ? undefined : () => void activate(selected, { assumeConfigured: true })
            }
            onChanged={onChanged}
          />
        </div>
      )}

      <ExperimentalConsentDialog
        provider={consentFor?.provider ?? null}
        onCancel={() => setConsentFor(null)}
        onConfirm={() => {
          const pending = consentFor;
          setConsentFor(null);
          if (!pending) return;
          rememberExperimentalConsent(pending.provider.id);
          void activate(pending.provider, {
            consented: true,
            assumeConfigured: pending.assumeConfigured,
          });
        }}
      />
    </section>
  );
}

/**
 * What the voice runs on right now — one card, one sentence of state. It also
 * carries the one-click way back onto Realtime for an install that is still
 * currently configured for the separate pipeline engine.
 */
function VoiceNow({
  active,
  activeHealth,
  voice,
  billingOverride,
}: {
  active: ProviderDescriptor | null;
  activeHealth?: SectionHealth;
  voice: VoiceRuntime;
  billingOverride?: ProviderDescriptor["billing"];
}) {
  const t = useT();
  if (!voice.statusKnown) {
    // A placeholder, never an invented "not set up": the status simply has
    // not answered yet.
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label={t("apikeys_realtime.loading")}
        className="flex items-center gap-4 rounded-xl border border-border bg-card px-5 py-4"
      >
        <span className="h-9 w-9 shrink-0 rounded-md bg-foreground/10" />
        <span className="flex flex-1 flex-col gap-2">
          <span className="h-3.5 w-40 rounded bg-foreground/10" />
          <span className="h-3 w-24 rounded bg-foreground/10" />
        </span>
      </div>
    );
  }

  const realtimeOff = voice.mode !== "realtime";
  const onBackupPath =
    voice.sessionActive && voice.activeSessionMode === "pipeline" && !realtimeOff;
  const offerBlocked =
    !realtimeOff && voice.requiresWebRtcOffer && voice.transportOfferReady === false;
  const offerDetail = !offerBlocked
    ? ""
    : voice.transportIssue
      ? t(realtimeTransportIssueKey(voice.transportIssue))
      : voice.transportOfferDetail;

  const title = realtimeOff
    ? t("apikeys_realtime.state_off")
    : active
      ? active.label
      : t("apikeys_realtime.state_none");
  // A running call can still use the previous access, and a CLI can explicitly
  // select either Live runtime. Prefer that truth over the saved next-call mode.
  const runtimeProvider = voice.sessionActive ? voice.activeSessionProvider : voice.activeProvider;
  const billing = active?.configuration_surface === "live" && runtimeProvider === "openai-live-subscription"
    ? "subscription"
    : active?.configuration_surface === "live" && runtimeProvider === "openai-live"
      ? "api" : billingOverride ?? active?.billing;
  const subtitle = realtimeOff
    ? t(
        voice.realtimeAvailable
          ? "apikeys_realtime.off_hint"
          : "apikeys_realtime.off_hint_unavailable",
      )
    : active
      ? voice.activeModel || t(`provider_billing.${billing}`)
      : t("apikeys_realtime.none_hint");

  const state: { tone: "accent" | "pulse" | "warn" | "error" | "quiet"; label: string } | null =
    realtimeOff || !active
      ? null
      : voice.connecting
        ? { tone: "pulse", label: t("apikeys_realtime.state_connecting") }
        : voice.sessionActive && voice.activeSessionMode === "realtime"
          ? { tone: "accent", label: t("apikeys_realtime.state_call") }
          : onBackupPath
            ? { tone: "warn", label: t("apikeys_realtime.state_backup") }
            : activeHealth?.status === "error"
              ? { tone: "error", label: t("apikeys_view.health_error") }
              : { tone: "quiet", label: t("apikeys_realtime.state_ready") };

  const notes = [
    onBackupPath ? { key: "backup", alert: false, text: t("live.pipeline_fallback") } : null,
    !realtimeOff && voice.lastStartError
      ? {
          key: "start-error",
          alert: true,
          text: t("voice_state.connect_failed")
            .replace("{0}", voice.lastStartError.provider || "?")
            .replace("{1}", voice.lastStartError.message),
        }
      : null,
    offerDetail ? { key: "offer", alert: true, text: offerDetail } : null,
  ].filter((note): note is { key: string; alert: boolean; text: string } => note !== null);

  return (
    <div
      data-testid="voice-now"
      className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="flex items-center gap-4 px-5 py-4">
        {active && !realtimeOff ? (
          <ProviderLogo providerId={active.id} label={active.label} />
        ) : (
          <span
            aria-hidden="true"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-secondary text-muted-foreground"
          >
            <Radio className="h-4 w-4" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-semibold text-foreground-strong">{title}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
        </div>
        {realtimeOff && voice.realtimeAvailable && (
          <Button
            size="sm"
            data-testid="voice-now-turn-on"
            disabled={voice.isSaving}
            onClick={() => voice.setMode("realtime")}
          >
            {voice.isSaving && <Loader2 className="animate-spin" />}
            {t("apikeys_realtime.turn_on")}
          </Button>
        )}
        {state && (
          <span
            data-testid="voice-now-state"
            aria-live="polite"
            className="inline-flex shrink-0 items-center gap-2 text-sm text-muted-foreground"
          >
            <span
              aria-hidden="true"
              className={cn(
                "h-2 w-2 rounded-full",
                state.tone === "accent" && "bg-accent",
                state.tone === "pulse" && "animate-pulse bg-accent motion-reduce:animate-none",
                state.tone === "warn" && "bg-warning",
                state.tone === "error" && "bg-destructive",
                state.tone === "quiet" && "bg-muted-foreground",
              )}
            />
            <span className={cn(state.tone !== "quiet" && "font-medium text-foreground")}>
              {state.label}
            </span>
          </span>
        )}
      </div>
      {notes.map((note) => (
        <p
          key={note.key}
          role={note.alert ? "alert" : "status"}
          data-testid={note.key === "backup" ? "voice-engine-runtime-status" : `voice-now-note-${note.key}`}
          className={cn(
            "px-5 py-3 text-sm",
            note.alert ? "text-destructive" : "text-warning",
          )}
        >
          {note.text}
        </p>
      ))}
    </div>
  );
}

/** One provider in the list: a radio mark for "this is the voice", its state on the right. */
function ProviderRow({
  provider,
  billingOverride,
  statusLabel,
  selected,
  activating,
  health,
  onSelect,
}: {
  provider: ProviderDescriptor;
  billingOverride?: ProviderDescriptor["billing"];
  statusLabel?: string;
  selected: boolean;
  activating: boolean;
  health?: SectionHealth;
  onSelect: () => void;
}) {
  const t = useT();
  const broken = provider.active && health?.status === "error";
  return (
    <button
      type="button"
      aria-pressed={selected}
      data-testid={`realtime-provider-${provider.id}`}
      data-active={provider.active ? "true" : "false"}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        selected ? "bg-accent-soft" : "hover:bg-secondary",
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors",
          provider.active ? "border-accent" : "border-border-strong",
        )}
      >
        {provider.active && <span className="h-2 w-2 rounded-full bg-accent" />}
      </span>
      <ProviderLogo providerId={provider.id} label={provider.label} />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-base font-medium text-foreground">{provider.label}</span>
          {provider.recommended && <Tag tone="accent">{t("apikeys_view.recommended")}</Tag>}
          {provider.experimental && (
            <Tag tone="neutral" title={t("apikeys_view.experimental_note")}>
              {t("apikeys_view.experimental")}
            </Tag>
          )}
        </span>
        <span className="mt-0.5 block truncate text-sm text-muted-foreground">
          {t(`provider_billing.${billingOverride ?? provider.billing}`)}
        </span>
      </span>
      {activating ? (
        <span className="inline-flex shrink-0 items-center gap-1.5 text-sm text-muted-foreground">
          <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
          {t("apikeys_view.provider_activating")}
        </span>
      ) : broken ? (
        <span className="inline-flex shrink-0 items-center gap-1.5 text-sm font-medium text-destructive">
          <span aria-hidden="true" className="h-2 w-2 rounded-full bg-destructive" />
          {t("apikeys_view.health_error")}
        </span>
      ) : provider.active ? (
        <span className="inline-flex shrink-0 items-center gap-1.5 text-sm font-medium text-foreground">
          <Check aria-hidden="true" className="h-4 w-4 text-accent" />
          {t("apikeys_realtime.active")}
        </span>
      ) : statusLabel ? (
        <span title={statusLabel} className="inline-flex max-w-[45%] shrink-0 items-center gap-1.5 text-sm text-muted-foreground">
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-muted-foreground" />
          <span className="truncate">{statusLabel}</span>
        </span>
      ) : (
        <StatusBadge descriptor={provider} />
      )}
    </button>
  );
}

/**
 * Everything that edits the selected provider, as plain settings groups: its
 * connection (key, sign-in or local server) with a live test, then its model
 * and voice — or, for GPT-Live, its own conversation and thinking groups.
 */
function ProviderSettings({
  provider,
  health,
  activating,
  canSwitch,
  onActivate,
  onKeySaved,
  onChanged,
}: {
  provider: ProviderDescriptor;
  health?: SectionHealth;
  activating: boolean;
  canSwitch: boolean;
  onActivate: () => void;
  /** Called once a key was saved for a provider that had none. */
  onKeySaved?: () => void;
  onChanged: () => void;
}) {
  const t = useT();
  const isLive = provider.configuration_surface === "live";
  const selectedAuthMode = liveMode(provider);
  const showCredentials = !isLive || selectedAuthMode === "api_key";
  const broken = provider.active && health?.status === "error";
  const brokenDetail = health?.detail?.trim() || "";
  const showOptions =
    !isLive &&
    (provider.configured ||
      // Keep the pickers mounted through a transient busy probe so the group
      // does not flicker while the provider is being checked.
      provider.codex_status?.reason_code === "busy");

  return (
    <div data-testid={`realtime-settings-${provider.id}`} className="flex flex-col gap-group">
      {(showCredentials || broken) && <ProfileGroup
        title={provider.label}
        description={
          isLive
            ? t("apikeys_realtime.settings_desc_live")
            : t(`provider_billing.${provider.billing}`)
        }
        aside={
          canSwitch && !isLive ? (
            <Button
              size="sm"
              variant="outline"
              disabled={activating}
              data-testid={`realtime-use-${provider.id}`}
              onClick={onActivate}
            >
              {activating && <Loader2 className="animate-spin" />}
              {t("apikeys_realtime.use_provider").replace("{0}", provider.label)}
            </Button>
          ) : undefined
        }
      >
        {broken && (
          <div
            role="status"
            data-testid={`realtime-health-error-${provider.id}`}
            className="flex items-start gap-2 px-5 py-3 text-sm text-destructive"
          >
            <XCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="min-w-0 break-words">
              <span className="font-medium">{t("apikeys_view.health_error")}</span>
              {brokenDetail ? ` — ${brokenDetail}` : ""}
            </span>
          </div>
        )}
        {showCredentials && <div className="px-5 py-4">
          <AuthWidget descriptor={provider} onChanged={onChanged} onSavedActivate={onKeySaved} />
        </div>}
        {showOptions && (
          <SettingRow
            label={t("apikeys_realtime.model_voice")}
            hint={t("apikeys_realtime.model_voice_hint")}
          >
            <div className="mt-3">
              <RealtimeOptionsControl providerId={provider.id} healthActive={provider.active} />
            </div>
          </SettingRow>
        )}
        {showCredentials && <SettingRow
          label={t("apikeys_realtime.test_label")}
          hint={t("apikeys_realtime.test_hint")}
          control={
            <ProviderTestControl
              providerId={provider.id}
              providerLabel={provider.label}
              section={provider.tier}
              active={provider.active}
            />
          }
        />}
      </ProfileGroup>}

      {isLive && <LiveProfile onSaved={onChanged} selectedAuthMode={selectedAuthMode} />}
    </div>
  );
}

/**
 * The once-per-provider acknowledgement before an experimental route becomes
 * the voice. An in-app dialog, not `window.confirm`: the desktop WebView draws
 * that as a raw "127.0.0.1 says" box that blocks the whole window.
 */
function ExperimentalConsentDialog({
  provider,
  onCancel,
  onConfirm,
}: {
  provider: ProviderDescriptor | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  const subscriptionOnly =
    provider?.auth_mode === "codex" &&
    provider.billing === "subscription" &&
    provider.secret_keys.length === 0;
  return (
    <Dialog.Root open={provider !== null} onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-scrim/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="realtime-consent-dialog"
          className="fixed inset-0 z-[80] m-auto h-fit w-[min(440px,calc(100vw-32px))] rounded-2xl border border-border bg-popover p-6 text-popover-foreground shadow-float outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none"
        >
          <Dialog.Title className="text-base font-semibold text-foreground-strong">
            {provider?.label}
          </Dialog.Title>
          <Dialog.Description className="mt-1.5 text-sm text-muted-foreground">
            {t(
              subscriptionOnly
                ? "apikeys_view.experimental_subscription_consent"
                : "apikeys_view.experimental_consent",
            )}
          </Dialog.Description>
          <div className="mt-6 flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onCancel}>
              {t("common.cancel")}
            </Button>
            <Button type="button" onClick={onConfirm}>
              {t("common.yes")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

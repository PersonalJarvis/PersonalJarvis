import { useState } from "react";
import { AlertCircle, Bot, Brain, Check, Copy, KeyRound, Loader2, LogIn, LogOut, Mic, Phone, PlugZap, SlidersHorizontal, Terminal, Volume2, XCircle } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { AltCredentialNote } from "@/components/AltCredentialNote";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { CuModelSelector } from "@/components/CuModelSelector";
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";
import { SubagentSection } from "@/components/SubagentSection";
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
  startCodexLogin,
  switchBrainProvider,
  switchSttProvider,
  switchTtsProvider,
  testProvider,
  useProviders,
  useSectionHealth,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

// The view is organised around exactly four primary categories — Brain, Voice
// Output (TTS), Voice Input (STT) and Subagents — surfaced as a segmented tab
// bar. Everything else (Control-API key, team key proxy, telephony, Wiki) lives
// in a clearly separated, de-emphasized "Advanced" tab so it never competes with
// the four core categories.
type CategoryKey = ProviderTier | "subagents" | "advanced";

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
  };
}

export function ApiKeysView() {
  const t = useT();
  const { providers, loading, error, refetch, setActiveOptimistic } = useProviders();
  // Per-tab health (amber = the active provider isn't set up, red = it's set up
  // but failing a live check). Best-effort and off the render-blocking path.
  const { health } = useSectionHealth();
  const categories = makeProviderCategories(t);
  const [active, setActive] = useState<CategoryKey>("brain");

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<KeyRound className="h-4 w-4 text-primary" />}
        title={t("apikeys_view.title")}
        subtitle={t("apikeys_view.subtitle")}
      />

      <CategoryTabs active={active} onSelect={setActive} health={health} />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        {(active === "brain" || active === "tts" || active === "stt") && (
          <ProviderCategory
            meta={categories[active]}
            tier={active}
            providers={providers}
            loading={loading}
            error={error}
            onChanged={refetch}
            onActivateOptimistic={setActiveOptimistic}
          />
        )}
        {active === "subagents" && <SubagentCategory />}
        {active === "advanced" && <AdvancedCategory />}
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
}: {
  active: CategoryKey;
  onSelect: (key: CategoryKey) => void;
  /** Per-tab health rollup keyed by category; absent keys render no dot. */
  health: Record<string, SectionHealth>;
}) {
  const t = useT();
  const coreTabs: { key: CategoryKey; label: string; icon: LucideIcon }[] = [
    { key: "brain", label: t("apikeys_view.tab_brain"), icon: Brain },
    { key: "tts", label: t("apikeys_view.tab_tts"), icon: Volume2 },
    { key: "stt", label: t("apikeys_view.tab_stt"), icon: Mic },
    { key: "subagents", label: t("apikeys_view.tab_subagents"), icon: Bot },
  ];
  return (
    <div className="border-b border-border px-6 py-3">
      <div role="tablist" className="flex flex-wrap items-center gap-1.5">
        <div className="flex flex-wrap items-center gap-1 rounded-xl border border-border bg-card/40 p-1">
          {coreTabs.map((tab) => (
            <TabButton
              key={tab.key}
              icon={tab.icon}
              label={tab.label}
              selected={active === tab.key}
              onClick={() => onSelect(tab.key)}
              health={health[tab.key]}
            />
          ))}
        </div>
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
}: {
  meta: CategoryMeta;
  tier: ProviderTier;
  providers: ProviderDescriptor[];
  loading: boolean;
  error: string | null;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
}) {
  const t = useT();
  const tierProviders = providers.filter(
    (p) => p.tier === tier && p.brain_switchable !== false,
  );

  return (
    <div role="tabpanel">
      <CategoryHero icon={meta.icon} title={meta.title} description={meta.description} />

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
        />
      )}
    </div>
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
      <SubagentSection hideHeader />
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
      <div className="space-y-8">
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
}: {
  providers: ProviderDescriptor[];
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
}) {
  const tierHasActive = providers.some((p) => p.active);
  return (
    <ul className="space-y-3">
      {providers.map((p) => (
        <li key={p.id}>
          <ProviderCard
            descriptor={p}
            onChanged={onChanged}
            onActivateOptimistic={onActivateOptimistic}
            autoActivateOnSave={!tierHasActive}
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
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
  onActivateOptimistic: (tier: ProviderTier, id: string) => void;
  autoActivateOnSave: boolean;
}) {
  const t = useT();
  const [activating, setActivating] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

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
      } else {
        const result = await switchSttProvider(descriptor.id);
        const note = result.restart_required
          ? " (active from next voice start)"
          : "";
        pushToast("success", `Voice input → ${descriptor.label}${note}`);
        window.dispatchEvent(new CustomEvent("jarvis:stt-switched"));
      }
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
      // Roll the optimistic highlight back to the true active provider.
      onChanged();
    } finally {
      setActivating(false);
    }
  }

  // Single-Click UND Doppelklick auf die Karte aktivieren den Provider.
  // Wir filtern Klicks auf interaktive Sub-Elemente (Inputs, Buttons,
  // Links) explizit, damit ein Klick ins Passwort-Feld oder auf
  // "Ersetzen"/Trash NICHT versehentlich einen Switch ausloest. Das Radio
  // hat zusaetzlich ein eigenes stopPropagation aus historischen Gruenden
  // (Doppelschutz).
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

  // Wird vom ApiKeyForm aufgerufen, sobald ein Key fuer einen bisher offenen
  // Provider gespeichert wurde. Wenn niemand sonst in dieser Tier aktiv ist,
  // uebernimmt der frisch konfigurierte Provider automatisch — sonst muss der
  // User den nun sichtbaren "Aktivieren"-Button klicken.
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
        descriptor.active
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
            <span className="font-medium">{descriptor.label}</span>
            <StatusBadge descriptor={descriptor} />
            {descriptor.recommended && (
              <span
                className="rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-primary"
                title={
                  descriptor.recommended_model
                    ? t("apikeys_view.recommended_tooltip").replace(
                        "{0}",
                        descriptor.recommended_model,
                      )
                    : undefined
                }
              >
                {t("apikeys_view.recommended")}
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
          descriptor.configured)) && (
        <BrainModelSelector
          providerId={descriptor.id}
          recommendedModel={descriptor.recommended_model}
        />
      )}

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
          (defaults to the provider's main model — no automatic escalation). */}
      {descriptor.tier === "brain" && descriptor.configured && isBrainSwitchable && (
        <CuModelSelector
          providerId={descriptor.id}
          recommendedModel={descriptor.recommended_model}
        />
      )}

      <ProviderTestControl providerId={descriptor.id} />
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
function ProviderTestControl({ providerId }: { providerId: string }) {
  const t = useT();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ProviderTestResult | null>(null);

  async function run() {
    setRunning(true);
    setResult(null);
    try {
      setResult(await testProvider(providerId));
    } catch (e) {
      setResult({
        provider: providerId,
        status: "error",
        detail: (e as Error).message,
        latency_ms: 0,
        integration_ok: false,
      });
    } finally {
      setRunning(false);
      // Let the tab indicators re-check: if this was the active provider, its
      // tab dot should reflect the fresh result instead of a stale cached one.
      window.dispatchEvent(new Event("jarvis:provider-tested"));
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
 * Radio-Button-basierter Aktiv-Toggle. Source-of-Truth bleibt
 * `descriptor.active` aus `/api/providers` — das Radio spiegelt den Server-
 * Zustand, der State wird nicht lokal vorgehalten. `name="active-{tier}"`
 * sorgt fuer Browser-native Exklusivitaet pro Tier (Brain/TTS/STT).
 *
 * `disabled` wuerde onChange unterdruecken; deshalb ist das Radio bei
 * fehlendem Key NICHT disabled, sondern leitet via `activate()` einen
 * Warning-Toast aus. So bekommt der User auf jeden Klick eine Reaktion,
 * statt sich an einem stummen Element zu reiben.
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
        // Doppelklick auf das Radio-Label soll NICHT zusaetzlich den
        // onDoubleClick der Card triggern — sonst wuerde activate() zweimal
        // feuern (idempotent, aber sendet zwei API-Calls).
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

function StatusBadge({ descriptor }: { descriptor: ProviderDescriptor }) {
  if (descriptor.active) return <span className="chip-yellow">active</span>;
  if (descriptor.auth_mode === "codex") {
    const status = descriptor.codex_status;
    if (!status?.installed) {
      return <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-destructive">missing</span>;
    }
    if (descriptor.configured) {
      return <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">ready</span>;
    }
    return <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">not connected</span>;
  }
  if (descriptor.auth_mode === "antigravity") {
    const status = descriptor.antigravity_status;
    if (!status?.installed) {
      return <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-destructive">missing</span>;
    }
    if (status.connected) {
      return <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">ready</span>;
    }
    return <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">not connected</span>;
  }
  if (descriptor.configured) return <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">ready</span>;
  return <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">open</span>;
}

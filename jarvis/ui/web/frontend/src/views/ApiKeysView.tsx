import { useState } from "react";
import { Brain, Check, Copy, KeyRound, LogIn, LogOut, Mic, Phone, Shield, Terminal, Volume2, Loader2, AlertCircle } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ApiKeyForm } from "@/components/ApiKeyForm";
import { SubagentSection } from "@/components/SubagentSection";
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
import { Button } from "@/components/ui/button";
import {
  codexLogout,
  type ProviderDescriptor,
  type ProviderTier,
  startCodexLogin,
  switchBrainProvider,
  switchSttProvider,
  switchTtsProvider,
  useProviders,
} from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

function makeTierMeta(t: (k: string) => string): Record<ProviderTier, { label: string; icon: React.ReactNode }> {
  return {
    brain: { label: t("apikeys_view.tier_brain"), icon: <Brain className="h-3.5 w-3.5" /> },
    tts: { label: t("apikeys_view.tier_tts"), icon: <Volume2 className="h-3.5 w-3.5" /> },
    stt: { label: t("apikeys_view.tier_stt"), icon: <Mic className="h-3.5 w-3.5" /> },
  };
}

export function ApiKeysView() {
  const t = useT();
  const TIER_META = makeTierMeta(t);
  const { providers, loading, error, refetch } = useProviders();

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<KeyRound className="h-4 w-4 text-primary" />}
        title={t("apikeys_view.title")}
        subtitle={t("apikeys_view.subtitle")}
      />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <SecurityNotice />

        {loading && (
          <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("apikeys_view.loading_providers")}
          </div>
        )}

        {error && (
          <div className="mt-6 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            <AlertCircle className="mt-0.5 h-4 w-4" />
            <div>
              {t("apikeys_view.load_error")} ({error}).
              <button onClick={() => refetch()} className="ml-2 underline">{t("apikeys_view.retry")}</button>
            </div>
          </div>
        )}

        {!loading && providers.length > 0 && (
          <div className="mt-6 space-y-8">
            {(Object.keys(TIER_META) as ProviderTier[]).map((tier) => {
              const tierProviders = providers.filter((p) => p.tier === tier);
              if (!tierProviders.length) return null;
              return (
                <TierSection key={tier} tier={tier} providers={tierProviders} onChanged={refetch} />
              );
            })}
            {/* Subagent (OpenClaw) — own data source (/api/openclaw/status),
                rendered as a sibling tier so it shares the card system. */}
            <SubagentSection />
            {/* Telephony — the former standalone "Telephony" screen, folded in
                here as another tier section (own data source /api/telephony/*).
                Same header style as the tiers above; always expanded. */}
            <TelephonySection />
            {/* Wiki — dedicated long-term-memory curator provider/model. Own
                data source (/api/settings/wiki-provider); a thin sibling tier. */}
            <WikiProviderCard />
          </div>
        )}
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

function SecurityNotice() {
  const t = useT();
  return (
    <div className="flex items-start gap-3 rounded-lg border border-primary/20 bg-primary/5 p-4">
      <Shield className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="text-xs leading-relaxed text-muted-foreground">
        {t("apikeys_view.security_notice")}
      </div>
    </div>
  );
}

function TierSection({
  tier,
  providers,
  onChanged,
}: {
  tier: ProviderTier;
  providers: ProviderDescriptor[];
  onChanged: () => void;
}) {
  const t = useT();
  const meta = makeTierMeta(t)[tier];
  // Wenn niemand in dieser Tier aktiv ist, soll ein frisch gesetzter Key sich
  // selbst aktivieren — der erste konfigurierte Provider gewinnt automatisch.
  const tierHasActive = providers.some((p) => p.active);
  return (
    <section>
      <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        {meta.icon} {meta.label}
      </h3>
      <ul className="space-y-3">
        {providers.map((p) => (
          <li key={p.id}>
            <ProviderCard
              descriptor={p}
              onChanged={onChanged}
              autoActivateOnSave={!tierHasActive}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProviderCard({
  descriptor,
  onChanged,
  autoActivateOnSave,
}: {
  descriptor: ProviderDescriptor;
  onChanged: () => void;
  autoActivateOnSave: boolean;
}) {
  const t = useT();
  const [activating, setActivating] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  // Codex IS a selectable brain like every other provider, but a chat brain
  // needs an OpenAI API key — the ChatGPT login cannot back one (it powers the
  // Codex *worker*). `codex_brain_ready` reflects whether any OpenAI key is
  // configured; the radio is gated on it so activation never silently fails.
  const isCodex = descriptor.auth_mode === "codex";

  async function activate(assumeConfigured = false) {
    if (descriptor.active) return;
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
          : t("apikeys_codex.needs_key_full").replace("{0}", descriptor.label),
      );
      return;
    }
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
        pushToast("success", `Voice-Output → ${descriptor.label}${note}`);
        window.dispatchEvent(new CustomEvent("jarvis:tts-switched"));
      } else {
        const result = await switchSttProvider(descriptor.id);
        const note = result.restart_required
          ? " (active from next voice start)"
          : "";
        pushToast("success", `Voice-Input → ${descriptor.label}${note}`);
        window.dispatchEvent(new CustomEvent("jarvis:stt-switched"));
      }
      onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
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
          : descriptor.configured
            ? t("apikeys_view.click_to_activate")
            : descriptor.auth_mode === "codex"
              ? t("apikeys_view.needs_codex")
              : t("apikeys_view.needs_key")
      }
      className={cn(
        "card-outline space-y-3 p-4 transition-colors",
        descriptor.active
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : descriptor.configured
            ? "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]"
            : "opacity-95",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{descriptor.label}</span>
            <StatusBadge descriptor={descriptor} />
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <code className="font-mono">{descriptor.id}</code>
            {" · "}
            <span>
              {descriptor.auth_mode === "api_key" && "API-Key-Auth"}
              {descriptor.auth_mode === "codex" && "ChatGPT/Codex-Login"}
              {descriptor.auth_mode === "none" && "Lokal — keine Auth"}
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
          disabled={isCodex && !descriptor.codex_brain_ready}
        />
      </div>

      <AuthWidget
        descriptor={descriptor}
        onChanged={onChanged}
        onSavedActivate={handleSavedActivate}
      />
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
}) {
  const labelTitle = descriptor.active
    ? "Dieser Provider ist aktiv"
    : disabled
      ? "Erst einen OpenAI-API-Key setzen"
      : descriptor.configured
        ? "Diesen Provider aktivieren"
        : "Erst API-Key setzen";

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
      {activating ? "Aktiviere…" : "Als aktiv"}
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
  if (descriptor.auth_mode === "none") {
    return (
      <p className="text-xs text-muted-foreground">
        Local provider — no credentials needed.
      </p>
    );
  }

  if (descriptor.auth_mode === "codex") {
    return <CodexAuthWidget descriptor={descriptor} onChanged={onChanged} />;
  }

  // api_key
  return (
    <div className="space-y-2">
      {descriptor.secret_keys.map((k) => (
        <ApiKeyForm
          key={k}
          secretKey={k}
          dashboardUrl={descriptor.dashboard_url}
          configured={Boolean(descriptor.secrets_set[k])}
          onChanged={onChanged}
          onSavedActivate={onSavedActivate}
        />
      ))}
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
      pushToast("success", "Installationsbefehl kopiert");
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
            Trennen
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
            Befehl kopieren
          </Button>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={handleLogin} disabled={pending !== null || !status?.installed}>
          <LogIn className="h-3.5 w-3.5" />
          Mit ChatGPT verbinden
        </Button>
        <Button size="sm" variant="outline" asChild>
          <a href="https://help.openai.com/en/articles/11381614" target="_blank" rel="noreferrer">
            <Terminal className="h-3.5 w-3.5" />
            Codex installieren
          </a>
        </Button>
      </div>

    </div>
  );
}

function StatusBadge({ descriptor }: { descriptor: ProviderDescriptor }) {
  if (descriptor.active) return <span className="chip-yellow">aktiv</span>;
  if (descriptor.auth_mode === "codex") {
    const status = descriptor.codex_status;
    if (!status?.installed) {
      return <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-destructive">fehlt</span>;
    }
    if (descriptor.configured) {
      return <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">eingerichtet</span>;
    }
    return <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">nicht verbunden</span>;
  }
  if (descriptor.configured) return <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">eingerichtet</span>;
  return <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">offen</span>;
}

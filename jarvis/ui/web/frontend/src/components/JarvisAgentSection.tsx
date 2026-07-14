import { useCallback, useEffect, useState } from "react";
import { ArrowUp, Bot, CreditCard, Laptop, Lock, LogIn, LogOut, Sparkles, Terminal, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  codexLogout,
  loginAntigravity,
  loginClaude,
  logoutAntigravity,
  logoutClaude,
  saveSubagentModel,
  startCodexLogin,
  switchSubagentProvider,
  type AntigravityStatus,
  type Billing,
  type ClaudeStatus,
  type CodexStatus,
} from "@/hooks/useProviders";
import { BrainModelSelector } from "@/components/BrainModelSelector";

/**
 * Subagent tier for the API-Keys view.
 *
 * Visually a sibling of the brain/tts/stt tiers in `ApiKeysView`: the same
 * tier header + `card-outline` cards + identical `StatusBadge` styling. Each
 * provider card carries the same "Set active" radio as the API-key tiers, so
 * the user can switch the Heavy-Task subagent provider seamlessly instead of
 * being stuck on one. There is no key input — sub-agent workers reuse the
 * brain-provider keys entered above (`key_set` mirrors that state).
 *
 * Data source is `GET /api/jarvis-agent/status`; the switch posts to
 * `POST /api/jarvis-agent/switch` (3-layer persist). This is its own section
 * rather than a fourth `ProviderTier` because the status payload differs.
 * The endpoint path and the JSON keys below are the server contract; the
 * user-facing UI never says anything but "Jarvis-Agent".
 */

interface SubagentMappingRow {
  jarvis: string;
  /** Server contract: the worker-harness provider slug (e.g. "google"). */
  worker_slug: string;
  env_var: string;
  env_fallback: string | null;
  key_set: boolean;
  is_active_brain: boolean;
  /** How this subagent is billed — "api" (per token) / "subscription" /
   * "subscription_or_api". Drives the billing badge so the API-vs-subscription
   * distinction is visible right on the subagent cards. */
  billing: Billing;
}

interface SubagentStatus {
  configured: boolean;
  enabled: boolean;
  binary_path: string;
  binary_detected: string | null;
  version_pin: string | null;
  time_cap_min: number | null;
  concurrency: number | null;
  state_dir_root: string | null;
  brain_primary: string;
  provider_slug: string | null;
  model_override: string | null;
  /** The dedicated subagent LLM pin ([brain.sub_jarvis].model); empty/null
   * means "the active provider's deep model" (shown via model_resolved). */
  sub_model_override: string | null;
  model_resolved: string | null;
  mapping: SubagentMappingRow[];
}

// Human-readable card titles for the known sub-agent providers; falls back to
// the raw jarvis slug for anything not listed.
const PROVIDER_LABELS: Record<string, string> = {
  gemini: "Google Gemini",
  "claude-api": "Anthropic Claude",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  grok: "xAI Grok",
  nvidia: "NVIDIA NIM",
  // Codex is a direct worker (ChatGPT subscription / OpenAI key), not an
  // OpenClaw-routed provider — surfaced as its own selectable subagent row.
  "openai-codex": "OpenAI Codex",
  // Antigravity drives the Google subscription CLI as a direct worker (OAuth, no
  // API key), the Google sibling of Codex.
  antigravity: "Antigravity (Google subscription)",
};

/**
 * Poll a CLI status endpoint after starting an external login flow, refreshing
 * the section on each tick, until it reports `connected` or a timeout.
 *
 * Why: external CLI logins (Codex `codex login`, Antigravity Google sign-in)
 * complete in a SEPARATE browser/console window AFTER the POST returns, so a
 * single immediate refetch always reads the still-disconnected state. Without
 * this poll the card stays "open" / locked until the user manually reloads —
 * the exact "I connected Codex but can't select it" symptom.
 */
async function pollStatusUntilConnected(
  statusUrl: string,
  onTick: () => void | Promise<void>,
  { maxMs = 120_000, intervalMs = 2_500 }: { maxMs?: number; intervalMs?: number } = {},
): Promise<boolean> {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, intervalMs));
    await onTick();
    try {
      const res = await fetch(statusUrl);
      if (res.ok) {
        const data = await res.json();
        if (data?.connected) return true;
      }
    } catch {
      // transient (app restarting / network blip) — keep polling
    }
  }
  return false;
}

export function JarvisAgentSection({
  hideHeader = false,
}: {
  /** Suppress the section header — used inside the API-Keys "Subagents" tab,
   * whose category hero already shows the title (avoids a double label). */
  hideHeader?: boolean;
} = {}) {
  const t = useT();
  const [bridge, setBridge] = useState<SubagentStatus | null>(null);
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
  const [antigravityStatus, setAntigravityStatus] =
    useState<AntigravityStatus | null>(null);
  const [claudeStatus, setClaudeStatus] = useState<ClaudeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-fetch on brain-switch / subagent-switch / secret-set so the active
  // provider highlight + the per-provider "Key gesetzt" badges track live.
  const reload = useCallback(async () => {
    try {
      const [res, codexRes, antigravityRes, claudeRes] = await Promise.all([
        fetch("/api/jarvis-agent/status"),
        fetch("/api/codex/status").catch(() => null),
        fetch("/api/antigravity/status").catch(() => null),
        fetch("/api/claude/status").catch(() => null),
      ]);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SubagentStatus = await res.json();
      setBridge(data);
      if (codexRes?.ok) {
        setCodexStatus(await codexRes.json());
      } else {
        setCodexStatus(null);
      }
      if (antigravityRes?.ok) {
        setAntigravityStatus(await antigravityRes.json());
      } else {
        setAntigravityStatus(null);
      }
      if (claudeRes?.ok) {
        setClaudeStatus(await claudeRes.json());
      } else {
        setClaudeStatus(null);
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
    const onChange = () => void reload();
    window.addEventListener("jarvis:brain-switched", onChange);
    window.addEventListener("jarvis:agent-switched", onChange);
    window.addEventListener("jarvis:secret-configured", onChange);
    return () => {
      window.removeEventListener("jarvis:brain-switched", onChange);
      window.removeEventListener("jarvis:agent-switched", onChange);
      window.removeEventListener("jarvis:secret-configured", onChange);
    };
  }, [reload]);

  if (error) {
    return (
      <section>
        {!hideHeader && <SectionHeader label={t("apikeys_view.tier_subagent")} />}
        <p className="text-xs text-destructive">Status unavailable: {error}</p>
      </section>
    );
  }

  if (!bridge) return null;

  // Codex + Antigravity are subscription logins (OAuth) rendered as their own
  // connection cards; their "Set active" control now lives ON those cards, so
  // exclude them from the generic provider list to avoid a duplicate card per
  // CLI (the confusing "no select button on the subscription" the user hit).
  const codexRow = bridge.mapping.find((r) => r.jarvis === "openai-codex");
  const antigravityRow = bridge.mapping.find((r) => r.jarvis === "antigravity");
  // Claude is dual-billed (Claude Max subscription OR Anthropic API key) — it
  // gets its own connection card showing the signed-in account, like Codex /
  // Antigravity, so exclude it from the generic per-key provider list too.
  const claudeRow = bridge.mapping.find((r) => r.jarvis === "claude-api");
  const providerRows = bridge.mapping.filter(
    (r) =>
      r.jarvis !== "openai-codex" &&
      r.jarvis !== "antigravity" &&
      r.jarvis !== "claude-api",
  );
  // Split the generic providers by access type so each lands in the right
  // column. Practically these are all API-key providers (gemini/openai/…), but
  // splitting on the backend `billing` field keeps a future subscription
  // provider in the correct column instead of the wrong one (AP-21: gate on
  // capability, never a provider name).
  const subProviderRows = providerRows.filter((r) => r.billing !== "api");
  const apiProviderRows = providerRows.filter((r) => r.billing === "api");

  return (
    <section className="space-y-4">
      {!hideHeader && <SectionHeader label={t("apikeys_view.tier_subagent")} />}

      <BridgeStatusStrip status={bridge} />

      {/* The coupling is non-obvious: subagent providers have no key field of
          their own — they reuse the Brain-provider keys set above. A dim one-line
          hint instead of a loud banner: it informs without competing with the
          cards below. */}
      <p className="flex items-start gap-2 px-1 text-[11px] leading-relaxed text-muted-foreground">
        <ArrowUp className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
        <span>
          Agents reuse the keys from the{" "}
          <strong className="text-foreground">Brain</strong> tab. Codex uses the
          ChatGPT login, Antigravity the Google login — connect there, then pick
          which provider runs heavy background tasks.
        </span>
      </p>

      {/* Two access-typed columns so the two ways to power an agent never mix:
          subscription logins (violet) on the left, API-key providers (sky) on
          the right. The colour matches each card's access badge + accent stripe. */}
      <div className="grid gap-4 md:grid-cols-2 md:items-start">
        <div className="space-y-3">
          <ColumnHeader
            icon={Sparkles}
            title="Subscription logins"
            hint="sign in with an account"
            tone="violet"
          />
          {codexRow && (
            <CodexConnectionCard
              status={codexStatus}
              row={codexRow}
              onChanged={reload}
            />
          )}
          {antigravityRow && (
            <AntigravityConnectionCard
              status={antigravityStatus}
              row={antigravityRow}
              onChanged={reload}
            />
          )}
          {claudeRow && (
            <ClaudeConnectionCard
              status={claudeStatus}
              row={claudeRow}
              onChanged={reload}
            />
          )}
          {subProviderRows.map((row) => (
            <SubagentProviderCard key={row.jarvis} row={row} onSwitched={reload} />
          ))}
        </div>

        <div className="space-y-3">
          <ColumnHeader
            icon={CreditCard}
            title="API keys"
            hint="billed per token"
            tone="sky"
          />
          {claudeRow && (
            <ClaudeApiCard
              status={claudeStatus}
              row={claudeRow}
              onChanged={reload}
            />
          )}
          {apiProviderRows.map((row) => (
            <SubagentProviderCard key={row.jarvis} row={row} onSwitched={reload} />
          ))}
        </div>
      </div>

      <SubagentModelCard status={bridge} onSaved={reload} />
    </section>
  );
}

/**
 * The dedicated subagent LLM model pin. Mirrors the Wiki card's
 * "model (optional)" pattern: empty means the active provider's deep
 * (frontier) model — shown in the hint via `model_resolved` — and a concrete
 * id overrides it for every heavy-task worker spawn.
 */
/**
 * The dedicated subagent LLM model pin — the SAME dropdown as the brain cards,
 * showing the active subagent provider's catalog (``brain_primary``) and saving
 * through the subagent endpoint (POST /api/subagent/model) instead of the
 * per-provider model route. Empty selection = the provider's deep/frontier model
 * (shown in the hint via ``model_resolved``).
 */
function SubagentModelCard({
  status,
  onSaved,
}: {
  status: SubagentStatus;
  onSaved: () => void;
}) {
  const t = useT();
  // The subagent worker slug → the catalog provider id (Codex's worker slug
  // "openai-codex" maps to the catalog's "codex"; all others match 1:1).
  const catalogProvider =
    status.brain_primary === "openai-codex" ? "codex" : status.brain_primary;
  return (
    <div className="card-outline space-y-3 p-4">
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {t("subagent_model.description")}
      </p>
      {catalogProvider ? (
        <BrainModelSelector
          providerId={catalogProvider}
          currentModel={status.sub_model_override ?? ""}
          healthSection="subagents"
          healthActive
          onSave={async (model) => {
            const r = await saveSubagentModel(model);
            window.dispatchEvent(new Event("jarvis:agent-switched"));
            onSaved();
            return {
              ok: true,
              provider: status.brain_primary,
              model,
              persisted: r.persisted,
              applied_live: false,
              restart_required: r.restart_required,
              probe: null,
            };
          }}
        />
      ) : (
        <p className="text-[11px] text-muted-foreground">{t("subagent_model.model_hint")}</p>
      )}
      <p className="text-[11px] text-muted-foreground">
        {t("subagent_model.model_hint")}
        {status.model_resolved ? ` (${status.model_resolved})` : ""}
      </p>
    </div>
  );
}

function SectionHeader({ label }: { label: string }) {
  return (
    <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
      <Bot className="h-3.5 w-3.5" /> {label}
    </h3>
  );
}

/** A small meta pill for the bridge status strip. */
function BridgeMeta({
  children,
  mono = false,
}: {
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <span
      className={cn(
        "rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground",
        mono && "font-mono",
      )}
    >
      {children}
    </span>
  );
}

/**
 * The bridge status — a calm one-line strip instead of a debug key/value table.
 * A status dot + plain-language state on the left, the read-only worker / model
 * / limits as dim meta pills on the right. Engine internals (binary path etc.)
 * are reduced to "installed" / "not installed"; the concrete path is developer
 * noise and is not surfaced.
 */
function BridgeStatusStrip({ status }: { status: SubagentStatus }) {
  const installed = Boolean(status.binary_detected);
  const live = status.enabled && installed;
  const worker = PROVIDER_LABELS[status.brain_primary] ?? status.brain_primary;
  const model = status.model_resolved ?? status.sub_model_override ?? null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-border bg-card/60 px-4 py-3 backdrop-blur">
      <span
        className={cn(
          "h-2 w-2 shrink-0 rounded-full",
          live
            ? "bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.7)]"
            : "bg-muted-foreground",
        )}
      />
      <span className="text-sm font-medium">
        {live ? "Agent bridge active" : "Agent bridge inactive"}
      </span>
      <span className="text-[11px] text-muted-foreground">
        {installed ? "Engine installed" : "Engine not installed"}
        {status.version_pin && (
          <>
            {" · pin "}
            <span className="font-mono text-foreground">{status.version_pin}</span>
          </>
        )}
      </span>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <BridgeMeta>
          worker <strong className="font-semibold text-foreground">{worker}</strong>
        </BridgeMeta>
        {model && <BridgeMeta mono>{model}</BridgeMeta>}
        {status.time_cap_min !== null && (
          <BridgeMeta>
            {status.time_cap_min} min · max {status.concurrency} parallel
          </BridgeMeta>
        )}
      </div>
    </div>
  );
}

// Subagent worker slug → local brand-logo file under public/provider-logos/
// (monochrome white SVGs, so they work offline and never depend on a live CDN;
// nominative-use brand marks, see TRADEMARK.md). A slug with no entry — or a
// logo that fails to load — falls back to the neutral letter monogram, so a new
// or logo-less provider never renders broken.
const PROVIDER_ICON: Record<string, string> = {
  openai: "openai",
  "openai-codex": "openai",
  "claude-api": "claude",
  gemini: "gemini",
  openrouter: "openrouter",
  nvidia: "nvidia",
  antigravity: "antigravity",
};

/**
 * The tile on the left of every provider card. Shows the provider's real brand
 * logo when we have a glyph for its slug, and falls back to a neutral letter
 * monogram otherwise — including when the logo can't load (offline / unknown
 * slug), so the tile is never blank. Tints gold when its card is the active
 * worker. The logo is decorative (the card title carries the accessible label),
 * so it is aria-hidden.
 */
function ProviderLogo({
  slug,
  label,
  active,
}: {
  slug?: string;
  label: string;
  active?: boolean;
}) {
  const icon = slug ? PROVIDER_ICON[slug] : undefined;
  const [failed, setFailed] = useState(false);
  return (
    <div
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg border text-sm font-semibold",
        active
          ? "border-primary/40 bg-primary/15 text-primary"
          : "border-border bg-muted text-muted-foreground",
      )}
    >
      {icon && !failed ? (
        <img
          src={`/provider-logos/${icon}.svg`}
          alt=""
          aria-hidden="true"
          className="h-5 w-5"
          onError={() => setFailed(true)}
        />
      ) : (
        label.trim().slice(0, 1).toUpperCase() || "?"
      )}
    </div>
  );
}

/**
 * Access-type metadata for a subagent card — deliberately more prominent than a
 * plain billing badge so "subscription login vs API key" reads at a glance. That
 * is exactly what tells the two same-named Anthropic Claude cards apart, and the
 * left accent stripe colour-groups the whole section into subscription (violet)
 * vs API-key (sky). Driven by the backend `billing` field, never a provider name.
 */
const ACCESS_META: Record<
  Billing,
  { label: string; icon: LucideIcon; badge: string; accent: string }
> = {
  subscription: {
    label: "Subscription",
    icon: Sparkles,
    badge: "border-violet-500/40 bg-violet-500/15 text-violet-600 dark:text-violet-300",
    accent: "bg-violet-500/70",
  },
  subscription_or_api: {
    label: "Subscription or API key",
    icon: Sparkles,
    badge: "border-violet-500/40 bg-violet-500/15 text-violet-600 dark:text-violet-300",
    accent: "bg-violet-500/70",
  },
  api: {
    label: "API key",
    icon: CreditCard,
    badge: "border-sky-500/40 bg-sky-500/15 text-sky-600 dark:text-sky-300",
    accent: "bg-sky-500/70",
  },
  local: {
    label: "Local · no key",
    icon: Laptop,
    badge: "border-emerald-500/40 bg-emerald-500/15 text-emerald-600 dark:text-emerald-300",
    accent: "bg-emerald-500/70",
  },
};

/**
 * The prominent access-type badge (subscription vs API key) shown next to a
 * provider card title — larger and higher-contrast than the old billing badge,
 * so the subscription/API split is obvious even when two cards share a name.
 */
function AccessBadge({ billing }: { billing?: Billing }) {
  if (!billing) return null;
  const m = ACCESS_META[billing];
  if (!m) return null;
  const Icon = m.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold",
        m.badge,
      )}
    >
      <Icon className="h-3 w-3" />
      {m.label}
    </span>
  );
}

/**
 * A colour-coded header for one of the two access columns — violet for the
 * subscription-login column, sky for the API-key column — matching the per-card
 * access badges and accent stripes so the split reads instantly.
 */
function ColumnHeader({
  icon: Icon,
  title,
  hint,
  tone,
}: {
  icon: LucideIcon;
  title: string;
  hint: string;
  tone: "violet" | "sky";
}) {
  const toneCls = tone === "violet" ? "text-violet-400" : "text-sky-400";
  return (
    <div className="flex items-center gap-2 px-1 pb-0.5">
      <Icon className={cn("h-4 w-4", toneCls)} />
      <span className={cn("text-xs font-semibold uppercase tracking-wider", toneCls)}>
        {title}
      </span>
      <span className="text-[11px] text-muted-foreground">· {hint}</span>
    </div>
  );
}

/**
 * The one shared shell every provider card is built from — so the whole section
 * reads as a single system instead of seven hand-rolled cards. Owns the layout
 * (logo · title + access badge · subtitle · optional warning · footer actions),
 * the active/interactive highlight, and the left access-accent stripe; each card
 * only supplies its content and its own action controls in `footer`.
 */
function AgentCardShell({
  label,
  slug,
  title,
  billing,
  badge,
  subtitle,
  warning,
  footer,
  active = false,
  interactive = false,
  tooltip,
  className,
  ...rest
}: {
  label: string;
  /** Worker slug driving the brand-logo tile (falls back to the label monogram). */
  slug?: string;
  title: React.ReactNode;
  billing?: Billing;
  badge?: React.ReactNode;
  subtitle?: React.ReactNode;
  warning?: React.ReactNode;
  footer?: React.ReactNode;
  active?: boolean;
  interactive?: boolean;
  /** Native hover tooltip for the whole card (kept separate from `title`,
   * which is the visible card heading). */
  tooltip?: string;
  className?: string;
} & Omit<React.HTMLAttributes<HTMLDivElement>, "title">) {
  return (
    <div
      title={tooltip}
      className={cn(
        "relative flex flex-col gap-3 rounded-2xl border bg-card/60 p-4 backdrop-blur transition-colors",
        active
          ? "border-primary/55 bg-primary/[0.06] shadow-[0_0_0_1px_rgba(255,214,10,0.25),0_0_34px_rgba(255,214,10,0.06)]"
          : interactive
            ? "cursor-pointer border-border hover:border-primary/40 hover:bg-primary/[0.02]"
            : "border-border",
        className,
      )}
      {...rest}
    >
      {/* Left access-accent stripe: violet = subscription, sky = API key — so the
          section colour-splits into the two access types at a glance. The active
          card's gold frame takes over, so the stripe is hidden there. */}
      {billing && !active && (
        <span
          aria-hidden="true"
          className={cn(
            "absolute bottom-4 left-0 top-4 w-[3px] rounded-r-full",
            ACCESS_META[billing]?.accent,
          )}
        />
      )}
      <div className="flex items-start gap-3">
        <ProviderLogo label={label} slug={slug} active={active} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{title}</span>
            {badge}
            <AccessBadge billing={billing} />
          </div>
          {subtitle && (
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {warning}
      {footer && (
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-1">{footer}</div>
      )}
    </div>
  );
}

/** The active / ready / open status pill shared by every provider card. */
function StatusPill({ state }: { state: "active" | "ready" | "open" }) {
  if (state === "active") return <span className="chip-yellow">active</span>;
  if (state === "ready")
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
        ready
      </span>
    );
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      open
    </span>
  );
}

/** A small amber hint line (install / locked) shown inside a provider card. */
function CardHint({
  icon: Icon,
  children,
}: {
  icon: LucideIcon;
  children: React.ReactNode;
}) {
  return (
    <p className="flex items-start gap-1.5 text-[11px] text-amber-600">
      <Icon className="mt-0.5 h-3 w-3 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

/** Connect (gold) action shared by the OAuth-login cards. */
function ConnectButton({
  onClick,
  disabled,
}: {
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
    >
      <LogIn className="h-3.5 w-3.5" />
      Connect
    </button>
  );
}

/** Disconnect (ghost) action shared by the OAuth-login cards. */
function DisconnectButton({
  onClick,
  disabled,
}: {
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
    >
      <LogOut className="h-3.5 w-3.5" />
      Disconnect
    </button>
  );
}

function CodexConnectionCard({
  status,
  row,
  onChanged,
}: {
  status: CodexStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
}) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected);
  const installed = status?.installed ?? false;
  const isActive = Boolean(row?.is_active_brain);
  const email =
    status?.user_email ??
    status?.account_label ??
    status?.accountLabel ??
    null;
  const detail = connected
    ? email
      ? `Connected as ${email}`
      : status?.message || "Connected via ChatGPT"
    : status?.message || "ChatGPT login not connected";

  async function connect() {
    setPending(true);
    try {
      await startCodexLogin();
      pushToast("info", "Codex login started — finish it in the browser window");
      await onChanged();
      // The login finishes asynchronously in the spawned console/browser, so
      // poll until the CLI reports connected; then the card flips to selectable
      // on its own — no manual reload needed.
      void pollStatusUntilConnected("/api/codex/status", onChanged).then((ok) => {
        if (ok) pushToast("success", "Codex connected — now selectable as a Jarvis-Agent");
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await codexLogout();
      pushToast("info", "Codex login disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AgentCardShell
      label="OpenAI Codex"
      slug={row?.jarvis}
      title="OpenAI Codex"
      billing={row?.billing}
      active={isActive}
      badge={<StatusPill state={isActive ? "active" : connected ? "ready" : "open"} />}
      subtitle={detail}
      warning={
        !installed && <CardHint icon={Terminal}>Install Codex before connecting.</CardHint>
      }
      footer={
        <>
          {/* When connected, the same "Set active" control as the other
              provider cards — so this subscription login is selectable right
              here, not only via a duplicate card further down. */}
          {connected && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
        </>
      }
    />
  );
}

function AntigravityConnectionCard({
  status,
  row,
  onChanged,
}: {
  status: AntigravityStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
}) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected);
  const installed = status?.installed ?? false;
  const isActive = Boolean(row?.is_active_brain);
  const detail = connected
    ? status?.user_email
      ? `Connected as ${status.user_email}`
      : status?.message || "Connected"
    : status?.message || "Google login not connected";

  async function connect() {
    setPending(true);
    try {
      await loginAntigravity();
      pushToast("info", "Google login started — finish it in the browser window");
      await onChanged();
      // Same as Codex: the Google sign-in completes asynchronously, so poll
      // until agy reports connected and the card unlocks on its own.
      void pollStatusUntilConnected("/api/antigravity/status", onChanged).then((ok) => {
        if (ok) pushToast("success", "Antigravity connected — now selectable as a Jarvis-Agent");
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await logoutAntigravity();
      pushToast("info", "Google login disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AgentCardShell
      label="Antigravity"
      slug={row?.jarvis}
      title="Antigravity"
      billing={row?.billing}
      active={isActive}
      badge={<StatusPill state={isActive ? "active" : connected ? "ready" : "open"} />}
      subtitle={detail}
      warning={
        !installed && (
          <CardHint icon={Terminal}>
            Install Antigravity or the Gemini CLI before connecting.
          </CardHint>
        )
      }
      footer={
        <>
          {connected && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
        </>
      }
    />
  );
}

function ClaudeConnectionCard({
  status,
  row,
  onChanged,
}: {
  status: ClaudeStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
}) {
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const { activating, activate } = useSubagentActivate(row, onChanged);
  const connected = Boolean(status?.connected);
  const installed = status?.installed ?? false;
  // Claude has ONE subagent slug (claude-api) reached by EITHER the Claude Max
  // OAuth login OR an Anthropic API key — split into two sibling cards (mirror
  // of Codex/OpenAI + Antigravity/Gemini). This subscription card lights up only
  // when claude-api is the active worker AND it is running over the OAuth login
  // (mode != "api_key"); the API key card owns the api_key mode. So exactly one
  // of the two ever shows "active".
  const isActive = Boolean(row?.is_active_brain) && status?.mode !== "api_key";
  // A subscription login shows the signed-in account + tier ("Connected as
  // ruben@… · Claude Max"); not connected shows how to sign in. The API-key
  // alternative now lives on its own card below, not here.
  const detail = connected
    ? status?.user_email
      ? `Connected as ${status.user_email}${
          status.account_label ? ` · ${status.account_label}` : ""
        }`
      : status?.message || "Connected"
    : status?.message || "Claude login not connected";

  async function connect() {
    setPending(true);
    try {
      await loginClaude();
      pushToast("info", "Claude login started — finish it in the terminal window");
      await onChanged();
      // The sign-in finishes asynchronously in the spawned console, so poll
      // until the CLI reports connected; the card then unlocks on its own.
      void pollStatusUntilConnected("/api/claude/status", onChanged).then((ok) => {
        if (ok) pushToast("success", "Claude connected — now selectable as a Jarvis-Agent");
      });
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  async function disconnect() {
    setPending(true);
    try {
      await logoutClaude();
      pushToast("info", "Claude subscription disconnected");
      await onChanged();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <AgentCardShell
      label="Anthropic Claude"
      slug={row?.jarvis}
      title="Anthropic Claude"
      billing="subscription"
      active={isActive}
      badge={<StatusPill state={isActive ? "active" : connected ? "ready" : "open"} />}
      subtitle={detail}
      warning={
        !installed && (
          <CardHint icon={Terminal}>Install the Claude CLI before connecting.</CardHint>
        )
      }
      footer={
        <>
          {connected && row && (
            <SubagentActiveControl
              row={row}
              active={isActive}
              activating={activating}
              onActivate={activate}
            />
          )}
          {connected ? (
            <DisconnectButton onClick={disconnect} disabled={pending} />
          ) : (
            <ConnectButton onClick={connect} disabled={pending || !installed} />
          )}
        </>
      }
    />
  );
}

/**
 * The Anthropic Claude (API) card — the per-token sibling of the subscription
 * card above, built to behave EXACTLY like the OpenAI / Google Gemini subagent
 * cards (`SubagentProviderCard`): there is NO key field here. The Anthropic API
 * key is entered ONCE on the "Claude (API-Key)" brain provider above, and this
 * card just reflects whether that key is set (`row.key_set`) and lets the user
 * pick Claude-on-the-key as the heavy-task worker — locked with a pointer to the
 * Brain section until the key exists, exactly like OpenAI/Gemini.
 *
 * Claude has ONE subagent slug (claude-api) reached by either auth, so this card
 * and the subscription card both drive `useSubagentActivate(row)`. To keep only
 * one of the two lit, "active" here means claude-api is the active worker AND it
 * is running over the API key (status mode === "api_key"); the subscription card
 * owns every other mode.
 */
function ClaudeApiCard({
  status,
  row,
  onChanged,
}: {
  status: ClaudeStatus | null;
  row: SubagentMappingRow | undefined;
  onChanged: () => void | Promise<void>;
}) {
  const { activating, activate } = useSubagentActivate(row, onChanged);
  // Mirror the other API cards: ready/locked tracks the stored brain key.
  const keySet = Boolean(row?.key_set);
  const isActive = Boolean(row?.is_active_brain) && status?.mode === "api_key";

  // Click anywhere on the card activates — except the radio/label (own handler)
  // so a single user click never fires activate() twice (mirror of the others).
  function handleCardActivate(e: React.MouseEvent<HTMLDivElement>) {
    const target = e.target as HTMLElement | null;
    if (target && (target.closest("input") || target.closest("label"))) {
      return;
    }
    void activate();
  }

  return (
    <AgentCardShell
      label="Anthropic Claude"
      slug={row?.jarvis}
      title="Anthropic Claude"
      billing="api"
      active={isActive}
      interactive={keySet && !isActive}
      onClick={handleCardActivate}
      onDoubleClick={handleCardActivate}
      tooltip={
        isActive
          ? "This Jarvis-Agent provider is active"
          : keySet
            ? "Activate this Jarvis-Agent provider"
            : "Add the Claude API key in the Brain section first"
      }
      badge={<StatusPill state={isActive ? "active" : keySet ? "ready" : "open"} />}
      subtitle="API key · billed per token"
      warning={
        !keySet && (
          <CardHint icon={Lock}>
            Locked &mdash; add the <strong>Claude (API-Key)</strong> key in the
            Brain tab to unlock it.
          </CardHint>
        )
      }
      footer={
        row && (
          <SubagentActiveControl
            row={row}
            active={isActive}
            activating={activating}
            onActivate={activate}
          />
        )
      }
    />
  );
}

/**
 * Shared "activate this subagent provider" action (POST /api/subagent/switch,
 * 3-layer persist). Used by the generic provider cards AND the Codex /
 * Antigravity subscription cards, so every selectable card behaves identically
 * — including the subscription logins, which previously had no "Set active".
 */
function useSubagentActivate(
  row: SubagentMappingRow | undefined,
  onSwitched: () => void | Promise<void>,
) {
  const [activating, setActivating] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  const activate = useCallback(async () => {
    if (!row || row.is_active_brain || activating) return;
    const label = PROVIDER_LABELS[row.jarvis] ?? row.jarvis;
    if (!row.key_set) {
      pushToast(
        "warning",
        row.jarvis === "openai-codex"
          ? `${label}: connect the ChatGPT login first.`
          : row.jarvis === "antigravity"
            ? `${label}: connect the Google login first.`
            : `${label}: set the API key on the brain provider above first.`,
      );
      return;
    }
    setActivating(true);
    window.dispatchEvent(
      new CustomEvent("jarvis:provider-selection-pending", {
        detail: { section: "subagents", provider: row.jarvis },
      }),
    );
    try {
      const result = await switchSubagentProvider(row.jarvis);
      const note = result.restart_required ? " (active from next restart)" : "";
      pushToast("success", `Jarvis-Agent → ${label}${note}`);
      window.dispatchEvent(new CustomEvent("jarvis:agent-switched"));
      await onSwitched();
    } catch (e) {
      window.dispatchEvent(
        new CustomEvent("jarvis:provider-switch-failed", {
          detail: { section: "subagents", provider: row.jarvis },
        }),
      );
      pushToast("error", (e as Error).message);
    } finally {
      setActivating(false);
    }
  }, [row, activating, pushToast, onSwitched]);

  return { activating, activate };
}

/**
 * One sub-agent-capable provider, styled to match the `ProviderCard` in
 * `ApiKeysView` (header + badge + id·auth sub-line + active highlight + the
 * "Set active" radio). Clicking the card or the radio switches the Heavy-Task
 * subagent provider via `POST /api/subagent/switch` (3-layer persist). The key
 * itself is managed by the brain-provider card above; a provider with no key
 * cannot be activated (warning toast instead of a silent no-op).
 */
function SubagentProviderCard({
  row,
  onSwitched,
}: {
  row: SubagentMappingRow;
  onSwitched: () => void | Promise<void>;
}) {
  const label = PROVIDER_LABELS[row.jarvis] ?? row.jarvis;
  const { activating, activate } = useSubagentActivate(row, onSwitched);

  // Click anywhere on the card activates — except on the radio/label (which
  // has its own handler) so a single user click never fires activate() twice.
  function handleCardActivate(e: React.MouseEvent<HTMLDivElement>) {
    const target = e.target as HTMLElement | null;
    if (target && (target.closest("input") || target.closest("label"))) {
      return;
    }
    void activate();
  }

  return (
    <AgentCardShell
      label={label}
      slug={row.jarvis}
      title={label}
      billing={row.billing}
      active={row.is_active_brain}
      interactive={row.key_set && !row.is_active_brain}
      onClick={handleCardActivate}
      onDoubleClick={handleCardActivate}
      tooltip={
        row.is_active_brain
          ? "This Jarvis-Agent provider is active"
          : row.key_set
            ? "Activate this Jarvis-Agent provider"
            : "Set an API key first"
      }
      badge={
        <StatusPill
          state={row.is_active_brain ? "active" : row.key_set ? "ready" : "open"}
        />
      }
      subtitle="Reuses the matching key from the Brain tab"
      warning={
        !row.key_set && (
          <CardHint icon={Lock}>
            {row.jarvis === "openai-codex" ? (
              <>
                Locked &mdash; connect <strong>{label}</strong> with ChatGPT above
                to unlock it.
              </>
            ) : row.jarvis === "antigravity" ? (
              <>
                Locked &mdash; connect <strong>{label}</strong> with Google above
                to unlock it.
              </>
            ) : (
              <>
                Locked &mdash; add the <strong>{label}</strong> key in the Brain
                tab to unlock it.
              </>
            )}
          </CardHint>
        )
      }
      footer={
        <SubagentActiveControl
          row={row}
          activating={activating}
          onActivate={activate}
        />
      }
    />
  );
}

/**
 * Radio-based active toggle, mirroring `ActiveControl` in `ApiKeysView`.
 * Source of truth stays `row.is_active_brain` from `/api/openclaw/status`;
 * the radio reflects the server state and is not held locally.
 * `name="active-subagent"` gives native single-select across the cards.
 * The radio is NOT disabled when the key is missing — instead `activate()`
 * routes a warning toast so every click gets a reaction.
 */
function SubagentActiveControl({
  row,
  activating,
  onActivate,
  active,
}: {
  row: SubagentMappingRow;
  activating: boolean;
  onActivate: () => void;
  /**
   * Explicit active state, overriding `row.is_active_brain`. The two Claude
   * cards share ONE slug (claude-api), so the raw row flag would light BOTH
   * radios at once. Each Claude card passes its mode-split flag here so only the
   * card matching the live auth (subscription vs API key) shows "Active". Other
   * cards omit it and fall back to the row flag.
   */
  active?: boolean;
}) {
  const isActive = active ?? row.is_active_brain;
  const labelTitle = isActive
    ? "This Jarvis-Agent provider is active"
    : row.key_set
      ? "Activate this Jarvis-Agent provider"
      : "Set an API key first";

  return (
    <label
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex shrink-0 cursor-pointer select-none items-center gap-1.5 text-xs",
        isActive
          ? "font-medium text-primary"
          : row.key_set
            ? "text-muted-foreground hover:text-foreground"
            : "text-muted-foreground/70",
      )}
      title={labelTitle}
    >
      <input
        type="radio"
        name="active-subagent"
        checked={isActive}
        onChange={() => onActivate()}
        disabled={activating}
        className="accent-primary"
      />
      {activating ? "Activating…" : isActive ? "Active" : "Set active"}
    </label>
  );
}


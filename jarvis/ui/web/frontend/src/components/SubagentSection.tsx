import { useCallback, useEffect, useState } from "react";
import { ArrowUp, Bot, CheckCircle2, Lock, LogIn, LogOut, Terminal, XCircle } from "lucide-react";
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
import { ProviderBillingBadge } from "@/components/ProviderBillingBadge";

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
 * Data source is `GET /api/openclaw/status`; the switch posts to
 * `POST /api/subagent/switch` (3-layer persist). This is its own section
 * rather than a fourth `ProviderTier` because the status payload differs.
 * The endpoint path and the JSON keys below are the server contract; the
 * user-facing UI never says anything but "Subagent".
 */

interface SubagentMappingRow {
  jarvis: string;
  /** Server contract: the engine-side provider slug (e.g. "google"). */
  openclaw: string;
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

export function SubagentSection() {
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
        fetch("/api/openclaw/status"),
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
    window.addEventListener("jarvis:subagent-switched", onChange);
    window.addEventListener("jarvis:secret-configured", onChange);
    return () => {
      window.removeEventListener("jarvis:brain-switched", onChange);
      window.removeEventListener("jarvis:subagent-switched", onChange);
      window.removeEventListener("jarvis:secret-configured", onChange);
    };
  }, [reload]);

  if (error) {
    return (
      <section>
        <SectionHeader label={t("apikeys_view.tier_subagent")} />
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

  return (
    <section>
      <SectionHeader label={t("apikeys_view.tier_subagent")} />
      {/* The coupling is non-obvious: subagent providers have no key field of
          their own — they reuse the Brain-provider keys set above. Spell it out
          so users know where to add the key instead of looking for an input
          that isn't here. */}
      <p className="mb-3 flex items-start gap-2 rounded-md border border-primary/25 bg-primary/[0.04] px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        <ArrowUp className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
        <span>
          Most subagents reuse the API keys from the{" "}
          <strong className="text-foreground">Brain</strong> section above.
          Codex uses the ChatGPT login here, and Antigravity uses the Google
          login here. Connect the provider first, then pick which one runs heavy
          background tasks.
        </span>
      </p>
      <ul className="space-y-3">
        <li>
          <BridgeCard status={bridge} />
        </li>
        <li>
          <SubagentModelCard status={bridge} onSaved={reload} />
        </li>
        {codexRow && (
          <li>
            <CodexConnectionCard
              status={codexStatus}
              row={codexRow}
              onChanged={reload}
            />
          </li>
        )}
        {antigravityRow && (
          <li>
            <AntigravityConnectionCard
              status={antigravityStatus}
              row={antigravityRow}
              onChanged={reload}
            />
          </li>
        )}
        {claudeRow && (
          <li>
            <ClaudeConnectionCard
              status={claudeStatus}
              row={claudeRow}
              onChanged={reload}
            />
          </li>
        )}
        {claudeRow && (
          <li>
            <ClaudeApiCard
              status={claudeStatus}
              row={claudeRow}
              onChanged={reload}
            />
          </li>
        )}
        {providerRows.map((row) => (
          <li key={row.jarvis}>
            <SubagentProviderCard row={row} onSwitched={reload} />
          </li>
        ))}
      </ul>
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
          onSave={async (model) => {
            const r = await saveSubagentModel(model);
            window.dispatchEvent(new Event("jarvis:subagent-switched"));
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

/**
 * The subagent bridge meta-card — read-only configuration (engine state, pin,
 * active worker, model, time-cap). Shares the active-card highlight with
 * the provider cards so the section reads as one system. Engine internals
 * (binary path etc.) are reduced to an "installed"/"not installed"
 * status — the concrete path is developer noise and is not surfaced.
 */
function BridgeCard({ status }: { status: SubagentStatus }) {
  const installed = Boolean(status.binary_detected);
  const live = status.enabled && installed;
  const stateLabel = !status.configured
    ? "not configured"
    : !installed
      ? "engine not installed"
      : status.enabled
        ? "enabled"
        : "disabled (enabled = false)";

  const modelLabel =
    status.model_resolved ??
    (status.provider_slug ? "(follows brain.primary, no model resolvable)" : "—");

  return (
    <div className="card-outline space-y-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Subagent bridge</span>
            {live ? (
              <span className="chip-yellow">active</span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                inactive
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            External subagent for heavy tasks (read a repo, build a feature,
            reproduce a bug). It reuses the same brain-provider keys you set
            above &mdash; no separate input field needed. Every registered MCP
            server is handed to the subagent at pre-boot with a mission-isolated
            state directory.
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Bridge</dt>
        <dd className="font-mono">
          {live ? (
            <CheckCircle2 className="mr-1 inline h-3 w-3 text-emerald-500" />
          ) : (
            <XCircle className="mr-1 inline h-3 w-3 text-muted-foreground" />
          )}
          {stateLabel}
        </dd>

        <dt className="text-muted-foreground">Engine</dt>
        <dd className="font-mono">{installed ? "installed" : "not installed"}</dd>

        <dt className="text-muted-foreground">Pin version</dt>
        <dd className="font-mono">{status.version_pin ?? "—"}</dd>

        <dt className="text-muted-foreground">Active worker</dt>
        <dd className="font-mono">
          <strong>{PROVIDER_LABELS[status.brain_primary] ?? status.brain_primary}</strong>
        </dd>

        <dt className="text-muted-foreground">Model</dt>
        <dd className="break-all font-mono">
          {modelLabel}
          {status.model_override && (
            <span className="ml-2 text-muted-foreground">(override from config)</span>
          )}
        </dd>

        {status.time_cap_min !== null && (
          <>
            <dt className="text-muted-foreground">Time cap</dt>
            <dd className="font-mono">
              {status.time_cap_min} min · max. {status.concurrency} parallel
            </dd>
          </>
        )}
      </dl>
    </div>
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
        if (ok) pushToast("success", "Codex connected — now selectable as a subagent");
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
    <div
      className={cn(
        "card-outline space-y-3 p-4 transition-colors",
        isActive && "border-primary bg-primary/[0.06] ring-1 ring-primary/30",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">OpenAI Codex (Subscription)</span>
            {isActive ? (
              <span className="chip-yellow">active</span>
            ) : connected ? (
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
                ready
              </span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                open
              </span>
            )}
            {row && <ProviderBillingBadge billing={row.billing} />}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {detail}
          </p>
          {!installed && (
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-amber-600">
              <Terminal className="h-3 w-3 shrink-0" />
              <span>Install Codex before connecting.</span>
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3">
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
            <button
              type="button"
              onClick={disconnect}
              disabled={pending}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <LogOut className="h-3.5 w-3.5" />
              Disconnect
            </button>
          ) : (
            <button
              type="button"
              onClick={connect}
              disabled={pending || !installed}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              <LogIn className="h-3.5 w-3.5" />
              Connect
            </button>
          )}
        </div>
      </div>
    </div>
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
        if (ok) pushToast("success", "Antigravity connected — now selectable as a subagent");
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
    <div
      className={cn(
        "card-outline space-y-3 p-4 transition-colors",
        isActive && "border-primary bg-primary/[0.06] ring-1 ring-primary/30",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Antigravity (Subscription)</span>
            {isActive ? (
              <span className="chip-yellow">active</span>
            ) : connected ? (
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
                ready
              </span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                open
              </span>
            )}
            {row && <ProviderBillingBadge billing={row.billing} />}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {detail}
          </p>
          {!installed && (
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-amber-600">
              <Terminal className="h-3 w-3 shrink-0" />
              <span>Install Antigravity or the Gemini CLI before connecting.</span>
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {connected && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
          {connected ? (
            <button
              type="button"
              onClick={disconnect}
              disabled={pending}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <LogOut className="h-3.5 w-3.5" />
              Disconnect
            </button>
          ) : (
            <button
              type="button"
              onClick={connect}
              disabled={pending || !installed}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              <LogIn className="h-3.5 w-3.5" />
              Connect
            </button>
          )}
        </div>
      </div>
    </div>
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
  // alex@… · Claude Max"); not connected shows how to sign in. The API-key
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
        if (ok) pushToast("success", "Claude connected — now selectable as a subagent");
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
    <div
      className={cn(
        "card-outline space-y-3 p-4 transition-colors",
        isActive && "border-primary bg-primary/[0.06] ring-1 ring-primary/30",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Anthropic Claude (Subscription)</span>
            {isActive ? (
              <span className="chip-yellow">active</span>
            ) : connected ? (
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
                ready
              </span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                open
              </span>
            )}
            {/* Split card: this one is the subscription login only. */}
            <ProviderBillingBadge billing="subscription" />
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            {detail}
          </p>
          {!installed && (
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-amber-600">
              <Terminal className="h-3 w-3 shrink-0" />
              <span>Install the Claude CLI before connecting.</span>
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-3">
          {connected && row && (
            <SubagentActiveControl
              row={row}
              activating={activating}
              onActivate={activate}
            />
          )}
          {connected ? (
            <button
              type="button"
              onClick={disconnect}
              disabled={pending}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <LogOut className="h-3.5 w-3.5" />
              Disconnect
            </button>
          ) : (
            <button
              type="button"
              onClick={connect}
              disabled={pending || !installed}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              <LogIn className="h-3.5 w-3.5" />
              Connect
            </button>
          )}
        </div>
      </div>
    </div>
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
    <div
      onClick={handleCardActivate}
      onDoubleClick={handleCardActivate}
      title={
        isActive
          ? "This subagent provider is active"
          : keySet
            ? "Activate this subagent provider"
            : "Add the Claude API key in the Brain section first"
      }
      className={cn(
        "card-outline space-y-2 p-4 transition-colors",
        isActive
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : keySet
            ? "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]"
            : "opacity-95",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">Anthropic Claude (API)</span>
            {isActive ? (
              <span className="chip-yellow">active</span>
            ) : keySet ? (
              <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
                ready
              </span>
            ) : (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                open
              </span>
            )}
            <ProviderBillingBadge billing="api" />
          </div>
          {row && (
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              <code className="font-mono">
                {row.jarvis} → {row.openclaw}
              </code>
              {" · "}
              <span className="font-mono">
                {row.env_var}
                {row.env_fallback && ` / ${row.env_fallback}`}
              </span>
            </p>
          )}
        </div>

        {row && (
          <SubagentActiveControl
            row={row}
            activating={activating}
            onActivate={activate}
          />
        )}
      </div>

      {!keySet && (
        <p className="flex items-center gap-1.5 text-[11px] text-amber-600">
          <Lock className="h-3 w-3 shrink-0" />
          <span>
            Locked &mdash; add the <strong>Claude (API-Key)</strong> key in the
            Brain section above to unlock it.
          </span>
        </p>
      )}
    </div>
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
    try {
      const result = await switchSubagentProvider(row.jarvis);
      const note = result.restart_required ? " (active from next restart)" : "";
      pushToast("success", `Subagent → ${label}${note}`);
      window.dispatchEvent(new CustomEvent("jarvis:subagent-switched"));
      await onSwitched();
    } catch (e) {
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
    <div
      onClick={handleCardActivate}
      onDoubleClick={handleCardActivate}
      title={
        row.is_active_brain
          ? "This subagent provider is active"
          : row.key_set
            ? "Activate this subagent provider"
            : "Set an API key first"
      }
      className={cn(
        "card-outline space-y-2 p-4 transition-colors",
        row.is_active_brain
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : row.key_set
            ? "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]"
            : "opacity-95",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{label}</span>
            <SubagentStatusBadge row={row} />
            <ProviderBillingBadge billing={row.billing} />
          </div>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <code className="font-mono">
              {row.jarvis} → {row.openclaw}
            </code>
            {" · "}
            <span className="font-mono">
              {row.env_var}
              {row.env_fallback && ` / ${row.env_fallback}`}
            </span>
          </p>
        </div>

        <SubagentActiveControl
          row={row}
          activating={activating}
          onActivate={activate}
        />
      </div>

      {!row.key_set && (
        <p className="flex items-center gap-1.5 text-[11px] text-amber-600">
          <Lock className="h-3 w-3 shrink-0" />
          <span>
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
                section above to unlock it.
              </>
            )}
          </span>
        </p>
      )}
    </div>
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
}: {
  row: SubagentMappingRow;
  activating: boolean;
  onActivate: () => void;
}) {
  const labelTitle = row.is_active_brain
    ? "This subagent provider is active"
    : row.key_set
      ? "Activate this subagent provider"
      : "Set an API key first";

  return (
    <label
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      className={cn(
        "inline-flex shrink-0 cursor-pointer select-none items-center gap-1.5 text-xs",
        row.is_active_brain
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
        checked={row.is_active_brain}
        onChange={() => onActivate()}
        disabled={activating}
        className="accent-primary"
      />
      {activating ? "Activating…" : "Set active"}
    </label>
  );
}

/**
 * Mirrors the three StatusBadge variants from `ApiKeysView` (chip-yellow
 * "active" / emerald "ready" / muted "open") so the subagent cards are
 * visually indistinguishable from the API-key cards above.
 */
function SubagentStatusBadge({ row }: { row: SubagentMappingRow }) {
  if (row.is_active_brain) return <span className="chip-yellow">active</span>;
  if (row.key_set) {
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-emerald-600">
        ready
      </span>
    );
  }
  return (
    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
      open
    </span>
  );
}
